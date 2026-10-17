"""Real traffic is observed data only: it must never move PM2.5. Corridors, readings and budgets are tested with injected state (no network)."""
import numpy as np
import pytest
from sklearn.ensemble import HistGradientBoostingRegressor

from backend import traffic
from backend.cai.features import compute_features
from backend.cai.graph import get_net
from backend.cai.score import CaiModel
from backend.ml.pm_model import LEVEL_FEATURES, SPATIAL_COLS, STATIC_COLS, PmModel


@pytest.fixture(scope="module")
def world():
    net = get_net("walk")
    feats = compute_features(net)
    hours = [{"time": f"2026-01-15T{h:02d}:00", "score": 80.0, "blh": 150, "wind10m": 3, "t2m": -2, "t925": 3, "wind925": 8, "cloud": 10,
              "apparent": -4, "radiation": 0, "rh": 85, "pm25": 20.0, "pm10": 30.0} for h in range(24)]
    return net, feats, hours


def toy_ml(coef_elev: float = 0.0) -> PmModel:
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 1, (3000, len(LEVEL_FEATURES))).astype("float32")
    X[:, LEVEL_FEATURES.index("cams25")] *= 40
    y = np.log1p(X[:, LEVEL_FEATURES.index("cams25")])
    spatial = {"mean": [600.0], "std": [100.0], "coef": [coef_elev, 0.0]}
    return PmModel(HistGradientBoostingRegressor(max_iter=40, random_state=0).fit(X, y), spatial, {"trained": "test"})


@pytest.fixture(autouse=True)
def _reset_traffic_state():
    yield
    traffic._state = None
    traffic._profile = None
    traffic._usage.update(day="", google=0, tomtom=0)


# ---- traffic never moves PM2.5 -------------------------------------------------------------------------
def test_features_carry_no_assumed_traffic(world):
    _, feats, _ = world
    assert not hasattr(feats, "T") and "T" not in feats.at([23.32], [42.69])


def test_no_traffic_feature_reaches_the_learned_model(world):
    """The learned model's street features are terrain and greenery only; road class cannot move a street's PM2.5."""
    net, feats, hours = world
    assert "T" not in STATIC_COLS and "T" not in SPATIAL_COLS
    pm = CaiModel(net, feats, hours, 8, [], ml=toy_ml()).hour(8)["pm"]
    major = net.hw_mask("primary", "secondary")
    quiet = net.hw_mask("footway", "path")
    assert pm[major].mean() == pytest.approx(pm[quiet].mean(), rel=1e-3)  # the toy model has no terrain coefficient: nothing else differs


def test_explain_has_no_traffic_component(world):
    net, feats, hours = world
    for ml in (False, toy_ml(0.3)):
        ex = CaiModel(net, feats, hours, 8, [], ml=ml).explain(0, 8)
        assert "traffic" not in ex["components_ugm3"]
        assert not {"traffic_term", "rush", "live_traffic", "traffic_source", "amplifier"} & set(ex["drivers"])


# ---- corridors and readings -------------------------------------------------------------------------
def test_pick_corridors_are_plausible_spokes(world):
    net = world[0]
    cs = traffic.pick_corridors(net)
    assert 6 <= len(cs) <= traffic.N_SPOKES and len({c["id"] for c in cs}) == len(cs)
    for c in cs:
        assert 42.5 < c["o"][0] < 42.9 and 23.0 < c["o"][1] < 23.7


def test_assign_edges_picks_the_street_that_was_routed(world):
    net = world[0]
    major = np.where(net.hw_mask(*traffic.MAJOR))[0]
    e = int(major[len(major) // 2])
    got = traffic.assign_edges(net, [(float(lat), float(lon)) for lon, lat in net.geometry(e)])
    assert e in got


def _inject(edges, tti_google, tti_tomtom=None, typical=1.1):
    c = {"id": "c00", "name": "Blvd Test (N)", "edges": edges, "profile": {"wk": [typical] * 24, "we": [typical] * 24, "free_s": 100},
         "google": {"tti": tti_google, "duration_s": 600.0, "jam_share": 0.1}, "tomtom": {"tti": tti_tomtom} if tti_tomtom else {}}
    traffic._state = traffic.State([c])


def test_edge_reading_only_on_measured_streets(world):
    net = world[0]
    major = np.where(net.hw_mask(*traffic.MAJOR))[0][:50]
    _inject(major, tti_google=1.5, tti_tomtom=1.3)
    r = traffic.edge_reading(net, int(major[3]))
    assert r["corridor"] == "Blvd Test (N)" and r["live_index"] == pytest.approx(1.4) and r["vs_typical"] == pytest.approx(1.4 / 1.1, abs=0.01)
    assert r["google_min"] == 10.0 and r["tomtom_index"] == 1.3
    other = int(np.setdiff1d(np.arange(net.n_edges), major)[0])
    assert traffic.edge_reading(net, other) is None  # no interpolation: no measurement, no value


def test_no_live_reading_means_no_edge_reading(world):
    net = world[0]
    traffic._state = traffic.State([{"id": "c", "name": "x", "edges": np.array([0]), "profile": None, "google": {}, "tomtom": {}}])
    assert traffic.edge_reading(net, 0) is None


# ---- budget and privacy ------------------------------------------------------------------------------
def test_daily_request_cap(monkeypatch):
    monkeypatch.setenv("TRAFFIC_GOOGLE_DAILY_MAX", "2")
    assert traffic._spend("google") and traffic._spend("google") and not traffic._spend("google")
    assert traffic._spend("tomtom")  # separate counter


def test_snapshot_never_contains_keys(monkeypatch, world):
    monkeypatch.setenv("GOOGLE_ROUTES_SERVER_KEY", "AIzaSECRET")
    monkeypatch.setenv("TOMTOM_API_KEY", "TOMSECRET")
    _inject(np.array([0, 1]), tti_google=1.1)
    s = str(traffic.snapshot())
    assert "AIzaSECRET" not in s and "TOMSECRET" not in s
