"""Application configuration management using Pydantic Settings."""

import errno
import ipaddress
import os
import stat
import urllib.parse
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.prompts import ORBIT_SYSTEM_PROMPT

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
        default="dev-session-token",
        validation_alias="ORBIT_SESSION_TOKEN",
        description="Ephemeral session token passed by Tauri host or development default",
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

    # AI & Local Inference Providers (Ollama)
    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434",
        validation_alias="ORBIT_OLLAMA_BASE_URL",
        description="Base URL for local Ollama daemon (strictly loopback)",
    )
    ollama_connect_timeout: float = Field(
        default=3.0,
        gt=0.0,
        validation_alias="ORBIT_OLLAMA_CONNECT_TIMEOUT",
        description="TCP socket connection timeout in seconds",
    )
    ollama_read_timeout: float = Field(
        default=60.0,
        gt=0.0,
        validation_alias="ORBIT_OLLAMA_READ_TIMEOUT",
        description="Read timeout for model loading and first token generation in seconds",
    )
    ollama_inter_token_timeout: float = Field(
        default=15.0,
        gt=0.0,
        validation_alias="ORBIT_OLLAMA_INTER_TOKEN_TIMEOUT",
        description="Maximum idle duration between streaming token chunks in seconds",
    )
    ollama_keep_alive: str = Field(
        default="5m",
        validation_alias="ORBIT_OLLAMA_KEEP_ALIVE",
        description="Model memory residency duration in Ollama daemon (e.g. '5m', '0')",
    )

    # AI Persona & System Instructions
    default_system_prompt: str = Field(
        default=ORBIT_SYSTEM_PROMPT,
        validation_alias="ORBIT_DEFAULT_SYSTEM_PROMPT",
        description="Canonical baseline system prompt defining assistant persona and identity",
    )

    # Resource Manager & Memory Safeguards (M1.3)
    resource_os_safety_reserve_bytes: int = Field(
        default=1_073_741_824,  # 1.0 GB
        ge=536_870_912,  # Min 512 MB
        validation_alias="ORBIT_RESOURCE_OS_SAFETY_RESERVE_BYTES",
        description="Headroom floor reserved for macOS kernel and apps",
    )
    resource_inactive_reclaim_factor: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        validation_alias="ORBIT_RESOURCE_INACTIVE_RECLAIM_FACTOR",
        description="Fraction of inactive memory considered reclaimable",
    )
    resource_min_free_memory_for_load_bytes: int = Field(
        default=1_073_741_824,  # 1.0 GB
        ge=0,
        validation_alias="ORBIT_RESOURCE_MIN_FREE_MEMORY_FOR_LOAD_BYTES",
        description="Minimum verified free host memory required to admit a new model load",
    )
    resource_default_context_length: int = Field(
        default=4096,
        gt=0,
        validation_alias="ORBIT_RESOURCE_DEFAULT_CONTEXT_LENGTH",
        description="Default context window length in tokens for model inference",
    )
    resource_reduced_context_length: int = Field(
        default=2048,
        gt=0,
        validation_alias="ORBIT_RESOURCE_REDUCED_CONTEXT_LENGTH",
        description="Clamped context window length under memory constraint",
    )
    resource_min_context_length: int = Field(
        default=1024,
        gt=0,
        validation_alias="ORBIT_RESOURCE_MIN_CONTEXT_LENGTH",
        description="Minimum viable context window length before admission rejection",
    )
    resource_unload_cooldown_seconds: float = Field(
        default=30.0,
        ge=0.0,
        validation_alias="ORBIT_RESOURCE_UNLOAD_COOLDOWN_SECONDS",
        description="Anti-thrashing cooldown duration preventing rapid model evictions",
    )
    resource_reservation_timeout_seconds: float = Field(
        default=60.0,
        gt=0.0,
        validation_alias="ORBIT_RESOURCE_RESERVATION_TIMEOUT_SECONDS",
        description="Max duration a pending model load reservation holds budget",
    )
    resource_pressure_poll_interval_seconds: float = Field(
        default=3.0,
        gt=0.0,
        validation_alias="ORBIT_RESOURCE_PRESSURE_POLL_INTERVAL_SECONDS",
        description="Background interval in seconds for sampling pressure",
    )
    resource_snapshot_freshness_seconds: float = Field(
        default=1.0,
        gt=0.0,
        validation_alias="ORBIT_RESOURCE_SNAPSHOT_FRESHNESS_SECONDS",
        description="Max age in seconds for cached host memory snapshot",
    )

    @field_validator("host")
    @classmethod
    def validate_loopback_only(cls, v: str) -> str:
        """Enforce strict binding to loopback addresses only."""
        if v not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError(
                f"Security violation: Binding to non-loopback host '{v}' is prohibited."
            )
        return v

    @field_validator("ollama_base_url")
    @classmethod
    def validate_ollama_base_url(cls, v: str) -> str:
        """Validate and normalize Ollama base URL strictly to loopback endpoints."""
        raw = v.strip()
        if not raw:
            raise ValueError("Ollama base URL cannot be empty.")

        try:
            parsed = urllib.parse.urlsplit(raw)
        except Exception as err:
            raise ValueError(f"Security violation: Malformed Ollama URL '{raw}': {err}") from err

        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(
                f"Security violation: Invalid scheme '{scheme}'. "
                "Only 'http' and 'https' are permitted."
            )

        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "Security violation: Embedded credentials in Ollama URL are prohibited."
            )

        # Reject custom path (must be empty or "/")
        if parsed.path and parsed.path.rstrip("/") != "":
            raise ValueError(
                f"Security violation: Custom URL path '{parsed.path}' is prohibited; "
                "must point to daemon root."
            )

        if parsed.query:
            raise ValueError("Security violation: Query strings in Ollama URL are prohibited.")

        if parsed.fragment:
            raise ValueError("Security violation: URL fragments in Ollama URL are prohibited.")

        hostname = parsed.hostname
        if not hostname:
            raise ValueError("Security violation: Missing hostname in Ollama URL.")

        try:
            port = parsed.port
        except ValueError as err:
            raise ValueError(f"Security violation: Malformed port in Ollama URL: {err}") from err

        if port is not None and not (1 <= port <= 65535):
            raise ValueError(f"Security violation: Port '{port}' out of valid range (1-65535).")

        normalized_host = hostname.lower()
        is_loopback = False

        if normalized_host == "localhost":
            normalized_host = "127.0.0.1"
            is_loopback = True
        else:
            try:
                ip = ipaddress.ip_address(normalized_host)
                if ip.is_loopback:
                    is_loopback = True
            except ValueError:
                is_loopback = False

        if not is_loopback:
            raise ValueError(
                f"Security violation: Host '{hostname}' is not a valid loopback address. "
                "Only local loopback endpoints (127.0.0.1, [::1], localhost) are permitted."
            )

        port_str = f":{port}" if port is not None else ""
        if ":" in normalized_host and not normalized_host.startswith("["):
            normalized_host = f"[{normalized_host}]"

        return f"{scheme}://{normalized_host}{port_str}"

    @field_validator(
        "ollama_connect_timeout",
        "ollama_read_timeout",
        "ollama_inter_token_timeout",
    )
    @classmethod
    def validate_positive_timeout(cls, v: float) -> float:
        """Validate timeout values are strictly positive numbers."""
        if v <= 0:
            raise ValueError("Timeout must be a strictly positive number.")
        return v

    @field_validator("ollama_keep_alive")
    @classmethod
    def validate_ollama_keep_alive(cls, v: str) -> str:
        """Validate keep_alive duration string is non-empty."""
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Ollama keep_alive setting cannot be empty.")
        return cleaned

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

    @model_validator(mode="after")
    def validate_production_token(self) -> Self:
        """Reject default development token when running in production."""
        if self.app_env == "production" and self.session_token == "dev-session-token":
            msg = (
                "Security violation: Default development session token is "
                "strictly prohibited in production."
            )
            raise ValueError(msg)
        return self

    def setup_directories(self) -> None:
        """Ensure necessary storage and log directories exist."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.setup_directories()
