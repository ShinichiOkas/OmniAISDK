"""I/O を持たないストリームデコーダー（SSE / NDJSON）。バイト列を任意の位置で分割して渡してよい。"""

from __future__ import annotations

import codecs
import json
from dataclasses import dataclass, field
from typing import Any

__all__ = ["MalformedLine", "NDJSONDecoder", "SSEDecoder", "SSEEvent"]


class _LineSplitter:
    """バイト列を UTF-8 として安全に復号し、行（CR / LF / CRLF 区切り）に分ける。"""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._buf = ""
        self._pending_cr = False

    def feed(self, chunk: bytes) -> list[str]:
        text = self._decoder.decode(chunk)
        return self._split(text)

    def flush(self) -> list[str]:
        text = self._decoder.decode(b"", final=True)
        lines = self._split(text)
        if self._buf:
            lines.append(self._buf)
            self._buf = ""
        return lines

    def _split(self, text: str) -> list[str]:
        lines: list[str] = []
        for ch in text:
            if self._pending_cr:
                self._pending_cr = False
                if ch == "\n":
                    continue  # CRLF の LF
            if ch == "\r":
                lines.append(self._buf)
                self._buf = ""
                self._pending_cr = True
            elif ch == "\n":
                lines.append(self._buf)
                self._buf = ""
            else:
                self._buf += ch
        return lines


@dataclass
class SSEEvent:
    data: str
    event: str = "message"
    id: str | None = None

    def json(self) -> Any:
        return json.loads(self.data)


@dataclass
class SSEDecoder:
    """W3C Server-Sent Events の最小実装。"""

    _lines: _LineSplitter = field(default_factory=_LineSplitter)
    _data: list[str] = field(default_factory=list)
    _event: str = ""
    _id: str | None = None

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        return self._process(self._lines.feed(chunk))

    def flush(self) -> list[SSEEvent]:
        events = self._process(self._lines.flush())
        events.extend(self._dispatch())
        return events

    def _process(self, lines: list[str]) -> list[SSEEvent]:
        out: list[SSEEvent] = []
        for line in lines:
            if line == "":
                out.extend(self._dispatch())
                continue
            if line.startswith(":"):
                continue
            name, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if name == "data":
                self._data.append(value)
            elif name == "event":
                self._event = value
            elif name == "id":
                self._id = value
        return out

    def _dispatch(self) -> list[SSEEvent]:
        if not self._data:
            self._event = ""
            return []
        ev = SSEEvent(data="\n".join(self._data), event=self._event or "message", id=self._id)
        self._data = []
        self._event = ""
        return [ev]


@dataclass(frozen=True)
class MalformedLine:
    """JSON として読めなかった行。例外にせず、順序を保ったまま呼び出し側に渡す。"""

    text: str


def _parse_line(line: str) -> Any:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return MalformedLine(line[:200])


@dataclass
class NDJSONDecoder:
    """改行区切り JSON。空行は無視し、壊れた行は MalformedLine として返す。"""

    _lines: _LineSplitter = field(default_factory=_LineSplitter)

    def feed(self, chunk: bytes) -> list[Any]:
        return [_parse_line(line) for line in self._lines.feed(chunk) if line.strip()]

    def flush(self) -> list[Any]:
        return [_parse_line(line) for line in self._lines.flush() if line.strip()]
