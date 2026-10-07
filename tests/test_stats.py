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
