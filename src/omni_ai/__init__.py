"""OmniAI SDK — マルチプロバイダ統合 Python SDK。"""

from ._json_extract import extract_json_block
from ._model_info import ModelInfo
from ._resilience import DeadUpstreamGate, StreamIdleError, UpstreamUnreachableError
from ._version import __version__
from .builder import ConfigurationError
from .capabilities import ChatCapability
from .exceptions import (
    AuthenticationError,
    CapabilityNotConfiguredError,
    ContextLengthExceededError,
    InvalidRequestError,
    NotFoundError,
    OmniError,
    PermissionDeniedError,
    ProviderConnectionError,
    ProviderOverloadedError,
    ProviderTimeoutError,
    QuotaExceededError,
    RateLimitError,
    SchemaValidationError,
    StreamInterruptedError,
    ToolLoopLimitError,
    UnsupportedFeatureError,
)
from .pricing import ModelPrice, PricingTable
from .router import FallbackChatAdapter, OmniClient, TelemetryTracker
from .tools import ToolRunner, ToolRunResult
from .types import (
    BuiltinTool,
    ChatMessage,
    ChatResponse,
    FileRef,
    MediaPart,
    ResponseMeta,
    Role,
    StreamEvent,
    TextPart,
    ToolCall,
    ToolDefinition,
    UrlRef,
    omni_tool,
)

__all__ = [
    "AuthenticationError",
    "BuiltinTool",
    "CapabilityNotConfiguredError",
    "ChatCapability",
    "ChatMessage",
    "ChatResponse",
    "ConfigurationError",
    "ContextLengthExceededError",
    "DeadUpstreamGate",
    "FallbackChatAdapter",
    "FileRef",
    "InvalidRequestError",
    "MediaPart",
    "ModelInfo",
    "ModelPrice",
    "NotFoundError",
    "OmniClient",
    "OmniError",
    "PermissionDeniedError",
    "PricingTable",
    "ProviderConnectionError",
    "ProviderOverloadedError",
    "ProviderTimeoutError",
    "QuotaExceededError",
    "RateLimitError",
    "ResponseMeta",
    "Role",
    "SchemaValidationError",
    "StreamEvent",
    "StreamIdleError",
    "StreamInterruptedError",
    "TelemetryTracker",
    "TextPart",
    "ToolCall",
    "ToolDefinition",
    "ToolLoopLimitError",
    "ToolRunResult",
    "ToolRunner",
    "UnsupportedFeatureError",
    "UpstreamUnreachableError",
    "UrlRef",
    "__version__",
    "extract_json_block",
    "omni_tool",
]
