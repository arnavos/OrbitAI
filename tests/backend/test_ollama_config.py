"""Unit tests for centralized Ollama configuration and loopback security validation."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_ollama_config_defaults():
    """Verify default Ollama settings are safe, loopback-bound, and properly initialized."""
    s = Settings(app_env="testing")
    assert s.ollama_base_url == "http://127.0.0.1:11434"
    assert s.ollama_connect_timeout == 3.0
    assert s.ollama_read_timeout == 60.0
    assert s.ollama_inter_token_timeout == 15.0
    assert s.ollama_keep_alive == "5m"


def test_ollama_config_custom_valid_loopback_urls():
    """Verify custom loopback configurations succeed and normalize cleanly."""
    # Custom port on IPv4 loopback
    s1 = Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL="http://127.0.0.1:18888")
    assert s1.ollama_base_url == "http://127.0.0.1:18888"

    # Trailing slash stripped/normalized
    s2 = Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL="http://127.0.0.1:11434/")
    assert s2.ollama_base_url == "http://127.0.0.1:11434"

    # Alternate IPv4 loopback address (127.0.0.2)
    s3 = Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL="http://127.0.0.2:11434")
    assert s3.ollama_base_url == "http://127.0.0.2:11434"

    # HTTPS loopback (certificate verification preserved)
    s4 = Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL="https://127.0.0.1:11434")
    assert s4.ollama_base_url == "https://127.0.0.1:11434"


def test_ollama_config_ipv6_loopback():
    """Verify literal IPv6 loopback addresses are accepted and bracketed."""
    s = Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL="http://[::1]:11434")
    assert s.ollama_base_url == "http://[::1]:11434"


def test_ollama_config_localhost_normalization():
    """Verify localhost is normalized to literal 127.0.0.1 to eliminate DNS rebinding."""
    s = Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL="http://localhost:11434")
    assert s.ollama_base_url == "http://127.0.0.1:11434"


def test_ollama_config_external_hostname_rejection():
    """Verify external domains and hostnames are rejected fail-closed."""
    for host in ("http://evil.com:11434", "http://ollama.ai:11434", "http://internal.lan:11434"):
        with pytest.raises(ValidationError, match="not a valid loopback address"):
            Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL=host)


def test_ollama_config_external_ip_rejection():
    """Verify private and public remote IP addresses are rejected."""
    for ip in (
        "http://192.168.1.1:11434",
        "http://10.0.0.1:11434",
        "http://172.16.0.1:11434",
        "http://8.8.8.8:11434",
        "http://169.254.169.254:11434",
    ):
        with pytest.raises(ValidationError, match="not a valid loopback address"):
            Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL=ip)


def test_ollama_config_credentials_rejection():
    """Verify URLs with embedded user credentials (userinfo) are rejected."""
    with pytest.raises(ValidationError, match="Embedded credentials in Ollama URL are prohibited"):
        Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL="http://admin:secret@127.0.0.1:11434")


def test_ollama_config_invalid_scheme_rejection():
    """Verify non-http/https schemes are rejected."""
    for scheme in ("ftp://127.0.0.1:11434", "ws://127.0.0.1:11434", "file:///127.0.0.1"):
        with pytest.raises(ValidationError, match="Invalid scheme"):
            Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL=scheme)


def test_ollama_config_malformed_port_rejection():
    """Verify out-of-range or malformed ports are rejected."""
    # Port 0
    with pytest.raises(ValidationError, match="out of valid range"):
        Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL="http://127.0.0.1:0")

    # Port > 65535
    with pytest.raises(ValidationError, match="Malformed port"):
        Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL="http://127.0.0.1:99999")

    # Non-numeric port
    with pytest.raises(ValidationError, match="Malformed port"):
        Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL="http://127.0.0.1:port")


def test_ollama_config_unexpected_paths_queries_fragments_rejected():
    """Verify custom paths, queries, and fragments are rejected to enforce daemon root binding."""
    # Custom path
    with pytest.raises(ValidationError, match="Custom URL path"):
        Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL="http://127.0.0.1:11434/api/generate")

    # Query string
    with pytest.raises(ValidationError, match="Query strings in Ollama URL are prohibited"):
        Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL="http://127.0.0.1:11434/?format=json")

    # Fragment
    with pytest.raises(ValidationError, match="URL fragments in Ollama URL are prohibited"):
        Settings(app_env="testing", ORBIT_OLLAMA_BASE_URL="http://127.0.0.1:11434/#tag")


def test_ollama_config_invalid_timeout_values():
    """Verify zero and negative timeouts are rejected."""
    for val in (0.0, -5.0):
        with pytest.raises(ValidationError, match="greater than 0"):
            Settings(app_env="testing", ORBIT_OLLAMA_CONNECT_TIMEOUT=val)

        with pytest.raises(ValidationError, match="greater than 0"):
            Settings(app_env="testing", ORBIT_OLLAMA_READ_TIMEOUT=val)

        with pytest.raises(ValidationError, match="greater than 0"):
            Settings(app_env="testing", ORBIT_OLLAMA_INTER_TOKEN_TIMEOUT=val)


def test_ollama_config_empty_keep_alive_rejected():
    """Verify empty or purely whitespace keep_alive setting is rejected."""
    with pytest.raises(ValidationError, match="keep_alive setting cannot be empty"):
        Settings(app_env="testing", ORBIT_OLLAMA_KEEP_ALIVE="   ")
