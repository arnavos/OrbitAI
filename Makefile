.PHONY: setup dev dev-frontend dev-backend test test-frontend test-backend lint clean

SHELL := /bin/bash

setup:
	@echo "==> Setting up Node.js dependencies with pnpm..."
	pnpm install
	@echo "==> Setting up Python virtual environment with uv..."
	cd backend && uv sync --extra dev
	@echo "==> Setup complete."

dev:
	@echo "Starting development environment..."
	pnpm dev

test: test-frontend test-backend test-rust

test-frontend:
	@echo "==> Running frontend tests (Vitest)..."
	pnpm --filter @orbit/frontend test

test-backend:
	@echo "==> Running backend tests (Pytest)..."
	cd backend && uv run pytest -v -c pyproject.toml

test-rust:
	@echo "==> Running Rust desktop tests (Cargo)..."
	cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml

verify:
	@echo "==> Running unified M0 verification runner..."
	bash scripts/verify_m0.sh

benchmark:
	@echo "==> Running idle memory benchmark..."
	backend/.venv/bin/python3 scripts/benchmark_memory.py

lint:
	@echo "==> Checking TypeScript, Python, and Rust linting..."
	pnpm --filter @orbit/frontend lint
	cd backend && uv run ruff check .
	cargo clippy --manifest-path apps/desktop/src-tauri/Cargo.toml -- -D warnings

clean:
	@echo "==> Cleaning build artifacts and cache..."
	rm -rf apps/desktop/frontend/dist
	rm -rf apps/desktop/src-tauri/target
	rm -rf backend/.pytest_cache
	rm -rf backend/__pycache__
	rm -rf tests/backend/__pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} +
