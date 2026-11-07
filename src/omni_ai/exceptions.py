"""統一例外階層（仕様書 v3.1 §3 / 実装設計書 §5.1）。

``retryable`` は「同じ要求をそのまま送り直せば成功しうるか」だけを表す。
別プロバイダへ切り替えるかどうかは ``FallbackChatAdapter.fallback_on`` が型で決める。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AuthenticationError",
    "CapabilityNotConfiguredError",
    "ContextLengthExceededError",
    "InvalidRequestError",
    "NotFoundError",
    "OmniError",
    "PermissionDeniedError",
    "ProviderConnectionError",
    "ProviderOverloadedError",
    "ProviderTimeoutError",
    "QuotaExceededError",
    "RateLimitError",
    "SchemaValidationError",
    "StreamInterruptedError",
    "ToolLoopLimitError",
    "UnsupportedFeatureError",
]


class OmniError(Exception):
    """OmniAI SDK すべての例外の基底クラス。"""

    default_retryable: bool = False

    def __init__(
        self,
        message: str,
        provider: str = "omni",
        model: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        raw_error: Any = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.retryable = self.default_retryable if retryable is None else retryable
        # APIキーを含むヘッダは保持しない（アダプター側で除去してから渡す）
        self.raw_error = raw_error
        self.request_id = request_id

    def __str__(self) -> str:
        parts = [self.message]
        ctx = [f"provider={self.provider}"]
        if self.model:
            ctx.append(f"model={self.model}")
        if self.status_code is not None:
            ctx.append(f"status={self.status_code}")
        parts.append(f"({', '.join(ctx)})")
        return " ".join(parts)


class AuthenticationError(OmniError):
    """APIキー不正 (401、および Gemini の 400 API_KEY_INVALID)。"""


class PermissionDeniedError(AuthenticationError):
    """権限不足・地域制限 (403)。"""


class InvalidRequestError(OmniError):
    """要求不正 (400)。"""


class ContextLengthExceededError(InvalidRequestError):
    """最大コンテキスト長超過。"""


class UnsupportedFeatureError(InvalidRequestError):
    """要求された機能をこのアダプター／モデルが扱えない。送信前に SDK が検出する。"""


class NotFoundError(OmniError):
    """モデル・ファイル等が存在しない (404)。"""


class QuotaExceededError(OmniError):
    """課金残高・利用枠の枯渇 (Gemini 402 等)。再試行はしないがフォールバック対象。"""


class RateLimitError(OmniError):
    """レート制限 (429)。"""

    default_retryable = True

    def __init__(self, *args: Any, retry_after_seconds: float | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after_seconds = retry_after_seconds


class ProviderOverloadedError(OmniError):
    """プロバイダ過負荷・一時障害 (500/502/503/504/529)。"""

    default_retryable = True


class ProviderConnectionError(OmniError):
    """接続拒否・DNS 失敗など。"""

    default_retryable = True


class ProviderTimeoutError(ProviderConnectionError):
    """タイムアウト。"""


class StreamInterruptedError(OmniError):
    """最初のイベント送出後のストリーム失敗。フォールバックしない (ADR-07)。"""


class SchemaValidationError(OmniError):
    """構造化出力の型検証失敗。"""


class ToolLoopLimitError(OmniError):
    """ToolRunner が max_turns に達した。途中結果を ``partial_result`` に保持する。"""

    def __init__(self, *args: Any, partial_result: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.partial_result = partial_result


class CapabilityNotConfiguredError(OmniError):
    """OmniClient で未設定の Capability にアクセスした。"""
