"""Learned PM2.5 model: feature layout, serving path and fallback (uses a tiny synthetic model, not the trained artifact)."""
import numpy as np
import pytest
from sklearn.ensemble import HistGradientBoostingRegressor

from backend.cai.features import compute_features
from backend.cai.graph import get_net
from backend.cai.score import CaiModel
from backend.ml.pm_model import DEV_CLIP, HOUR_COLS, LEVEL_FEATURES, NOW_COLS, STATIC_COLS, PmModel, hour_vector, now_vector


def toy_model(coef_T=0.0, coef_cams=1.0, coef_elev=0.0) -> PmModel:
    """Level: log1p(pm) = coef_cams * log1p(cams). Spatial: the given log-deviation per SD of traffic / terrain."""
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 1, (4000, len(LEVEL_FEATURES))).astype("float32")
    X[:, LEVEL_FEATURES.index("cams25")] *= 40
    y = coef_cams * np.log1p(X[:, LEVEL_FEATURES.index("cams25")])
    spatial = {"mean": [1.0, 600.0], "std": [1.0, 100.0], "coef": [coef_T, coef_elev, 0.0, 0.0]}
    return PmModel(HistGradientBoostingRegressor(max_iter=60, random_state=0).fit(X, y), spatial, {"trained": "test"})


def test_feature_layout():
    assert LEVEL_FEATURES == HOUR_COLS + NOW_COLS and len(set(LEVEL_FEATURES + STATIC_COLS)) == len(LEVEL_FEATURES) + len(STATIC_COLS)
    h = {"pm25": 10, "pm10": 20, "blh": 300, "wind10m": 5, "wind925": 9, "t2m": 1, "t925": 4, "cloud": 50, "rh": 80, "radiation": 0, "score": 70}
    assert len(hour_vector(h, hour=8, weekend=False)) == len(HOUR_COLS)
    assert hour_vector(h, hour=8, weekend=False)[HOUR_COLS.index("inversion")] == 3
    assert len(now_vector(3, 12.0, 8.0)) == len(NOW_COLS)
    assert np.isnan(now_vector(0, float("nan"), 8.0)[-1])  # no sensors: ratio unknown, trees handle NaN


def test_missing_inputs_are_nan_not_crash():
    h = {"pm25": None, "pm10": None, "blh": 300, "wind10m": 5, "wind925": 9, "t2m": 1, "t925": 4, "cloud": 50, "rh": None, "radiation": None, "score": 10}
    v = hour_vector(h, hour=3, weekend=True)
    assert np.isnan(v[0]) and v[-1] == 1.0
    out = toy_model().predict(np.zeros((5, len(STATIC_COLS)), "float32"), v, now_vector(0, float("nan"), float("nan")))
    assert out.shape == (5,) and np.isfinite(out).all() and (out >= 0).all()


def test_prediction_follows_cams_and_is_nonnegative():
    m = toy_model()
    st = np.zeros((1, len(STATIC_COLS)), "float32")
    h = {"pm10": 20, "blh": 300, "wind10m": 5, "wind925": 9, "t2m": 1, "t925": 4, "cloud": 50, "rh": 80, "radiation": 0, "score": 70}
    lo = m.predict(st, hour_vector({**h, "pm25": 5}, hour=8, weekend=False), now_vector(0, 5, 5))[0]
    hi = m.predict(st, hour_vector({**h, "pm25": 35}, hour=8, weekend=False), now_vector(0, 5, 5))[0]
    assert hi > 2 * lo > 0


def test_spatial_term_direction_and_cap():
    m = toy_model(coef_T=0.2, coef_elev=-0.3)
    h = {"pm25": 20, "pm10": 20, "blh": 300, "wind10m": 5, "wind925": 9, "t2m": 1, "t925": 4, "cloud": 50, "rh": 80, "radiation": 0, "score": 50}
    hv, nv = hour_vector(h, hour=8, weekend=False), now_vector(0, 20, 20)
    st = np.zeros((3, len(STATIC_COLS)), "float32")
    st[:, STATIC_COLS.index("T")] = [0.0, 3.0, 5000.0]  # quiet side street, avenue, motorway
    st[:, STATIC_COLS.index("elev")] = 600.0
    p = m.predict(st, hv, nv)
    assert p[0] < p[1] < p[2]  # more traffic, more PM
    assert p[2] / p[0] <= np.exp(2 * DEV_CLIP) + 1e-3  # a street never gets more than the capped factor
    valley, hill = st.copy(), st.copy()
    hill[:, STATIC_COLS.index("elev")] = 900.0
    assert (m.predict(hill, hv, nv) < m.predict(valley, hv, nv)).all()  # valley floor is dirtier
    assert np.allclose(toy_model().predict(st, hv, nv), toy_model().predict(st[:1].repeat(3, 0), hv, nv))  # no coefficients: flat


def test_stale_feature_layout_is_ignored(tmp_path):
    import joblib
    joblib.dump({"est": None, "meta": {}, "features": ["old"]}, tmp_path / "m.joblib")  # also: pre-two-part files have no "format"
    assert PmModel.load(tmp_path / "m.joblib") is None
    assert PmModel.load(tmp_path / "missing.joblib") is None


@pytest.fixture(scope="module")
def ml_world():
    net = get_net("walk")
    feats = compute_features(net)
    hours = [{"time": f"2026-01-15T{h:02d}:00", "score": 80.0, "blh": 150, "wind10m": 3, "t2m": -2, "t925": 3, "wind925": 8, "cloud": 10,
              "apparent": -4, "radiation": 0, "rh": 85, "pm25": 40.0 + h, "pm10": 60.0} for h in range(24)]
    return net, feats, hours


def test_cai_model_with_ml_end_to_end(ml_world):
    net, feats, hours = ml_world
    sensors = [{"id": i, "lat": 42.69 + 0.003 * i, "lon": 23.32, "pm25": 45.0 + i} for i in range(12)]
    m = CaiModel(net, feats, hours, 8, sensors, ml=toy_model(coef_T=0.2, coef_elev=-0.1))
    h = m.hour(10)
    assert h["pm"].shape == (net.n_edges,) and np.isfinite(h["pm"]).all()
    assert h["cai"].min() >= 0 and h["cai"].max() <= 100
    e = int(np.argmax(feats.T))
    ex = m.explain(e, 10)
    parts = sum(ex["components_ugm3"].values())
    assert ex["model"] == "ml"
    assert parts == pytest.approx(ex["pm25"], abs=0.6)  # components sum to PM (rounding + the >=0 clamp)


def test_physics_fallback_when_no_model(ml_world):
    net, feats, hours = ml_world
    m = CaiModel(net, feats, hours, 8, [], ml=False)
    assert m.ml is None and m.explain(0, 10)["model"] == "physics"
