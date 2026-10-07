"""The emergency gate against the labelled set in evals/redflags.yaml (no LLM)."""

from evals.redflags import evaluate


def test_gate_catches_every_explicit_red_flag():
    m = evaluate()
    caught, total = m["gate_recall"]
    assert caught == total, f"missed emergencies: {m['missed']}"


def test_over_escalation_stays_within_budget():
    # Soft budget: false alarms cost a transfer, not harm. This only catches a runaway pattern.
    fired, total = evaluate()["over_escalation"]
    assert fired / total <= 0.3
