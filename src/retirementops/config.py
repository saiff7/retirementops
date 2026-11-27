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
    AGENT_RECORDER = "svc_agent_recorder"  # M3: writes run traces only (migration 0007)
    # M4 (ADR-010 Amendment 1, point 1a): SELECT-only scoring reader, `retirementops_eval` only.
    # Not a runtime role: never granted CONNECT on other databases, never in the service container.
    EVAL_READER = "svc_eval_reader"


# Roles the running system uses. The owner and the evaluation reader are deliberately excluded:
# `create_database` grants CONNECT only to these, and `make_runtime_engines` builds only these.
NON_RUNTIME_ROLES = frozenset({DbRole.OWNER, DbRole.EVAL_READER})
RUNTIME_ROLES: tuple[DbRole, ...] = tuple(r for r in DbRole if r not in NON_RUNTIME_ROLES)
PRIVILEGED_SECRETS = ("postgres_superuser_password", "ro_pw_ro_owner")


class MissingSecret(RuntimeError):
    """A privileged secret was requested in a process that must not hold it (OPEN-11)."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    retirementops_env: str = "dev"

    ro_db_host: str = "127.0.0.1"
    ro_db_port: int = 55432
    ro_db_name: str = "retirementops"
    postgres_superuser: str = "postgres"
    # Privileged secrets (OPEN-11). Optional so that a process can be built without them
    # (`Settings.unprivileged()`); every use goes through `role_password` / `superuser_conninfo`,
    # which stop with MissingSecret when they are absent.
    postgres_superuser_password: SecretStr | None = None

    ro_pw_ro_owner: SecretStr | None = None
    ro_pw_app_service: SecretStr
    ro_pw_svc_zendesk_reader: SecretStr
    ro_pw_svc_zendesk_writer: SecretStr
    ro_pw_svc_planadmin_reader: SecretStr
    ro_pw_svc_payroll_reader: SecretStr
    ro_pw_svc_payroll_ingest: SecretStr
    ro_pw_svc_servicekb_reader: SecretStr
    ro_pw_svc_agent_recorder: SecretStr
    ro_pw_svc_eval_reader: SecretStr

    ro_jwt_private_key_path: Path = Path("keys/dev_jwt_private.pem")
    ro_jwt_public_key_path: Path = Path("keys/dev_jwt_public.pem")
    ro_jwt_issuer: str = "retirementops-dev"
    ro_jwt_audience: str = "retirementops-api"
    ro_jwt_ttl_seconds: int = 900
    ro_agent_run_ttl_seconds: int = 600

    # Local model (ADR-005). Loopback only; the model is chosen by a small M3 comparison.
    ro_ollama_url: str = "http://127.0.0.1:11434"
    ro_ollama_model: str = "qwen3:8b"
    ro_ollama_timeout_seconds: float = 120.0
    # Kill switch for new agent runs (threat model T-13). True: every new run ends in manual review.
    ro_agent_kill_switch: bool = False

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

    @classmethod
    def unprivileged(cls) -> Settings:
        """Settings WITHOUT the superuser and `ro_owner` secrets, even when `.env` holds them
        (init values take precedence over the environment and `.env`). Used by the M4
        run-and-score process; the API, MCP server and worker are unchanged (OPEN-11)."""
        return cls(**{name: None for name in PRIVILEGED_SECRETS})

    @property
    def is_privileged(self) -> bool:
        return any(getattr(self, name) is not None for name in PRIVILEGED_SECRETS)

    def role_password(self, role: DbRole) -> SecretStr:
        value: SecretStr | None = getattr(self, f"ro_pw_{role.value}")
        if value is None:
            raise MissingSecret(
                f"the {role.value} secret is not loaded in this process; run this step from a "
                "privileged entry point (bootstrap, migrations, seed or eval provisioning)"
            )
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
        if self.postgres_superuser_password is None:
            raise MissingSecret(
                "the Postgres superuser secret is not loaded in this process; run this step from "
                "a privileged entry point (bootstrap or eval provisioning)"
            )
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
