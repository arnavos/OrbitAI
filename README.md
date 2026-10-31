# ORBIT — AI Native Desktop Platform

ORBIT is a high-performance desktop platform pairing a native Tauri 2 desktop shell with an authenticated local Python backend daemon.

## Architecture Highlights

- **Host Process:** Tauri 2 (Rust native shell targeting Apple Silicon `aarch64-apple-darwin`)
- **Frontend:** React 18, TypeScript, Vite, custom design system tokens
- **Backend:** FastAPI, Python 3.13, managed with `uv`
- **Persistence:** SQLite 3.51 in WAL (Write-Ahead Logging) mode, Alembic migrations
- **IPC & Security:** Dynamic loopback (`127.0.0.1`), ephemeral cryptographic session tokens (`X-Orbit-Session-Token`), strict CSP
- **Resource Constraints:** Designed for 16 GB Unified Memory constraint and ₹0/month operating budget

---

## Directory Layout

```text
OrbitAI/
├── apps/
│   └── desktop/               # Tauri 2 native desktop application
│       ├── src-tauri/         # Rust native shell & sidecar manager
│       └── frontend/          # React + TypeScript + Vite UI
├── backend/                   # Python 3.13 + FastAPI application service
│   ├── app/                   # Application packages (core, db, models, api, services)
│   ├── migrations/            # Alembic schema migrations
│   └── pyproject.toml         # Python dependencies managed via uv
├── contracts/                 # Cross-tier OpenAPI and TypeScript contract generators
├── tests/                     # Automated test suites
│   ├── backend/               # Pytest async test suite
│   └── frontend/              # Vitest component test suite
├── Makefile                   # Unified workflow commands
└── package.json               # Monorepo pnpm configuration
```

---

## Prerequisites

- **Node.js:** `>= 22.0.0`
- **pnpm:** `10.17.1`
- **Rust & Cargo:** `>= 1.80` (stable `aarch64-apple-darwin`)
- **Python:** `3.13.x`
- **uv:** `>= 0.4.0`

---

## Getting Started

### 1. Setup Dependencies

```bash
make setup
```

This installs frontend packages via `pnpm` and provisions a local `.venv` pinned to Python 3.13 with `uv`.

### 2. Run Tests

```bash
make test
```

Executes both Vitest frontend unit tests and Pytest backend async tests.

### 3. Run Development Servers

```bash
# Frontend dev server (Vite)
pnpm --filter @orbit/frontend dev

# Backend dev server (FastAPI)
cd backend && uv run uvicorn app.main:app --reload --port 18420 --host 127.0.0.1
```
