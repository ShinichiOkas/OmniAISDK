"""既存ベクトル DB と bit 単位で一致する L2 正規化（sentence-transformers 互換の順序）。

sentence-transformers の ``encode(normalize_embeddings=True)`` と、呼び出し側の多くの実装が使う
『まず float32 に落とし、L2 で割って、もう一度 float32 に落とす』順序で正規化する。順序を変えると
丸めが変わり、保存済みのベクトルと bit が合わなくなる:

1. ``np.asarray(v, dtype=np.float32)`` で float32 に落とす（fastembed の生出力は float64）
2. ``np.linalg.norm(arr, axis=-1, keepdims=True)`` で L2 ノルム
3. ゼロベクトル除算回避: ``np.where(norm == 0, 1.0, norm)``
4. ``(arr / norm).astype(np.float32)`` で float32 に再度落とす
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np

__all__ = ["normalize_bit_compat", "vectors_to_float32_array"]


def normalize_bit_compat(arr: Any) -> Any:
    """既存の保存済みベクトルと bit 単位で一致する L2 正規化（float32 保存）。"""
    import numpy as np

    a = np.asarray(arr, dtype=np.float32)
    norm = np.linalg.norm(a, axis=-1, keepdims=True)
    norm = np.where(norm == 0, 1.0, norm)
    return (a / norm).astype(np.float32)


def vectors_to_float32_array(vectors: Any) -> Any:
    """fastembed の反復子／リストを float32 の 2 次元 ``np.ndarray`` に揃える。"""
    import numpy as np

    return np.asarray(list(vectors), dtype=np.float32)
