import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_expiration_minutes: int = int(os.getenv("JWT_EXPIRATION_MINUTES", "60"))
    jwt_issuer: str = os.getenv("JWT_ISSUER", "quant-auth-service")
    postgres_url: str = os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform")
    bootstrap_admin_email: str = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "admin@quant.local")
    bootstrap_admin_password: str = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "ChangeMe123!")
    bootstrap_admin_display_name: str = os.getenv("BOOTSTRAP_ADMIN_DISPLAY_NAME", "Quant Admin")
    bootstrap_admin_token: str = os.getenv("BOOTSTRAP_ADMIN_TOKEN", "dev-bootstrap-token")
    internal_admin_secret: str = os.getenv("INTERNAL_ADMIN_SECRET", "dev-internal-admin-secret")
    admin_header_ttl_seconds: int = int(os.getenv("INTERNAL_ADMIN_HEADER_TTL_SECONDS", "300"))
    order_service_base_url: str = os.getenv("ORDER_SERVICE_BASE_URL", "http://order-service:8000")
    logout_cancel_timeout_seconds: float = float(os.getenv("LOGOUT_CANCEL_TIMEOUT_SECONDS", "5"))


settings = Settings()
