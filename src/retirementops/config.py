"""Runtime settings loaded from the environment / `.env`. Secrets are `SecretStr` and never logged."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class DbRole(StrEnum):
    """Postgres login roles. One per connector identity (ADR-003)."""

    OWNER = "ro_owner"
    APP_SERVICE = "app_service"
    ZENDESK_READER = "svc_zendesk_reader"
    ZENDESK_WRITER = "svc_zendesk_writer"
    PLANADMIN_READER = "svc_planadmin_reader"
    PAYROLL_READER = "svc_payroll_reader"
    PAYROLL_INGEST = "svc_payroll_ingest"
    SERVICEKB_READER = "svc_servicekb_reader"


RUNTIME_ROLES: tuple[DbRole, ...] = tuple(r for r in DbRole if r is not DbRole.OWNER)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    retirementops_env: str = "dev"

    ro_db_host: str = "127.0.0.1"
    ro_db_port: int = 55432
    ro_db_name: str = "retirementops"
    postgres_superuser: str = "postgres"
    postgres_superuser_password: SecretStr

    ro_pw_ro_owner: SecretStr
    ro_pw_app_service: SecretStr
    ro_pw_svc_zendesk_reader: SecretStr
    ro_pw_svc_zendesk_writer: SecretStr
    ro_pw_svc_planadmin_reader: SecretStr
    ro_pw_svc_payroll_reader: SecretStr
    ro_pw_svc_payroll_ingest: SecretStr
    ro_pw_svc_servicekb_reader: SecretStr

    ro_jwt_private_key_path: Path = Path("keys/dev_jwt_private.pem")
    ro_jwt_public_key_path: Path = Path("keys/dev_jwt_public.pem")
    ro_jwt_issuer: str = "retirementops-dev"
    ro_jwt_audience: str = "retirementops-api"
    ro_jwt_ttl_seconds: int = 900

    ro_log_pepper: SecretStr
    ro_audit_pepper: (
        SecretStr  # separate key: audit pseudonyms cannot be joined with log pseudonyms
    )

    # Abuse protection (ADR-012). Per process; see the ADR for multi-worker limits.
    ro_rl_client_per_minute: int = 300
    ro_rl_client_burst: int = 60
    ro_rl_auth_failures_per_minute: int = 10
    ro_rl_principal_per_minute: int = 600
    ro_rl_principal_burst: int = 120
    ro_audit_anon_per_client_per_window: int = 2
    ro_audit_anon_global_per_window: int = 60

    def role_password(self, role: DbRole) -> SecretStr:
        value: SecretStr = getattr(self, f"ro_pw_{role.value}")
        return value

    def dsn(self, role: DbRole, db_name: str | None = None) -> str:
        """psycopg URL for SQLAlchemy. Contains a secret, so never log it."""
        from sqlalchemy.engine import URL

        return URL.create(
            "postgresql+psycopg",
            username=role.value,
            password=self.role_password(role).get_secret_value(),
            host=self.ro_db_host,
            port=self.ro_db_port,
            database=db_name or self.ro_db_name,
        ).render_as_string(hide_password=False)

    def superuser_conninfo(self, db_name: str = "postgres") -> dict[str, object]:
        return {
            "host": self.ro_db_host,
            "port": self.ro_db_port,
            "dbname": db_name,
            "user": self.postgres_superuser,
            "password": self.postgres_superuser_password.get_secret_value(),
        }

    def resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else ROOT / path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # values come from the environment / .env
