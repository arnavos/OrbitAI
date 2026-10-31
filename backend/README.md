# ORBIT Backend Service

FastAPI local daemon managing persistence, telemetry, and background services for the ORBIT platform.

## Development

Prerequisites: Python 3.13 and `uv`.

```bash
# Sync dependencies and create .venv
uv sync --extra dev

# Run development server
uv run uvicorn app.main:app --reload --port 18420 --host 127.0.0.1

# Run tests
uv run pytest
```
