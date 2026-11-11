"""設定（config.toml / secrets.toml）から OmniClient を組み立てる。

MCP は接続の開閉が非同期で、サーバーごとに作るため、ここでは組み立てない（MCPClientAdapter を直接使う）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .env_overlay import EnvOverrideSummary, apply_env_overlay
from .exceptions import OmniError
from .settings import ConfigIssue, OmniConfig, validate_files

if TYPE_CHECKING:
    from .capabilities import ChatCapability
    from .router import OmniClient

__all__ = ["ConfigurationError", "build_client", "client_from_config"]


class ConfigurationError(OmniError):
    """設定に誤りがある。``issues`` に場所つきの一覧を持つ。"""

    def __init__(self, issues: list[ConfigIssue]) -> None:
        lines = "\n".join(f"  - {i}" for i in issues)
        super().__init__(f"configuration has {len(issues)} problem(s):\n{lines}", provider="omni")
        self.issues = issues


def _base_url(cfg: OmniConfig, provider: str) -> str | None:
    conn = cfg.connection(provider)
    return getattr(conn, "base_url", None) if conn else None


def _chat_adapter(cfg: OmniConfig, name: str, model: str | None) -> ChatCapability:
    """Chat プロバイダー名 → アダプター（キーは各アダプターが secrets.toml・環境変数から解決する）。"""
    kw: dict[str, Any] = {"base_url": _base_url(cfg, name)}
    if model:
        kw["default_model"] = model
    if name == "gemini":
        from .providers.gemini import GeminiChatAdapter

        return GeminiChatAdapter(**kw)
    if name == "ollama":
        from .providers.ollama import OllamaChatAdapter

        return OllamaChatAdapter(**kw)
    if name == "ollama-cloud":
        from .providers.ollama import OllamaCloudChatAdapter

        return OllamaCloudChatAdapter(**kw)
    if name == "anthropic":
        from .providers.anthropic import AnthropicChatAdapter

        return AnthropicChatAdapter(**kw)
    from .providers import openai_compatible as oc

    classes: dict[str, type[oc.OpenAICompatibleChatAdapter]] = {
        "xai": oc.XAIChatAdapter,
        "agnes": oc.AgnesChatAdapter,
        "openrouter": oc.OpenRouterChatAdapter,
        "openai-compatible": oc.OpenAICompatibleChatAdapter,
    }
    if name not in classes:  # 設定の検証を通っていれば来ない
        raise ValueError(f"unknown chat provider: {name}")
    return classes[name](**kw)


def _chat(cfg: OmniConfig) -> ChatCapability | None:
    sec = cfg.client.chat
    if sec is None:
        return None
    adapters = [_chat_adapter(cfg, name, sec.models.get(name)) for name in sec.providers]
    if len(adapters) == 1:
        return adapters[0]
    from .router import FallbackChatAdapter

    return FallbackChatAdapter(adapters)


def build_client(
    cfg: OmniConfig,
    *,
    env_overlay: bool = True,
    env_summary: list[EnvOverrideSummary] | None = None,
    **client_kwargs: Any,
) -> OmniClient:
    """検証済みの OmniConfig から OmniClient を作る。

    - ``env_overlay=True``（既定）で、起動時の環境変数（``OMNI_CHAT_PROVIDER`` ほか）を
      TOML の上に重ねる（優先順位: 引数 → 環境変数 → TOML → 既定）。
    - ``env_summary`` に空のリストを渡すと、何が上書きされたかの一覧を受け取れる
      （``cli checks`` 用）。
    """
    from .router import OmniClient

    if env_overlay:
        cfg, summary = apply_env_overlay(cfg)
        if env_summary is not None:
            env_summary.append(summary)

    c = cfg.client
    parts: dict[str, Any] = {"chat": _chat(cfg)}
    if c.embedding:
        e = c.embedding
        if e.provider == "gemini":
            from .providers.gemini.embedding import GeminiEmbeddingAdapter

            kw: dict[str, Any] = {"base_url": _base_url(cfg, "gemini")}
            if e.model:
                kw["default_model"] = e.model
            parts["embedding"] = GeminiEmbeddingAdapter(**kw)
        elif e.provider == "ollama":
            from .providers.ollama.embedding import OllamaEmbeddingAdapter

            parts["embedding"] = OllamaEmbeddingAdapter(
                default_model=e.model, base_url=_base_url(cfg, "ollama")
            )
        else:
            from .providers.fastembed import FastEmbedEmbeddingAdapter

            kw = {"cache_dir": Path(e.cache_dir).expanduser() if e.cache_dir else None}
            if e.model:
                kw["model"] = e.model
            parts["embedding"] = FastEmbedEmbeddingAdapter(**kw)
    if c.rerank:
        from .providers.fastembed.rerank import FastEmbedRerankAdapter

        kw = {"cache_dir": Path(c.rerank.cache_dir).expanduser() if c.rerank.cache_dir else None}
        if c.rerank.model:
            kw["model"] = c.rerank.model
        parts["rerank"] = FastEmbedRerankAdapter(**kw)
    if c.search:
        if c.search.provider == "openai-compatible":
            from .providers.openai_compatible.search import OpenAICompatibleSearchAdapter

            parts["search"] = OpenAICompatibleSearchAdapter(base_url=c.search.base_url, backend=c.search.backend)
        else:
            from .providers.ddgs import DDGSSearchAdapter

            parts["search"] = DDGSSearchAdapter(region=c.search.region, safesearch=c.search.safesearch)
    if c.image:
        from .providers.openai_compatible.image import OpenAICompatibleImageAdapter

        parts["image"] = OpenAICompatibleImageAdapter(base_url=c.image.base_url, default_model=c.image.model)
    if c.doc_parser:
        from .providers import default_document_parser
        from .providers.markitdown import MarkItDownDocumentParser
        from .providers.pypdf import PyPDFDocumentParser

        parsers: dict[str, Any] = {
            "default": default_document_parser,
            "pypdf": PyPDFDocumentParser,
            "markitdown": MarkItDownDocumentParser,
        }
        parts["doc_parser"] = parsers[c.doc_parser.provider]()
    if c.judge:
        from .providers.ollama.judge import OllamaJudgeAdapter

        parts["judge"] = OllamaJudgeAdapter(default_model=c.judge.model, base_url=_base_url(cfg, "ollama"))
    if c.audio:
        from .providers import AudioRouter

        speech = transcription = None
        if c.audio.speech:
            from .providers.edge_tts import EdgeTTSAudioAdapter

            speech = EdgeTTSAudioAdapter(voice=c.audio.voice) if c.audio.voice else EdgeTTSAudioAdapter()
        if c.audio.transcription:
            from .providers.faster_whisper import FasterWhisperAudioAdapter

            transcription = FasterWhisperAudioAdapter(
                model=c.audio.whisper_model,
                cache_dir=Path(c.audio.cache_dir).expanduser() if c.audio.cache_dir else None,
            )
        if speech or transcription:
            parts["audio"] = AudioRouter(speech=speech, transcription=transcription)
    if c.sandbox:
        from .providers.docker import DEFAULT_IMAGE, DockerSandboxAdapter

        parts["sandbox"] = DockerSandboxAdapter(
            image=c.sandbox.image or DEFAULT_IMAGE, memory=c.sandbox.memory, network=c.sandbox.network
        )
    return OmniClient(**parts, **client_kwargs)


def client_from_config(
    config_file: Path | None = None,
    secrets_file: Path | None = None,
    *,
    env_overlay: bool = True,
    env_summary: list[EnvOverrideSummary] | None = None,
    **client_kwargs: Any,
) -> OmniClient:
    """config.toml / secrets.toml を検証して OmniClient を作る。誤りがあれば ConfigurationError。

    ``env_overlay=True``（既定）で、起動時の環境変数（``OMNI_CHAT_PROVIDER`` ほか）を上書きに使う。
    """
    cfg, issues = validate_files(config_file, secrets_file)
    errors = [i for i in issues if i.level == "error"]
    if cfg is None or errors:
        raise ConfigurationError(errors or issues)
    return build_client(cfg, env_overlay=env_overlay, env_summary=env_summary, **client_kwargs)
