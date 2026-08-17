UV ?= uv
DEVELOPMENT_EXTRAS = --extra dev --extra database --extra workflow --extra storage --extra data
# The full local stack lives in the repository root; backend has no private Compose file.
COMPOSE_FILE ?= ../compose.dev.yaml
COMPOSE ?= docker compose -f $(COMPOSE_FILE)

.PHONY: install format-check lint type test-core test openapi openapi-check check run compose-config build build-api build-worker up down

install:
	$(UV) sync --frozen $(DEVELOPMENT_EXTRAS)

format-check:
	$(UV) run ruff format --check src tests

lint:
	$(UV) run ruff check src tests

type:
	$(UV) run mypy src

test-core:
	$(UV) run pytest tests/core

test:
	$(UV) run pytest

openapi:
	$(UV) run python -m hc_data_platform.core.openapi

openapi-check:
	$(UV) run python -m hc_data_platform.core.openapi --check

check: format-check lint type test openapi-check

run:
	$(UV) run uvicorn hc_data_platform.core.app:create_app --factory --reload

compose-config:
	$(COMPOSE) config --quiet

build: build-api build-worker

build-api:
	docker build --target api --tag hc-data-platform-backend:local .

build-worker:
	docker build --target worker --tag hc-data-platform-worker:local .

up:
	$(COMPOSE) up --build --detach postgres minio minio-init temporal migration api worker

down:
	$(COMPOSE) down
