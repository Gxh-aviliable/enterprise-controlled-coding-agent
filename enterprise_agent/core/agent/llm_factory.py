"""LLM Factory for multi-provider support.

Provides a unified interface for creating LLM instances based on provider configuration.
Supported providers: Anthropic, GLM, DeepSeek, OpenAI (and OpenAI-compatible APIs).
"""

from langchain_core.language_models import BaseChatModel

from enterprise_agent.config.settings import settings


# Provider-specific imports (lazy loaded to avoid errors when not used)
def _get_anthropic_llm() -> BaseChatModel:
    """Create Anthropic Claude LLM."""
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=settings.get_effective_model_id(),
        api_key=settings.get_effective_api_key(),
    )


def _get_mimo_llm() -> BaseChatModel:
    """Create MiMo LLM via Anthropic-compatible API."""
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=settings.get_effective_model_id(),
        api_key=settings.get_effective_api_key(),
        base_url=settings.get_effective_base_url(),
    )


def _get_deepseek_llm() -> BaseChatModel:
    """Create DeepSeek LLM.

    Uses ChatAnthropic for the Anthropic-compatible endpoint
    (/anthropic) or ChatOpenAI for the OpenAI-compatible endpoint.

    Note: DeepSeek V4 thinking mode can have long response times
    during thinking generation, so timeout is increased to 300 seconds.
    Also disables SDK internal retries to prevent duplicate retry logic.
    """
    base_url = settings.get_effective_base_url() or ""

    if "/anthropic" in base_url:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=settings.get_effective_model_id(),
            api_key=settings.get_effective_api_key(),
            base_url=base_url,
            timeout=300,  # DeepSeek thinking models need longer timeout
            max_retries=0,  # Disable SDK retries, let langchain handle it
        )

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.get_effective_model_id(),
        api_key=settings.get_effective_api_key(),
        base_url=base_url,
        request_timeout=300,  # OpenAI SDK timeout
        max_retries=0,
    )


def _get_openai_compatible_llm(provider: str) -> BaseChatModel:
    """Create OpenAI-compatible LLM (GLM, DeepSeek, OpenAI)."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.get_effective_model_id(),
        api_key=settings.get_effective_api_key(),
        base_url=settings.get_effective_base_url(),
    )


def get_llm() -> BaseChatModel:
    """Get LLM instance based on current provider configuration.

    The provider is determined by settings.LLM_PROVIDER:
    - "anthropic": Claude models via Anthropic API
    - "glm": GLM models via OpenAI-compatible API (Zhipu AI)
    - "deepseek": DeepSeek models via OpenAI-compatible API
    - "openai": GPT models via OpenAI API

    Returns:
        BaseChatModel: LangChain chat model instance

    Raises:
        ValueError: If provider is not supported or API key is missing
    """
    provider = settings.LLM_PROVIDER.lower()
    api_key = settings.get_effective_api_key()

    if not api_key:
        raise ValueError(f"API key is required for provider '{provider}'. Set LLM_API_KEY or legacy ANTHROPIC_API_KEY.")

    providers = {
        "anthropic": _get_anthropic_llm,
        "glm": lambda: _get_openai_compatible_llm("glm"),
        "deepseek": _get_deepseek_llm,
        "openai": lambda: _get_openai_compatible_llm("openai"),
        "mimo": _get_mimo_llm,
    }

    if provider not in providers:
        raise ValueError(f"Unsupported LLM provider: '{provider}'. Supported: {list(providers.keys())}")

    return providers[provider]()


def get_llm_for_subagent() -> dict:
    """Get LLM configuration for subagent (Anthropic SDK style).

    For subagents that use Anthropic SDK directly, return configuration dict.

    Returns:
        dict with api_key and base_url (for OpenAI-compatible providers)
    """
    provider = settings.LLM_PROVIDER.lower()
    api_key = settings.get_effective_api_key()
    base_url = settings.get_effective_base_url()
    model = settings.get_effective_model_id()

    return {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
    }


# Provider metadata for documentation/UI
PROVIDER_INFO = {
    "anthropic": {
        "name": "Anthropic Claude",
        "models": ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"],
        "tool_support": True,
        "embedding_support": False,
    },
    "glm": {
        "name": "Zhipu GLM",
        "models": ["glm-4", "glm-4-flash", "glm-4-plus"],
        "tool_support": True,
        "embedding_support": True,
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
    },
    "deepseek": {
        "name": "DeepSeek",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "tool_support": True,
        "embedding_support": False,
        "base_url": "https://api.deepseek.com",
        "anthropic_base_url": "https://api.deepseek.com/anthropic",
    },
    "openai": {
        "name": "OpenAI",
        "models": ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"],
        "tool_support": True,
        "embedding_support": True,
        "base_url": "https://api.openai.com/v1",
    },
    "mimo": {
        "name": "MiMo",
        "models": ["mimo-v2.5-pro"],
        "tool_support": True,
        "embedding_support": False,
        "base_url": "https://api.xiaomimimo.com/anthropic",
    },
}


def list_providers() -> dict:
    """List all supported providers and their metadata."""
    return PROVIDER_INFO
