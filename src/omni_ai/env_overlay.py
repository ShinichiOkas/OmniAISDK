"""起動時の環境変数で `OmniConfig` を上書きする層。

`.env` は読まない（既存の方針どおり）。読むのは **その場で動いているプロセスの環境変数** だけ。

優先順位: **引数 → 環境変数 → TOML → 既定**。本モジュールが担うのは「環境変数 vs TOML」の層。
`resolve_setting` が担うのは接続の中身（``OMNI_<PROVIDER>_<KEY>``）で、こちらは
**どの機能にどのプロバイダを割り当てるか**（``client.chat.providers`` ほか）を触る。

``ellm-proxy <launch-name>`` のような前段プロキシを挟むときは、起動時に次を立てれば SDK は環境変数だけで切り替わる::

    $env:OMNI_CHAT_PROVIDER = "openai-compatible"
    $env:OMNI_CHAT_BASE_URL = "http://127.0.0.1:8082"
    $env:OMNI_CHAT_MODEL    = "claude-opus-4-8"
    # OpenAI 互換側の API キーは OMNI_OPENAI_COMPATIBLE_API_KEY で渡す（resolve_setting の層）

変数一覧（左から順に見る。設定が無い項目は TOML / 既定が通る）:

- ``OMNI_CHAT_PROVIDER``: 使う chat プロバイダを 1 本に差し替える。TOML の ``client.chat.providers`` を完全置換
- ``OMNI_CHAT_MODEL``: 選ばれた chat プロバイダのモデル
- ``OMNI_CHAT_BASE_URL``: 選ばれた chat プロバイダの ``base_url``（接続先ごとの TOML/secrets を使う ``resolve_setting`` を介さない直指定）
- ``OMNI_EMBEDDING_PROVIDER``・``OMNI_EMBEDDING_MODEL``・``OMNI_EMBEDDING_CACHE_DIR``
- ``OMNI_SEARCH_PROVIDER``（``ddgs`` | ``openai-compatible``）・``OMNI_SEARCH_BASE_URL``・``OMNI_SEARCH_REGION``・``OMNI_SEARCH_SAFESEARCH``
  — ``openai-compatible`` は前段プロキシの ``{OMNI_SEARCH_BASE_URL}/search``（``http://127.0.0.1:<port>/v1`` の形）
- ``OMNI_IMAGE_PROVIDER``（``openai-compatible``）・``OMNI_IMAGE_BASE_URL``
  — 前段プロキシの ``{OMNI_IMAGE_BASE_URL}/images/generations``
- ``OMNI_AUDIO_SPEECH``・``OMNI_AUDIO_TRANSCRIPTION``・``OMNI_AUDIO_VOICE``・``OMNI_AUDIO_WHISPER_MODEL``・``OMNI_AUDIO_CACHE_DIR``
- ``OMNI_JUDGE_MODEL``・``OMNI_DOC_PARSER_PROVIDER``
- ``OMNI_SANDBOX_IMAGE``・``OMNI_SANDBOX_MEMORY``・``OMNI_SANDBOX_NETWORK``（``1``/``0``）

接続先の API キー・各プロバイダ標準の環境変数は従来どおり :mod:`omni_ai.config` の ``resolve_setting``。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .settings import (
    CHAT_PROVIDERS,
    AudioSection,
    ChatSection,
    ClientSection,
    DocParserSection,
    EmbeddingSection,
    ImageSection,
    JudgeSection,
    OmniConfig,
    SandboxSection,
    SearchSection,
)

SEARCH_PROVIDERS = ("ddgs", "openai-compatible")
IMAGE_PROVIDERS = ("openai-compatible",)

__all__ = ["EnvOverrideSummary", "apply_env_overlay", "summarize_env_overrides"]


@dataclass(frozen=True)
class EnvOverrideSummary:
    """環境変数で上書きされた項目の一覧（cli checks 用）。"""

    items: tuple[tuple[str, str, str], ...]  # (env_var, path, value)

    def is_empty(self) -> bool:
        return not self.items

    def format_lines(self) -> list[str]:
        """1 変数 1 行の表示。"""
        return [f"{env} → {path} = {value!r}" for env, path, value in self.items]


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _bool(raw: str, *, default: bool) -> bool:
    low = raw.strip().lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    return default


def _env(environ: Mapping[str, str], name: str) -> str | None:
    """環境変数を 1 本だけ読む。空文字は未設定扱い（``$env:X=""`` の誤設定を区別しない）。"""
    val = environ.get(name)
    return val if val else None


def apply_env_overlay(
    cfg: OmniConfig, *, environ: Mapping[str, str] | None = None
) -> tuple[OmniConfig, EnvOverrideSummary]:
    """環境変数で ``cfg`` を書き換えた複製と、適用の一覧を返す。元の ``cfg`` は変えない。

    - 未設定の変数は何もしない（TOML の値がそのまま通る）
    - 上書きの効く対象が TOML に無いときは、新しいセクションを足す
      （例: TOML に ``client.chat`` が無くても ``OMNI_CHAT_PROVIDER`` で chat が立つ）
    """
    env = environ if environ is not None else os.environ
    client = cfg.client
    applied: list[tuple[str, str, str]] = []

    # ------------------------- chat -------------------------
    chat_provider = _env(env, "OMNI_CHAT_PROVIDER")
    chat_model = _env(env, "OMNI_CHAT_MODEL")
    chat_base_url = _env(env, "OMNI_CHAT_BASE_URL")
    chat = client.chat
    if chat_provider or chat_model or chat_base_url:
        if chat_provider:
            if chat_provider not in CHAT_PROVIDERS:
                allowed = ", ".join(sorted(CHAT_PROVIDERS))
                raise ValueError(
                    f"OMNI_CHAT_PROVIDER={chat_provider!r} is not a known provider. "
                    f"Allowed: {allowed}"
                )
            new_providers = [chat_provider]
            # TOML の models は TOML の providers と対になっている。provider を差し替えたら
            # models も新しい provider のものだけに絞る（旧 provider のモデルを引きずらない）。
            new_models = {
                k: v for k, v in (chat.models.items() if chat else []) if k == chat_provider
            }
            applied.append(("OMNI_CHAT_PROVIDER", "client.chat.providers", chat_provider))
        else:
            new_providers = list(chat.providers) if chat else []
            new_models = dict(chat.models) if chat else {}
        if chat_model:
            target = chat_provider or (new_providers[0] if new_providers else None)
            if target is None:
                raise ValueError(
                    "OMNI_CHAT_MODEL requires a chat provider (set OMNI_CHAT_PROVIDER or configure client.chat.providers)"
                )
            new_models[target] = chat_model
            applied.append(("OMNI_CHAT_MODEL", f"client.chat.models.{target}", chat_model))
        if new_providers:
            chat = ChatSection(providers=list(new_providers), models=new_models)  # type: ignore[arg-type]

    # chat の base_url は OmniConfig の接続セクションへ（provider ごとの ``base_url``）
    connections: dict[str, Any] = {}
    if chat_base_url:
        target = chat_provider or (chat.providers[0] if chat else None)
        if target is None:
            raise ValueError(
                "OMNI_CHAT_BASE_URL requires a chat provider (set OMNI_CHAT_PROVIDER or configure client.chat.providers)"
            )
        connections[target] = {"base_url": chat_base_url}
        applied.append(("OMNI_CHAT_BASE_URL", f"{target}.base_url", chat_base_url))

    # ------------------------- embedding -------------------------
    emb_provider = _env(env, "OMNI_EMBEDDING_PROVIDER")
    emb_model = _env(env, "OMNI_EMBEDDING_MODEL")
    emb_cache_dir = _env(env, "OMNI_EMBEDDING_CACHE_DIR")
    emb = client.embedding
    if emb_provider or emb_model or emb_cache_dir:
        provider = emb_provider or (emb.provider if emb else None)
        if provider is None:
            raise ValueError(
                "OMNI_EMBEDDING_MODEL / _CACHE_DIR require a provider (set OMNI_EMBEDDING_PROVIDER or configure client.embedding)"
            )
        emb = EmbeddingSection(
            provider=provider,  # type: ignore[arg-type]
            model=emb_model if emb_model is not None else (emb.model if emb else None),
            cache_dir=emb_cache_dir if emb_cache_dir is not None else (emb.cache_dir if emb else None),
        )
        if emb_provider:
            applied.append(("OMNI_EMBEDDING_PROVIDER", "client.embedding.provider", emb_provider))
        if emb_model:
            applied.append(("OMNI_EMBEDDING_MODEL", "client.embedding.model", emb_model))
        if emb_cache_dir:
            applied.append(("OMNI_EMBEDDING_CACHE_DIR", "client.embedding.cache_dir", emb_cache_dir))

    # ------------------------- search -------------------------
    search_provider = _env(env, "OMNI_SEARCH_PROVIDER")
    search_base_url = _env(env, "OMNI_SEARCH_BASE_URL")
    search_region = _env(env, "OMNI_SEARCH_REGION")
    search_safesearch = _env(env, "OMNI_SEARCH_SAFESEARCH")
    search = client.search
    if search_provider and search_provider not in SEARCH_PROVIDERS:
        raise ValueError(
            f"OMNI_SEARCH_PROVIDER={search_provider!r} is not a known provider. "
            f"Allowed: {', '.join(SEARCH_PROVIDERS)}"
        )
    if search_provider or search_base_url or search_region or search_safesearch:
        provider = search_provider or (search.provider if search else "ddgs")
        base_url = search_base_url or (search.base_url if search else None)
        if search_base_url and provider != "openai-compatible":
            raise ValueError(
                "OMNI_SEARCH_BASE_URL is only used with OMNI_SEARCH_PROVIDER=openai-compatible "
                f"(provider resolved to {provider!r})"
            )
        if provider == "openai-compatible" and not base_url:
            raise ValueError(
                "OMNI_SEARCH_PROVIDER=openai-compatible requires OMNI_SEARCH_BASE_URL "
                "(e.g. http://127.0.0.1:8080/v1) or client.search.base_url"
            )
        search = SearchSection(
            provider=provider,  # type: ignore[arg-type]
            region=search_region if search_region else (search.region if search else "ja-jp"),
            safesearch=(  # type: ignore[arg-type]
                search_safesearch
                if search_safesearch
                else (search.safesearch if search else "on")
            ),
            base_url=base_url,
            backend=search.backend if search else None,
        )
        if search_provider:
            applied.append(("OMNI_SEARCH_PROVIDER", "client.search.provider", search_provider))
        if search_base_url:
            applied.append(("OMNI_SEARCH_BASE_URL", "client.search.base_url", search_base_url))
        if search_region:
            applied.append(("OMNI_SEARCH_REGION", "client.search.region", search_region))
        if search_safesearch:
            applied.append(("OMNI_SEARCH_SAFESEARCH", "client.search.safesearch", search_safesearch))

    # ------------------------- image -------------------------
    image_provider = _env(env, "OMNI_IMAGE_PROVIDER")
    image_base_url = _env(env, "OMNI_IMAGE_BASE_URL")
    image = client.image
    if image_provider and image_provider not in IMAGE_PROVIDERS:
        raise ValueError(
            f"OMNI_IMAGE_PROVIDER={image_provider!r} is not a known provider. "
            f"Allowed: {', '.join(IMAGE_PROVIDERS)}"
        )
    if image_provider or image_base_url:
        if image_provider is None and image is None:
            raise ValueError(
                "OMNI_IMAGE_BASE_URL requires a provider (set OMNI_IMAGE_PROVIDER or configure client.image)"
            )
        base_url = image_base_url or (image.base_url if image else None)
        if not base_url:
            raise ValueError(
                "OMNI_IMAGE_PROVIDER=openai-compatible requires OMNI_IMAGE_BASE_URL "
                "(e.g. http://127.0.0.1:8080/v1) or client.image.base_url"
            )
        image = ImageSection(provider="openai-compatible", base_url=base_url, model=image.model if image else None)
        if image_provider:
            applied.append(("OMNI_IMAGE_PROVIDER", "client.image.provider", image_provider))
        if image_base_url:
            applied.append(("OMNI_IMAGE_BASE_URL", "client.image.base_url", image_base_url))

    # ------------------------- audio -------------------------
    audio_speech = _env(env, "OMNI_AUDIO_SPEECH")
    audio_transcription = _env(env, "OMNI_AUDIO_TRANSCRIPTION")
    audio_voice = _env(env, "OMNI_AUDIO_VOICE")
    audio_whisper = _env(env, "OMNI_AUDIO_WHISPER_MODEL")
    audio_cache = _env(env, "OMNI_AUDIO_CACHE_DIR")
    audio = client.audio
    if any([audio_speech, audio_transcription, audio_voice, audio_whisper, audio_cache]):
        audio = AudioSection(
            speech=(
                None
                if audio_speech == "none"
                else (audio_speech if audio_speech else (audio.speech if audio else "edge-tts"))  # type: ignore[arg-type]
            ),
            transcription=(
                None
                if audio_transcription == "none"
                else (  # type: ignore[arg-type]
                    audio_transcription
                    if audio_transcription
                    else (audio.transcription if audio else "faster-whisper")
                )
            ),
            voice=audio_voice if audio_voice else (audio.voice if audio else None),
            whisper_model=audio_whisper if audio_whisper else (audio.whisper_model if audio else "medium"),
            cache_dir=audio_cache if audio_cache else (audio.cache_dir if audio else None),
        )
        if audio_speech:
            applied.append(("OMNI_AUDIO_SPEECH", "client.audio.speech", audio_speech))
        if audio_transcription:
            applied.append(("OMNI_AUDIO_TRANSCRIPTION", "client.audio.transcription", audio_transcription))
        if audio_voice:
            applied.append(("OMNI_AUDIO_VOICE", "client.audio.voice", audio_voice))
        if audio_whisper:
            applied.append(("OMNI_AUDIO_WHISPER_MODEL", "client.audio.whisper_model", audio_whisper))
        if audio_cache:
            applied.append(("OMNI_AUDIO_CACHE_DIR", "client.audio.cache_dir", audio_cache))

    # ------------------------- judge -------------------------
    judge_model = _env(env, "OMNI_JUDGE_MODEL")
    judge = client.judge
    if judge_model:
        judge = JudgeSection(model=judge_model)
        applied.append(("OMNI_JUDGE_MODEL", "client.judge.model", judge_model))

    # ------------------------- doc parser -------------------------
    dp_provider = _env(env, "OMNI_DOC_PARSER_PROVIDER")
    doc_parser = client.doc_parser
    if dp_provider:
        doc_parser = DocParserSection(provider=dp_provider)  # type: ignore[arg-type]
        applied.append(("OMNI_DOC_PARSER_PROVIDER", "client.doc_parser.provider", dp_provider))

    # ------------------------- sandbox -------------------------
    sb_image = _env(env, "OMNI_SANDBOX_IMAGE")
    sb_memory = _env(env, "OMNI_SANDBOX_MEMORY")
    sb_network = _env(env, "OMNI_SANDBOX_NETWORK")
    sandbox = client.sandbox
    if sb_image or sb_memory or sb_network:
        sandbox = SandboxSection(
            image=sb_image if sb_image else (sandbox.image if sandbox else None),
            memory=sb_memory if sb_memory else (sandbox.memory if sandbox else "256m"),
            network=_bool(sb_network, default=sandbox.network if sandbox else False) if sb_network else (sandbox.network if sandbox else False),
        )
        if sb_image:
            applied.append(("OMNI_SANDBOX_IMAGE", "client.sandbox.image", sb_image))
        if sb_memory:
            applied.append(("OMNI_SANDBOX_MEMORY", "client.sandbox.memory", sb_memory))
        if sb_network:
            applied.append(("OMNI_SANDBOX_NETWORK", "client.sandbox.network", sb_network))

    # ------------------------- client セクションを組み立てる -------------------------
    new_client = ClientSection(
        chat=chat,
        embedding=emb,
        rerank=client.rerank,
        search=search,
        image=image,
        doc_parser=doc_parser,
        judge=judge,
        audio=audio,
        sandbox=sandbox,
    )

    # ------------------------- 接続上書き（chat base_url のみ） -------------------------
    # connections を既存の接続に重ねる（他のキーは触らない）
    cfg_dict = cfg.model_dump(by_alias=True, exclude_none=True)
    for prov, over in connections.items():
        key = prov  # alias そのまま（"ollama-cloud" など）
        existing = cfg_dict.get(key)
        if isinstance(existing, dict):
            existing.update(over)
        else:
            cfg_dict[key] = dict(over)
    cfg_dict["client"] = new_client.model_dump(exclude_none=True)
    new_cfg = OmniConfig.model_validate(cfg_dict)

    return new_cfg, EnvOverrideSummary(tuple(applied))


def summarize_env_overrides(environ: Mapping[str, str] | None = None) -> list[str]:
    """環境変数で何が上書きされるかの一覧を人が読める形で返す（cli checks 用）。

    `cfg` を持たない場面（``cli checks`` の冒頭）で、どの変数が立っているかだけを表示する。
    """
    env = environ if environ is not None else os.environ
    known = [
        "OMNI_CHAT_PROVIDER",
        "OMNI_CHAT_MODEL",
        "OMNI_CHAT_BASE_URL",
        "OMNI_EMBEDDING_PROVIDER",
        "OMNI_EMBEDDING_MODEL",
        "OMNI_EMBEDDING_CACHE_DIR",
        "OMNI_SEARCH_PROVIDER",
        "OMNI_SEARCH_BASE_URL",
        "OMNI_SEARCH_REGION",
        "OMNI_SEARCH_SAFESEARCH",
        "OMNI_IMAGE_PROVIDER",
        "OMNI_IMAGE_BASE_URL",
        "OMNI_AUDIO_SPEECH",
        "OMNI_AUDIO_TRANSCRIPTION",
        "OMNI_AUDIO_VOICE",
        "OMNI_AUDIO_WHISPER_MODEL",
        "OMNI_AUDIO_CACHE_DIR",
        "OMNI_JUDGE_MODEL",
        "OMNI_DOC_PARSER_PROVIDER",
        "OMNI_SANDBOX_IMAGE",
        "OMNI_SANDBOX_MEMORY",
        "OMNI_SANDBOX_NETWORK",
    ]
    lines = []
    for name in known:
        val = _env(env, name)
        if val is not None:
            lines.append(f"{name} = {val!r}")
    return lines
