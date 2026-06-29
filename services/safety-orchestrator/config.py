from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llmguard_url: str = "http://agentshield-llm-guard.agentshield-platform:8000"
    presidio_analyzer_url: str = "http://agentshield-presidio-analyzer.agentshield-platform:3000"
    presidio_anonymizer_url: str = "http://agentshield-presidio-anonymizer.agentshield-platform:3001"
    nemo_url: str = "http://agentshield-nemo.agentshield-platform:8080"
    database_url: str = "postgresql+asyncpg://postgres:DevPass2024@agentshield-postgresql:5432/agentshield"
    pii_ttl_hours: int = 24

    # Scanner enable flags — set to false to skip a scanner entirely (pass-through, no fail-close).
    # When all three are false the orchestrator is a pure pass-through.
    llmguard_enabled: bool = True
    presidio_enabled: bool = True
    nemo_enabled: bool = True

    # Langfuse observability — leave empty to disable tracing.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
