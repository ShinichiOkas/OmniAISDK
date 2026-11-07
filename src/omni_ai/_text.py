"""テキストの正規化。"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["normalize_cjk_forms"]

# 康熙部首 / CJK 部首補助 / CJK 互換漢字 / CJK 互換漢字補助
_TARGET_RE = re.compile(r"[⺀-⻿⼀-⿟豈-﫿\U0002f800-\U0002fa1f]")


def normalize_cjk_forms(text: str) -> str:
    """康熙部首・互換漢字を、文字ごとの NFKC で通常の漢字へ写す。

    PDF の抽出では、見た目が同じ「口」が U+2F1D（康熙部首）で返ることがあり、
    完全一致や重複判定で別の字になる。
    対象の 4 範囲の文字だけを写し、全角英数字・丸数字・半角カナなどは 1 字も変えない（全体の NFKC はしない）。
    """
    return _TARGET_RE.sub(lambda m: unicodedata.normalize("NFKC", m.group(0)), text)
