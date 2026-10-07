import pytest

from evals.stats import sign_test, wilson


def test_wilson_known_values():
    assert wilson(3, 3) == pytest.approx((0.438, 1.0), abs=1e-3)
    assert wilson(2, 3) == pytest.approx((0.208, 0.939), abs=1e-3)
    assert wilson(0, 0) == (0.0, 1.0)


def test_sign_test_known_values():
    assert sign_test(6, 0) == pytest.approx(0.03125)  # 6 scenarios improve, none worsen: p < 0.05
    assert sign_test(5, 0) == pytest.approx(0.0625)  # 5 is not enough
    assert sign_test(2, 0) == 0.5
    assert sign_test(0, 0) == 1.0
    assert sign_test(3, 3) == 1.0


def test_cost_summary_keeps_agent_separate_from_eval_overhead():
    from evals.cost import summarize

    usages = [
        {"agent": [1000, 100], "sim": [500, 50], "judge": [2000, 200], "turn_ms": [800, 1200], "llm_calls": 3},
        {"agent": [3000, 300], "sim": [500, 50], "judge": [2000, 200], "turn_ms": [1000], "llm_calls": 2},
    ]
    c = summarize(usages, {"agent": "gpt-4.1-mini", "sim": "gpt-4.1-mini", "judge": "gpt-4.1"})
    assert c["agent"]["tokens_in"] == 4000 and c["agent"]["usd"] == pytest.approx(0.00224)
    assert c["agent"]["usd_per_conversation"] == pytest.approx(0.0011, abs=1e-4)
    assert c["agent"]["turn_ms_p50"] == 1000 and c["agent"]["llm_calls_per_turn"] == pytest.approx(5 / 3, abs=0.01)
    assert c["judge"]["usd"] == pytest.approx(0.0112)  # 4000 in x $2/M + 400 out x $8/M
    assert summarize(usages, {"agent": "unknown-model", "sim": "x", "judge": "y"})["agent"]["usd"] is None
