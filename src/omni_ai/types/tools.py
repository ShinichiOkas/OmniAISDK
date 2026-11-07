"""ツール定義型と ``@omni_tool``（ADR-12）。"""

from __future__ import annotations

import copy
import inspect
import re
import uuid
from collections.abc import Callable
from enum import StrEnum
from typing import Any, Union, overload

import anyio.to_thread
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, create_model

__all__ = [
    "BuiltinTool",
    "OmniTool",
    "ToolCall",
    "ToolDefinition",
    "ToolSpec",
    "new_tool_call_id",
    "normalize_json_schema",
    "omni_tool",
]


class BuiltinTool(StrEnum):
    WEB_SEARCH = "builtin::web_search"
    CODE_EXECUTION = "builtin::code_execution"


class ToolDefinition(BaseModel):
    """プロバイダ非依存のツール定義。"""

    name: str
    description: str = ""
    parameters_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    strict: bool = True


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # アダプターだけが読み書きする不透明な状態 (ADR-11)
    provider_state: dict[str, Any] = Field(default_factory=dict)


def new_tool_call_id() -> str:
    """プロバイダが id を返さないときに SDK が採番する。"""
    return f"call_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# JSON Schema 正規化
# ---------------------------------------------------------------------------

_REF_RE = re.compile(r"^#/\$defs/(.+)$")


def normalize_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """pydantic の出力を、各社が受け付ける JSON Schema サブセットに寄せる。

    - ``$defs`` / ``$ref`` を展開する（再帰参照は展開せず ``{"type": "object"}`` に縮退）
    - スキーマ節点のメタデータとしての ``title`` を取り除く（プロパティ名の ``title`` は残す）
    """
    defs: dict[str, Any] = dict(schema.get("$defs", {}))

    def walk(node: Any, stack: tuple[str, ...]) -> Any:
        if isinstance(node, list):
            return [walk(n, stack) for n in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and (m := _REF_RE.match(ref)):
            name = m.group(1)
            if name in stack or name not in defs:
                return {"type": "object"}
            merged = {**copy.deepcopy(defs[name]), **{k: v for k, v in node.items() if k != "$ref"}}
            return walk(merged, (*stack, name))
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in ("$defs", "title"):
                continue
            if key == "properties" and isinstance(value, dict):
                out[key] = {pname: walk(pschema, stack) for pname, pschema in value.items()}
            else:
                out[key] = walk(value, stack)
        return out

    result = walk(schema, ())
    assert isinstance(result, dict)
    return result


# ---------------------------------------------------------------------------
# @omni_tool
# ---------------------------------------------------------------------------

_ARGS_HEADER = re.compile(r"^\s*(Args|Arguments|Parameters)\s*:\s*$")
_ARG_LINE = re.compile(r"^\s*(\*{0,2}\w+)\s*(?:\([^)]*\))?\s*:\s*(.*)$")


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Google 形式の docstring から (要約, {引数名: 説明}) を取り出す。失敗しても空を返すだけ。"""
    lines = inspect.cleandoc(doc).splitlines() if doc else []
    summary: list[str] = []
    args: dict[str, str] = {}
    i = 0
    while i < len(lines) and not _ARGS_HEADER.match(lines[i]):
        summary.append(lines[i])
        i += 1
    if i < len(lines):
        i += 1
        current: str | None = None
        base_indent: int | None = None
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            indent = len(line) - len(line.lstrip())
            if base_indent is None:
                base_indent = indent
            if indent < base_indent or (
                indent == base_indent and line.rstrip().endswith(":") and not _ARG_LINE.match(line)
            ):
                break
            m = _ARG_LINE.match(line)
            if indent == base_indent and m:
                current = m.group(1).lstrip("*")
                args[current] = m.group(2).strip()
            elif current is not None:
                args[current] = (args[current] + " " + line.strip()).strip()
            i += 1
    return "\n".join(summary).strip(), args


class OmniTool(ToolDefinition):
    """``@omni_tool`` が返す、呼び出し可能なツール定義。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    handler: Callable[..., Any] = Field(exclude=True, repr=False)
    is_async: bool = False
    _args_model: type[BaseModel] | None = PrivateAttr(default=None)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.handler(*args, **kwargs)

    def validate_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """モデルが出した引数を検証して型変換する。失敗時は pydantic.ValidationError。"""
        if self._args_model is None:
            return dict(arguments)
        model = self._args_model.model_validate(arguments)
        return {name: getattr(model, name) for name in self._args_model.model_fields}

    async def ainvoke(self, arguments: dict[str, Any]) -> Any:
        kwargs = self.validate_arguments(arguments)
        if self.is_async:
            return await self.handler(**kwargs)
        return await anyio.to_thread.run_sync(lambda: self.handler(**kwargs))

    def invoke(self, arguments: dict[str, Any]) -> Any:
        kwargs = self.validate_arguments(arguments)
        if self.is_async:
            raise TypeError(f"tool {self.name!r} is async; use ainvoke()")
        return self.handler(**kwargs)


def _build_tool(
    func: Callable[..., Any], name: str | None, description: str | None, strict: bool
) -> OmniTool:
    sig = inspect.signature(func)
    summary, arg_docs = _parse_docstring(func.__doc__ or "")
    fields: dict[str, Any] = {}
    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            raise TypeError(f"@omni_tool cannot wrap methods (parameter {pname!r}); wrap a plain function")
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            raise TypeError(f"@omni_tool does not support *args/**kwargs (parameter {pname!r})")
        if param.kind is inspect.Parameter.POSITIONAL_ONLY:
            raise TypeError(f"@omni_tool does not support positional-only parameters ({pname!r})")
        annotation = param.annotation if param.annotation is not inspect.Parameter.empty else str
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[pname] = (annotation, Field(default, description=arg_docs.get(pname) or None))
    args_model = create_model(f"{func.__name__}_args", **fields)
    tool = OmniTool(
        name=name or func.__name__,
        description=description if description is not None else summary,
        parameters_schema=normalize_json_schema(args_model.model_json_schema()),
        strict=strict,
        handler=func,
        is_async=inspect.iscoroutinefunction(func),
    )
    tool._args_model = args_model
    return tool


@overload
def omni_tool(func: Callable[..., Any], /) -> OmniTool: ...
@overload
def omni_tool(
    *, name: str | None = None, description: str | None = None, strict: bool = True
) -> Callable[[Callable[..., Any]], OmniTool]: ...


def omni_tool(
    func: Callable[..., Any] | None = None,
    /,
    *,
    name: str | None = None,
    description: str | None = None,
    strict: bool = True,
) -> OmniTool | Callable[[Callable[..., Any]], OmniTool]:
    """Python 関数から ``OmniTool`` を生成する（``@omni_tool`` / ``@omni_tool(name=...)``）。"""
    if func is not None:
        return _build_tool(func, name, description, strict)

    def decorator(f: Callable[..., Any]) -> OmniTool:
        return _build_tool(f, name, description, strict)

    return decorator


ToolSpec = Union[ToolDefinition, OmniTool, BuiltinTool]  # noqa: UP007

# 再エクスポート用（ToolRunner が使う）
ToolArgumentsError = ValidationError
