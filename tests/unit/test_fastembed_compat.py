"""1.D: FastEmbedEmbeddingAdapter の embed_array と保存済みベクトル互換の L2 正規化の単体試験。

本物の fastembed は開かない（CI では重い）。偽物のモデルで embed_array の形と
正規化の順序（sentence-transformers 互換・保存済みベクトルと bit 一致）を確かめる。
"""

from __future__ import annotations

import numpy as np
import pytest

from omni_ai.providers.fastembed import FastEmbedEmbeddingAdapter
from omni_ai.providers.fastembed._numpy_compat import normalize_bit_compat


class _FakeTE:
    """fastembed.TextEmbedding の最小の偽物。``embed`` は float64 の生出力（未正規化）を返す。"""

    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = [np.asarray(v, dtype=np.float64) for v in vectors]

    def embed(self, texts, batch_size: int = 64):  # noqa: ARG002 - fastembed の署名に合わせる
        for v in self._vectors[: len(list(texts))]:
            yield v


def _factory_for(vectors):
    def make(**_kw):
        return _FakeTE(vectors)

    return make


def _reference_normalize(vectors: list[list[float]]) -> np.ndarray:
    """下流（sentence-transformers 互換）の正規化の引き写し（検証用）。"""
    arr = np.asarray(vectors, dtype=np.float32)
    norm = np.linalg.norm(arr, axis=-1, keepdims=True)
    norm = np.where(norm == 0, 1.0, norm)
    return (arr / norm).astype(np.float32)


# ---------- normalize_bit_compat の bit 一致 ----------


def test_normalize_bit_compat_matches_reference_bits() -> None:
    raw = [
        [1.0, 2.0, 2.0],       # norm = 3
        [0.0, 0.0, 0.0],       # ゼロベクトル
        [3.1415926535, -2.718281828, 1.4142135623],  # 中途半端な浮動小数
    ]
    out = normalize_bit_compat(raw)
    ref = _reference_normalize(raw)
    assert out.dtype == np.float32
    # dtype・形・bit 列が完全一致
    assert out.shape == ref.shape
    assert np.array_equal(out, ref)
    assert out.tobytes() == ref.tobytes()


# ---------- embed_array ----------


def test_embed_array_returns_float32_and_bit_compat() -> None:
    raw = [[1.0, 2.0, 2.0], [3.0, 4.0, 0.0]]
    ad = FastEmbedEmbeddingAdapter(model="fake", model_factory=_factory_for(raw))
    arr = ad.embed_array(["a", "b"])
    assert arr.dtype == np.float32
    assert arr.shape == (2, 3)
    ref = _reference_normalize(raw)
    assert np.array_equal(arr, ref)


def test_embed_array_no_normalize_returns_raw_float32() -> None:
    raw = [[1.0, 2.0, 2.0]]
    ad = FastEmbedEmbeddingAdapter(model="fake", model_factory=_factory_for(raw))
    arr = ad.embed_array(["a"], normalize=False)
    assert arr.dtype == np.float32
    assert np.allclose(arr, np.asarray(raw, dtype=np.float32))


def test_embed_array_empty_input_returns_empty_array() -> None:
    ad = FastEmbedEmbeddingAdapter(model="fake", model_factory=_factory_for([]))
    with pytest.raises(Exception):  # 空文字は prepare_texts で弾かれる
        ad.embed_array([""])


# ---------- embed() と embed_array() が同じ値を返す ----------


def test_embed_list_matches_embed_array_bits() -> None:
    raw = [[1.0, 2.0, 2.0], [3.0, 4.0, 0.0]]
    ad = FastEmbedEmbeddingAdapter(model="fake", model_factory=_factory_for(raw))
    arr = ad.embed_array(["a", "b"])
    res = ad.embed(["a", "b"])
    from_list = np.asarray(res.vectors, dtype=np.float32)
    # list → float → ndarray の経路でも同じ bit 列になる
    assert np.array_equal(arr, from_list)


# ---------- 非同期 ----------


@pytest.mark.asyncio
async def test_aembed_array_matches_sync() -> None:
    raw = [[1.0, 2.0, 2.0]]
    ad = FastEmbedEmbeddingAdapter(model="fake", model_factory=_factory_for(raw))
    sync = ad.embed_array(["a"])
    asyn = await ad.aembed_array(["a"])
    assert np.array_equal(sync, asyn)


# ---------- mnemo_compat_normalize=False では bit 一致しない（境界の確認） ----------


def test_pure_python_normalize_is_close_but_not_bit_identical() -> None:
    raw = [[1.0, 2.0, 2.0]]
    ad = FastEmbedEmbeddingAdapter(
        model="fake", model_factory=_factory_for(raw), bit_compat_normalize=False
    )
    arr = ad.embed_array(["a"])
    # 丸めが異なるが、値自体は近い
    ref = _reference_normalize(raw)
    assert np.allclose(arr, ref, atol=1e-6)
