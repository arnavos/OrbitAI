#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "==> Exporting OpenAPI schema from FastAPI backend..."
"${ROOT_DIR}/backend/.venv/bin/python3" -c "
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path('${ROOT_DIR}') / 'backend'))
from app.main import app

openapi_schema = app.openapi()
out_path = Path('${SCRIPT_DIR}') / 'openapi.json'
with open(out_path, 'w') as f:
    json.dump(openapi_schema, f, indent=2)
print(f'OpenAPI schema exported to {out_path}')
"

echo "==> Generating TypeScript contracts..."
pnpm dlx openapi-typescript "${SCRIPT_DIR}/openapi.json" -o "${ROOT_DIR}/apps/desktop/frontend/src/types/api.ts"
echo "==> TypeScript contracts successfully generated at apps/desktop/frontend/src/types/api.ts"
