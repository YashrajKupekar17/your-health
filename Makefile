.PHONY: install chat test

install:
	uv sync

chat:
	uv run python -m yourhealth.chat --debug

test:
	uv run pytest -q
