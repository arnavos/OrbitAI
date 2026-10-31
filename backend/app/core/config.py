"""Application configuration management using Pydantic Settings."""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Central configuration for ORBIT Backend."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Environment
    app_env: Literal["development", "production", "testing"] = "development"
    app_name: str = "ORBIT"
    app_version: str = "0.1.0"
    debug: bool = False

    # Server binding (strictly loopback)
    host: str = Field(default="127.0.0.1", description="Must bind strictly to loopback")
    port: int = Field(default=18420, description="Default loopback port")

    # Security
    session_token: str | None = Field(
        default=None,
        validation_alias="ORBIT_SESSION_TOKEN",
        description="Ephemeral session token passed by Tauri host",
    )
    allowed_origins: list[str] = [
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]

    # Database & Storage
    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{BASE_DIR}/storage/orbit.db",
        description="SQLite database path with aiosqlite async driver",
    )
    storage_dir: Path = Field(
        default=BASE_DIR / "storage",
        description="Local storage directory for data and databases",
    )
    log_dir: Path = Field(
        default=BASE_DIR / "storage" / "logs",
        description="Log directory for rotating JSON logs",
    )
    log_level: str = "INFO"

    @field_validator("host")
    @classmethod
    def validate_loopback_only(cls, v: str) -> str:
        """Enforce strict binding to loopback addresses only."""
        if v not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError(
                f"Security violation: Binding to non-loopback host '{v}' is prohibited."
            )
        return v

    def setup_directories(self) -> None:
        """Ensure necessary storage and log directories exist."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.setup_directories()
