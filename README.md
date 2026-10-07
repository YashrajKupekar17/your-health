# YourHealth: a self-improving patient scheduling agent

A chat agent that books, reschedules and cancels clinic appointments, an eval harness that scores it
against designed scenarios (including the hard cases), and an improvement loop that turns failures
into a gated, versioned prompt rule, then re-runs the suite to prove the score moved without regressions.

Design note: [`docs/DESIGN.md`](docs/DESIGN.md) · AI usage: [`docs/AI_USAGE.md`](docs/AI_USAGE.md) ·
Before/after evidence: [`docs/evidence/cycle1/`](docs/evidence/cycle1/comparison.md)

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env     # add OPENAI_API_KEY
make install
```

## Run the agent

```bash
make ui                  # web UI at http://127.0.0.1:8000: chat + live session state + tool calls
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
`uv run python -m improve rollback 1` restores an earlier config.

Other commands:

```bash
make eval                # full suite: 15 scenarios x 3 trials, deterministic checks + advisory judge
make eval-quick          # 1 trial, no judge
make test                # 45 deterministic tests, no API key needed
uv run python -m evals --scenario S05 S13 --trials 3     # selected scenarios
```

Each eval run writes `runs/eval/<id>/` (config snapshot, `results.json`, `report.md`, one trace per
conversation). Each improvement attempt is appended to `improve/ledger.jsonl`, accepted or not.
Models can be overridden with `AGENT_MODEL`, `SIM_MODEL`, `JUDGE_MODEL`, `PROPOSER_MODEL`.

## Layout

```
src/yourhealth/   clinic.py (schedule rules) · tools.py (gated tools + session state)
                  safety.py (pre-LLM emergency gate) · agent.py (tool-calling loop) · chat.py · server.py
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
