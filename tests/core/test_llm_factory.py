"""Provider factory configuration tests."""

import langchain_anthropic
import langchain_openai

from enterprise_agent.config.settings import Settings, settings
from enterprise_agent.core.agent.llm_factory import (
    _get_anthropic_llm,
    _get_deepseek_llm,
    _get_openai_compatible_llm,
    get_llm_for_subagent,
)


class CapturedModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_output_limit_has_safe_configurable_default():
    assert Settings.model_fields["MODEL_MAX_OUTPUT_TOKENS"].default == 16_384


def test_anthropic_factories_pass_configured_output_limit(monkeypatch):
    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", CapturedModel)
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "MODEL_MAX_OUTPUT_TOKENS", 12_345)

    direct = _get_anthropic_llm()
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://api.deepseek.com/anthropic")
    deepseek = _get_deepseek_llm()

    assert direct.kwargs["max_tokens"] == 12_345
    assert deepseek.kwargs["max_tokens"] == 12_345


def test_openai_compatible_factory_passes_configured_output_limit(monkeypatch):
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", CapturedModel)
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "MODEL_MAX_OUTPUT_TOKENS", 23_456)

    model = _get_openai_compatible_llm("openai")

    assert model.kwargs["max_tokens"] == 23_456


def test_subagent_config_exposes_same_output_limit(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_MAX_OUTPUT_TOKENS", 34_567)

    assert get_llm_for_subagent()["max_tokens"] == 34_567
