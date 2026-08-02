.PHONY: lint type test check sync

sync:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

type:
	uv run mypy packages/core/src/datasentry_core

test:
	uv run pytest

check: lint type test
