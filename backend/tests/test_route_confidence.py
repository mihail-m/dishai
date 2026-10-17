"""Route comparison under uncertainty: identical routes, tiny differences, large differences, and what the uncertainty depends on."""
from types import SimpleNamespace

import numpy as np
import pytest

from backend.cai import route_confidence as rc
from backend.cai.krige import SpatialParams

P = SpatialParams(nugget=0.07, psill=0.15, range_m=12000.0, tau2=0.23, sd_scale={"heating": 0.9, "summer": 0.6})


def net(n=40, length=100.0):
    """A straight line of n edges (undirected id = index) 100 m long, plus a parallel line of n edges 300 m north."""
    mid = np.array([[i * length, 0.0] for i in range(n)] + [[i * length, 300.0] for i in range(n)])
    return SimpleNamespace(e_und=np.arange(2 * n), e_len=np.full(2 * n, length), mid_xy=mid)


def test_identical_routes_are_not_distinguishable():
    n = net()
    r = rc.compare(n, np.arange(40), np.arange(40), 20.0, 20.0, P)
    assert not r["distinguishable"] and r["reason"] == "same route"


def test_a_big_difference_over_long_unshared_stretches_is_distinguishable():
    n = net()
    r = rc.compare(n, np.arange(40), np.arange(40, 80), 30.0, 18.0, P, month=1)  # 40 % cleaner over 4 km
    assert r["distinguishable"] and r["diff_pct"] == pytest.approx(-40.0) and r["sd_pct"] < 40


def test_a_small_difference_is_not():
    n = net()
    assert not rc.compare(n, np.arange(40), np.arange(40, 80), 20.0, 19.0, P, month=1)["distinguishable"]  # 5 %


def test_shared_streets_cancel_and_short_detours_are_less_certain():
    n = net()
    a = np.arange(40)
    mostly_shared = np.concatenate([np.arange(35), np.arange(40, 45)])  # differs in 5 of 40 edges
    long_detour = np.arange(40, 80)
    short = rc.compare(n, a, mostly_shared, 20.0, 16.0, P, month=1)
    long = rc.compare(n, a, long_detour, 20.0, 16.0, P, month=1)
    assert short["unshared_m"][0] == 500 and long["unshared_m"][0] == 4000
    assert short["sd_pct"] > long["sd_pct"]  # fewer independent pieces to average over


def test_summer_scale_makes_the_same_difference_easier_to_resolve():
    n = net()
    w = rc.compare(n, np.arange(40), np.arange(40, 80), 20.0, 17.0, P, month=1)
    s = rc.compare(n, np.arange(40), np.arange(40, 80), 20.0, 17.0, P, month=7)
    assert s["sd_pct"] < w["sd_pct"]


def test_summary_message_only_when_nothing_is_better():
    none = rc.summarise({"cleanest": {"distinguishable": False, "diff_pct": -3.0}})
    assert not none["cleaner_exists"] and "about the same air" in none["message"]
    some = rc.summarise({"cleanest": {"distinguishable": True, "diff_pct": -30.0}, "balanced": {"distinguishable": True, "diff_pct": 5.0}})
    assert some["cleaner_exists"] and some["better"] == ["cleanest"] and some["message"] is None
