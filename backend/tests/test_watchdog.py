import subprocess
import sys


def test_parent_watchdog_terminates_on_dead_parent():
    """Verify that Python sidecar terminates immediately if its parent PID disappears."""
    script = (
        "import time, sys\n"
        "from app.main import _start_parent_watchdog\n"
        "# Pass non-existent PID\n"
        "_start_parent_watchdog(999999)\n"
        "time.sleep(4.0)\n"
        "sys.exit(1)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=5,
    )
    # The watchdog should trigger os._exit(0) within ~1.0-1.5s
    assert result.returncode == 0
    assert "terminated (ESRCH)" in result.stderr or "terminated (ESRCH)" in result.stdout
