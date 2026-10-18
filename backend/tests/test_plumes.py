"""CAMS-direct plume reading, the transport suggestion and the green ticket. No network: CAMS rows are hand-built or a recorded real episode."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import alerts, fires, green_ticket, main, plumes
from backend.cache import Cache

NOW = "2026-01-14T09:00"
CLIM = {"1": {"pm25_day_p50": 22.0, "pm25_day_p75": 30.0, "pm10_day_p50": 26.0, "pm10_day_p75": 35.0, "pm25_hour_p90": 43.0, "co_hour_p90": 413},
        "7": {"pm25_day_p50": 10.0, "pm25_day_p75": 13.0, "pm10_day_p50": 14.0, "pm10_day_p75": 18.0, "pm25_hour_p90": 16.0, "co_hour_p90": 250}}
THREAT = {"count": 14, "frp_mw": 300, "nearest_km": 120, "direction": "west", "arrival_h": 5.0, "wind_kmh": 24, "wind_from_deg": 270, "near": False}


def day_rows(date, pm10=20.0, pm25=12.0, dust=0.0, co=200.0, aod=0.1, hours=24):
    return [{"time": f"{date}T{h:02d}:00", "pm10": pm10, "pm2_5": pm25, "dust": dust, "aerosol_optical_depth": aod, "carbon_monoxide": co} for h in range(hours)]


# ---- the yardstick and "worse than usual" -----------------------------------------------------------------------------------------------------
def test_a_day_is_worse_than_usual_only_if_above_the_month_and_above_the_who_guideline():
    clean_summer = plumes.outlook(day_rows("2026-07-10", pm10=19, pm25=14), "2026-07-10", CLIM)[0]
    assert not clean_summer["worse_than_usual"]  # above July's 75th percentile (13) but below the WHO PM2.5 guideline (15)
    bad_summer = plumes.outlook(day_rows("2026-07-10", pm10=25, pm25=17), "2026-07-10", CLIM)[0]
    assert bad_summer["worse_than_usual"] and bad_summer["worse_by"] == "pm25"
    ordinary_winter = plumes.outlook(day_rows("2026-01-14", pm10=24, pm25=22), "2026-01-14", CLIM)[0]
    assert not ordinary_winter["worse_than_usual"]  # a normal January day is not "worse than usual", although it is above the guideline
    bad_winter = plumes.outlook(day_rows("2026-01-14", pm10=40, pm25=32), "2026-01-14", CLIM)[0]
    assert bad_winter["worse_than_usual"] and bad_winter["usual_pm25"] == 22.0


def test_without_the_yardstick_nothing_is_called_worse_than_usual():
    assert not plumes.outlook(day_rows("2026-01-14", pm10=90, pm25=80), "2026-01-14", {})[0]["worse_than_usual"]


def test_short_days_are_skipped():
    assert plumes.outlook(day_rows("2026-01-14", hours=10), "2026-01-14", CLIM) == []


# ---- what kind of event ------------------------------------------------------------------------------------------------------------------------
def test_dust_is_named_from_cams_dust_and_its_share_of_pm10():
    rows = day_rows("2026-01-14", pm10=60, dust=30)
    c = plumes.classify(rows, NOW, None, False, CLIM)
    assert c["kind"] == "dust" and c["dust_share"] == 0.5 and "vertical" in c and "surface" in c["vertical"]


def test_smoke_needs_fires_and_cams_agreeing_otherwise_it_is_only_possible():
    calm = day_rows("2026-07-10", pm10=14, pm25=9, co=180)
    assert plumes.classify(calm, "2026-07-10T09:00", THREAT, False, CLIM)["kind"] == "smoke_possible"
    smoky = day_rows("2026-07-10", pm10=40, pm25=30, co=400)
    c = plumes.classify(smoky, "2026-07-10T09:00", THREAT, False, CLIM)
    assert c["kind"] == "smoke" and c["co_elevated"] and c["pm25_elevated"]
    assert plumes.classify(smoky, "2026-07-10T09:00", None, False, CLIM)["kind"] == "none"  # high CO and PM2.5 in July with no fire is not called smoke


def test_the_same_numbers_in_winter_are_local_pollution_not_a_plume():
    rows = day_rows("2026-01-14", pm10=60, pm25=50, co=450)
    assert plumes.classify(rows, NOW, None, True, CLIM)["kind"] == "local"
    assert plumes.classify(rows, NOW, None, False, CLIM)["kind"] == "none"            # needs trapped air
    assert plumes.classify(rows, NOW, THREAT, True, CLIM)["kind"] == "smoke"          # fires nearby change the answer
    assert plumes.classify(day_rows("2026-07-10", pm10=60, pm25=50, co=450), "2026-07-10T09:00", None, True, CLIM)["kind"] == "none"  # not heating season


def test_high_column_but_low_surface_dust_is_reported_as_overhead():
    c = plumes.classify(day_rows("2026-07-10", pm10=15, dust=2, aod=0.7), "2026-07-10T09:00", None, False, CLIM)
    assert c["kind"] == "none" and "overhead" in c["vertical"]


# ---- a real episode, replayed -------------------------------------------------------------------------------------------------------------------
def test_real_cams_dust_episode_of_april_2024_gives_a_warning_with_cams_numbers():
    rows = json.loads((Path(__file__).parent / "data" / "cams_dust_2024_03.json").read_text())["rows"]
    now = "2024-04-01T06:00"
    pl = plumes.assess(rows, now, None, False)
    assert pl["plume"]["kind"] == "dust" and pl["plume"]["dust_peak"] >= 80
    out = alerts.dust_alerts([{"time": r["time"], "dust": r["dust"]} for r in rows], now, pl["days"])
    assert out and out[0]["level"] == "warning" and "CAMS puts that day's PM10" in out[0]["message"] and out[0]["detail"]["dust_share"] > 0.5


# ---- fires: several satellites, close fires -------------------------------------------------------------------------------------------------------
def test_one_fire_seen_by_two_satellites_counts_once_and_far_away_ones_are_dropped():
    a = [{"lat": 42.001, "lon": 23.001, "frp": 5.0, "conf": "n"}]
    b = [{"lat": 42.002, "lon": 23.002, "frp": 9.0, "conf": "n"}, {"lat": 60.0, "lon": 23.0, "frp": 9.0, "conf": "n"}]
    got = fires.merge_detections(a, b)
    assert len(got) == 1 and got[0]["frp"] == 9.0


def test_a_close_fire_counts_whatever_the_wind_does():
    close = [{"lat": 42.70 + 0.01 * k, "lon": 23.10, "frp": 20.0, "conf": "n"} for k in range(12)]  # ~17 km west
    for wind in ((25.0, 90.0), (2.0, 90.0), None):
        t = fires.smoke_threat(close, wind)
        assert t and t["near"] and t["arrival_h"] == 0.0 and t["direction"] == "west"
    far = [{"lat": 42.70 + 0.01 * k, "lon": 21.30, "frp": 20.0, "conf": "n"} for k in range(12)]
    assert fires.smoke_threat(far, (25.0, 90.0)) is None  # downwind and not close


def test_cams_confirmation_makes_the_fire_alert_likely_and_a_pm25_warning_raises_it():
    assert alerts.fire_alert(THREAT, NOW)["title"] == "Smoke may reach the city"
    watch = alerts.fire_alert(THREAT, NOW, {"components": ["smoke"], "pm25_peak_3h": 20.0})
    assert watch["level"] == "watch" and watch["title"] == "Smoke is likely over the city" and watch["confidence"] == "model"
    assert alerts.fire_alert(THREAT, NOW, {"components": ["smoke"], "pm25_peak_3h": 31.0})["level"] == "warning"
    assert alerts.fire_alert(THREAT, NOW, {"components": ["smoke_possible"], "pm25_peak_3h": 31.0})["level"] == "watch"


def test_a_pm_alert_with_a_single_cause_gets_advice_for_that_cause():
    pm = {"level": "warning", "audience": "everyone", "start": "2026-01-14T12:00", "end": "2026-01-14T20:00", "mean_pm25": 40.0, "peak_pm25": 50.0, "probability": 0.8}
    fc = {"hours": [{"time": f"2026-01-14T{h:02d}:00", "t2m": 0.0, "apparent": 0.0} for h in range(24)], "now_index": 9, "generated_at": NOW, "alert": pm, "source": "live"}
    local = alerts.build_alerts(fc, [], None, NOW, {"plume": {"kind": "local", "components": ["local"]}, "days": []})[0]
    assert local["cause"] == ["local"] and any(a["id"] == "burn" for a in local["actions"]) and not any(a["id"] == "mask" for a in local["actions"])
    smoke = alerts.build_alerts(fc, [], THREAT, NOW, {"plume": {"kind": "smoke", "components": ["smoke"], "pm25_peak_3h": 45.0}, "days": []})
    pm_alert = next(a for a in smoke if a["kind"] == "pm")
    assert pm_alert["cause"] == ["fire"] and any(a["id"] == "mask" for a in pm_alert["actions"])


# ---- transport and the green ticket ---------------------------------------------------------------------------------------------------------------
def _days(**kw):
    base = {"date": "2026-01-14", "pm10": 40.0, "pm25": 32.0, "usual_pm10": 26.0, "usual_pm25": 22.0, "worse_than_usual": True, "worse_by": "pm25"}
    return [{**base, **kw}, {**base, "date": "2026-01-15", "worse_than_usual": False, "worse_by": None}]


def test_a_worse_than_usual_day_suggests_public_transport_and_says_no_green_ticket_is_announced():
    a = alerts.transport_alerts(_days(), [], NOW)
    assert len(a) == 1 and a[0]["kind"] == "transport" and a[0]["level"] == "info" and a[0]["id"] == "transport-2026-01-14"
    assert "public transport" in a[0]["title"] and "none is announced" in a[0]["message"] and a[0]["actions"][0]["id"] == "transit"
    assert "usual January day: 22" in a[0]["message"]


def test_no_suggestion_on_an_ordinary_day_or_when_the_day_is_almost_over():
    assert alerts.transport_alerts(_days(worse_than_usual=False, worse_by=None), [], NOW) == []
    assert alerts.transport_alerts(_days(), [], "2026-01-14T18:00") == []


def test_an_announced_green_ticket_is_a_watch_that_replaces_the_suggestion_for_that_day():
    t = [{"date": "2026-01-14", "source": "https://example.org/announcement", "note": "Free buffer parking."}]
    a = alerts.transport_alerts(_days(), t, NOW)
    assert [x["id"] for x in a] == ["transport-2026-01-14"] and a[0]["level"] == "watch" and a[0]["title"].startswith("Green ticket today")
    assert "Free buffer parking." in a[0]["message"] and a[0]["detail"]["source_url"] == "https://example.org/announcement"
    assert alerts.transport_alerts([], [{"date": "2026-01-16", "source": "", "note": ""}], NOW)[0]["title"].startswith("Green ticket Friday")  # announced for a later day, even with no forecast row


def test_green_ticket_file_roundtrip_ignores_old_and_malformed_entries(tmp_path):
    p = tmp_path / "gt.json"
    assert green_ticket.announced("2026-01-14", path=p) == []
    green_ticket.add("2026-01-14", "https://example.org/a", "note", p)
    green_ticket.add("2026-01-16", path=p)
    green_ticket.add("2025-12-01", path=p)
    p.write_text(json.dumps({"entries": json.loads(p.read_text())["entries"] + [{"date": "nonsense"}, {"nodate": 1}]}))
    assert [e["date"] for e in green_ticket.announced("2026-01-14", 3, p)] == ["2026-01-14", "2026-01-16"]
    assert green_ticket.announced("2026-01-14", 1, p)[0]["source"] == "https://example.org/a"
    assert green_ticket.remove("2026-01-14", p) is True and green_ticket.remove("2026-01-14", p) is False


# ---- API ------------------------------------------------------------------------------------------------------------------------------------
async def _boom():
    raise RuntimeError("offline")


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "cache", Cache(fixture_dir=main.cache.fixture_dir))
    monkeypatch.setattr(main, "fetch_weather", _boom)
    monkeypatch.setattr(main, "fetch_air_quality", _boom)
    monkeypatch.setattr(main, "fetch_sensors", _boom)
    monkeypatch.setattr(Cache, "_save_fixture", lambda *a, **k: None)
    return TestClient(main.app)


def test_plumes_endpoint_has_no_live_feed_in_a_demo_scenario(client):
    j = client.get("/api/plumes?scenario=winter_inversion").json()
    assert j["plume"] is None and j["green_ticket"] == [] and j["sources"]["cams_dust"] == "scenario"
