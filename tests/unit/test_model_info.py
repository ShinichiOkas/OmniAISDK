"""モデル情報の解釈の単体試験。"""

from __future__ import annotations

from omni_ai._model_info import (
    parse_ollama_show,
    parse_ollama_tags,
    parse_openrouter_models,
    static_context_window,
)


def test_static_context_window_exact():
    assert static_context_window("claude-opus-4-8") == 200_000
    assert static_context_window("gemini-3.1-pro") == 1_000_000


def test_static_context_window_strips_cloud():
    assert static_context_window("gemma4:31b-cloud") == 131_072


def test_static_context_window_versioned():
    assert static_context_window("claude-opus-4-8-20260801") == 200_000


def test_static_context_window_unknown():
    assert static_context_window("never-heard-of-this") is None


def test_parse_ollama_show_model_info():
    data = {
        "model_info": {"gemma4.context_length": 131072, "general.architecture": "gemma4"},
        "capabilities": ["tools", "vision"],
    }
    info = parse_ollama_show(data, name="gemma4:31b")
    assert info.context_window == 131072
    assert info.supports_tools is True
    assert info.supports_vision is True


def test_parse_ollama_show_parameters_fallback():
    data = {"parameters": "stop <|end|>\nnum_ctx 8192\nllama.context_length = 131072"}
    info = parse_ollama_show(data, name="llama3")
    assert info.context_window == 131072


def test_parse_ollama_show_missing():
    info = parse_ollama_show({}, name="x")
    assert info.context_window is None
    assert info.supports_tools is None


def test_parse_ollama_tags():
    data = {"models": [{"name": "a"}, {"name": "b"}, {"size": 1}]}
    assert parse_ollama_tags(data) == ["a", "b"]


def test_parse_openrouter_models():
    data = {
        "data": [
            {
                "id": "openai/gpt-4o",
                "context_length": 128000,
                "top_provider": {"max_completion_tokens": 16384},
                "pricing": {"prompt": "2.5", "completion": "10"},
                "architecture": {"input_modalities": ["text", "image"]},
                "supported_parameters": ["tools", "temperature"],
            }
        ]
    }
    result = parse_openrouter_models(data)
    assert "openai/gpt-4o" in result
    info = result["openai/gpt-4o"]
    assert info.context_window == 128000
    assert info.max_output_tokens == 16384
    assert info.supports_tools is True
    assert info.supports_vision is True
    assert info.pricing == {"input": 2.5, "output": 10.0}
