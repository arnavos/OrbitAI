#!/usr/bin/env bash
set -euo pipefail

echo "==> Exporting OpenAPI schema from FastAPI backend..."
python3 -c "
import json
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
from app.main import app

openapi_schema = app.openapi()
with open(Path(__file__).resolve().parent / 'openapi.json', 'w') as f:
    json.dump(openapi_schema, f, indent=2)
print('OpenAPI schema exported to contracts/openapi.json')
"

echo "==> Generating TypeScript contracts..."
# Once openapi-typescript is invoked:
# pnpm dlx openapi-typescript contracts/openapi.json -o apps/desktop/frontend/src/contracts/api.d.ts
echo "Done."
