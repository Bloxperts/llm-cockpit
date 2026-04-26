"""Settings driven by environment variables (12-factor)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="COCKPIT_", extra="ignore")

    # HTTP
    host: str = "127.0.0.1"
    port: int = 8080

    # Auth
    jwt_secret: str = "CHANGE-ME-PLEASE"
    jwt_alg: str = "HS256"
    jwt_ttl_seconds: int = 7 * 24 * 3600  # 7 days

    # Storage
    db_path: str = "./data/cockpit.db"

    # Upstream services
    scheduler_url: str = "http://127.0.0.1:8001"
    ollama_url: str = "http://127.0.0.1:11434"

    # Telemetry sampling
    gpu_sample_interval_s: int = 5
    scheduler_sample_interval_s: int = 60
    ollama_ps_sample_interval_s: int = 30

    # Rate limit (login)
    login_max_failures: int = 5
    login_lockout_window_s: int = 300


settings = Settings()
