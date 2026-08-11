.PHONY: install lock dev infra-up infra-down migrate test

install:
	uv sync --all-groups --frozen

lock:
	uv lock

dev:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

infra-up:
	docker compose up -d postgres minio minio-init

infra-down:
	docker compose down

migrate:
	uv run alembic upgrade heads

test:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_asyncio.plugin
