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

test: test-frontend test-backend

test-frontend:
	@echo "==> Running frontend tests (Vitest)..."
	pnpm --filter @orbit/frontend test

test-backend:
	@echo "==> Running backend tests (Pytest)..."
	cd backend && uv run pytest -v -c pyproject.toml

lint:
	@echo "==> Checking TypeScript and Python linting..."
	pnpm --filter @orbit/frontend lint
	cd backend && uv run ruff check .

clean:
	@echo "==> Cleaning build artifacts and cache..."
	rm -rf apps/desktop/frontend/dist
	rm -rf apps/desktop/src-tauri/target
	rm -rf backend/.pytest_cache
	rm -rf backend/__pycache__
	rm -rf tests/backend/__pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} +
