"""
LLM factory — returns a LangChain chat model based on env vars.

Supported providers (injected by the deploy controller):
    LLM_PROVIDER=anthropic  →  langchain_anthropic.ChatAnthropic
    LLM_PROVIDER=bedrock    →  langchain_aws.ChatBedrockConverse
    LLM_PROVIDER=ollama     →  langchain_ollama.ChatOllama

Credentials are read from env vars (never hardcoded):
    Anthropic: ANTHROPIC_API_KEY
    Bedrock:   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
    Ollama:    OLLAMA_BASE_URL   (no auth — Ollama has no built-in credentials)
"""
from __future__ import annotations

import os
from typing import Any


def get_llm(model_override: str | None = None) -> Any:
    """Return a configured LangChain chat model.

    Args:
        model_override: If provided, overrides the LLM_MODEL env var.
                        Used when Agent.model is set explicitly.

    Returns:
        A LangChain BaseChatModel instance.

    Raises:
        ValueError: If LLM_PROVIDER is not "anthropic", "bedrock", or "ollama".
        ImportError: If the required provider package is not installed.
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic")
    model = model_override or os.getenv("LLM_MODEL", "claude-sonnet-4-6")

    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "langchain-anthropic is required for LLM_PROVIDER=anthropic. "
                "Install it with: pip install langchain-anthropic"
            ) from exc
        return ChatAnthropic(model=model)  # reads ANTHROPIC_API_KEY from env

    if provider == "bedrock":
        try:
            from langchain_aws import ChatBedrockConverse  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "langchain-aws is required for LLM_PROVIDER=bedrock. "
                "Install it with: pip install langchain-aws"
            ) from exc
        from botocore.config import Config as BotoConfig  # type: ignore[import]

        region = os.getenv("AWS_DEFAULT_REGION") or None
        return ChatBedrockConverse(
            model=model,
            region_name=region,
            config=BotoConfig(read_timeout=300, connect_timeout=10, retries={"max_attempts": 2}),
        )

    if provider == "ollama":
        try:
            from langchain_ollama import ChatOllama  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "langchain-ollama is required for LLM_PROVIDER=ollama. "
                "Install it with: pip install langchain-ollama"
            ) from exc
        base_url = os.getenv("OLLAMA_BASE_URL")
        if not base_url:
            raise ValueError(
                "OLLAMA_BASE_URL is required for LLM_PROVIDER=ollama "
                "(e.g. http://host.docker.internal:11434)."
            )
        # Ollama has no auth; base_url is the only credential. num_ctx left at the
        # model default; tool-calling models (e.g. gemma/qwen) bind via .bind_tools.
        return ChatOllama(model=model, base_url=base_url)

    raise ValueError(
        f"Unknown LLM_PROVIDER: {provider!r}. Supported values: 'anthropic', 'bedrock', 'ollama'."
    )
