"""Calibration turns observed lag into a persist_window: the longest divergence that
self-healed, plus a margin. An unresolved trailing divergence is a possible drain, not
lag, and must not inflate the window.
"""

from kaspa_verifiable_index.calibrate import recommend_persist_window


def test_lag_run_of_three_recommends_four():
    c = recommend_persist_window([False, True, True, True, False, False], margin=1)
    assert c.observed_max_lag == 3
    assert c.recommended_window == 4


def test_takes_the_longest_self_healed_run():
    c = recommend_persist_window([True, False, True, True, False, True, True, True, True, False])
    assert c.self_healed_runs == [1, 2, 4]
    assert c.observed_max_lag == 4
    assert c.recommended_window == 5


def test_short_lag_falls_back_to_floor():
    c = recommend_persist_window([False, True, False, True, False])  # max lag 1
    assert c.observed_max_lag == 1
    assert c.recommended_window == 2  # max(1 + 1, floor 2)


def test_unresolved_tail_is_not_lag():
    c = recommend_persist_window([False, True, True, True])  # never healed
    assert c.observed_max_lag == 0
    assert c.unresolved_tail == 3
    assert c.recommended_window == 2
    assert "drain" in c.note


def test_no_divergence_uses_floor():
    c = recommend_persist_window([False] * 5)
    assert c.recommended_window == 2
    assert "no divergence" in c.note


def test_empty_series_uses_floor():
    c = recommend_persist_window([])
    assert c.samples == 0
    assert c.recommended_window == 2
