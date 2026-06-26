from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    registry_api_url: str  # e.g. "http://agentshield-registry-api.agentshield-platform:8000"
    poll_interval_seconds: int = 5
    log_level: str = "INFO"
    # OPA image to inject into every agent pod
    opa_image: str = "openpolicyagent/opa:0.69.0-static"
    # Default namespace prefix for agent workloads
    agent_namespace_prefix: str = "agents"
    # Image used for declarative workflow runners (agent_type="declarative")
    declarative_runner_image: str = "registry.agentshield.internal/declarative-runner:latest"


settings = Settings()
