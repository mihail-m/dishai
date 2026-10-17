"""The alert is built from the learned city-level PM2.5; the stagnation score is context, not the trigger."""
from datetime import datetime, timedelta

import pytest

from backend import alert
from backend.cai import config as C
from backend.ml.pm_model import PmModel

T0 = datetime(2026, 1, 14, 0, 0)
NOW = T0 + timedelta(hours=12)


def series(pm: list[float | None], score: float | list[float] = 20.0):
    n = len(pm)
    times = [(T0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(n)]
    scores = score if isinstance(score, list) else [score] * n
    return times, scores


def run(pm, score=20.0, now_idx=12, basis="model"):
    times, scores = series(pm, score)
    return alert.build_alert(times, pm, scores, now_idx, T0 + timedelta(hours=now_idx), basis)


def test_clean_forecast_with_a_high_stagnation_score_is_not_a_red_banner():
    a = run([4.0] * 48, score=90.0)
    assert not a["active"] and a["level"] == "good" and a["audience"] == "none"
    assert a["trapped"] and a["peak_score"] == 90.0  # the trapped air is still reported, as context
    assert "No pollution episode" in a["message"]


def test_a_polluted_forecast_with_a_low_score_is_still_red():
    pm = [8.0] * 14 + [30.0] * 6 + [8.0] * 28
    a = run(pm, score=10.0)
    assert a["active"] and a["level"] == "warning" and not a["trapped"] and a["hours"] == 6
    assert "30 µg/m³" in a["message"] and a["start_hhmm"] == "14:00" and a["end_hhmm"] == "20:00"


def test_short_exceedance_is_not_an_alert():
    a = run([8.0] * 14 + [40.0] * (C.ALERT_MIN_HOURS - 1) + [8.0] * 40)
    assert not a["active"]
    assert a["level"] == "watch" and a["peak_pm25"] == 40.0  # still worth a mention


def test_run_already_in_progress_counts_hours_before_now():
    pm = [8.0] * 10 + [30.0] * 5 + [8.0] * 33  # 10:00-14:00, now = 12:00
    a = run(pm)
    assert a["active"] and a["in_progress"] and a["message"].startswith("Now until 15:00")


def test_everyone_only_above_the_high_threshold():
    assert run([8.0] * 14 + [30.0] * 4 + [8.0] * 30)["audience"] == "sensitive"
    a = run([8.0] * 14 + [30.0, 55.0, 60.0, 40.0] + [8.0] * 30)
    assert a["audience"] == "everyone" and a["message"].endswith("Everyone should limit time outside.")


def test_watch_between_the_who_and_eu_values():
    a = run([6.0] * 14 + [19.0] * 6 + [6.0] * 28)
    assert not a["active"] and a["level"] == "watch" and a["audience"] == "sensitive" and "19 µg/m³" in a["message"]


def test_alert_level_is_monotonic_in_pm():
    levels = ["good", "watch", "warning"]
    seen = [levels.index(run([6.0] * 14 + [x] * 5 + [6.0] * 29)["level"]) for x in (3, 10, 16, 22, 27, 45, 80)]
    assert seen == sorted(seen)


def test_far_future_run_is_not_the_banner():
    pm = [5.0] * 40 + [60.0] * 8  # starts 28 h after now
    assert not run(pm)["active"]


def test_without_any_pm_series_the_old_stagnation_rule_still_works():
    times, scores = series([None] * 48, [20.0] * 14 + [80.0] * 5 + [20.0] * 29)
    a = alert.build_alert(times, [None] * 48, scores, 12, NOW, basis="stagnation")
    assert a["active"] and a["basis"] == "stagnation" and "Inversion expected" in a["message"]


def hours(n=48, cams=20.0):
    return [{"time": (T0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M"), "pm25": cams, "pm10": 30.0, "blh": 300.0, "wind10m": 3.0,
             "wind925": 5.0, "t2m": 0.0, "t925": 4.0, "cloud": 10, "rh": 80, "radiation": 0.0, "score": 70.0} for i in range(n)]


def test_levels_fall_back_to_cams_times_sensor_bias_without_a_model():
    lv, basis = alert.city_levels(hours(), 12, [10.0] * 30, None)
    assert basis == "cams_bias" and lv[0] == pytest.approx(10.0)  # sensors read half of CAMS
    lv, basis = alert.city_levels(hours(), 12, [], None)
    assert basis == "stagnation" and set(lv) == {None}


def test_levels_from_the_model_use_the_nowcast(tmp_path):
    m = PmModel.load()
    if m is None:
        pytest.skip("no trained model file")
    low, b1 = alert.city_levels(hours(), 12, [3.0] * 30, m)
    high, b2 = alert.city_levels(hours(), 12, [40.0] * 30, m)
    assert b1 == b2 == "model" and len(low) == 48
    assert sum(high[12:36]) > sum(low[12:36])  # a polluted nowcast raises the forecast levels


def test_model_cache_follows_the_file(tmp_path):
    assert alert.get_model(tmp_path / "missing.joblib") is None
