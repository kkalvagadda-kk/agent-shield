"""LLM provider registry — one place that declares which providers exist and
what credential fields each needs.

Adding a provider is a single dict entry here (plus the matching factory branch
in ``sdk/agentshield_sdk/llm.py`` and env-map entry in
``deploy-controller/manifest_builder.py``) — NOT a new ``if/elif`` scattered
across schemas, a DB CHECK constraint, and the router. This is the enforcement
point that replaces the dropped ``ck_llm_providers_provider`` CHECK constraint
(migration 0066).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMProviderSpec:
    key: str
    label: str
    required_credential_fields: tuple[str, ...]
    optional_credential_fields: tuple[str, ...] = ()


LLM_PROVIDER_SPECS: dict[str, LLMProviderSpec] = {
    "anthropic": LLMProviderSpec("anthropic", "Anthropic", ("api_key",)),
    "bedrock": LLMProviderSpec(
        "bedrock",
        "Amazon Bedrock",
        ("aws_access_key_id", "aws_secret_access_key", "aws_region"),
    ),
    "ollama": LLMProviderSpec("ollama", "Ollama", ("base_url",)),
}


def validate_provider_credentials(provider: str, credentials: dict) -> None:
    """Raise ``ValueError`` if the provider is unknown or required creds are missing.

    ``credentials`` should be the non-null credential dict (``exclude_none``).
    """
    spec = LLM_PROVIDER_SPECS.get(provider)
    if spec is None:
        raise ValueError(
            f"unsupported provider {provider!r}; must be one of "
            f"{sorted(LLM_PROVIDER_SPECS)}"
        )
    creds = credentials or {}
    missing = [f for f in spec.required_credential_fields if not creds.get(f)]
    if missing:
        raise ValueError(
            f"provider {provider!r} requires credential field(s): {missing}"
        )
    if provider == "ollama":
        base_url = creds.get("base_url", "")
        if not (base_url.startswith("http://") or base_url.startswith("https://")):
            raise ValueError("ollama base_url must start with http:// or https://")
