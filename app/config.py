from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://eve:eve@localhost:5432/eve"
    redis_url: str | None = None  # empty/None -> caching disabled, in-memory rate limiting

    jwt_secret: str = "dev-only-change-me-please-use-32-bytes-min"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    bcrypt_rounds: int = 12
    webhook_secret: str = "whsec_dev"

    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 120
    auth_rate_limit_per_minute: int = 10
    payment_rate_limit_per_minute: int = 30

    cache_ttl_seconds: int = 60
    payment_success_rate: float = 0.8  # used when the client doesn't force an outcome
    log_level: str = "INFO"
    auto_create_tables: bool = False  # schema is managed by Alembic; True is a dev/test convenience

    # --- background jobs (Celery) ---
    celery_broker_url: str | None = None  # defaults to redis_url, then in-memory
    celery_eager: bool = False  # run tasks inline (tests)
    pending_payment_timeout_minutes: int = 15
    simulate_provider_callback: bool = False  # mock provider POSTs the webhook itself for PENDING payments
    provider_callback_delay_seconds: int = 3
    provider_callback_success_rate: float = 0.8
    webhook_target_url: str = "http://localhost:8000/payments/webhook/"

    admin_email: str = "admin@example.com"
    admin_password: str = "Admin12345"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
