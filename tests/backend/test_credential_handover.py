import os
from pathlib import Path

import pytest

from app.core.config import Settings


def test_valid_token_file_read_and_unlinked(tmp_path: Path):
    """Verify that a valid 0600 token file is read into memory and immediately unlinked."""
    token_file = tmp_path / "test_session.token"
    token_file.write_text("secure-ephemeral-token-12345\n", encoding="utf-8")
    os.chmod(token_file, 0o600)

    settings = Settings(ORBIT_SESSION_TOKEN_FILE=str(token_file), app_env="testing")

    assert settings.session_token == "secure-ephemeral-token-12345"
    # Token file must be unlinked immediately
    assert not token_path_exists(token_file)


def test_symlink_token_file_rejected(tmp_path: Path):
    """Verify that symlinks are strictly rejected for credential handover."""
    target_file = tmp_path / "actual_token.txt"
    target_file.write_text("secret", encoding="utf-8")
    os.chmod(target_file, 0o600)

    symlink_file = tmp_path / "symlink_token.token"
    os.symlink(target_file, symlink_file)

    with pytest.raises(ValueError, match="Session token file cannot be a symbolic link"):
        Settings(ORBIT_SESSION_TOKEN_FILE=str(symlink_file), app_env="testing")


def test_permissive_token_file_rejected(tmp_path: Path):
    """Verify that token files with permissions other than 0600 are rejected."""
    token_file = tmp_path / "leaky_session.token"
    token_file.write_text("secret", encoding="utf-8")
    os.chmod(token_file, 0o644)  # World/group readable

    with pytest.raises(ValueError, match="must be strictly 0600"):
        Settings(ORBIT_SESSION_TOKEN_FILE=str(token_file), app_env="testing")


def test_empty_token_file_rejected(tmp_path: Path):
    """Verify that empty token files are rejected."""
    token_file = tmp_path / "empty_session.token"
    token_file.write_text("   \n", encoding="utf-8")
    os.chmod(token_file, 0o600)

    with pytest.raises(ValueError, match="Session token file is empty"):
        Settings(ORBIT_SESSION_TOKEN_FILE=str(token_file), app_env="testing")


def token_path_exists(p: Path) -> bool:
    try:
        p.stat()
        return True
    except FileNotFoundError:
        return False
