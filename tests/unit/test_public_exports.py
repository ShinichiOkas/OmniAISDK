"""私的モジュール（``_`` 付き）の部品が、公開の名前から import できること。

利用側は ``omni_ai._resilience`` のような非公開の場所に依存しない。公開の名前と
従来の非公開の名前が **同じオブジェクト** であることも確かめる（二重定義にしない）。
"""

from __future__ import annotations

import importlib

import pytest

import omni_ai

CASES = [
    # (公開モジュール, 非公開モジュール, 名前)
    ("omni_ai.resilience", "omni_ai._resilience", "DeadUpstreamGate"),
    ("omni_ai.resilience", "omni_ai._resilience", "StreamIdleError"),
    ("omni_ai.resilience", "omni_ai._resilience", "UpstreamUnreachableError"),
    ("omni_ai.json_extract", "omni_ai._json_extract", "extract_json_block"),
    ("omni_ai.model_info", "omni_ai._model_info", "ModelInfo"),
]


@pytest.mark.parametrize(("public", "private", "name"), CASES)
def test_public_module_reexports_same_object(public: str, private: str, name: str) -> None:
    pub = importlib.import_module(public)
    priv = importlib.import_module(private)
    assert name in pub.__all__
    assert getattr(pub, name) is getattr(priv, name)


@pytest.mark.parametrize(("public", "private", "name"), CASES)
def test_top_level_exports_same_object(public: str, private: str, name: str) -> None:
    assert name in omni_ai.__all__
    assert getattr(omni_ai, name) is getattr(importlib.import_module(private), name)


@pytest.mark.parametrize("public", ["omni_ai.resilience", "omni_ai.json_extract", "omni_ai.model_info"])
def test_public_module_all_matches_private_all(public: str) -> None:
    private = public.replace("omni_ai.", "omni_ai._")
    pub = importlib.import_module(public)
    priv = importlib.import_module(private)
    assert set(pub.__all__) == set(priv.__all__)
    for n in pub.__all__:
        assert getattr(pub, n) is getattr(priv, n)


def test_public_names_work_behaviorally() -> None:
    from omni_ai.json_extract import extract_json_block
    from omni_ai.model_info import ModelInfo
    from omni_ai.resilience import DeadUpstreamGate, StreamIdleError, UpstreamUnreachableError

    assert extract_json_block('前置き ```json\n{"a": 1}\n``` 後ろ') == '{"a": 1}'
    assert ModelInfo(name="m").name == "m"
    assert issubclass(UpstreamUnreachableError, Exception)
    assert issubclass(StreamIdleError, Exception)
    gate = DeadUpstreamGate("x", cooldown_seconds=5.0)
    gate.check()  # 何も起きていなければ通る
