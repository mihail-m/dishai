import pytest

from backend.score import clamp, label_for, stagnation_score


def test_clamp():
    assert clamp(-1) == 0 and clamp(2) == 1 and clamp(0.4) == 0.4


def test_worst_case_is_100():
    # shallow layer, dead calm, strong warm lid
    assert stagnation_score(blh=50, wind10m_kmh=0, t2m=-5, t925=5) == pytest.approx(100)


def test_best_case_is_0():
    # deep mixing, windy, cold air aloft
    assert stagnation_score(blh=1500, wind10m_kmh=30, t2m=15, t925=8) == 0


def test_known_mid_value():
    # blh 450 -> 0.5, wind 6 -> 0.5, delta 2 -> (2+2)/8 = 0.5  => 50
    assert stagnation_score(450, 6, 10, 12) == pytest.approx(50)


def test_each_term_is_monotonic():
    base = dict(blh=500, wind10m_kmh=6, t2m=5, t925=6)
    s0 = stagnation_score(**base)
    assert stagnation_score(**{**base, "blh": 300}) > s0
    assert stagnation_score(**{**base, "wind10m_kmh": 2}) > s0
    assert stagnation_score(**{**base, "t925": 9}) > s0


@pytest.mark.parametrize(
    "score,label",
    [(0, "good"), (34.9, "good"), (35, "watch"), (59.9, "watch"), (60, "warning"), (100, "warning")],
)
def test_labels(score, label):
    assert label_for(score) == label
