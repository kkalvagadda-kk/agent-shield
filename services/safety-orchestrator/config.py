from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llmguard_url: str = "http://agentshield-llm-guard.agentshield-platform:8000"
    presidio_analyzer_url: str = "http://agentshield-presidio-analyzer.agentshield-platform:3000"
    presidio_anonymizer_url: str = "http://agentshield-presidio-anonymizer.agentshield-platform:3001"
    nemo_url: str = "http://agentshield-nemo.agentshield-platform:8080"
    database_url: str = "postgresql+asyncpg://postgres:DevPass2024@agentshield-postgresql:5432/agentshield"
    pii_ttl_hours: int = 24

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
