"""Tests for research_helper.llm.client."""
from __future__ import annotations

import pytest
from research_helper import config
from research_helper.llm.client import (
    _DEFAULT_MODELS,
    _base_url,
    _api_key,
)


class TestDefaultModels:
    def test_has_all_providers(self):
        expected = {"anthropic", "openai", "deepseek", "qwen", "mimo"}
        assert set(_DEFAULT_MODELS.keys()) == expected

    def test_mimo_model(self):
        assert _DEFAULT_MODELS["mimo"] == "mimo-v2.5-pro"


class TestBaseUrl:
    def test_deepseek_default(self, monkeypatch):
        monkeypatch.setattr(config, "DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        url = _base_url("deepseek")
        assert url == "https://api.deepseek.com"

    def test_qwen_default(self, monkeypatch):
        monkeypatch.setattr("research_helper.config.QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        url = _base_url("qwen")
        assert url == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_mimo_default(self, monkeypatch):
        monkeypatch.setattr(config, "MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
        url = _base_url("mimo")
        assert url == "https://token-plan-cn.xiaomimimo.com/v1"

    def test_openai_returns_none_by_default(self, monkeypatch):
        monkeypatch.setattr(config, "OPENAI_BASE_URL", "")
        url = _base_url("openai")
        assert url is None

    def test_unknown_provider_returns_none(self):
        assert _base_url("anthropic") is None


class TestApiKey:
    def test_mimo_key_name(self, monkeypatch):
        monkeypatch.setattr(config, "MIMO_API_KEY", "test-mimo-key")
        assert _api_key("mimo") == "test-mimo-key"

    def test_raises_on_missing(self, monkeypatch):
        monkeypatch.setattr("research_helper.config.MIMO_API_KEY", "")
        with pytest.raises((ValueError, OSError), match="MIMO_API_KEY"):
            _api_key("mimo")
