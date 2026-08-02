.PHONY: lint type test check sync

sync:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

type:
	uv run mypy packages/core/src/datasentry_core

test:
	uv run pytest --cov=datasentry_core --cov-fail-under=85 --cov-report=term

check: lint type test
