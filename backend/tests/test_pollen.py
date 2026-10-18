"""Pollen forecast, typical season, UV / ozone / dust cards, species triggers, symptom check-ins and pollen-aware routing."""
from datetime import date

import numpy as np
import pytest

from backend import advice, pollen, symptoms
from backend.cai import config as C

ZONE = {"zone": None, "illustrative": True, "label": "", "basis": ""}
CAL = {"species": {"ragweed": {"season": {"start_doy": 210, "end_doy": 264}}, "birch": {"season": {"start_doy": 88, "end_doy": 132}}, "olive": {}}}


def _hours(n=48, **fields):
    out = []
    for i in range(n):
        d = 1 + i // 24
        h = {"time": f"2026-07-{d:02d}T{i % 24:02d}:00", "t2m": 25.0, "apparent": 25.0, "pollen": None}
        h.update({k: (v(i) if callable(v) else v) for k, v in fields.items()})
        out.append(h)
    return out


def test_forecast_daily_peaks_and_levels():
    hrs = _hours(pollen=lambda i: {"birch": 120.0 if i % 24 == 10 else 2.0, "grass": 0.0, "olive": None})
    f = pollen.forecast(hrs, 0)
    assert f["available"] and [s["type"] for s in f["species"]] == list(advice.POLLEN_TYPES)
    birch = next(s for s in f["species"] if s["type"] == "birch")
    assert birch["days"][0] == {"date": "2026-07-01", "peak": 120.0, "peak_hhmm": "10:00", "mean": birch["days"][0]["mean"], "level": "high"}
    assert len(birch["days"]) == 2 and len(birch["hourly"]) == 48 and birch["now"]["level"] == "low"
    olive = next(s for s in f["species"] if s["type"] == "olive")
    assert olive["now"] is None and olive["days"][0]["level"] == "none"
    assert pollen.forecast(_hours(), 0) == {"available": False, "species": []}


def test_environment_levels_follow_who_and_eu_anchors():
    hrs = _hours(uv=lambda i: 9.0 if i == 12 else 0.0, ozone=lambda i: 190.0 if i == 14 else 40.0, dust=60.0)
    env = pollen.environment(hrs)
    assert env["uv"]["days"][0]["level"] == "very high" and env["ozone"]["days"][0]["level"] == "high" and env["dust"]["days"][0]["level"] == "notable"
    assert pollen.environment(_hours())["uv"]["available"] is False
    s = pollen.environment_summary(hrs)
    assert s["uv"]["level"] == 3 and s["ozone"]["peak_hhmm"] == "14:00" and pollen.environment_summary(_hours())["uv"] is None


def test_season_status_in_upcoming_and_over():
    assert pollen.season_status(CAL, "ragweed", date(2026, 8, 20))["status"] == "in_season"
    up = pollen.season_status(CAL, "ragweed", date(2026, 7, 20))
    assert up["status"] == "upcoming" and up["in_days"] == 210 - 201 and up["start"] == "29 Jul"
    over = pollen.season_status(CAL, "ragweed", date(2026, 10, 18))
    assert over["status"] == "over" and over["in_days"] == 210 + 365 - 291
    assert pollen.season_status(CAL, "olive", date(2026, 5, 1))["status"] == "none" and pollen.season_status(None, "birch", date(2026, 5, 1))["status"] == "none"


def test_baked_calendar_is_consistent():
    cal = pollen.load_calendar()
    assert cal and set(cal["species"]) == set(advice.POLLEN_TYPES)
    for k, v in cal["species"].items():
        assert len(v["bins"]) == 36 and v["years"]
        if "season" in v:
            assert 1 <= v["season"]["start_doy"] < v["season"]["end_doy"] <= 365
    assert cal["species"]["birch"]["season"]["start_doy"] < cal["species"]["grass"]["season"]["start_doy"] < cal["species"]["ragweed"]["season"]["start_doy"]


def test_species_triggers_restrict_pollen_and_the_outdoor_slot():
    hrs = _hours(24, pollen={"birch": 200.0, "grass": 0.0})
    allp = advice.conditions(hrs, [10.0] * 24, True)
    assert allp["pollen"]["level"] == "high" and allp["pollen"]["triggers"] == []
    mine = advice.conditions(hrs, [10.0] * 24, True, ["grass"])
    assert mine["pollen"]["level"] == "none" and mine["pollen"]["triggers"] == ["grass"]
    # without the allergy toggle the species are ignored
    assert advice.conditions(hrs, [10.0] * 24, False, ["grass"])["pollen"]["level"] == "high"
    only_grass_peak = _hours(24, pollen=lambda i: {"birch": 0.0, "grass": 30.0 if 6 <= i <= 9 else 0.0})
    w = advice.activity_window(only_grass_peak, [10.0] * 24, True, {"birch"})
    assert int(w["start_hhmm"][:2]) <= 9  # grass hours are ignored for a birch-only user, so the morning stays open


def _cards(env, toggles=(), season=None, hrs=None):
    cond = advice.conditions(hrs or _hours(24, pollen={"birch": 0.0}), [10.0] * 24, "pollen_allergy" in toggles)
    cond["environment"], cond["season"] = env, season or []
    return {c["id"]: c for c in advice.build_cards("driver", "good", None, None, ZONE, cond, toggles)}


def test_uv_ozone_dust_cards_have_thresholds_and_audiences():
    env = {"uv": {"peak": 9.0, "level": 3, "peak_hhmm": "13:00"}, "ozone": {"peak": 120.0, "level": 1, "peak_hhmm": "16:00"}, "dust": {"peak": 70.0, "level": 1, "peak_hhmm": "15:00"}}
    c = _cards(env)
    assert c["uv"]["severity"] == "action" and "ozone" not in c and "dust" not in c  # elevated ozone / notable dust only for sensitive users
    c = _cards(env, ("asthma",))
    assert c["ozone"]["severity"] == "info" and c["dust"]["severity"] == "info"
    env["ozone"], env["dust"] = {"peak": 200.0, "level": 2, "peak_hhmm": "16:00"}, {"peak": 120.0, "level": 2, "peak_hhmm": "15:00"}
    c = _cards(env)
    assert c["ozone"]["severity"] == "action" and c["dust"]["severity"] == "action"
    assert "uv" not in _cards({"uv": {"peak": 2.0, "level": 0, "peak_hhmm": "13:00"}})


def test_season_outlook_and_cross_reaction_cards_only_for_allergic_users():
    season = pollen.seasons(CAL, date(2026, 7, 20))
    assert "pollen_season" not in _cards({}, season=season) and "pollen_season" not in _cards({}, ("asthma",), season)
    c = _cards({}, ("pollen_allergy",), season)
    assert "ragweed" in c["pollen_season"]["title"].lower() and "9 days" in c["pollen_season"]["title"]
    hi = _hours(24, pollen={"birch": 200.0})
    c = _cards({}, ("pollen_allergy",), season, hi)
    assert "cross_reaction" in c and "apple" in c["cross_reaction"]["body"] and "pollen_season" not in c  # high pollen now: no "starts in" nag


def test_symptom_store_summary_and_cap(tmp_path):
    f = tmp_path / "s.jsonl"
    day = date(2026, 4, 15)
    for i in range(25):
        assert symptoms.record(day, "Sredets", "bad" if i < 10 else "none", "high", "birch", f)
    symptoms.record(day, "Sredets", "mild", "low", "grass", f)
    f.write_text(f.read_text() + "{torn line\n")
    s = symptoms.summary(date(2026, 4, 16), 30, f)
    hi = next(x for x in s["levels"] if x["level"] == "high")
    assert hi["n"] == 25 and hi["bad_share"] == 0.4 and hi["enough"]
    lo = next(x for x in s["levels"] if x["level"] == "low")
    assert lo["n"] == 1 and lo["bad_share"] is None and not lo["enough"]  # no share is claimed from one report
    assert s["reports"] == 26 and s["daily"][0]["date"] == "2026-04-15"
    assert symptoms.summary(date(2027, 1, 1), 30, f)["reports"] == 0
    monkey = symptoms.MAX_PER_DAY
    try:
        symptoms.MAX_PER_DAY = 26
        assert symptoms.record(day, "Sredets", "none", "none", None, f) is False
    finally:
        symptoms.MAX_PER_DAY = monkey


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from backend import main
    from backend.cache import Cache

    async def boom():
        raise RuntimeError("offline")

    monkeypatch.setattr(main, "cache", Cache(fixture_dir=main.cache.fixture_dir))
    for fn in ("fetch_weather", "fetch_air_quality", "fetch_sensors"):
        monkeypatch.setattr(main, fn, boom)
    monkeypatch.setattr(Cache, "_save_fixture", lambda *a, **k: None)
    monkeypatch.setattr(symptoms, "STORE", tmp_path / "symptoms.jsonl")
    monkeypatch.setattr(symptoms.record, "__defaults__", (tmp_path / "symptoms.jsonl",))
    monkeypatch.setattr(symptoms.summary, "__defaults__", (30, tmp_path / "symptoms.jsonl"))
    return TestClient(main.app)


def test_pollen_endpoint_scenario_and_calendar(client):
    r = client.get("/api/pollen?scenario=birch_season").json()
    assert r["pollen"]["available"] and r["today"].endswith("-04-15")
    birch = next(s for s in r["pollen"]["species"] if s["type"] == "birch")
    assert birch["days"][0]["level"] == "high" and len(birch["days"]) == 2
    assert next(s for s in r["season"] if s["type"] == "birch")["status"] == "in_season"
    hot = client.get("/api/pollen?scenario=summer_heatwave").json()
    assert hot["environment"]["uv"]["days"][0]["level"] in ("very high", "extreme") and hot["environment"]["dust"]["days"][1]["level"] == "notable"
    cal = client.get("/api/pollen/calendar").json()
    assert cal["available"] and len(cal["species"]["birch"]["levels"]) == 36 and 0 <= cal["today_bin"] < 36
    assert client.get("/api/pollen?scenario=nope").status_code == 422


def test_advice_species_and_season_in_response(client):
    url = "/api/advice?profile=driver&district=Serdika&scenario=birch_season&toggles=pollen_allergy"
    mine = client.get(url + "&species=grass&species=grass").json()
    assert mine["species"] == ["grass"] and mine["conditions"]["pollen"]["triggers"] == ["grass"]
    assert mine["conditions"]["pollen"]["level"] != "high"  # birch is high in this demo, but this user reacts to grass
    allp = client.get(url).json()
    assert allp["conditions"]["pollen"]["level"] == "high" and {"pollen", "cross_reaction"} <= {c["id"] for c in allp["cards"]}
    assert client.get(url + "&species=cactus").status_code == 422
    base = "/api/advice?profile=driver&district=Serdika&scenario=summer_heatwave"
    assert "uv" in {c["id"] for c in client.get(base).json()["cards"]}  # UV is for everyone; elevated ozone only for sensitive users
    assert {"uv", "ozone"} <= {c["id"] for c in client.get(base + "&toggles=asthma").json()["cards"]}


def test_symptom_endpoint_stores_live_only_and_validates(client):
    assert client.post("/api/symptoms", json={"district": "Serdika", "severity": "bad", "scenario": "birch_season"}).json()["stored"] is False
    r = client.post("/api/symptoms", json={"district": "Serdika", "severity": "mild", "species": ["birch"]})
    assert r.status_code == 200 and r.json()["stored"] is True
    assert client.get("/api/symptoms/summary").json()["reports"] == 1
    assert client.post("/api/symptoms", json={"district": "Serdika", "severity": "dire"}).status_code == 422
    assert client.post("/api/symptoms", json={"district": "Nowhere", "severity": "none"}).status_code == 404
    assert client.post("/api/symptoms", json={"district": "Serdika", "severity": "none", "name": "x"}).status_code == 422  # nothing personal accepted


def test_allergy_weight_is_documented_assumption():
    assert 0 < C.POLLEN_GREEN_WEIGHT <= 1
