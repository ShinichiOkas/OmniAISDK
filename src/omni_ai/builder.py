"""設定（config.toml / secrets.toml）から OmniClient を組み立てる。

MCP は接続の開閉が非同期で、サーバーごとに作るため、ここでは組み立てない（MCPClientAdapter を直接使う）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    conn = getattr(cfg, provider, None)
    return getattr(conn, "base_url", None) if conn else None


def _chat(cfg: OmniConfig) -> ChatCapability | None:
    sec = cfg.client.chat
    if sec is None:
        return None
    adapters: list[ChatCapability] = []
    for name in sec.providers:
        model = sec.models.get(name)
        if name == "gemini":
            from .providers.gemini import GeminiChatAdapter

            kw: dict[str, Any] = {"base_url": _base_url(cfg, "gemini")}
            if model:
                kw["default_model"] = model
            adapters.append(GeminiChatAdapter(**kw))
        else:
            from .providers.ollama import OllamaChatAdapter

            adapters.append(OllamaChatAdapter(default_model=model, base_url=_base_url(cfg, "ollama")))
    if len(adapters) == 1:
        return adapters[0]
    from .router import FallbackChatAdapter

    return FallbackChatAdapter(adapters)


def build_client(cfg: OmniConfig, **client_kwargs: Any) -> OmniClient:
    """検証済みの OmniConfig から OmniClient を作る。"""
    from .router import OmniClient

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
        from .providers.ddgs import DDGSSearchAdapter

        parts["search"] = DDGSSearchAdapter(region=c.search.region, safesearch=c.search.safesearch)
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
    config_file: Path | None = None, secrets_file: Path | None = None, **client_kwargs: Any
) -> OmniClient:
    """config.toml / secrets.toml を検証して OmniClient を作る。誤りがあれば ConfigurationError。"""
    cfg, issues = validate_files(config_file, secrets_file)
    errors = [i for i in issues if i.level == "error"]
    if cfg is None or errors:
        raise ConfigurationError(errors or issues)
    return build_client(cfg, **client_kwargs)
