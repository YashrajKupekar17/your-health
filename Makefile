.PHONY: install chat test eval eval-quick

install:
	uv sync

chat:            ## talk to the agent (tool calls shown)
	uv run python -m yourhealth.chat --debug

test:            ## deterministic tests, no API key needed
	uv run pytest -q

eval:            ## full suite: all scenarios, 3 trials, with judge
	uv run python -m evals --label full

eval-quick:      ## 1 trial, no judge
	uv run python -m evals --trials 1 --no-judge --label quick
