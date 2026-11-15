"""MLXLMChatAdapter — mlx-lm（Apple MLX）によるプロセス内の Chat。

extras ``[mlx]``。Apple Silicon の macOS 専用。

- サーバーを立てず、呼び出し側のプロセスでモデルを読み込んで生成する（初回利用時に 1 度だけ読み込む）
- ``model`` は Hugging Face のリポジトリ名（``mlx-community/gemma-4-e2b-it-4bit``）か、変換済みモデルの
  **ディレクトリパス**。既定のモデルは持たない。同時に保持するモデルは 1 本
  （別のモデルを指定すると読み替える）
- 生成は 1 本ずつ直列に行う（MLX の GPU 実行は並行に向かない）。async は別スレッドで回す
- 思考モードはモデル（chat template）の既定に従う。呼び出しごとに
  ``provider_options={"mlx-lm": {"think": bool}}`` で切り替える。思考は tokenizer の
  ``think_start`` / ``think_end`` で本文から分け、``reasoning`` に入れる
- 未対応（``UnsupportedFeatureError``）: ツール呼び出し・構造化出力・画像入力（次の段で実装する）
"""

from __future__ import annotations

import contextlib
import logging
import platform
import queue
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol

import anyio.to_thread
from pydantic import BaseModel

from ...capabilities import ChatCapability, ToolChoice
from ...exceptions import (
    InvalidRequestError,
    NotFoundError,
    OmniError,
    ProviderTimeoutError,
    StreamInterruptedError,
    UnsupportedFeatureError,
)
from ...types import (
    ChatMessage,
    ChatResponse,
    MediaPart,
    ResponseMeta,
    Role,
    StreamEvent,
    ToolSpec,
    Usage,
)
from ...types.common import FinishReason

__all__ = ["MLXBackend", "MLXLMChatAdapter", "is_supported_platform"]

logger = logging.getLogger("omni_ai")

PROVIDER = "mlx-lm"
DEFAULT_MAX_TOKENS = 8192
# provider_options={"mlx-lm": {...}} で受け付けるキー
_SAMPLER_KEYS = ("top_p", "top_k", "min_p")
_OPTION_KEYS = frozenset({"think", *_SAMPLER_KEYS})


def is_supported_platform() -> bool:
    """mlx-lm が動くのは Apple Silicon の macOS だけ。"""
    return sys.platform == "darwin" and platform.machine() == "arm64"


# ----------------------------------------------------------------------------- 実行部（差し替え可能）


@dataclass
class GenChunk:
    """生成の 1 区切り（mlx-lm の ``GenerationResponse`` から必要なものだけ）。"""

    text: str
    prompt_tokens: int = 0
    generation_tokens: int = 0
    finish_reason: str | None = None


class Backend(Protocol):
    """モデルの読み込み・プロンプトの組み立て・生成。試験では偽物に差し替える。"""

    def load(self, model: str) -> tuple[Any, Any]: ...

    def render(self, tokenizer: Any, messages: list[dict[str, Any]], think: bool | None) -> str: ...

    def think_markers(self, tokenizer: Any) -> tuple[str, str] | None: ...

    def generate(
        self,
        model: Any,
        tokenizer: Any,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float | None,
        sampler_options: dict[str, Any],
    ) -> Iterator[GenChunk]: ...

    def release(self) -> None: ...


class MLXBackend:
    """本物の mlx-lm を呼ぶ実行部。"""

    def load(self, model: str) -> tuple[Any, Any]:
        try:
            from mlx_lm import load
        except ImportError as exc:  # pragma: no cover - extras 未導入
            raise ImportError(
                'MLXLMChatAdapter requires mlx-lm: pip install "omni-ai-sdk[mlx]" (Apple Silicon macOS only)'
            ) from exc
        loaded = load(model)
        return loaded[0], loaded[1]

    def render(self, tokenizer: Any, messages: list[dict[str, Any]], think: bool | None) -> str:
        kw: dict[str, Any] = {}
        if think is not None:
            # mlx-lm の TokenizerWrapper がモデルごとの引数名（enable_thinking など）に読み替える
            kw["enable_thinking"] = think
        return str(tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False, **kw))

    def think_markers(self, tokenizer: Any) -> tuple[str, str] | None:
        start = getattr(tokenizer, "think_start", None)
        end = getattr(tokenizer, "think_end", None)
        if isinstance(start, str) and isinstance(end, str) and start and end:
            return start, end
        return None

    def generate(
        self,
        model: Any,
        tokenizer: Any,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float | None,
        sampler_options: dict[str, Any],
    ) -> Iterator[GenChunk]:
        from mlx_lm import stream_generate

        kw: dict[str, Any] = {"max_tokens": max_tokens}
        if temperature is not None or sampler_options:
            from mlx_lm.sample_utils import make_sampler

            skw = dict(sampler_options)
            if temperature is not None:
                skw["temp"] = temperature
            kw["sampler"] = make_sampler(**skw)
        for r in stream_generate(model, tokenizer, prompt, **kw):
            yield GenChunk(
                text=r.text,
                prompt_tokens=int(r.prompt_tokens),
                generation_tokens=int(r.generation_tokens),
                finish_reason=r.finish_reason,
            )

    def release(self) -> None:
        with contextlib.suppress(Exception):
            import mlx.core as mx

            mx.clear_cache()


# ----------------------------------------------------------------------------- 思考の切り分け


class ThinkSplitter:
    """生成文を思考（reasoning）と本文（content）に分ける。区切りがトークンの途中で切れても扱える。"""

    def __init__(self, markers: tuple[str, str] | None, *, start_in_thinking: bool = False) -> None:
        self._markers = markers
        self._thinking = start_in_thinking
        self._buf = ""
        self._at_block_start = True

    def _emit(self, kind: str, text: str, out: list[tuple[str, str]]) -> None:
        if self._at_block_start:
            text = text.lstrip()
            if not text:
                return
            self._at_block_start = False
        out.append((kind, text))

    def feed(self, text: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if self._markers is None:
            if text:
                self._emit("content", text, out)
            return out
        self._buf += text
        while self._buf:
            marker = self._markers[1] if self._thinking else self._markers[0]
            kind = "reasoning" if self._thinking else "content"
            idx = self._buf.find(marker)
            if idx >= 0:
                if idx:
                    self._emit(kind, self._buf[:idx], out)
                self._buf = self._buf[idx + len(marker) :]
                self._thinking = not self._thinking
                self._at_block_start = True
                continue
            # 末尾が区切りの途中かもしれない分は持ち越す
            keep = _partial_suffix(self._buf, marker)
            if len(self._buf) > keep:
                self._emit(kind, self._buf[: len(self._buf) - keep], out)
                self._buf = self._buf[len(self._buf) - keep :]
            break
        return out

    def finish(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if self._buf:
            self._emit("reasoning" if self._thinking else "content", self._buf, out)
            self._buf = ""
        return out


def _partial_suffix(text: str, marker: str) -> int:
    """``text`` の末尾が ``marker`` の先頭と一致する最長の長さ。"""
    for n in range(min(len(marker) - 1, len(text)), 0, -1):
        if marker.startswith(text[-n:]):
            return n
    return 0


# ----------------------------------------------------------------------------- 要求


@dataclass
class _Request:
    model: str
    messages: list[dict[str, Any]]
    max_tokens: int
    temperature: float | None
    timeout: float | None
    think: bool | None
    sampler_options: dict[str, Any] = field(default_factory=dict)


def _is_oom(exc: BaseException) -> bool:
    msg = str(exc)
    return "Insufficient Memory" in msg or "OutOfMemory" in msg


class MLXLMChatAdapter(ChatCapability):
    provider_name: ClassVar[str] = PROVIDER

    def __init__(
        self,
        *,
        default_model: str | None = None,
        default_max_tokens: int = DEFAULT_MAX_TOKENS,
        backend: Backend | None = None,
    ) -> None:
        if backend is None and not is_supported_platform():
            raise UnsupportedFeatureError(
                f"mlx-lm runs only on Apple Silicon macOS (this is {sys.platform}/{platform.machine()})",
                provider=PROVIDER,
            )
        self.default_model = default_model
        self.default_max_tokens = default_max_tokens
        self._backend: Backend = backend or MLXBackend()
        self._loaded_name: str | None = None
        self._model: Any = None
        self._tokenizer: Any = None
        self._lock = threading.Lock()  # 読み込みと生成を直列にする
        self._closed = False

    # ------------------------------------------------------------------ モデル

    @property
    def loaded_model(self) -> str | None:
        return self._loaded_name

    def _ensure_loaded(self, name: str) -> tuple[Any, Any]:
        """``self._lock`` を持った状態で呼ぶ。"""
        if self._closed:
            raise OmniError("adapter is closed", provider=PROVIDER)
        if self._loaded_name == name:
            return self._model, self._tokenizer
        if self._loaded_name is not None:
            logger.info("mlx-lm: unloading %s to load %s", self._loaded_name, name)
            self._model = self._tokenizer = None
            self._loaded_name = None
            self._backend.release()
        started = time.perf_counter()
        logger.info("mlx-lm: loading %s", name)
        try:
            self._model, self._tokenizer = self._backend.load(name)
        except ImportError:
            raise
        except Exception as exc:
            raise self._map_load_error(exc, name) from exc
        self._loaded_name = name
        logger.info("mlx-lm: loaded %s in %.1fs", name, time.perf_counter() - started)
        return self._model, self._tokenizer

    @staticmethod
    def _map_load_error(exc: Exception, name: str) -> OmniError:
        text = f"{type(exc).__name__}: {exc}"
        if _is_oom(exc):
            return OmniError(
                f"not enough GPU memory to load the model: {text}",
                provider=PROVIDER,
                model=name,
                raw_error=exc,
            )
        not_found = isinstance(exc, FileNotFoundError) or type(exc).__name__ in (
            "RepositoryNotFoundError",
            "GatedRepoError",
            "EntryNotFoundError",
            "LocalEntryNotFoundError",
        )
        if not_found:
            return NotFoundError(f"model not found: {text}", provider=PROVIDER, model=name, raw_error=exc)
        return OmniError(
            f"mlx-lm failed to load the model: {text}", provider=PROVIDER, model=name, raw_error=exc
        )

    # ------------------------------------------------------------------ 要求の正規化

    def _prepare(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        tools: Sequence[ToolSpec] | None,
        response_schema: type[BaseModel] | None,
        timeout: float | None,
        provider_options: dict[str, Any],
    ) -> _Request:
        resolved = model or self.default_model
        if not resolved:
            raise InvalidRequestError(
                "model is required (mlx-lm adapter has no default_model)", provider=PROVIDER
            )
        resolved = _normalize_model(resolved)
        if tools:
            raise UnsupportedFeatureError(
                "tool calling is not supported by mlx-lm adapter yet", provider=PROVIDER, model=resolved
            )
        if response_schema is not None:
            raise UnsupportedFeatureError(
                "structured output (response_schema) is not supported by mlx-lm adapter yet",
                provider=PROVIDER,
                model=resolved,
            )
        ours: dict[str, Any] = {}
        for key, value in provider_options.items():
            if key == PROVIDER and isinstance(value, dict):
                ours = dict(value)
            else:
                logger.debug("mlx-lm adapter ignored provider option %r", key)
        unknown = sorted(set(ours) - _OPTION_KEYS)
        if unknown:
            raise InvalidRequestError(
                f"unknown mlx-lm option(s): {', '.join(unknown)} "
                f"(accepted: {', '.join(sorted(_OPTION_KEYS))})",
                provider=PROVIDER,
                model=resolved,
            )
        think = ours.get("think")
        if think is not None and not isinstance(think, bool):
            raise InvalidRequestError(
                "mlx-lm option 'think' must be a bool", provider=PROVIDER, model=resolved
            )
        limit = self.default_max_tokens if max_tokens is None else max_tokens
        if limit <= 0:
            raise InvalidRequestError("max_tokens must be positive", provider=PROVIDER, model=resolved)
        return _Request(
            model=resolved,
            messages=_to_template_messages(messages, resolved),
            max_tokens=limit,
            temperature=temperature,
            timeout=timeout,
            think=think,
            sampler_options={k: ours[k] for k in _SAMPLER_KEYS if k in ours},
        )

    # ------------------------------------------------------------------ 生成（芯）

    def _run(self, req: _Request, cancel: threading.Event | None = None) -> Iterator[StreamEvent]:
        """ロックを持って 1 本生成し、イベントを流す。最後に message_complete を出す。"""
        started = time.perf_counter()
        deadline = started + req.timeout if req.timeout is not None else None
        emitted = False
        with self._lock:
            mdl, tok = self._ensure_loaded(req.model)
            try:
                prompt = self._backend.render(tok, req.messages, req.think)
            except Exception as exc:
                raise InvalidRequestError(
                    f"chat template failed: {type(exc).__name__}: {exc}",
                    provider=PROVIDER,
                    model=req.model,
                    raw_error=exc,
                ) from exc
            markers = self._backend.think_markers(tok)
            # chat template が思考の開始をプロンプト側に置く型（本文が思考の途中から始まる）
            opened = markers is not None and prompt.rstrip().endswith(markers[0])
            splitter = ThinkSplitter(markers, start_in_thinking=opened)
            content: list[str] = []
            reasoning: list[str] = []
            first_at: float | None = None
            last: GenChunk | None = None
            try:
                for chunk in self._backend.generate(
                    mdl,
                    tok,
                    prompt,
                    max_tokens=req.max_tokens,
                    temperature=req.temperature,
                    sampler_options=req.sampler_options,
                ):
                    last = chunk
                    for kind, text in splitter.feed(chunk.text):
                        if first_at is None:
                            first_at = time.perf_counter()
                        (reasoning if kind == "reasoning" else content).append(text)
                        yield StreamEvent(
                            type="reasoning_delta" if kind == "reasoning" else "text_delta", delta_text=text
                        )
                        emitted = True
                    if cancel is not None and cancel.is_set():
                        return
                    if deadline is not None and time.perf_counter() > deadline:
                        raise ProviderTimeoutError(
                            f"generation exceeded timeout={req.timeout}s", provider=PROVIDER, model=req.model
                        )
            except OmniError as exc:
                if emitted and not isinstance(exc, StreamInterruptedError):
                    raise StreamInterruptedError(
                        f"stream interrupted after partial output: {exc.message}",
                        provider=PROVIDER,
                        model=req.model,
                        raw_error=exc,
                    ) from exc
                raise
            except Exception as exc:
                text = f"{type(exc).__name__}: {exc}"
                if _is_oom(exc):
                    err: OmniError = OmniError(
                        f"not enough GPU memory to run the model (try a smaller model or raise "
                        f"iogpu.wired_limit_mb): {text}",
                        provider=PROVIDER,
                        model=req.model,
                        raw_error=exc,
                    )
                else:
                    err = OmniError(
                        f"mlx-lm generation failed: {text}", provider=PROVIDER, model=req.model, raw_error=exc
                    )
                if emitted:
                    raise StreamInterruptedError(
                        f"stream interrupted after partial output: {err.message}",
                        provider=PROVIDER,
                        model=req.model,
                        raw_error=err,
                    ) from exc
                raise err from exc
            for kind, text in splitter.finish():
                if first_at is None:
                    first_at = time.perf_counter()
                (reasoning if kind == "reasoning" else content).append(text)
                yield StreamEvent(
                    type="reasoning_delta" if kind == "reasoning" else "text_delta", delta_text=text
                )
        finish: FinishReason = "length" if last is not None and last.finish_reason == "length" else "stop"
        prompt_tokens = last.prompt_tokens if last else 0
        completion = last.generation_tokens if last else 0
        meta = ResponseMeta(
            provider=PROVIDER,
            model=req.model,
            latency_ms=(time.perf_counter() - started) * 1000,
            time_to_first_event_ms=(first_at - started) * 1000 if first_at is not None else None,
            estimated_cost_usd=0.0,
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion,
                total_tokens=prompt_tokens + completion,
            ),
        )
        res = ChatResponse(
            content="".join(content),
            reasoning="".join(reasoning) or None,
            finish_reason=finish,
            meta=meta,
        )
        yield StreamEvent(type="message_complete", finish_reason=finish, response=res, meta=meta)

    # ------------------------------------------------------------------ 公開面（同期）

    def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        response_schema: type[BaseModel] | None = None,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> ChatResponse:
        req = self._prepare(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            response_schema=response_schema,
            timeout=timeout,
            provider_options=provider_options,
        )
        return _collect(self._run(req))

    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        response_schema: type[BaseModel] | None = None,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> Iterator[StreamEvent]:
        req = self._prepare(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            response_schema=response_schema,
            timeout=timeout,
            provider_options=provider_options,
        )
        return self._run(req)

    # ------------------------------------------------------------------ 公開面（非同期）

    async def agenerate(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        response_schema: type[BaseModel] | None = None,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> ChatResponse:
        req = self._prepare(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            response_schema=response_schema,
            timeout=timeout,
            provider_options=provider_options,
        )
        return await anyio.to_thread.run_sync(lambda: _collect(self._run(req)))

    def astream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        response_schema: type[BaseModel] | None = None,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> AsyncIterator[StreamEvent]:
        req = self._prepare(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            response_schema=response_schema,
            timeout=timeout,
            provider_options=provider_options,
        )
        return self._astream(req)

    async def _astream(self, req: _Request) -> AsyncIterator[StreamEvent]:
        """生成はスレッドで回し、イベントを順に受け取る。途中で抜けたら生成を止める。"""
        done = object()
        q: queue.Queue[Any] = queue.Queue(maxsize=64)
        cancel = threading.Event()

        def put(item: Any) -> None:
            while not cancel.is_set():
                try:
                    q.put(item, timeout=0.1)
                    return
                except queue.Full:
                    continue

        def worker() -> None:
            try:
                for ev in self._run(req, cancel):
                    put(ev)
            except BaseException as exc:  # 受け手に渡す
                put(("error", exc))
            finally:
                put(done)

        def take() -> Any:
            # 受け手が取り消されてもスレッドが残り続けないよう、短い間隔で起きる
            while True:
                try:
                    return q.get(timeout=0.1)
                except queue.Empty:
                    if cancel.is_set():
                        return done

        thread = threading.Thread(target=worker, name="omni-mlx-lm-generate", daemon=True)
        thread.start()
        try:
            while True:
                item = await anyio.to_thread.run_sync(take, abandon_on_cancel=True)
                if item is done:
                    break
                if isinstance(item, tuple) and item and item[0] == "error":
                    raise item[1]
                yield item
        finally:
            cancel.set()
            # 生成はトークンの区切りで止まる。ロックを放すまで待つ（イベントループは塞がない）
            await anyio.to_thread.run_sync(thread.join)

    # ------------------------------------------------------------------ 後始末

    def close(self) -> None:
        with self._lock:
            self._model = self._tokenizer = None
            self._loaded_name = None
            self._closed = True
            self._backend.release()

    async def aclose(self) -> None:
        await anyio.to_thread.run_sync(self.close)


# ----------------------------------------------------------------------------- 補助


def _normalize_model(name: str) -> str:
    """``~`` を含むローカルパスは展開する（リポジトリ名はそのまま）。"""
    if name.startswith("~"):
        return str(Path(name).expanduser())
    return name


def _to_template_messages(messages: Sequence[ChatMessage], model: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == Role.TOOL or m.tool_calls:
            raise UnsupportedFeatureError(
                "tool messages are not supported by mlx-lm adapter yet", provider=PROVIDER, model=model
            )
        if not isinstance(m.content, str) and any(isinstance(p, MediaPart) for p in m.content):
            raise UnsupportedFeatureError(
                "image / media input is not supported by mlx-lm adapter yet", provider=PROVIDER, model=model
            )
        out.append({"role": m.role.value, "content": m.text()})
    if not out:
        raise InvalidRequestError("messages must not be empty", provider=PROVIDER, model=model)
    return out


def _collect(events: Iterator[StreamEvent]) -> ChatResponse:
    for ev in events:
        if ev.type == "message_complete" and ev.response is not None:
            return ev.response
    raise OmniError("generation ended without a response", provider=PROVIDER)  # pragma: no cover
