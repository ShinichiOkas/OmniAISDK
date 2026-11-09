"""LLM 応答から JSON 本体だけを取り出す寛容なヘルパー。

フェンスがどこにあっても拾う・末尾カンマを機械的に直す・切り詰めは呼び出し側で救う。
実測の事故（実運用で見た事例）:

- Gemini（Claude 経由）: フェンスの前後に地の文があり ``strip_code_fence`` で剥がせなかった → KG 0 ノード
- Ollama gemma4:31b: 末尾カンマ ``{"a": 1,}`` で全試行が落ちた（2026-10-12・6 件）
- 任意のモデル: 先頭 JSON の後ろに地の文と**同じ JSON の再掲**が続き ``Extra data`` で落ちた（6 日 208 件）

このため、取り出しは機械的な規則で済ます（モデルに依存しない）。
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["extract_json_block", "parse_json_response", "repair_trailing_commas"]

_FENCE_RE = re.compile(r"```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n(.*?)(?:```|\Z)", re.DOTALL)


def _matching_span(text: str, start: int) -> str:
    """``text[start]`` の括弧に対応する閉じまでを返す（文字列リテラル内は無視）。

    閉じが見つからない（応答が途中で切れた）場合は start 以降を全部返し、呼び出し側に委ねる。
    """
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def extract_json_block(text: str, *, scan_embedded: bool = True) -> str:
    """LLM 応答から JSON 本体だけを取り出す。

    1. ``` フェンスが**どこにあっても**その中身（JSON らしいブロックを優先）
    2. 残りが ``{`` / ``[`` で始まればそのまま（ただし先頭対応括弧までで切れて JSON として読めれば
       そこで切る＝「同じ JSON の再掲」で ``Extra data`` にならない）
    3. ``scan_embedded`` なら地の文に埋まった最初の ``{`` / ``[`` から対応する閉じ括弧まで
    4. どれにも当たらなければ元のテキスト
    """
    text = text.strip()
    if not text:
        return text

    blocks = [m.group(1).strip() for m in _FENCE_RE.finditer(text)]
    blocks = [b for b in blocks if b]
    if blocks:
        for block in blocks:
            if block.startswith(("{", "[")):
                return block
        return blocks[0]

    if text.startswith(("{", "[")):
        span = _matching_span(text, 0)
        if len(span) < len(text):
            try:
                json.loads(span)
            except json.JSONDecodeError:
                return text
            return span
        return text

    if scan_embedded:
        candidates = [i for i in (text.find("{"), text.find("[")) if i >= 0]
        if candidates:
            return _matching_span(text, min(candidates))

    return text


def repair_trailing_commas(text: str) -> str:
    """``}`` / ``]`` の直前にある余計なカンマ（``{"a": 1,}`` ・ ``[1, 2,]``）だけを取り除く。

    直すのはこれだけ。欠けた括弧・``,,`` のような「どう直すか意図が分からないもの」は触らない。
    文字列リテラルの中の ``,}`` は触らない。
    """
    out: list[str] = []
    in_string = False
    escape = False
    n = len(text)
    for i, ch in enumerate(text):
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                continue
        out.append(ch)
    return "".join(out)


def parse_json_response(text: str, *, scan_embedded: bool = True) -> Any:
    """応答から JSON 本体を取り出して ``json.loads`` する。

    - フェンスのどこにあっても拾う
    - 失敗したら末尾カンマだけ機械的に直して読み直す
    - それでも読めなければ元の例外を投げ直す
    """
    block = extract_json_block(text, scan_embedded=scan_embedded)
    try:
        return json.loads(block)
    except json.JSONDecodeError as first_error:
        if block.startswith(("{", "[")):
            repaired = repair_trailing_commas(block)
            if repaired != block:
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError:
                    pass
        raise first_error
