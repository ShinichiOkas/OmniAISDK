"""読み上げの記法を区間（スタイルつき）に割る（1.20）。

受け付ける書式は 3 つ:

- **SSML**（既定）: ``<speak>...</speak>`` の中で ``<prosody rate='...' pitch='...' volume='...'>``、
  ``<emphasis level='strong|moderate|reduced'>``、``<break time='500ms'/>`` を見る
- **HTML 風**: ``<b>／<strong>／<em>`` を強調、``<break time='300ms'/>`` を間、
  ``<prosody rate='...'>`` を話速として扱う
- **Markdown 風**: ``**strong**``（強調）、``[pause:500ms]`` または ``[間:500ms]``（間）

区間ごとに ``SpeechSpan`` を返す。``kind='text'`` は読み上げる文字列、
``kind='pause'`` は無音の長さ（ミリ秒）を持つ。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

__all__ = ["SpeechSpan", "detect_markup", "parse_speech_markup"]

Markup = Literal["ssml", "html", "markdown", "plain"]


@dataclass
class SpeechSpan:
    """合成の 1 区間。``kind='text'`` ならスタイル、``kind='pause'`` なら ``pause_ms`` を使う。"""

    kind: Literal["text", "pause"] = "text"
    text: str = ""
    rate: int = 0        # -50..+100 %
    pitch: int = 0       # -50..+50 Hz
    volume: int = 0      # -100..+100 %
    pause_ms: int = 0    # kind='pause' のときだけ使う
    tags: list[str] = field(default_factory=list)  # 入れ子で付いていた記法（デバッグ用）


def detect_markup(text: str) -> Markup:
    """text の形から書式を推測する（``speak_markup=`` が未指定のとき使う）。"""
    t = text.lstrip()
    low = t[:200].lower()
    if low.startswith("<speak"):
        return "ssml"
    if "<break" in low or "<prosody" in low or "<emphasis" in low:
        return "ssml"
    if "<b>" in low or "<strong>" in low or "<em>" in low:
        return "html"
    if "**" in t or "[pause:" in t or "[間:" in t:
        return "markdown"
    return "plain"


# ---------- SSML / HTML ----------

_TAG_RE = re.compile(r"<(/?)([a-zA-Z]+)([^>]*?)(/?)>")
_ATTR_RE = re.compile(r"""([a-zA-Z-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s/>]+)""")


def _parse_attrs(blob: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR_RE.finditer(blob):
        v = m.group(2)
        if v and v[0] in "'\"" and v[-1] == v[0]:
            v = v[1:-1]
        out[m.group(1).lower()] = v
    return out


def _percent(value: str) -> int:
    """``'+10%'`` / ``'-5%'`` / ``'10'`` / ``'medium'`` を整数の % に落とす。"""
    v = value.strip()
    aliases = {"x-slow": -50, "slow": -25, "medium": 0, "fast": 50, "x-fast": 100,
               "x-soft": -50, "soft": -25, "loud": 50, "x-loud": 100}
    if v.lower() in aliases:
        return aliases[v.lower()]
    if v.endswith("%"):
        v = v[:-1]
    try:
        return int(round(float(v)))
    except ValueError:
        return 0


def _hz(value: str) -> int:
    """``'+10Hz'`` / ``'-5Hz'`` / ``'medium'`` を Hz に落とす。"""
    v = value.strip()
    aliases = {"x-low": -50, "low": -25, "medium": 0, "high": 25, "x-high": 50}
    if v.lower() in aliases:
        return aliases[v.lower()]
    if v.lower().endswith("hz"):
        v = v[:-2]
    if v.endswith("%"):
        v = v[:-1]
    try:
        return int(round(float(v)))
    except ValueError:
        return 0


def _duration_ms(value: str) -> int:
    v = value.strip().lower()
    if v.endswith("ms"):
        try:
            return int(round(float(v[:-2])))
        except ValueError:
            return 0
    if v.endswith("s"):
        try:
            return int(round(float(v[:-1]) * 1000))
        except ValueError:
            return 0
    try:
        return int(round(float(v)))
    except ValueError:
        return 0


def _parse_xml_like(text: str, *, html: bool) -> list[SpeechSpan]:
    """SSML / HTML 風のどちらも、同じトークナイザで処理する。"""
    spans: list[SpeechSpan] = []
    stack: list[dict[str, int]] = [{"rate": 0, "pitch": 0, "volume": 0, "emph": 0}]
    tags_stack: list[str] = []

    def current() -> dict[str, int]:
        return stack[-1]

    def push_text(s: str) -> None:
        s = s.strip()
        if not s:
            return
        c = current()
        rate = c["rate"] + (10 if c["emph"] > 0 else 0)
        volume = c["volume"] + (10 * c["emph"])  # 強調の積み重ね
        spans.append(
            SpeechSpan(
                kind="text", text=s,
                rate=max(-50, min(100, rate)),
                pitch=max(-50, min(50, c["pitch"])),
                volume=max(-100, min(100, volume)),
                tags=list(tags_stack),
            )
        )

    pos = 0
    for m in _TAG_RE.finditer(text):
        push_text(text[pos:m.start()])
        closing, name, attrs, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        attrs_map = _parse_attrs(attrs)
        is_void = selfclose == "/" or name == "break"
        if closing:
            if stack and len(stack) > 1:
                stack.pop()
            if tags_stack:
                tags_stack.pop()
        elif name == "break":
            dur = _duration_ms(attrs_map.get("time", "500ms"))
            if dur > 0:
                spans.append(SpeechSpan(kind="pause", pause_ms=dur, tags=list(tags_stack)))
        elif name == "speak":
            # 外側の speak はスタックに積まない（属性は無視）
            pass
        else:
            new = dict(current())
            if name in ("b", "strong", "em", "i", "emphasis"):
                level = attrs_map.get("level", "moderate").lower()
                step = {"reduced": -1, "moderate": 1, "strong": 2}.get(level, 1)
                new["emph"] = new["emph"] + step
            elif name == "prosody":
                if "rate" in attrs_map:
                    new["rate"] = _percent(attrs_map["rate"])
                if "pitch" in attrs_map:
                    new["pitch"] = _hz(attrs_map["pitch"])
                if "volume" in attrs_map:
                    new["volume"] = _percent(attrs_map["volume"])
            else:
                # 不明なタグは透過
                pass
            if not is_void:
                stack.append(new)
                tags_stack.append(name)
        pos = m.end()
    push_text(text[pos:])
    return spans


# ---------- Markdown ----------

_MD_PAUSE_RE = re.compile(r"\[(?:pause|間):\s*([^\]]+)\]")
_MD_STRONG_RE = re.compile(r"\*\*([^*]+)\*\*")


def _parse_markdown(text: str) -> list[SpeechSpan]:
    spans: list[SpeechSpan] = []
    pos = 0
    pattern = re.compile(r"\[(?:pause|間):\s*([^\]]+)\]|\*\*([^*]+)\*\*")
    for m in pattern.finditer(text):
        before = text[pos:m.start()].strip()
        if before:
            spans.append(SpeechSpan(kind="text", text=before))
        if m.group(1) is not None:
            spans.append(SpeechSpan(kind="pause", pause_ms=_duration_ms(m.group(1))))
        else:
            spans.append(SpeechSpan(kind="text", text=m.group(2), rate=10, volume=10, tags=["md-strong"]))
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        spans.append(SpeechSpan(kind="text", text=tail))
    return spans


# ---------- 公開 ----------


def parse_speech_markup(text: str, markup: Optional[Markup] = None) -> list[SpeechSpan]:
    """``text`` を記法に沿って区間に割る。``markup`` が ``None`` なら形から推測する。"""
    if not text or not text.strip():
        return []
    m: Markup = markup or detect_markup(text)
    if m == "ssml":
        return _parse_xml_like(text, html=False)
    if m == "html":
        return _parse_xml_like(text, html=True)
    if m == "markdown":
        return _parse_markdown(text)
    return [SpeechSpan(kind="text", text=text.strip())]
