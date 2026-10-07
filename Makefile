.PHONY: install ui chat test lint redflags eval eval-quick improve improve-auto

install:
	uv sync

ui:              ## web UI at http://127.0.0.1:8000 (chat + behind-the-scenes panel)
	DEMO_MODE=1 uv run python -m yourhealth.server

chat:            ## talk to the agent (tool calls shown)
	uv run python -m yourhealth.chat --debug

test:            ## deterministic tests, no API key needed
	uv run pytest -q

lint:            ## ruff lint + format check (what CI runs)
	uv run ruff check . && uv run ruff format --check .

eval:            ## full suite: all scenarios, 3 trials, with judge
	uv run python -m evals --label full

redflags:        ## emergency gate recall / over-escalation on a labelled set (no LLM)
	uv run python -m evals.redflags

eval-quick:      ## 1 trial, no judge
	uv run python -m evals --trials 1 --no-judge --label quick

improve:         ## one improvement cycle: propose -> gate -> ask -> apply
	uv run python -m improve

improve-auto:    ## same, apply automatically if the gate passes (for demos)
	uv run python -m improve --yes
