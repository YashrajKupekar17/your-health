"""Gate and lint are pure logic: test them without an LLM."""

from improve.gate import evaluate
from improve.propose import MAX_RULES, lint


def results(**passes):
    """results(S01=(2, False), ...) -> minimal results dict; tuple = (passes, critical)."""
    return {
        "scenarios": {
            sid: {"passes": p, "valid": 3, "all_pass": p == 3, "critical": c, "title": sid, "split": "train"}
            for sid, (p, c) in passes.items()
        }
    }


def test_gate_accepts_improvement_without_regression():
    before = results(S01=(1, False), S02=(3, False), S05=(3, True))
    after = results(S01=(3, False), S02=(3, False), S05=(3, True))
    assert evaluate(before, after, ["S01"]).accepted


def test_gate_rejects_no_improvement():
    before = results(S01=(1, False))
    assert not evaluate(before, results(S01=(1, False)), ["S01"]).accepted


def test_gate_rejects_regression_elsewhere():
    before = results(S01=(1, False), S02=(3, False))
    g = evaluate(before, results(S01=(3, False), S02=(2, False)), ["S01"])
    assert not g.accepted and g.drops == ["S02"]


def test_gate_rejects_critical_failure_even_if_it_was_already_failing():
    before = results(S01=(1, False), S05=(2, True))
    assert not evaluate(before, results(S01=(3, False), S05=(2, True)), ["S01"]).accepted


def test_gate_rejects_one_target_getting_worse():
    before = results(S01=(1, False), S02=(2, False))
    assert not evaluate(before, results(S01=(3, False), S02=(1, False)), ["S01", "S02"]).accepted


def test_lint_blocks_overfitting_and_bloat():
    assert lint("Offer at most three times per message.", []) == []
    assert lint("For S13 always ask which Wednesday.", [])
    assert lint("If Tom asks, book Dr Chen.", [])
    assert lint("Never book on 2026-10-14.", [])
    assert lint("word " * 41, [])
    assert lint("Offer at most three times.", [{"rule": "offer at most three times"}])
    assert lint("Be brief.", [{"rule": f"r{i}"} for i in range(MAX_RULES)])
