"""Chat 以外の 9 領域の応答型（仕様書 §4 のまま。アダプターは未実装）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Union

from pydantic import BaseModel, Field

from .common import ResponseMeta

__all__ = [
    "AudioSpeechResponse",
    "AudioTranscriptionResponse",
    "ChoiceQuestion",
    "EmbeddingResponse",
    "GeneratedImage",
    "ImageResponse",
    "JudgeQuestion",
    "JudgeResponse",
    "MCPAttachment",
    "MCPToolResult",
    "NoulQuestion",
    "ParsedDocument",
    "RerankItem",
    "RerankResponse",
    "SandboxExecutionResult",
    "ScoreQuestion",
    "SearchItem",
    "SearchResponse",
    "VerdictResult",
]


class NoulQuestion(BaseModel):
    kind: Literal["noul"] = "noul"
    instructions: str
    # "false" / "true" それぞれの説明（省略可。実測: Ollama の decision モデルは省略を受け付けた）
    criteria: dict[Literal["false", "true"], str] | None = None


class ChoiceQuestion(BaseModel):
    kind: Literal["choice"] = "choice"
    instructions: str
    criteria: dict[str, str | None]


class ScoreQuestion(BaseModel):
    kind: Literal["score"] = "score"
    instructions: str
    criteria: list[str]


JudgeQuestion = Union[NoulQuestion, ChoiceQuestion, ScoreQuestion]  # noqa: UP007


class VerdictResult(BaseModel):
    """判定 1 件の結果。

    - noul: ``value`` は bool（true の確率 >= 0.5）、``probability`` は true の確率
    - choice: ``value`` は選ばれた選択肢、``probability`` はその確率、``distribution`` は全選択肢の確率
    - score: ``value`` は段階番号（0 始まり）の確率加重平均、``distribution`` は段階名ごとの確率、
      ``probability`` は最も確率の高い段階の確率
    """

    value: bool | str | float
    probability: float
    distribution: dict[str, float] | None = None
    confidence: float | None = None


class JudgeResponse(BaseModel):
    verdicts: dict[str, VerdictResult]
    meta: ResponseMeta


class GeneratedImage(BaseModel):
    data: bytes
    mime_type: str = "image/png"
    revised_prompt: str | None = None

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.write_bytes(self.data)
        return target


class ImageResponse(BaseModel):
    images: list[GeneratedImage]
    meta: ResponseMeta


class AudioSpeechResponse(BaseModel):
    audio_bytes: bytes
    format: Literal["mp3", "wav", "opus", "pcm"]
    meta: ResponseMeta


class AudioTranscriptionResponse(BaseModel):
    text: str
    language: str | None = None
    duration_seconds: float | None = None
    meta: ResponseMeta


class EmbeddingResponse(BaseModel):
    vectors: list[list[float]]
    dimensions: int
    meta: ResponseMeta
    normalized: bool = False  # L2 正規化済みか


class RerankItem(BaseModel):
    index: int
    document: str
    relevance_score: float


class RerankResponse(BaseModel):
    results: list[RerankItem]
    meta: ResponseMeta


class SearchItem(BaseModel):
    title: str
    url: str
    content: str
    score: float | None = None


class SearchResponse(BaseModel):
    results: list[SearchItem]
    meta: ResponseMeta


class ParsedDocument(BaseModel):
    markdown: str
    page_count: int | None = None
    mime_type: str | None = None
    # 本文を取り出せなかった理由（暗号化・画像だけの PDF・変換失敗など）。入っているとき markdown は空
    empty_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    meta: ResponseMeta


class SandboxExecutionResult(BaseModel):
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    artifacts: list[bytes] = Field(default_factory=list)
    meta: ResponseMeta


class MCPAttachment(BaseModel):
    """MCP ツール結果のうち、テキストでないもの（画像・音声・blob・リンク）。base64 はテキストに混ぜない。"""

    kind: Literal["image", "audio", "blob", "link"]
    mime_type: str | None = None
    data_base64: str | None = None
    uri: str | None = None


class MCPToolResult(BaseModel):
    text: str = ""
    is_error: bool = False
    structured: dict[str, Any] | None = None
    attachments: list[MCPAttachment] = Field(default_factory=list)

    def for_model(self) -> str:
        """LLM へ返す文字列。エラーは {"error": ...}、添付はテキストで注記する（base64 は渡さない）。"""
        import json

        if self.is_error:
            return json.dumps({"error": self.text or "tool failed"}, ensure_ascii=False)
        notes = [f"[attachment: {a.kind} {a.mime_type or a.uri or ''}]".strip() for a in self.attachments]
        body = self.text
        if not body and self.structured is not None:
            body = json.dumps(self.structured, ensure_ascii=False)
        return "\n".join([body, *notes]).strip()
