"""Application configuration management using Pydantic Settings."""

import errno
import os
import stat
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
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
    session_token_file: str | None = Field(
        default=None,
        validation_alias="ORBIT_SESSION_TOKEN_FILE",
        description="Path to secure 0600 token file written by Tauri host",
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

    @model_validator(mode="after")
    def consume_token_file(self) -> Self:
        """Read session token from secure 0600 file using file-descriptor validation, and unlink."""
        if not self.session_token_file:
            return self

        import contextlib

        token_file = self.session_token_file
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        fd: int | None = None
        try:
            try:
                fd = os.open(token_file, flags)
            except OSError as err:
                if err.errno == errno.ELOOP or (
                    hasattr(errno, "EMLINK") and err.errno == errno.EMLINK
                ):
                    msg = (
                        "Security violation: Session token file cannot be a symbolic link: "
                        f"'{token_file}'"
                    )
                    raise ValueError(msg) from err
                if err.errno == errno.ENOENT:
                    raise ValueError(
                        f"Security violation: Token file '{token_file}' not found."
                    ) from err
                raise ValueError(
                    f"Security violation: Failed to open token file '{token_file}': {err}"
                ) from err

            file_stat = os.fstat(fd)

            # 1. Verify regular file type (reject directories, FIFOs, sockets, device nodes)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError(
                    f"Security violation: Token file '{token_file}' is not a regular file."
                )

            # 2. POSIX ownership validation
            if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
                raise ValueError(
                    f"Security violation: Token file owner ({file_stat.st_uid}) "
                    f"does not match current UID ({os.getuid()})."
                )

            # 3. POSIX permission validation (strictly 0600: read/write by owner only)
            file_mode = stat.S_IMODE(file_stat.st_mode)
            if file_mode != 0o600:
                msg = (
                    f"Security violation: Token permissions ({oct(file_mode)}) "
                    "must be strictly 0600."
                )
                raise ValueError(msg)

            # 4. Read token value securely through validated descriptor
            with os.fdopen(fd, "r", encoding="utf-8") as f:
                fd = None  # os.fdopen takes ownership of closing descriptor
                token_value = f.read().strip()

            if not token_value:
                raise ValueError("Security violation: Session token file is empty.")

            self.session_token = token_value

            # 5. Immediately unlink file from disk to eliminate persisted credentials
            with contextlib.suppress(OSError):
                os.unlink(token_file)

        finally:
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)

        return self

    def setup_directories(self) -> None:
        """Ensure necessary storage and log directories exist."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.setup_directories()
