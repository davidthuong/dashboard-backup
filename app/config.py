from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Backup Job Dashboard"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    timezone: str = "Asia/Bangkok"

    database_url: str = "sqlite:///./backup_dashboard.db"

    admin_username: str = "admin"
    admin_password: str = "admin123"
    session_secret: str = "change_me_please"
    session_cookie_name: str = "backup_dash_session"
    session_max_age_seconds: int = 60 * 60 * 12
    session_cookie_secure: bool = False
    allow_insecure_defaults: bool = False

    polling_interval_seconds: int = Field(default=60, ge=15, le=3600)
    active_server_window_minutes: int = Field(default=1440, ge=5, le=10080)

    veeam_enabled: bool = True
    veeam_base_url: str = ""
    veeam_username: str = ""
    veeam_password: str = ""
    veeam_verify_ssl: bool = False
    veeam_token_url: str = ""
    veeam_sessions_url: str = ""
    veeam_timeout_seconds: int = 20
    veeam_api_version: str = "1.1-rev0"
    veeam_targets_json: str = ""

    rclone_enabled: bool = True
    rclone_log_paths: str = ""
    rclone_max_lines_per_file: int = 5000

    directadmin_enabled: bool = False
    directadmin_log_paths: str = ""
    directadmin_max_lines_per_file: int = 5000

    agent_trigger_enabled: bool = False
    agent_nodes_json: str = ""
    agent_request_ttl_seconds: int = Field(default=120, ge=30, le=900)

    ingest_enabled: bool = True
    ingest_api_token: str = ""

    alert_enabled: bool = False
    alert_mode: Literal["telegram", "email"] = "telegram"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_api_base: str = "https://api.telegram.org"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_from_email: str = ""
    alert_email_to: str = ""

    @property
    def rclone_log_path_list(self) -> list[str]:
        return [p.strip() for p in self.rclone_log_paths.split(",") if p.strip()]

    @property
    def directadmin_log_path_list(self) -> list[str]:
        return [p.strip() for p in self.directadmin_log_paths.split(",") if p.strip()]

    @property
    def pull_collection_enabled(self) -> bool:
        return bool(self.veeam_enabled or self.rclone_enabled or self.directadmin_enabled)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


INSECURE_ADMIN_PASSWORDS = {
    "admin",
    "admin123",
    "change_me",
    "change_me_now",
    "password",
    "123456",
}

INSECURE_SESSION_SECRETS = {
    "change_me",
    "change_me_please",
    "replace_with_random_secret",
    "change_with_long_random_secret",
}


def validate_runtime_security(settings: Settings) -> None:
    if settings.allow_insecure_defaults:
        return

    errors: list[str] = []
    admin_user = settings.admin_username.strip()
    admin_pass = settings.admin_password.strip()
    session_secret = settings.session_secret.strip()

    if not admin_user:
        errors.append("ADMIN_USERNAME must not be empty")
    if len(admin_pass) < 12 or admin_pass.lower() in INSECURE_ADMIN_PASSWORDS:
        errors.append("ADMIN_PASSWORD is too weak (must be >= 12 chars and not a default value)")
    if len(session_secret) < 32 or session_secret.lower() in INSECURE_SESSION_SECRETS:
        errors.append("SESSION_SECRET is too weak (must be >= 32 chars and not a default value)")

    if errors:
        hint = "Set ALLOW_INSECURE_DEFAULTS=true only for temporary local debugging."
        raise RuntimeError("Unsafe runtime configuration:\n- " + "\n- ".join(errors) + f"\n{hint}")
