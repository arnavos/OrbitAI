#!/usr/bin/env bash
# ==============================================================================
# ORBIT M0 Unified Verification Runner
#
# Validates complete architectural integrity across all platform components:
# 1. OpenAPI TypeScript contract synchronization (without modifying tracked files)
# 2. Python code quality (ruff check & ruff format --check)
# 3. Clean-state SQLite database migrations (upgrade -> downgrade -> upgrade)
# 4. Backend unit and integration test suite (pytest)
# 5. Frontend TypeScript type safety (tsc --noEmit)
# 6. Frontend component and unit test suite (vitest)
# 7. Frontend production bundle build (vite build)
# 8. Rust code formatting (cargo fmt --check)
# 9. Rust static analysis and linting (cargo clippy -- -D warnings)
# 10. Rust desktop shell and supervisor tests (cargo test)
#
# Returns 0 on complete pass, 1 on any failure.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

TOTAL_START=$(date +%s)
FAILED_STEPS=()
PASSED_STEPS=()

step_header() {
  local num="$1"
  local title="$2"
  echo -e "\n${BLUE}======================================================================${NC}"
  echo -e "${BLUE}[STEP ${num}/10] ${title}${NC}"
  echo -e "${BLUE}======================================================================${NC}"
}

run_step() {
  local num="$1"
  local name="$2"
  shift 2
  local cmd=("$@")

  step_header "${num}" "${name}"
  local start_time=$(date +%s)

  if "${cmd[@]}"; then
    local end_time=$(date +%s)
    local elapsed=$((end_time - start_time))
    echo -e "${GREEN}✓ ${name} PASSED (${elapsed}s)${NC}"
    PASSED_STEPS+=("${name}|${elapsed}s|PASSED")
  else
    local end_time=$(date +%s)
    local elapsed=$((end_time - start_time))
    echo -e "${RED}✗ ${name} FAILED (${elapsed}s)${NC}"
    FAILED_STEPS+=("${name}|${elapsed}s|FAILED")
    return 1
  fi
}

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║                  ORBIT M0 — UNIFIED VERIFICATION RUNNER              ║"
echo "║       Apple Silicon macOS Local-First Engineering Foundation         ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# 1. Verify OpenAPI TypeScript Contracts without modifying tracked files
verify_contract_drift() {
  local tmp_dir
  tmp_dir=$(mktemp -d /tmp/orbit_contracts_verify_XXXXXX)

  "${ROOT_DIR}/backend/.venv/bin/python3" -c "
import json, sys
from pathlib import Path
sys.path.insert(0, '${ROOT_DIR}/backend')
from app.main import app
with open('${tmp_dir}/openapi.json', 'w') as f:
    json.dump(app.openapi(), f, indent=2)
"

  pnpm dlx openapi-typescript "${tmp_dir}/openapi.json" -o "${tmp_dir}/api.ts" > /dev/null 2>&1

  if ! cmp -s "${tmp_dir}/api.ts" "${ROOT_DIR}/apps/desktop/frontend/src/types/api.ts"; then
    echo -e "${RED}Contract drift detected! Generated types do not match apps/desktop/frontend/src/types/api.ts.${NC}"
    echo "Run 'bash contracts/generate_types.sh' to synchronize."
    diff -u "${ROOT_DIR}/apps/desktop/frontend/src/types/api.ts" "${tmp_dir}/api.ts" || true
    rm -rf "${tmp_dir}"
    return 1
  fi
  rm -rf "${tmp_dir}"
  return 0
}

# 2. Python Linting
python_lint() {
  cd "${ROOT_DIR}/backend"
  uv run ruff check . ../tests/backend
}

# 3. Python Formatting Check
python_fmt_check() {
  cd "${ROOT_DIR}/backend"
  uv run ruff format --check . ../tests/backend
}

# 4. Clean-State SQLite Database Migrations
verify_clean_database_migrations() {
  local mig_db="/tmp/orbit_verify_migration_$$.db"
  rm -f "${mig_db}"

  cd "${ROOT_DIR}/backend"
  DATABASE_URL="sqlite+aiosqlite:///${mig_db}" uv run alembic upgrade head
  DATABASE_URL="sqlite+aiosqlite:///${mig_db}" uv run alembic downgrade base
  DATABASE_URL="sqlite+aiosqlite:///${mig_db}" uv run alembic upgrade head
  rm -f "${mig_db}"
  return 0
}

# 5. Backend Tests (Pytest)
python_tests() {
  cd "${ROOT_DIR}/backend"
  uv run pytest -v -c pyproject.toml ../tests/backend
}

# 6. Frontend TypeScript Checks
frontend_typecheck() {
  cd "${ROOT_DIR}"
  pnpm --filter @orbit/frontend lint
}

# 7. Frontend Vitest Suite
frontend_tests() {
  cd "${ROOT_DIR}"
  pnpm --filter @orbit/frontend test
}

# 8. Frontend Production Build
frontend_build() {
  cd "${ROOT_DIR}"
  pnpm --filter @orbit/frontend build
}

# 9. Rust Formatting & Clippy
rust_fmt_and_clippy() {
  cd "${ROOT_DIR}"
  cargo fmt --check --manifest-path apps/desktop/src-tauri/Cargo.toml
  cargo clippy --manifest-path apps/desktop/src-tauri/Cargo.toml -- -D warnings
}

# 10. Rust Tests (Tauri & Sidecar Supervisor)
rust_tests() {
  cd "${ROOT_DIR}"
  cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
}

# Execute sequence
run_step "1" "OpenAPI TypeScript Contract Drift" verify_contract_drift || true
run_step "2" "Python Linting (Ruff Check)" python_lint || true
run_step "3" "Python Formatting (Ruff Format)" python_fmt_check || true
run_step "4" "Clean-State Database Migration (Alembic)" verify_clean_database_migrations || true
run_step "5" "Backend Pytest Suite (FastAPI & SQLite)" python_tests || true
run_step "6" "Frontend TypeScript Checks (tsc --noEmit)" frontend_typecheck || true
run_step "7" "Frontend Vitest Suite (React Components)" frontend_tests || true
run_step "8" "Frontend Production Build (Vite Build)" frontend_build || true
run_step "9" "Rust Formatting & Clippy (Tauri Desktop)" rust_fmt_and_clippy || true
run_step "10" "Rust Unit & Supervisor Tests (Cargo Test)" rust_tests || true

# Summary
TOTAL_END=$(date +%s)
TOTAL_TIME=$((TOTAL_END - TOTAL_START))

echo -e "\n${BLUE}======================================================================${NC}"
echo -e "${BLUE}                   M0 VERIFICATION SUMMARY REPORT                     ${NC}"
echo -e "${BLUE}======================================================================${NC}"
printf "%-48s | %-10s | %-10s\n" "Verification Stage" "Duration" "Result"
echo "----------------------------------------------------------------------"

if [ "${#PASSED_STEPS[@]}" -gt 0 ]; then
  for entry in "${PASSED_STEPS[@]}"; do
    IFS="|" read -r name duration status <<< "${entry}"
    printf "%-48s | %-10s | ${GREEN}%-10s${NC}\n" "${name}" "${duration}" "${status}"
  done
fi

if [ "${#FAILED_STEPS[@]}" -gt 0 ]; then
  for entry in "${FAILED_STEPS[@]}"; do
    IFS="|" read -r name duration status <<< "${entry}"
    printf "%-48s | %-10s | ${RED}%-10s${NC}\n" "${name}" "${duration}" "${status}"
  done
fi

echo "----------------------------------------------------------------------"
echo -e "Total Execution Time: ${TOTAL_TIME} seconds"

if [ "${#FAILED_STEPS[@]}" -eq 0 ]; then
  echo -e "\n${GREEN}✓ ALL M0 VERIFICATION STAGES PASSED! Milestone M0 criteria satisfied.${NC}\n"
  exit 0
else
  echo -e "\n${RED}✗ VERIFICATION FAILED: ${#FAILED_STEPS[@]} stage(s) reported errors.${NC}\n"
  exit 1
fi
