"""Sensor calibration: direction of the humidity effect, missing inputs, missing or stale files."""
import numpy as np
import pytest

from backend import main
from backend.ml import calibration as cal_mod
from backend.ml.calibration import Calibration
from backend.ml.pm_model import PmModel

# the shape of the fitted model (Sofia 2026): factor falls as humidity rises, rises in the heating season
CAL = Calibration("linear", {"const": 0.8, "log_raw": 0.9, "rh": -0.6, "temp": 0.15, "heating": 0.3}, rh_fill=65.0, t_fill=10.0)


def test_humid_air_needs_less_correction_than_dry_air():
    dry, humid = CAL.apply([10.0], rh=30, temp=10, month=7)[0], CAL.apply([10.0], rh=95, temp=10, month=7)[0]
    assert dry > humid > 10.0 * 0.7  # sensors under-read more when it is dry, and both are scaled up


def test_calibration_is_monotonic_in_the_raw_reading():
    out = CAL.apply(np.linspace(0, 200, 50), rh=70, temp=5, month=1)
    assert np.all(np.diff(out) >= 0) and out[0] >= 0


def test_missing_humidity_and_temperature_use_the_training_median():
    assert CAL.apply([10.0], rh=None, temp=None, month=7)[0] == pytest.approx(CAL.apply([10.0], rh=65.0, temp=10.0, month=7)[0])
    assert np.isfinite(CAL.apply([10.0], rh=float("nan"), temp=float("nan"), month=None)).all()


def test_factor_is_clipped_so_tiny_readings_are_not_blown_up():
    f = CAL.factor([0.2, 3.0, 400.0], rh=30, temp=10, month=1)
    assert (f >= CAL.clip[0] - 1e-9).all() and (f <= CAL.clip[1] + 1e-9).all()


def test_ratio_calibration():
    assert Calibration("ratio", {"k": 2.0}).apply([5.0])[0] == 10.0


def test_missing_file_means_no_calibration(tmp_path):
    assert Calibration.load(tmp_path / "nope.joblib") is None and cal_mod.get("pm25", tmp_path / "nope.joblib") is None


def test_wrong_format_is_ignored(tmp_path):
    import joblib
    joblib.dump({"format": 999, "pm25": CAL.to_dict()}, tmp_path / "c.joblib")
    assert Calibration.load(tmp_path / "c.joblib") is None


def test_roundtrip(tmp_path):
    cal_mod.save({"pm25": CAL}, tmp_path / "c.joblib")
    back = Calibration.load(tmp_path / "c.joblib")
    assert back.coef == CAL.coef and back.apply([12.0], rh=80, temp=0, month=12)[0] == pytest.approx(CAL.apply([12.0], rh=80, temp=0, month=12)[0])
    assert Calibration.load(tmp_path / "c.joblib", "pm10") is None


def test_saved_calibration_has_the_expected_physics():
    cal = Calibration.load()
    if cal is None:
        pytest.skip("no data/calibration.joblib")
    assert cal.coef["rh"] < 0  # the factor falls with humidity
    assert cal.apply([5.0], rh=60, temp=5, month=1)[0] > 7.0  # sensors read roughly half of the reference


def test_live_sensors_pass_through_and_say_so_without_a_calibration(monkeypatch):
    monkeypatch.setattr(cal_mod, "get", lambda *a, **k: None)
    recs, info = main.calibrate_records([{"pm25": 5.0, "pm10": 8.0}], {"rh": 80, "temp": 3, "month": 1})
    assert info["calibrated"] is False and recs[0]["pm25"] == 5.0 and recs[0]["pm25_raw"] == 5.0


def test_live_sensors_are_scaled_and_keep_the_raw_value(monkeypatch):
    monkeypatch.setattr(cal_mod, "get", lambda p="pm25", *a, **k: Calibration("ratio", {"k": 2.0}) if p == "pm25" else None)
    recs, info = main.calibrate_records([{"pm25": 5.0, "pm10": 8.0}, {"pm25": 1.0, "pm10": None}], {"rh": 80, "temp": 3, "month": 1})
    assert info["calibrated"] is True and [r["pm25"] for r in recs] == [10.0, 2.0] and recs[0]["pm25_raw"] == 5.0 and recs[0]["pm10"] == 8.0


def test_model_trained_on_another_calibration_is_not_used(tmp_path, monkeypatch):
    import joblib
    path = tmp_path / "m.joblib"
    from backend.ml.pm_model import FORMAT, LEVEL_FEATURES
    blob = {"format": FORMAT, "level": None, "spatial": {}, "features": LEVEL_FEATURES, "meta": {"calibrated": True, "calibration_coef": {"k": 1.0}}}
    joblib.dump(blob, path)
    monkeypatch.setattr(cal_mod, "get", lambda *a, **k: None)
    assert PmModel.load(path) is None  # calibrated model, no calibration file
    monkeypatch.setattr(cal_mod, "get", lambda *a, **k: Calibration("ratio", {"k": 2.0}))
    assert PmModel.load(path) is None  # calibration changed since training
    monkeypatch.setattr(cal_mod, "get", lambda *a, **k: Calibration("ratio", {"k": 1.0}))
    assert PmModel.load(path) is not None
