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

### 2. Run Unified Verification Suite

```bash
make verify
```

Executes the unified 10-stage M0 verification runner (`scripts/verify_m0.sh`), validating OpenAPI contract drift, Python lint/format, clean-state SQLite migrations, pytest backend suite, frontend type safety, vitest component suite, frontend production build, and Rust clippy/tests.

### 3. Launch Native Desktop Application

```bash
pnpm tauri dev
```

Launches the native macOS desktop shell, automatically spawning and supervising the Python backend sidecar with ephemeral loopback port allocation and secure single-use session credential handover.

### 4. Run Idle Memory Benchmark

```bash
make benchmark
```

Executes `scripts/benchmark_memory.py`, reporting individual RSS and macOS physical footprint across the Rust host, WebKit WebContent, and Python backend.

---

## Milestone Verification & Architecture Documentation

For complete verification artifacts, benchmark analysis, security audits, and lifecycle runbooks, see:
- [`docs/M0_VERIFICATION.md`](docs/M0_VERIFICATION.md)
