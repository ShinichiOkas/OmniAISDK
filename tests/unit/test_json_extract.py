"""寛容な JSON 取り出しの単体試験（Mnemo の実測事故を再現する）。"""

from __future__ import annotations

import json

import pytest

from omni_ai._json_extract import extract_json_block, parse_json_response, repair_trailing_commas


def test_plain_object():
    assert parse_json_response('{"a": 1}') == {"a": 1}


def test_fenced_json():
    text = "```json\n{\"a\": 1}\n```"
    assert parse_json_response(text) == {"a": 1}


def test_fence_with_prose_before_and_after():
    """KG 0 ノード事故（2026-07-30）の再現。"""
    text = "以下は結果です。\n\n```json\n{\"nodes\": [], \"edges\": []}\n```\n\n以上です。"
    assert parse_json_response(text) == {"nodes": [], "edges": []}


def test_trailing_comma_repair():
    """gemma4:31b の末尾カンマ事故（2026-10-12）の再現。"""
    text = '{"a": 1, "b": [1, 2,],}'
    assert parse_json_response(text) == {"a": 1, "b": [1, 2]}


def test_extra_data_after_valid_json():
    """`Extra data` 事故（6 日 208 件）の再現: 先頭 JSON のあとに同じ JSON の再掲や説明が続く。"""
    text = '{"unknown": ["foo"]}\n\n（注: 上の JSON の意味は...）\n{"unknown": ["foo"]}'
    assert parse_json_response(text) == {"unknown": ["foo"]}


def test_json_in_middle_of_prose():
    text = "これが結果: {\"x\": 42} です"
    assert parse_json_response(text) == {"x": 42}


def test_truncated_json_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_json_response('{"a": 1, "b":')


def test_trailing_comma_in_string_untouched():
    assert repair_trailing_commas('{"a": "x,"}') == '{"a": "x,"}'


def test_extract_only():
    assert extract_json_block("ignored\n```json\n{\"a\":1}\n```\nignored") == '{"a":1}'
