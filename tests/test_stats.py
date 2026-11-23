"""Intervals for small samples (round 3, DECISIONS.md D72): the Wilson score interval."""
import pytest

from invoice_desk import stats


def test_wilson_matches_hand_computed_values():
    low, high = stats.wilson(0, 49)
    assert low == 0.0 and high == pytest.approx(0.0727, abs=0.0001)
    low, high = stats.wilson(45, 49)
    assert low == pytest.approx(0.808, abs=0.001) and high == pytest.approx(0.968, abs=0.001)
    low, high = stats.wilson(0, 17)
    assert high == pytest.approx(0.184, abs=0.001)


def test_wilson_stays_inside_zero_and_one_and_refuses_an_empty_sample():
    assert stats.wilson(49, 49)[1] == 1.0 and 0 < stats.wilson(49, 49)[0] < 1
    with pytest.raises(ValueError):
        stats.wilson(1, 0)
    with pytest.raises(ValueError):
        stats.wilson(5, 4)


def test_the_sample_needed_for_an_upper_bound_under_one_percent_with_no_misses():
    n = stats.documents_needed(0.01)
    assert stats.wilson(0, n)[1] < 0.01 <= stats.wilson(0, n - 1)[1]
    assert 375 <= n <= 390                                     # about 381, as section 2F of the plan says
