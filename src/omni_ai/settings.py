"""設定ファイル（config.toml）と秘密ファイル（secrets.toml）。

- 置き場所: OS 標準の設定ディレクトリ（platformdirs）。環境変数 ``OMNI_CONFIG_DIR`` で変えられる
- ``config.toml``: 共有してよい設定（接続先・機能の構成）。``[client.<機能>]`` に OmniClient の構成を書く
- ``secrets.toml``: API キーなど。``[gemini] api_key = "..."``。macOS / Linux では権限 600 で書く
- 検証は ``validate_files()``。誤り（未知のキー・型違い・未知のプロバイダー）を、場所つきで一覧にする
"""

from __future__ import annotations

import os
import tempfile
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .compat import PlatformCompat

__all__ = [
    "CHAT_PROVIDERS",
    "ApiConnection",
    "AudioSection",
    "ChatProviderInfo",
    "ChatSection",
    "ClientSection",
    "ConfigIssue",
    "EmbeddingSection",
    "OmniConfig",
    "config_dir",
    "config_path",
    "dump_toml",
    "load_config",
    "load_secrets",
    "save_config",
    "save_secrets",
    "secrets_path",
    "validate_files",
]

ChatProvider = Literal["gemini", "ollama", "ollama-cloud", "xai", "agnes", "openrouter", "openai-compatible"]


@dataclass(frozen=True)
class ChatProviderInfo:
    """Chat プロバイダーの設定上の性質（検証・メニュー・doctor が共通に使う）。"""

    label: str
    key_env: tuple[str, ...] = ()  # 各社の標準の環境変数（OMNI_<P>_API_KEY は常に読む）
    key_required: bool = False
    needs_model: bool = True  # 既定のモデルを持たない
    needs_base_url: bool = False  # 既定の接続先を持たない
    model_example: str = ""


CHAT_PROVIDERS: dict[str, ChatProviderInfo] = {
    "gemini": ChatProviderInfo("Gemini", ("GEMINI_API_KEY", "GOOGLE_API_KEY"), True, False),
    "ollama": ChatProviderInfo("Ollama（ローカル）", model_example="gemma4:31b-cloud"),
    "ollama-cloud": ChatProviderInfo("Ollama Cloud", ("OLLAMA_API_KEY",), True, model_example="gpt-oss:120b"),
    "xai": ChatProviderInfo("xAI（Grok）", ("XAI_API_KEY",), True, model_example="grok-4"),
    "agnes": ChatProviderInfo("Agnes AI", ("AGNES_API_KEY",), True, model_example="agnes-2.5-flash"),
    "openrouter": ChatProviderInfo(
        "OpenRouter", ("OPENROUTER_API_KEY",), True, model_example="openai/gpt-4o-mini"
    ),
    "openai-compatible": ChatProviderInfo(
        "OpenAI 互換サーバー（llama.cpp など）", needs_base_url=True, model_example="local-model"
    ),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeminiConnection(_Strict):
    base_url: str | None = None
    api_key: str | None = None  # 互換のため読めるが、secrets.toml に置くべき（validate で警告）


class OllamaConnection(_Strict):
    base_url: str | None = None


class ApiConnection(_Strict):
    """接続先とキー（キーは secrets.toml に置く。config.toml にあれば validate で警告）。"""

    base_url: str | None = None
    api_key: str | None = None


class ChatSection(_Strict):
    providers: list[ChatProvider] = Field(min_length=1)  # この順にフォールバック
    models: dict[str, str] = Field(default_factory=dict)  # プロバイダー名 → モデル


class EmbeddingSection(_Strict):
    provider: Literal["gemini", "ollama", "fastembed"]
    model: str | None = None
    cache_dir: str | None = None


class RerankSection(_Strict):
    provider: Literal["fastembed"] = "fastembed"
    model: str | None = None
    cache_dir: str | None = None


class SearchSection(_Strict):
    provider: Literal["ddgs"] = "ddgs"
    region: str = "ja-jp"
    safesearch: Literal["on", "moderate", "off"] = "on"


class DocParserSection(_Strict):
    provider: Literal["default", "pypdf", "markitdown"] = "default"


class JudgeSection(_Strict):
    provider: Literal["ollama"] = "ollama"
    model: str


class AudioSection(_Strict):
    speech: Literal["edge-tts"] | None = "edge-tts"
    transcription: Literal["faster-whisper"] | None = "faster-whisper"
    voice: str | None = None
    whisper_model: str = "medium"
    cache_dir: str | None = None


class SandboxSection(_Strict):
    provider: Literal["docker"] = "docker"
    image: str | None = None
    memory: str = "256m"
    network: bool = False


class ClientSection(_Strict):
    chat: ChatSection | None = None
    embedding: EmbeddingSection | None = None
    rerank: RerankSection | None = None
    search: SearchSection | None = None
    doc_parser: DocParserSection | None = None
    judge: JudgeSection | None = None
    audio: AudioSection | None = None
    sandbox: SandboxSection | None = None


class OmniConfig(_Strict):
    gemini: GeminiConnection | None = None
    ollama: OllamaConnection | None = None
    ollama_cloud: ApiConnection | None = Field(default=None, alias="ollama-cloud")
    xai: ApiConnection | None = None
    agnes: ApiConnection | None = None
    openrouter: ApiConnection | None = None
    openai_compatible: ApiConnection | None = Field(default=None, alias="openai-compatible")

    def connection(self, provider: str) -> Any:
        """プロバイダー名（"ollama-cloud" など）→ 接続設定。"""
        return getattr(self, provider.replace("-", "_"), None)

    client: ClientSection = Field(default_factory=ClientSection)


# ----------------------------------------------------------------------------- 場所と読み書き


def config_dir() -> Path:
    override = os.environ.get("OMNI_CONFIG_DIR")
    return Path(override).expanduser() if override else PlatformCompat.get_config_dir(create=False)


def config_path() -> Path:
    return config_dir() / "config.toml"


def secrets_path() -> Path:
    return config_dir() / "secrets.toml"


def _read(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def load_config(path: Path | None = None) -> dict[str, Any]:
    return _read(path or config_path())


def load_secrets(path: Path | None = None) -> dict[str, Any]:
    return _read(path or secrets_path())


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
        return f'"{escaped}"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_toml_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def _key(k: str) -> str:
    return k if k.replace("_", "").replace("-", "").isalnum() else _toml_value(k)


def dump_toml(data: dict[str, Any]) -> str:
    """設定に必要な範囲（文字列・数・真偽・配列・入れ子のテーブル・インラインテーブル）の TOML 書き出し。"""
    lines: list[str] = []

    def emit(table: dict[str, Any], prefix: str) -> None:
        scalars = {k: v for k, v in table.items() if not (isinstance(v, dict) and _is_table(v, k))}
        tables = {k: v for k, v in table.items() if isinstance(v, dict) and _is_table(v, k)}
        if prefix and (scalars or not table):  # 空のテーブルも見出しは書く（捨てると誤字のキーが消える）
            lines.append(f"[{prefix}]")
        for k, v in scalars.items():
            if v is None:
                continue
            lines.append(f"{_key(k)} = {_toml_value(v)}")
        if prefix and scalars:
            lines.append("")
        for k, v in tables.items():
            emit(v, f"{prefix}.{_key(k)}" if prefix else _key(k))

    emit(data, "")
    return "\n".join(lines).rstrip() + "\n"


# インライン表にする対応表のキー（それ以外の辞書は [テーブル] として書く）
_INLINE_KEYS = {"models"}


def _is_table(v: dict[str, Any], key: str = "") -> bool:
    return key not in _INLINE_KEYS or any(isinstance(x, dict) for x in v.values())


def _atomic_write(path: Path, text: str, *, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if private:
            PlatformCompat.make_private(tmp_path)
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)


def save_config(data: dict[str, Any], path: Path | None = None) -> Path:
    target = path or config_path()
    _atomic_write(target, dump_toml(data), private=False)
    return target


def save_secrets(data: dict[str, Any], path: Path | None = None) -> Path:
    target = path or secrets_path()
    _atomic_write(target, dump_toml(data), private=True)
    return target


# ----------------------------------------------------------------------------- 検証


@dataclass(frozen=True)
class ConfigIssue:
    level: Literal["error", "warning"]
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():7s} {self.where}: {self.message}"


def _issues_from_validation(exc: ValidationError, file: str) -> list[ConfigIssue]:
    out = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e["loc"])
        msg = "unknown key (typo?)" if e["type"] == "extra_forbidden" else e["msg"]
        out.append(ConfigIssue("error", f"{file}:{loc}", msg))
    return out


def parse_config(
    raw: dict[str, Any], file: str = "config.toml"
) -> tuple[OmniConfig | None, list[ConfigIssue]]:
    try:
        cfg = OmniConfig.model_validate(raw)
    except ValidationError as exc:
        return None, _issues_from_validation(exc, file)
    issues: list[ConfigIssue] = []
    for name in CHAT_PROVIDERS:
        conn = cfg.connection(name)
        if conn is not None and getattr(conn, "api_key", None):
            issues.append(
                ConfigIssue(
                    "warning",
                    f"{file}:{name}.api_key",
                    "API keys belong in secrets.toml (config.toml may be shared)",
                )
            )
    chat = cfg.client.chat
    if chat:
        for name in chat.models:
            if name not in chat.providers:
                issues.append(
                    ConfigIssue(
                        "warning",
                        f"{file}:client.chat.models.{name}",
                        f"model given for {name!r}, which is not in providers",
                    )
                )
        for name in chat.providers:
            info = CHAT_PROVIDERS[name]
            if info.needs_model and name not in chat.models:
                example = f" (e.g. {info.model_example})" if info.model_example else ""
                issues.append(
                    ConfigIssue(
                        "error",
                        f"{file}:client.chat.models",
                        f"{name} has no default model; set models.{_key(name)}{example}",
                    )
                )
        if len(set(chat.providers)) != len(chat.providers):
            issues.append(ConfigIssue("error", f"{file}:client.chat.providers", "duplicate provider"))
    emb = cfg.client.embedding
    if emb and emb.provider == "ollama" and not emb.model:
        issues.append(
            ConfigIssue(
                "error",
                f"{file}:client.embedding.model",
                "ollama embedding needs a model (e.g. nomic-embed-text)",
            )
        )
    return cfg, issues


def validate_files(
    config_file: Path | None = None, secrets_file: Path | None = None
) -> tuple[OmniConfig | None, list[ConfigIssue]]:
    """config.toml と secrets.toml を読み、構成と問題点の一覧を返す。"""
    cfg_path = config_file or config_path()
    sec_path = secrets_file or secrets_path()
    issues: list[ConfigIssue] = []
    try:
        raw = load_config(cfg_path)
    except tomllib.TOMLDecodeError as exc:
        return None, [ConfigIssue("error", str(cfg_path), f"invalid TOML: {exc}")]
    try:
        secrets = load_secrets(sec_path)
    except tomllib.TOMLDecodeError as exc:
        return None, [ConfigIssue("error", str(sec_path), f"invalid TOML: {exc}")]
    cfg, cfg_issues = parse_config(raw)
    issues.extend(cfg_issues)
    for section in secrets:
        if section not in CHAT_PROVIDERS:
            issues.append(ConfigIssue("warning", f"secrets.toml:{section}", "unknown section"))
    if cfg and cfg.client.chat:
        issues.extend(_connection_issues(cfg, cfg.client.chat.providers, secrets))
    return cfg, issues


def _connection_issues(
    cfg: OmniConfig, providers: Sequence[str], secrets: dict[str, Any]
) -> list[ConfigIssue]:
    """Chat に並べたプロバイダーのキー・接続先が揃っているか。"""
    from .config import resolve_setting

    out: list[ConfigIssue] = []
    for name in providers:
        info = CHAT_PROVIDERS[name]
        env = name.upper().replace("-", "_")
        if info.key_required:
            sec = secrets.get(name)
            in_file = isinstance(sec, dict) and sec.get("api_key")
            conn = cfg.connection(name)
            if not (in_file or (conn and conn.api_key) or resolve_setting(name, "api_key", config={}).value):
                where = ", ".join(("secrets.toml", *info.key_env, f"OMNI_{env}_API_KEY"))
                out.append(ConfigIssue("error", f"{name}.api_key", f"no API key ({where})"))
        if info.needs_base_url:
            conn = cfg.connection(name)
            if not ((conn and conn.base_url) or os.environ.get(f"OMNI_{env}_BASE_URL")):
                out.append(
                    ConfigIssue(
                        "error",
                        f"config.toml:{name}.base_url",
                        "no base_url (e.g. http://127.0.0.1:8080; there is no default)",
                    )
                )
    return out
