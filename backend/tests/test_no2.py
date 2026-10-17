"""NO2 model with measured traffic: serving maths, the saved model's honesty fields, and the endpoint (no network)."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import main, no2_model
from backend.cache import Cache

PROFILE = {"weekday": [0.1] * 7 + [1.0] * 2 + [0.7] * 8 + [1.0] + [0.5] * 6, "weekend": [0.1] * 8 + [0.5] * 14 + [0.2] * 2}


def toy_model(traffic_coef: float = 0.3) -> dict:
    # const, lblh, wind, t2m, rh, lcams, ltraffic
    coef = [2.0, -0.1, -0.02, 0.0, -0.5, 0.4, traffic_coef]
    return {"coef": coef, "coef_no_cams": [c for k, c in zip(no2_model.FEATS, coef) if k != "lcams"], "resid_q": {"p10": -0.3, "p50": 0.0, "p90": 0.3},
            "resid_q_no_cams": {"p10": -0.4, "p50": 0.0, "p90": 0.4}, "traffic_profile": PROFILE,
            "elasticity": {"mean": 0.1, "lo": 0.05, "hi": 0.15}}


def hour(t: str, **kw) -> dict:
    return {"time": t, "blh": 500.0, "wind10m": 3.0, "t2m": 15.0, "rh": 60.0, **kw}


def test_busier_hours_have_more_no2_everything_else_equal():
    m = toy_model()
    night, rush = (no2_model.predict(m, [hour(f"2026-10-14T{h:02d}:00")], {f"2026-10-14T{h:02d}:00": 10.0})[0]["no2"] for h in (3, 8))
    assert rush > night  # Wednesday 08:00 carries 10x the traffic of 03:00 in this profile


def test_weekend_uses_the_weekend_profile():
    m = toy_model()
    cams = {"2026-10-14T08:00": 10.0, "2026-10-17T08:00": 10.0}
    wd = no2_model.predict(m, [hour("2026-10-14T08:00")], cams)[0]["no2"]  # Wednesday
    we = no2_model.predict(m, [hour("2026-10-17T08:00")], cams)[0]["no2"]  # Saturday
    assert we < wd


def test_band_is_ordered_and_runs_without_cams():
    m = toy_model()
    p = no2_model.predict(m, [hour("2026-10-14T08:00")], None)[0]
    assert p["p10"] < p["no2"] < p["p90"] and p["with_cams"] is False


def test_hours_with_missing_weather_are_skipped():
    assert no2_model.predict(toy_model(), [hour("2026-10-14T08:00", blh=None)], {}) == []


def test_traffic_scenarios_follow_the_elasticity():
    s = no2_model.traffic_scenarios(toy_model())
    cut20, cut50 = s
    assert -3 < cut20["no2_change_pct"] < 0 and cut50["no2_change_pct"] < cut20["no2_change_pct"]
    assert cut20["range_pct"][0] < cut20["no2_change_pct"] < cut20["range_pct"][1]


# ---- the saved model ----------------------------------------------------------------------------------
def test_saved_model_reports_its_limits_and_beats_the_constant():
    m = no2_model.load_model()
    if m is None:
        pytest.skip("data/no2_model.json not built")
    assert m["eval"]["weather + CAMS NO2 + typical traffic (served)"]["skill_pct"] > 20
    assert m["eval"]["median_abs_error_ugm3"] < m["eval"]["median_abs_error_constant_ugm3"]
    assert 0.7 < m["eval"]["band_coverage_p10_p90"] < 0.9
    assert any("heating season" in c for c in m["caveats"])  # it has not seen winter and says so
    # the study's headline: NO2 tracks traffic, PM does not (interval includes zero)
    assert m["elasticity"]["lo"] > 0
    assert m["study"]["pm"]["pm25"]["lo"] <= 0 <= m["study"]["pm"]["pm25"]["hi"]


def test_endpoint(monkeypatch):
    from backend import no2_model as nm

    if nm.load_model() is None:
        pytest.skip("data/no2_model.json not built")

    async def boom():
        raise RuntimeError("offline")

    monkeypatch.setattr(main, "cache", Cache(fixture_dir=main.cache.fixture_dir))
    monkeypatch.setattr(main, "fetch_weather", boom)
    monkeypatch.setattr(main, "fetch_air_quality", boom)
    monkeypatch.setattr(main, "fetch_sensors", boom)
    monkeypatch.setattr(Cache, "_save_fixture", lambda *a, **k: None)

    async def no_cams():
        return {}

    monkeypatch.setattr(nm, "cams_no2", no_cams)
    r = TestClient(main.app).get("/api/no2").json()
    assert r["enabled"] and len(r["forecast"]) >= 24 and r["mean_24h"] > 0
    assert all(p["p10"] <= p["no2"] <= p["p90"] for p in r["forecast"])
    assert len(r["scenarios"]) == 2 and r["study"]["weekday_weekend_ratio"]["traffic"] > 1.1
