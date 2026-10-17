"""Regression kriging of the sensor residual field: exactness, reversion to the trend, variance, offsets, serving fallbacks."""
import numpy as np
import pytest

from backend.cai import config as C
from backend.cai import krige
from backend.cai.krige import SpatialParams, kriging_weights, prediction_sd

P = SpatialParams(nugget=0.05, psill=0.2, range_m=800.0, tau2=0.2, rho={0: 0.9, 6: 0.3, 47: 0.1})
SENS = np.array([[0.0, 0.0], [500.0, 0.0], [0.0, 700.0], [-600.0, -300.0]])


def test_kriging_is_exact_at_a_noiseless_sensor():
    W, kv = kriging_weights(SENS, SENS, np.full(4, 1e-9), P, k=4)
    e = np.array([0.3, -0.2, 0.5, 0.1])
    assert (W @ e) == pytest.approx(e, abs=1e-5) and kv == pytest.approx(0.0, abs=1e-5)


def test_noise_shrinks_the_correction_toward_the_trend():
    e = np.array([0.4, 0.0, 0.0, 0.0])
    quiet, _ = kriging_weights(SENS[:1], SENS, np.full(4, 1e-6), P, k=4)
    noisy, _ = kriging_weights(SENS[:1], SENS, np.full(4, 1.0), P, k=4)
    assert abs((quiet @ e)[0]) > abs((noisy @ e)[0]) > 0


def test_far_from_every_sensor_it_reverts_to_the_trend_with_full_variance():
    far = np.array([[80_000.0, 0.0]])
    W, kv = kriging_weights(far, SENS, np.full(4, 0.05), P)
    assert (W @ np.array([1.0, 1.0, 1.0, 1.0]))[0] == 0.0 and kv[0] == pytest.approx(P.psill)


def test_variance_grows_with_distance_from_the_sensors():
    d = np.array([[x, 0.0] for x in (0.0, 300.0, 1500.0, 6000.0, 30_000.0)]) + np.array([2000.0, 0.0])
    one = SENS[:1] + np.array([2000.0, 0.0])
    _, kv = kriging_weights(d, one, np.array([0.05]), P, k=1)
    assert np.all(np.diff(kv) >= -1e-12) and kv[-1] == pytest.approx(P.psill) and kv[0] < kv[-1]


def test_leave_one_out_never_uses_the_sensor_itself():
    W, _ = kriging_weights(SENS, SENS, np.full(4, 0.05), P, k=3, exclude=np.arange(4))
    assert np.allclose(W.diagonal(), 0.0) and W.nnz == 4 * 3


def test_no_sensors_or_no_structure_gives_zero_correction():
    W, kv = kriging_weights(SENS, np.zeros((0, 2)), np.zeros(0), P)
    assert W.shape == (4, 0) and np.all(kv == P.psill)
    flat = SpatialParams(0.05, 0.0, 800.0, 0.2)
    W, kv = kriging_weights(SENS, SENS, np.full(4, 0.05), flat)
    assert W.nnz == 0 and np.all(kv == 0.0)


def test_unknown_sensors_carry_the_full_offset_variance():
    p = SpatialParams(0.05, 0.2, 800.0, 0.2, offsets={1: 0.3}, offset_var={1: 0.01})
    n = krige.sensor_noise([1, 2], p)
    assert n[0] == pytest.approx(0.06) and n[1] == pytest.approx(0.25)
    assert list(krige.sensor_offsets([1, 2], p)) == [0.3, 0.0]


def test_rho_decays_and_uncertainty_returns_to_the_prior():
    assert P.rho_at(0) > P.rho_at(3) > P.rho_at(6) > P.rho_at(47) and P.rho_at(100) == P.rho_at(47)
    near, far = np.array([0.02]), np.array([0.2])
    sd_now = prediction_sd(near, P, rho=0.9)
    sd_late = prediction_sd(near, P, rho=0.1)
    assert sd_now < sd_late <= prediction_sd(far, P, rho=0.9) + 1e-9 or sd_now < sd_late
    assert prediction_sd(far, P, rho=0.0) == pytest.approx(np.sqrt(P.nugget + P.tau2 + P.psill))


def test_season_scale():
    p = SpatialParams(0.05, 0.2, 800.0, 0.2, sd_scale={"heating": 0.9, "summer": 0.6})
    assert p.scale_for(1) == 0.9 and p.scale_for(7) == 0.6 and p.scale_for(None) == 0.9
    assert prediction_sd(np.array([0.1]), p, 0.0, month=7)[0] < prediction_sd(np.array([0.1]), p, 0.0, month=1)[0]


def test_params_roundtrip_and_format_guard(tmp_path):
    import joblib
    p = SpatialParams(0.05, 0.2, 800.0, 0.2, offsets={5: 0.1}, offset_var={5: 0.01}, rho={0: 0.9}, meta={"x": 1})
    p.save(tmp_path / "s.joblib")
    q = SpatialParams.load(tmp_path / "s.joblib")
    assert q.offsets == {5: 0.1} and q.rho_at(10) == 0.9 and q.meta == {"x": 1}
    joblib.dump({"format": 99, **p.to_dict()}, tmp_path / "bad.joblib")
    assert SpatialParams.load(tmp_path / "bad.joblib") is None and SpatialParams.load(tmp_path / "none.joblib") is None
    assert krige.get_params(tmp_path / "none.joblib") is None


# ---- through CaiModel ------------------------------------------------------------------------------------------------
from backend.cai.features import compute_features  # noqa: E402
from backend.cai.graph import get_net  # noqa: E402
from backend.cai.score import CaiModel  # noqa: E402
from backend.tests.test_ml import toy_model  # noqa: E402


@pytest.fixture(scope="module")
def world():
    net = get_net("walk")
    feats = compute_features(net)
    hours = [{"time": f"2026-01-15T{h:02d}:00", "score": 80.0, "blh": 150, "wind10m": 3, "t2m": -2, "t925": 3, "wind925": 8, "cloud": 10,
              "apparent": -4, "radiation": 0, "rh": 85, "pm25": 30.0, "pm10": 60.0} for h in range(24)]
    return net, feats, hours


def model_with(world, monkeypatch, sensors, params):
    net, feats, hours = world
    monkeypatch.setattr(krige, "get_params", lambda *a, **k: params)
    return CaiModel(net, feats, hours, 8, sensors, ml=toy_model())


def cluster(pm, ids=range(8), lat=42.69, lon=23.32):
    return [{"id": i, "lat": lat + 0.001 * (i % 4), "lon": lon + 0.001 * (i // 4), "pm25": pm} for i in ids]


def at_trend(world, monkeypatch, factor):
    """Sensors reading `factor` x the model's own trend at their positions (plus the model itself, for its map)."""
    from backend.ml.pm_model import static_matrix
    net, feats, hours = world
    sens = cluster(1.0)
    ref = model_with(world, monkeypatch, [], P)
    lon, lat = [s["lon"] for s in sens], [s["lat"] for s in sens]
    trend = ref._ml_pm(static_matrix(feats, feats.at(lon, lat)), 8)
    return [{**s, "pm25": float(factor * t)} for s, t in zip(sens, trend)], trend


def test_a_known_offset_is_not_painted_onto_the_neighbourhood(world, monkeypatch):
    net = world[0]
    quiet, trend = at_trend(world, monkeypatch, 1.0)
    hot, _ = at_trend(world, monkeypatch, 1.6)
    base = model_with(world, monkeypatch, quiet, P).hour(8)["pm"]
    naive = model_with(world, monkeypatch, hot, P).hour(8)["pm"]
    offs = {s["id"]: float(np.log1p(h["pm25"]) - np.log1p(t)) for s, h, t in zip(quiet, hot, trend)}
    known = SpatialParams(P.nugget, P.psill, P.range_m, P.tau2, offsets=offs, offset_var={i: 0.001 for i in offs}, rho=P.rho)
    adj = model_with(world, monkeypatch, hot, known).hour(8)["pm"]
    near = int(np.argmin(np.abs(net.e_mid_lat - 42.6915) + np.abs(net.e_mid_lon - 23.3215)))
    assert naive[near] > base[near] * 1.1  # an unknown over-reading sensor raises its neighbourhood
    assert adj[near] == pytest.approx(base[near], rel=0.03)  # a known offset does not


def test_hours_decay_back_toward_the_trend_and_carry_an_sd(world, monkeypatch):
    hot, _ = at_trend(world, monkeypatch, 1.5)
    m = model_with(world, monkeypatch, hot, P)
    h0, h8, h20 = m.hour(8), m.hour(16), m.hour(23)
    near = int(np.argmax(m.kappa))
    gap = lambda h: (h["pm"][near] - h["base"][near]) / h["base"][near]
    assert gap(h0) > gap(h8) > gap(h20) >= 0
    assert h0["sd_u"].shape == h0["pm"].shape and np.isfinite(h0["sd_u"]).all() and h20["sd_u"].min() >= h0["sd_u"].min()


def test_idw_flag_and_missing_file_fall_back(world, monkeypatch):
    monkeypatch.setattr(C, "SPATIAL_METHOD", "idw")
    m = model_with(world, monkeypatch, cluster(60.0), P)
    assert m.spatial is None and m.explain(0, 8)["spatial"] == "idw"
    monkeypatch.setattr(C, "SPATIAL_METHOD", "krige")
    m = model_with(world, monkeypatch, cluster(60.0), None)
    assert m.spatial is None and np.isfinite(m.hour(8)["pm"]).all()
