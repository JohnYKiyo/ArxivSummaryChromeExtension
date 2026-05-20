"""Application configuration module.

Loads environment variables and provides typed configuration
using pydantic-settings for the arXiv Translator service.
Includes settings for LLM, AWS, Cognito, and application behavior.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # LLM
    # Optional: the Chrome extension supplies the key per-request via the
    # ``X-Google-Api-Key`` header (see ``api/routes.create_conversion``),
    # so the server can boot without one. Web UI requests still rely on
    # this env value, so omitting it disables the web flow.
    GOOGLE_API_KEY: str = ""
    LLM_MODEL: str = "gemini-2.5-pro-preview-05-06"

    # AWS / Cognito
    AWS_REGION: str = "ap-northeast-1"
    COGNITO_USER_POOL_ID: str = ""
    COGNITO_APP_CLIENT_ID: str = ""

    # S3
    S3_BUCKET_NAME: str = ""
    S3_PRESIGNED_URL_EXPIRY: int = 3600

    # DynamoDB
    DYNAMODB_TABLE_NAME: str = "arxiv-translator-jobs"
    DYNAMODB_ENDPOINT_URL: str = ""

    # Lambda
    PIPELINE_LAMBDA_NAME: str = ""

    # Application
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "http://localhost:5173"

    # Job processing
    JOB_TTL_SECONDS: int = 3600

    # Template paths
    SUMMARY_TEMPLATE_PATH: str = "docs/summary_template.md"
    SUMMARY_REVIEW_TEMPLATE_PATH: str = "docs/summary_review_template.md"

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse CORS_ORIGINS comma-separated string into a list."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.APP_ENV == "production"


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton of the application settings.

    Uses ``functools.lru_cache`` so the .env file is read only once
    per process lifetime.
    """
    return Settings()  # type: ignore[call-arg]
