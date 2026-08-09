.PHONY: lint type test check check-all demo bench serve build

sync:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

type:
	uv run mypy packages/core/src/datasentry_core src/datasentry

test:
	uv run pytest --cov=datasentry_core --cov-fail-under=85 --cov-report=term

check: lint type test

build:
	uv build
	uv build packages/core

demo:
	uv run python examples/demo/demo.py

bench:
	uv run python benchmarks/bench_scan.py

check-all: check demo bench
