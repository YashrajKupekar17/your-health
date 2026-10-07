# YourHealth: a self-improving patient scheduling agent

A chat agent that books, reschedules and cancels clinic appointments, an eval harness that scores it
against designed scenarios (including the hard cases), and an improvement loop that turns failures
into a gated, versioned prompt rule, then re-runs the suite to prove the score moved without regressions.

Design note: [`docs/DESIGN.md`](docs/DESIGN.md) · Architecture diagrams: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · AI usage: [`docs/AI_USAGE.md`](docs/AI_USAGE.md) ·
Before/after evidence: [`docs/evidence/cycle1/`](docs/evidence/cycle1/comparison.md)

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env     # add OPENAI_API_KEY
make install
```

Or with Docker, see below.

## Run the agent

```bash
make ui                  # web UI at http://127.0.0.1:8000: chat (typed or spoken) + live session state + tool calls
make chat                # same agent in the terminal
```

The clinic is simulated and frozen at **Monday 12 October 2026, 09:00**. Demo patients (name, DOB):
Priya Sharma 1990-04-12 · John Miller 1985-07-30 · John Miller 1972-01-05 · Maria Garcia 1958-11-23 ·
Aisha Khan 2015-03-02 · Tom Becker 1979-09-09. The UI has one-click examples (booking, emergency,
prompt injection, a relative fishing for details, ...).

## Run the eval loop

```bash
make improve             # baseline -> propose a rule from failures -> gate -> ask you -> apply as a new version
```

`make improve-auto` applies automatically when the gate passes (for demos).
`uv run python -m improve rollback 1` restores an earlier config; `uv run python -m improve ablate R4`
re-tests whether a learned rule still earns its place and retires it if nothing regresses.
Before/after evidence: `docs/evidence/cycle1/` and `docs/evidence/cycle2/`.

Other commands:

```bash
make eval                # full suite: 15 scenarios x 3 trials, deterministic checks + advisory judge
make eval-quick          # 1 trial, no judge
make redflags            # emergency gate recall / over-escalation on 29 labelled messages (no LLM)
make test                # deterministic tests, no API key needed (LLM calls are scripted)
make lint                # ruff lint + format check (CI runs lint + tests on every push)
uv run python -m evals --scenario S05 S13 --trials 3     # selected scenarios
```

Each eval run writes `runs/eval/<id>/` (config snapshot, `results.json`, `report.md` with 95% intervals
and a cost/latency block, one trace per conversation). Each improvement attempt is appended to `improve/ledger.jsonl`, accepted or not.
Models can be overridden with `AGENT_MODEL`, `SIM_MODEL`, `JUDGE_MODEL`, `PROPOSER_MODEL`.

## Configuration

All runtime settings come from the environment (`src/yourhealth/settings.py`, validated at startup);
`config/agent.yaml` is validated against a schema on every load and before the loop writes it.

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | required | model provider |
| `AGENT_MODEL` | from `agent.yaml` | override the agent model |
| `LLM_TIMEOUT_S` / `LLM_MAX_RETRIES` | 20 / 2 | per-request timeout and retries |
| `TURN_DEADLINE_S` | 60 | max time for one patient turn (all tool steps); then hand off |
| `MAX_TURNS` | 40 | max patient messages per conversation; then hand off |
| `MAX_SESSIONS` / `SESSION_IDLE_TTL_S` | 200 / 1800 | in-memory session store limits |
| `TRANSCRIBE_MODEL` | gpt-4o-mini-transcribe | speech-to-text for the mic button (server-side; audio never stored or logged) |
| `DEMO_MODE` | off (`make ui` sets 1) | expose tool calls, session state and demo patients in the UI |
| `LOG_LEVEL` | INFO (server), WARNING (CLIs) | JSON logs: one event per turn and tool call, no patient text |

## Run in Docker

```bash
docker build -t yourhealth .
docker run -p 8000:8000 --env-file .env yourhealth     # http://127.0.0.1:8000, /healthz for probes
```

## Production notes

What is in place: a runtime output guard (no false "you're booked", no other patient's identifiers),
streaming over SSE (`POST /api/session/{id}/message/stream`: progress while tools run, then the reply
sentence by sentence, each sentence guard-approved before it is sent), per-request timeouts, a per-turn deadline and turn cap that end in a handoff;
one shared schedule with atomic check-and-write (a test proves it double-books without the lock);
structured PHI-free logs with tokens and latency; a session store with idle expiry behind an
interface; internals hidden unless `DEMO_MODE`; config validation; CI; a non-root container with a
health check. What a real deployment adds: Postgres with a unique constraint on (provider, start)
instead of the in-memory clinic, sessions in Redis so several workers can serve one conversation,
async endpoints, idempotency keys on messages, authentication and rate limits, OpenTelemetry traces,
and a durable append-only audit log of every write.

## Layout

```
src/yourhealth/   clinic.py (schedule rules) · tools.py (gated tools + session state)
                  safety.py (pre-LLM emergency gate) · agent.py (tool-calling loop) · chat.py · server.py
                  settings.py (env + config schema) · sessions.py (session store) · logs.py (JSON logs)
config/           agent.yaml (human-owned core prompt + loop-owned learned_rules) · history/ (every version)
data/clinic.json  providers, patients, appointments
evals/            scenarios.yaml · simulator.py · checks.py · judge.py · runner.py
improve/          propose.py (failures -> one linted rule) · gate.py · loop.py · ledger.jsonl
docs/             DESIGN.md · AI_USAGE.md · evidence/
```

## Assumptions

- One clinic in one timezone (US, so 911/988), 4 providers, 30-minute slots, bookings open 21 days ahead.
- Scope is book / reschedule / cancel / browse availability. No medical advice, billing, prescriptions,
  results or new-patient registration: those are handed to staff.
- Identity = full name + date of birth. A caller may act for a family member if they know both.
- Changes within 24 hours of an appointment go to staff (common late-cancel policy).
- Text only. Voice would change the confirmation design (barge-in, misheard names and dates).
