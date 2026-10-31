from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    # Neon connection string, e.g. postgresql://user:pass@host/db?sslmode=require
    database_url: str

    cognito_user_pool_id: str
    cognito_client_id: str
    cognito_client_secret: str | None = None
    # Optional: derived from the user pool id (e.g. "us-east-1_abc" -> "us-east-1") when blank.
    cognito_region: str | None = None

    cloudinary_cloud_name: str
    cloudinary_api_key: str
    cloudinary_api_secret: str
    cloudinary_folder: str = "roleveya"

    # Comma-separated origins. Leave blank when the frontend proxies /api (same origin).
    cors_origins: str = ""
    cookie_secure: bool = True
    cookie_domain: str | None = None
    refresh_token_days: int = 30

    max_cv_mb: int = 5
    max_poster_mb: int = 5
    max_cvs_per_user: int = 20

    @field_validator("cognito_client_secret", "cognito_region", "cookie_domain", mode="before")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url.strip()
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def resolved_cognito_region(self) -> str:
        return self.cognito_region or self.cognito_user_pool_id.split("_", 1)[0]

    @property
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.resolved_cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
