"""describe_image 相当の流し方（画像部品つきメッセージ）がプロバイダに通る形になっているかの契約試験。

実機の確認は Mnemo 置き換えのときに合わせて実施する。ここでは SDK の送信側（codec）と
受信側（parse）がつながる形になっていることだけを単体で確かめる。
"""

from __future__ import annotations

import base64

from omni_ai.providers._base import ChatRequest
from omni_ai.providers.gemini.codec import build_contents as gemini_build_contents
from omni_ai.providers.openai_compatible.codec import build_chat_body
from omni_ai.types import ChatMessage, MediaPart, TextPart

_PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
)


def _image_message(prompt: str = "describe") -> ChatMessage:
    return ChatMessage.user(
        [
            TextPart(text=prompt),
            MediaPart(type="image", source=_PNG, mime_type="image/png"),
        ]
    )


def test_gemini_sends_image_part_as_inline_data():
    _, contents = gemini_build_contents([_image_message()], inline_limit=10_000_000)
    parts = contents[-1]["parts"]
    kinds = [list(p.keys())[0] for p in parts]
    assert "text" in kinds
    # インライン画像は inlineData か fileData のいずれかで出る
    assert any(k in ("inlineData", "fileData") for k in kinds)


def test_openai_compatible_sends_image_as_image_url():
    msg = _image_message()
    req = ChatRequest(messages=[msg], model="gpt-4o")
    body = build_chat_body(req, stream=False, provider="openai-compatible")
    content = body["messages"][-1]["content"]
    # 画像は image_url の形で出る（OpenAI 互換の既定）
    kinds = [p.get("type") for p in content if isinstance(p, dict)]
    assert "image_url" in kinds
    assert "text" in kinds
