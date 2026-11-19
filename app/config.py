import os
from datetime import timedelta


class BaseConfig:
    DEBUG = False
    TESTING = False
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=15)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=14)
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "local")
    HF_MODEL_ID = os.getenv("HF_MODEL_ID", "Qwen/Qwen2.5-1.5B-Instruct")
    TEAMS_CLIENT_ID = os.getenv("TEAMS_CLIENT_ID", "")
    TEAMS_CLIENT_SECRET = os.getenv("TEAMS_CLIENT_SECRET", "")
    TEAMS_WEBHOOK_CLIENT_STATE = os.getenv("TEAMS_WEBHOOK_CLIENT_STATE", "")
    ZOOM_WEBHOOK_SECRET_TOKEN = os.getenv("ZOOM_WEBHOOK_SECRET_TOKEN", "")
    INTEGRATION_CREDENTIALS_KEY = os.getenv("INTEGRATION_CREDENTIALS_KEY", "")
    INTEGRATION_CREDENTIALS_FALLBACK_KEYS = os.getenv("INTEGRATION_CREDENTIALS_FALLBACK_KEYS", "")
    AGENTIC_PROPOSAL_ENCRYPTION_KEY_ID = os.getenv("AGENTIC_PROPOSAL_ENCRYPTION_KEY_ID", "")
    AGENTIC_PROPOSAL_ENCRYPTION_KEY = os.getenv("AGENTIC_PROPOSAL_ENCRYPTION_KEY", "")
    AGENTIC_PROPOSAL_ENCRYPTION_FALLBACK_KEYS = os.getenv("AGENTIC_PROPOSAL_ENCRYPTION_FALLBACK_KEYS", "")
    AGENTIC_PROPOSAL_HMAC_KEY_ID = os.getenv("AGENTIC_PROPOSAL_HMAC_KEY_ID", "")
    AGENTIC_PROPOSAL_HMAC_KEY = os.getenv("AGENTIC_PROPOSAL_HMAC_KEY", "")
    AGENTIC_PROPOSAL_HMAC_FALLBACK_KEYS = os.getenv("AGENTIC_PROPOSAL_HMAC_FALLBACK_KEYS", "")
    AGENTIC_PROPOSAL_RECOVERY_KEY_ID = os.getenv("AGENTIC_PROPOSAL_RECOVERY_KEY_ID", "")
    AGENTIC_PROPOSAL_RECOVERY_KEY = os.getenv("AGENTIC_PROPOSAL_RECOVERY_KEY", "")
    AGENTIC_PROPOSAL_RECOVERY_FALLBACK_KEYS = os.getenv("AGENTIC_PROPOSAL_RECOVERY_FALLBACK_KEYS", "")
    AGENTIC_PROPOSAL_TTL_SECONDS = os.getenv("AGENTIC_PROPOSAL_TTL_SECONDS", "900")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
    CORS_ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",") if origin.strip()]
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT", "1"))


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class ProductionConfig(BaseConfig):
    DEBUG = False
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")


class TestingConfig(BaseConfig):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")
    JWT_SECRET_KEY = "test-only-secret-that-must-never-be-used-outside-tests"
    TEAMS_WEBHOOK_CLIENT_STATE = "test-teams-client-state-32-chars-long"
    ZOOM_WEBHOOK_SECRET_TOKEN = "test-zoom-webhook-secret-token-32-chars"
    CELERY_TASK_ALWAYS_EAGER = True


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
