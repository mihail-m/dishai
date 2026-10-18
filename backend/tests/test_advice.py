"""Heat, pollen and toggle cards. Synthetic hours: the live feed is quiet out of season."""
import pytest

from backend import advice
from backend.score import label_for

ZONE = {"zone": None, "illustrative": True, "label": "", "basis": ""}


def _hours(apparent=22.0, pollen=None, night_t2m=12.0):
    out = []
    for i in range(24):
        hot = 12 <= i <= 17
        out.append(
            {
                "time": f"2026-07-01T{i:02d}:00",
                "t2m": night_t2m if (i >= 22 or i < 6) else 25.0,
                "apparent": apparent if hot else 20.0,
                "pollen": pollen,
            }
        )
    return out


def _cards(profile="driver", level="good", hours=None, toggles=(), scores=None):
    hours = hours or _hours()
    cond = advice.conditions(hours, scores or [10.0] * 24, "pollen_allergy" in toggles)
    return advice.build_cards(profile, level, None, None, ZONE, cond, toggles), cond


def _ids(cards):
    return [c["id"] for c in cards]


def test_heat_levels_follow_feels_like():
    assert advice.heat_summary(_hours(apparent=22))["level"] == "none"
    assert advice.heat_summary(_hours(apparent=29))["level"] == "caution"
    assert advice.heat_summary(_hours(apparent=34))["level"] == "high"
    s = advice.heat_summary(_hours(apparent=40))
    assert s["level"] == "extreme" and (s["start_hhmm"], s["end_hhmm"]) == ("12:00", "17:00")
    assert advice.heat_summary(_hours(night_t2m=22))["warm_night"] is True


def test_pollen_summary_bands_and_missing_data():
    assert advice.pollen_summary(_hours(pollen=None))["available"] is False
    assert advice.pollen_summary(_hours(pollen={"birch": 0.0, "grass": 0.0}))["level"] == "none"
    s = advice.pollen_summary(_hours(pollen={"birch": 120.0, "grass": 6.0, "olive": None}))
    assert s["available"] and s["level"] == "high"
    assert [t["type"] for t in s["types"]] == ["birch", "grass"]  # worst first, null types ignored


def test_no_toggles_and_calm_day_adds_nothing():
    cards, _ = _cards()
    assert _ids(cards) == [c["id"] for c in advice._profile_cards("driver", "good", None, None, ZONE)]


def test_high_heat_and_pollen_reach_everyone_first():
    cards, _ = _cards(hours=_hours(apparent=36, pollen={"birch": 200.0}))
    assert _ids(cards)[:2] == ["heat", "pollen"] and all(c["severity"] == "action" for c in cards[:2])


def test_caution_heat_only_for_sensitive_users():
    hours = _hours(apparent=29)
    assert "heat" not in _ids(_cards(hours=hours)[0])
    assert "heat" in _ids(_cards(hours=hours, toggles=("heat_sensitive",))[0])


def test_each_toggle_always_gives_a_status_card():
    calm = _hours(pollen={"birch": 0.0})
    for toggle, card in [("pollen_allergy", "pollen"), ("heat_sensitive", "heat"), ("asthma", "asthma_air"), ("kids_or_elderly", "care_check"), ("outdoor_exercise", "exercise_window")]:
        cards, _ = _cards(hours=calm, toggles=(toggle,))
        c = next(x for x in cards if x["id"] == card)
        assert c["severity"] == "info", toggle
        assert c["measure"]["id"]


def test_pollen_toggle_without_feed_says_no_data():
    c = next(x for x in _cards(toggles=("pollen_allergy",))[0] if x["id"] == "pollen")
    assert "no data" in c["title"].lower()


def test_asthma_combines_triggers():
    hours = _hours(apparent=34, pollen={"grass": 30.0})
    cards, _ = _cards(level="warning", hours=hours, toggles=("asthma",))
    c = next(x for x in cards if x["id"] == "asthma_air")
    assert c["severity"] == "action" and all(w in c["body"] for w in ("trapped air", "heat", "high pollen"))


def test_exercise_window_avoids_the_bad_hours():
    hours = _hours(apparent=36)
    scores = [10.0] * 24
    w = advice.activity_window(hours, scores, False)
    assert int(w["start_hhmm"][:2]) not in range(11, 18)  # not in the 12-17 heat block
    allergic = advice.activity_window([{**h, "pollen": {"birch": 100.0 if 6 <= int(h["time"][11:13]) <= 9 else 0.0}} for h in hours], scores, True)
    assert int(allergic["start_hhmm"][:2]) > 9  # morning pollen peak is skipped


def test_levels_unchanged_helper():
    assert label_for(70) == "warning"  # guards the import used by the cards' level argument


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main
    from backend.cache import Cache

    async def boom():
        raise RuntimeError("offline")

    monkeypatch.setattr(main, "cache", Cache(fixture_dir=main.cache.fixture_dir))
    for fn in ("fetch_weather", "fetch_air_quality", "fetch_sensors"):
        monkeypatch.setattr(main, fn, boom)
    monkeypatch.setattr(Cache, "_save_fixture", lambda *a, **k: None)
    return TestClient(main.app)


def test_api_toggles_roundtrip_and_validation(client):
    url = "/api/advice?profile=driver&district=Serdika&scenario=winter_inversion"
    plain = client.get(url).json()
    r = client.get(url + "&toggles=asthma&toggles=outdoor_exercise&toggles=asthma").json()
    assert r["toggles"] == ["asthma", "outdoor_exercise"]
    assert {"asthma_air", "exercise_window"} <= {c["id"] for c in r["cards"]}
    assert len(r["cards"]) == len(plain["cards"]) + 2
    assert set(r["conditions"]) == {"heat", "pollen", "activity", "environment"}
    assert client.get(url + "&toggles=jetpack").status_code == 422


def test_heat_window_is_one_afternoon_even_when_the_slice_spans_two():
    # slice starts at 16:00 and runs into the next afternoon, so naive first/last hot hours would read "16:00 to 15:00"
    hours = []
    for i in range(24):
        hh = (16 + i) % 24
        hours.append({"time": f"2026-07-{1 if 16 + i < 24 else 2:02d}T{hh:02d}:00", "t2m": 25.0, "apparent": (36.0 if i >= 8 else 35.0) if 11 <= hh <= 16 else 20.0, "pollen": None})
    s = advice.heat_summary(hours)
    assert (s["start_hhmm"], s["end_hhmm"]) == ("11:00", "15:00")  # the slice ends at 15:00; never "16:00 to 15:00"


def test_demo_scenarios_drive_the_new_cards(client):
    heat = client.get("/api/advice?profile=driver&district=Serdika&scenario=summer_heatwave").json()
    assert heat["status"]["level"] == "good" and heat["conditions"]["heat"]["level"] in ("high", "extreme")
    assert heat["cards"][0]["id"] == "heat" and heat["cards"][0]["severity"] == "action"
    birch = client.get("/api/advice?profile=driver&district=Serdika&scenario=birch_season").json()
    assert birch["status"]["level"] == "good" and birch["conditions"]["pollen"]["level"] == "high"
    assert birch["conditions"]["pollen"]["types"][0]["type"] == "birch"
    assert birch["cards"][0]["id"] == "pollen"
    for sc in ("summer_heatwave", "birch_season"):
        fc = client.get(f"/api/forecast?scenario={sc}").json()
        assert fc["scenario"] == sc and len(fc["hours"]) == 48 and not fc["alert"]["active"]
        assert client.get(f"/api/sensors?scenario={sc}").json()["simulated"] is True
    assert client.get("/api/forecast?scenario=nope").status_code == 422


def test_construction_card_only_for_sensitive_users_in_busy_districts():
    data = {"high_from_pct": 1.0, "districts": {"Studentski": {"share_pct": 6.1, "n_sites": 272}, "Bankya": {"share_pct": 0.06, "n_sites": 1}}}
    assert advice.construction_context(data, "Studentski") == {"share_pct": 6.1, "n_sites": 272}
    assert advice.construction_context(data, "Bankya") is None and advice.construction_context(None, "Studentski") is None
    assert advice.construction_context(data, "Unknown") is None
