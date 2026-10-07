"""Gate and lint are pure logic: test them without an LLM."""

from improve.gate import evaluate, non_regression, pool
from improve.propose import MAX_RULES, lint


def results(**passes):
    """results(S01=(2, False), ...) -> results dict; tuple = (passes, critical[, valid])."""
    scen = {}
    for sid, spec in passes.items():
        p, c, n = (*spec, 3) if len(spec) == 2 else spec
        scen[sid] = {
            "passes": p,
            "valid": n,
            "all_pass": p == n,
            "critical": c,
            "title": sid,
            "split": "train",
            "trials": [],
        }
    return {"scenarios": scen}


def test_gate_accepts_improvement_without_regression():
    before = results(S01=(1, False), S02=(3, False), S05=(3, True))
    after = results(S01=(3, False), S02=(3, False), S05=(3, True))
    assert evaluate(before, after, ["S01"]).accepted


def test_gate_rejects_no_improvement():
    assert not evaluate(results(S01=(1, False)), results(S01=(1, False)), ["S01"]).accepted


def test_gate_flags_drops_anywhere_including_targets_for_confirmation():
    before = results(S01=(1, False), S02=(2, False), S03=(3, False))
    g = evaluate(before, results(S01=(3, False), S02=(1, False), S03=(2, False)), ["S01", "S02"])
    assert not g.accepted and g.drops == ["S02", "S03"]  # a dipping target is re-run, not rejected outright


def test_gate_rejects_critical_failure_even_if_it_was_already_failing():
    before = results(S01=(1, False), S05=(2, True))
    assert not evaluate(before, results(S01=(3, False), S05=(2, True)), ["S01"]).accepted


def test_rates_compare_pooled_and_unpooled_fairly():
    before = results(S01=(4, False, 6))  # pooled baseline: 4/6
    assert not evaluate(before, results(S01=(2, False)), ["S01"]).accepted  # 2/3 is the same rate
    assert evaluate(before, results(S01=(3, False)), ["S01"]).accepted


def test_pool_adds_trials_and_keeps_all_pass_honest():
    p = pool(results(S01=(3, False)), results(S01=(2, False), S02=(3, False)))
    assert p["scenarios"]["S01"]["passes"] == 5 and p["scenarios"]["S01"]["valid"] == 6
    assert p["scenarios"]["S01"]["all_pass"] is False and "S02" in p["scenarios"]


def test_non_regression_needs_no_improvement():
    same = results(S01=(3, False), S02=(2, False))
    assert non_regression(same, same).accepted
    assert not non_regression(same, results(S01=(2, False), S02=(2, False))).accepted


def test_lint_blocks_overfitting_and_bloat():
    assert lint("Offer at most three times per message.", []) == []
    assert lint("For S13 always ask which Wednesday.", [])
    assert lint("If Tom asks, book Dr Chen.", [])
    assert lint("Never book on 2026-10-14.", [])
    assert lint("word " * 41, [])
    assert lint("Offer at most three times.", [{"rule": "offer at most three times"}])
    assert lint("Be brief.", [{"rule": f"r{i}"} for i in range(MAX_RULES)])


def test_rule_ids_are_never_reused_after_retirement():
    from improve.loop import rule_id_for

    assert rule_id_for(4) == "R4" and rule_id_for(4) != rule_id_for(2)
