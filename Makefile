SHELL := /bin/sh
export UV_CACHE_DIR := $(CURDIR)/.cache/uv
export UV_PYTHON_DOWNLOADS := never
export PYTHONDONTWRITEBYTECODE := 1
UV := uv run --frozen --offline

.PHONY: setup check lint typecheck test
setup:
	uv sync --python 3.12

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .

typecheck:
	$(UV) mypy src tests scripts

test:
	$(UV) pytest

check: lint typecheck test
