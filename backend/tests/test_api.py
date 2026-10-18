"""API tests. Upstream calls are forced to fail so the fixture fallback is what is tested."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.cache import Cache


async def _boom():
    raise RuntimeError("offline")


@pytest.fixture()
def client(monkeypatch, tmp_path):
    fixtures = main.cache.fixture_dir
    monkeypatch.setattr(main, "cache", Cache(fixture_dir=fixtures))
    monkeypatch.setattr(main, "fetch_weather", _boom)
    monkeypatch.setattr(main, "fetch_air_quality", _boom)
    monkeypatch.setattr(main, "fetch_sensors", _boom)
    # do not let a failed run rewrite fixtures
    monkeypatch.setattr(Cache, "_save_fixture", lambda *a, **k: None)
    main.submitted.clear()
    main.submitted_eligible.clear()
    return TestClient(main.app)


def test_forecast_offline_uses_fixture(client):
    r = client.get("/api/forecast").json()
    assert r["source"] == "fixture"
    assert len(r["hours"]) >= 48  # 48 in the bundled fixture, 96 once a live fetch has rewritten it
    assert r["hours"][0]["time"].startswith(main.sofia_now().strftime("%Y-%m-%d"))  # rebased to today
    assert all(0 <= h["score"] <= 100 for h in r["hours"])


def test_scenario_has_strong_night_inversion(client):
    r = client.get("/api/forecast?scenario=winter_inversion").json()
    assert r["source"] == "scenario" and len(r["hours"]) == 48
    assert r["alert"]["active"] and r["alert"]["basis"] == "model" and r["alert"]["level"] == "warning"
    assert all(h["pm25_model"] is not None and h["level"] in ("good", "watch", "warning") for h in r["hours"])
    assert max(h["score"] for h in r["hours"]) > 90
    assert max(h["pm25"] for h in r["hours"]) > 45  # a real, moderate episode: the raw regional series reaches the 50s
    assert min(h["score"] for h in r["hours"]) < 60  # daytime relief


def test_bad_scenario_rejected(client):
    assert client.get("/api/forecast?scenario=nope").status_code == 422


def test_sensors_offline_and_scenario(client):
    live = client.get("/api/sensors").json()
    assert live["source"] == "fixture" and live["count"] > 0
    assert all(isinstance(s["pm25"], float) for s in live["sensors"])
    sim = client.get("/api/sensors?scenario=winter_inversion").json()
    assert sim["simulated"] and sim["median_pm25"] > live["median_pm25"]


def test_districts_geojson_and_elevation_factor(client):
    fc = client.get("/api/districts?scenario=winter_inversion").json()
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) >= 8
    by = {f["properties"]["name"]: f["properties"] for f in fc["features"]}
    floor = max(by.values(), key=lambda p: p["elevation_factor"])
    foot = min(by.values(), key=lambda p: p["elevation_factor"])
    assert floor["elevation_factor"] > 1.0 > foot["elevation_factor"]
    assert floor["score"] >= foot["score"]


@pytest.mark.parametrize("profile", ["driver", "wood_heater", "vulnerable"])
def test_advice_cards_cite_measures(client, profile):
    r = client.get(f"/api/advice?profile={profile}&district=Serdika&scenario=winter_inversion").json()
    assert 2 <= len(r["cards"]) <= 4
    assert all(c["measure"]["id"] and c["measure"]["label"] for c in r["cards"])
    assert r["status"]["level"] == "warning"


def test_wood_heater_has_zone_and_renovation_card(client):
    r = client.get("/api/advice?profile=wood_heater&district=Sredets&scenario=winter_inversion").json()
    ids = {c["id"] for c in r["cards"]}
    assert {"zone_lookup", "renovation"} <= ids
    assert r["zone"]["zone"] == "inner" and r["zone"]["illustrative"]
    outside = client.get("/api/advice?profile=wood_heater&district=Serdika&scenario=winter_inversion").json()
    assert outside["zone"]["zone"] is None
    assert any(c["measure"]["id"] == "C.3.2" for c in r["cards"])


def test_advice_validation(client):
    assert client.get("/api/advice?profile=cyclist&district=Serdika").status_code == 422
    assert client.get("/api/advice?profile=driver&district=Atlantis").status_code == 404


def test_lead_roundtrip_and_privacy(client):
    before = client.get("/api/dashboard?scenario=winter_inversion").json()
    n0 = next(r["leads"] for r in before["leads_by_district"] if r["district"] == "Lyulin")
    ok = client.post("/api/leads", json={"district": "lyulin", "heating_type": "wood", "likely_eligible": True})
    assert ok.status_code == 200 and ok.json()["district_total"] == n0 + 1
    after = client.get("/api/dashboard?scenario=winter_inversion").json()
    row = next(r for r in after["leads_by_district"] if r["district"] == "Lyulin")
    assert row["leads"] == n0 + 1 and row["submitted_leads"] == 1
    assert after["leads_submitted"] == 1
    # anything beyond district + heating type is rejected, so no personal data can be stored
    assert client.post("/api/leads", json={"district": "Lyulin", "heating_type": "wood", "address": "ul. X 1"}).status_code == 422
    assert client.post("/api/leads", json={"district": "Lyulin", "heating_type": "magic"}).status_code == 422
    assert client.post("/api/leads", json={"district": "Atlantis", "heating_type": "wood"}).status_code == 404


def test_dashboard_shape(client):
    d = client.get("/api/dashboard?scenario=winter_inversion").json()
    assert len(d["hotspots"]) == 5
    idx = [h["hotspot_index"] for h in d["hotspots"]]
    assert idx == sorted(idx, reverse=True)
    assert d["street_washing"] and all(s["sample"] for s in d["street_washing"])
    assert d["sensors_active"] > 0


# ---- PLAN2 endpoints --------------------------------------------------------------------------------
def test_cai_geometry_binary_aligns_with_values(client):
    import struct

    g = client.get("/api/cai/geometry")
    assert g.status_code == 200 and g.headers["content-type"] == "application/octet-stream"
    n, p = struct.unpack("<II", g.content[:8])
    assert len(g.content) == 8 + 4 * (n + 1) + 8 * p + n  # offsets u32, lonlat f32 x2, class u8
    v = client.get("/api/cai/values?scenario=winter_inversion").json()
    assert len(v["cai"]) == n and len(v["pm25"]) == n
    assert min(v["cai"]) >= 0 and max(v["cai"]) <= 100
    assert v["heat_weight"] == 0  # winter: heat gate closed


def test_cai_geojson_and_edge_popover(client):
    fc = client.get("/api/cai?bbox=23.31,42.69,23.33,42.70&limit=50&scenario=winter_inversion").json()
    assert 0 < len(fc["features"]) <= 50
    f = fc["features"][0]
    assert f["geometry"]["type"] == "LineString"
    assert {"cai", "pm25", "background", "ventilation", "greenery", "local_correction"} <= set(f["properties"])
    assert "traffic" not in f["properties"]  # no assumed traffic term
    e = client.get(f"/api/cai/edge?id={f['properties']['id']}&scenario=winter_inversion").json()
    assert e["cai"] == f["properties"]["cai"] and e["components_ugm3"]["background"] > 0
    assert client.get("/api/cai/edge?id=99999999").status_code == 404
    assert client.get("/api/cai?bbox=oops").status_code == 422


def test_route_endpoint_without_google_key(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_ROUTES_SERVER_KEY", raising=False)
    r = client.get("/api/route?from=42.6934,23.3331&to=42.7120,23.3230&lambda=2&scenario=winter_inversion").json()
    assert set(r["routes"]) >= {"fastest", "balanced", "cleanest", "chosen"}
    assert r["google_status"] == "no_key" and r["google_baseline"] is None
    assert r["snapped"]["used"] is False and r["snapped"]["polyline"] == r["routes"]["cleanest"]["polyline"]
    assert len(r["snapped"]["waypoints"]) <= 8
    assert "google.com/maps/dir" in r["open_in_google_maps_clean"]
    assert r["routes"]["cleanest"]["time_min"] <= r["routes"]["fastest"]["time_min"] * 1.3 + 1e-6


def test_pollen_aware_route_trades_greenery_for_pollen(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_ROUTES_SERVER_KEY", raising=False)
    q = "/api/route?from=42.6934,23.3331&to=42.7120,23.3230&lambda=2&include_google=false&scenario=birch_season"
    plain = client.get(q).json()
    allergic = client.get(q + "&allergy=true&species=birch").json()
    assert plain["allergy"] is None and allergic["allergy"]["level"] >= 1 and allergic["allergy"]["species"] == ["birch"]
    assert allergic["confidence"] is None  # the PM-based "clearly cleaner" test does not describe an allergy-weighted choice
    for r in (plain, allergic):
        assert all(0 <= v["mean_green_pct"] <= 100 for v in r["routes"].values())
    # with birch pollen up, the pollen-aware cleanest route has no more greenery than the plain cleanest one
    assert allergic["routes"]["cleanest"]["mean_green_pct"] <= plain["routes"]["cleanest"]["mean_green_pct"] + 1e-6
    quiet = client.get(q.replace("birch_season", "winter_inversion") + "&allergy=true").json()
    assert quiet["allergy"]["level"] == 0  # no pollen, no change of weighting


def test_route_validation_errors(client):
    assert client.get("/api/route?from=abc&to=42.7,23.3").status_code == 422
    assert client.get("/api/route?from=95,23&to=42.7,23.3").status_code == 422
    assert client.get("/api/route?from=43.5,23.3&to=42.7,23.3").status_code == 422  # far from the network
    assert client.get("/api/route?from=42.69,23.33&to=42.70,23.32&mode=car").status_code == 422
    assert client.get("/api/route?from=42.69,23.33&to=42.70,23.32&lambda=9").status_code == 422


def test_route_with_google_baseline_and_snap(client, monkeypatch):
    """Mocked Routes API: baseline is scored with our dose formula, snapped route replaces our polyline."""
    from backend import google_routes as gr

    own = client.get("/api/route?from=42.6934,23.3331&to=42.7120,23.3230&scenario=winter_inversion").json()
    line = [(lat, lng) for lng, lat in own["routes"]["fastest"]["polyline"]]
    calls = []

    async def fake_walk(o, d, intermediates=None):
        calls.append(intermediates)
        return {"polyline": line, "duration_s": 1500.0, "distance_m": own["routes"]["fastest"]["distance_m"]}

    monkeypatch.setenv("GOOGLE_ROUTES_SERVER_KEY", "x")
    monkeypatch.setattr(gr, "walk", fake_walk)
    r = client.get("/api/route?from=42.6934,23.3331&to=42.7120,23.3230&scenario=winter_inversion").json()
    assert r["google_status"] == "ok" and r["google_baseline"]["snapped_share"] > 0.9
    assert r["google_baseline"]["dose"] == pytest.approx(r["routes"]["fastest"]["dose"], rel=0.15)
    assert calls[0] is None and calls[1] and len(calls[1]) <= 8  # baseline has no waypoints, snapped has <= 8
    assert r["snapped"]["used"] is True and r["snapped"]["eta_min"] == 25.0


def test_google_failure_falls_back_silently(client, monkeypatch):
    from backend import google_routes as gr

    async def dead(o, d, intermediates=None):
        return None

    monkeypatch.setenv("GOOGLE_ROUTES_SERVER_KEY", "x")
    monkeypatch.setattr(gr, "walk", dead)
    r = client.get("/api/route?from=42.6934,23.3331&to=42.7120,23.3230&scenario=winter_inversion").json()
    assert r["google_status"] == "error" and r["snapped"]["used"] is False
    assert r["snapped"]["polyline"] == r["routes"]["cleanest"]["polyline"]


def test_network_endpoint_and_validation_scenario_warning(client):
    n = client.get("/api/network?scenario=winter_inversion").json()
    assert n["n_od"] == 400 and n["chokepoints"] and n["trips"] and n["segments"]
    assert n["chokepoints"][0]["measures"][0]["id"] in ("C.1.1", "A.4.7", "A.4.3", "A.4.4")
    v = client.get("/api/validation?scenario=winter_inversion").json()
    assert "not a real validation" in v["warning"]
    live = client.get("/api/validation").json()
    assert live["n"] > 50 and live["verdict"] in ("weak", "moderate", "reasonable")
    assert live["daily_mean"] and "traffic_gain_sensitivity" not in live["daily_mean"]


def test_names_districts_and_labels_in_api(client):
    e = client.get("/api/cai/edge?id=500&scenario=winter_inversion").json()
    assert {"street", "street_bg", "near_street", "district", "geometry"} <= set(e) and len(e["geometry"]) >= 2
    r = client.get("/api/route?from=42.6934,23.3331&to=42.7120,23.3230&scenario=winter_inversion").json()
    assert r["from_label"] and r["to_label"] and "," in r["from_label"] + r["to_label"]
    n = client.get("/api/network?scenario=winter_inversion").json()
    for c in n["chokepoints"]:
        assert c["street"].isascii() and c["district"]  # Latin name in the UI; only in-city chokepoints are ranked
        assert c["street_bg"]
    assert [c["rank"] for c in n["chokepoints"]] == list(range(1, len(n["chokepoints"]) + 1))
    assert 5 <= len(n["chokepoints"]) <= 15


def test_areas_endpoint_air_and_heat(client):
    a = client.get("/api/cai/areas?scenario=winter_inversion&hour=18").json()
    c = a["cells"]
    assert a["metric"] == "air" and len(c["lon"]) > 1000 and a["thresholds"]["clean"] > a["thresholds"]["dirty"]
    assert len(c["lon"]) == len(c["mean"]) == len(c["pm25"]) == len(c["street_m"])
    h = client.get("/api/cai/areas?metric=heat").json()
    assert h["metric"] == "heat" and h["thresholds"]["hot"] > 0 > h["thresholds"]["cool"] and "snapshot" not in h["note"]
    assert len(h["cells"]["anomaly"]) == len(h["cells"]["mean"]) == len(h["cells"]["air"]) == len(h["cells"]["offset"])
    assert client.get("/api/cai/areas?metric=noise").status_code == 422
    assert client.get("/api/cai/areas?mode=car").status_code == 422


def test_cycling_routes_and_network(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_ROUTES_SERVER_KEY", "x")  # even with a key, cycling never calls Google
    r = client.get("/api/route?from=42.6934,23.3331&to=42.7120,23.3230&mode=bike&scenario=winter_inversion").json()
    w = client.get("/api/route?from=42.6934,23.3331&to=42.7120,23.3230&mode=walk&scenario=winter_inversion").json()
    assert r["mode"] == "bike" and r["google_status"] == "n/a"
    assert r["routes"]["fastest"]["time_min"] < w["routes"]["fastest"]["time_min"] * 0.5
    n = client.get("/api/network?mode=bike&scenario=winter_inversion&hour=18").json()
    assert n["mode"] == "bike" and n["n_od"] == 400
    ch = [s for s in n["segments"] if s["channel"]]
    assert len(ch) == n["n_channel_segments"] > 0
    assert all(s["usage_clean"] >= 2 and s["cai"] >= n["channel_cai_cut"] for s in ch)  # channels are well used AND cleaner than the 60th pct


def test_preloaded_forecast_is_next_24_hours_across_midnight(client, monkeypatch):
    import base64
    from datetime import datetime
    monkeypatch.setattr(main, "sofia_now", lambda: datetime(2026, 10, 17, 23, 35, tzinfo=main.TZ))
    response = client.post("/api/forecast/refresh?scenario=winter_inversion")
    assert response.status_code == 200
    window = response.json()
    assert window["hours"] == list(range(23, 47))
    assert window["forecast"]["now_index"] == 0
    assert window["forecast"]["hours"][0]["time"] == "2026-10-17T23:00"
    assert window["forecast"]["hours"][-1]["time"] == "2026-10-18T22:00"
    assert len(window["values"]) == 24
    for value in window["values"]:
        scores = base64.b64decode(value["cai_base64"])
        assert len(scores) > 100000 and max(scores) <= 100
    edge = client.get(f"/api/cai/edge?id=500&hour=46&scenario=winter_inversion&forecast_id={window['id']}").json()
    assert edge["time"] == window["values"][-1]["time"]
    assert edge["cai"] == base64.b64decode(window["values"][-1]["cai_base64"])[500]
    assert client.get("/api/forecast/window?scenario=winter_inversion").json()["id"] == window["id"]


def test_refresh_bypasses_cache_and_old_snapshot_remains_consistent(client, monkeypatch):
    import copy
    calls = []
    weather = main.cache._load_fixture("open_meteo_forecast")
    air = main.cache._load_fixture("open_meteo_air_quality")
    # Match fixture dates to today, just as offline fallback does.
    weather["hourly"] = main._rebase(weather["hourly"], main.sofia_now())
    air["hourly"] = main._rebase(air["hourly"], main.sofia_now())
    async def fetch_weather():
        calls.append("weather")
        return copy.deepcopy(weather)
    async def fetch_air():
        calls.append("air")
        return copy.deepcopy(air)
    monkeypatch.setattr(main, "fetch_weather", fetch_weather)
    monkeypatch.setattr(main, "fetch_air_quality", fetch_air)
    first = client.post("/api/forecast/refresh").json()
    old_edge = client.get(f"/api/cai/edge?id=500&hour={first['hours'][0]}&forecast_id={first['id']}").json()
    air["hourly"]["pm2_5"] = [99.0] * len(air["hourly"]["time"])
    second = client.post("/api/forecast/refresh").json()
    assert calls == ["weather", "air", "weather", "air"]
    assert first["id"] != second["id"]
    assert second["forecast"]["hours"][0]["pm25"] == 99
    assert first["forecast"]["hours"][0]["pm25"] != 99
    assert client.get(f"/api/cai/edge?id=500&hour={first['hours'][0]}&forecast_id={first['id']}").json() == old_edge


def test_snapshot_validation_and_network_hour(client):
    assert client.get("/api/cai/edge?id=0&forecast_id=missing").status_code == 409
    window = client.post("/api/forecast/refresh?scenario=winter_inversion").json()
    assert client.get(f"/api/cai/edge?id=0&forecast_id={window['id']}").status_code == 409
    hour = window["hours"][-1]
    network = client.get(f"/api/network?hour={hour}&scenario=winter_inversion&forecast_id={window['id']}").json()
    assert network["hour"] == hour
    assert network["time"] == window["forecast"]["hours"][-1]["time"]


def test_route_preload_does_not_call_google(client, monkeypatch):
    from backend import google_routes as gr
    monkeypatch.setenv("GOOGLE_ROUTES_SERVER_KEY", "x")
    async def forbidden(*args, **kwargs):
        pytest.fail("Preloading must not make billed Google requests")
    monkeypatch.setattr(gr, "walk", forbidden)
    window = client.post("/api/forecast/refresh?scenario=winter_inversion").json()
    response = client.get(f"/api/route?from=42.6934,23.3331&to=42.7120,23.3230&scenario=winter_inversion&include_google=false&forecast_id={window['id']}")
    assert response.status_code == 200
    assert response.json()["google_available"] is True
    assert response.json()["routes"]["cleanest"]["polyline"]


def test_expiring_upstream_cache_keeps_stale_fallback(tmp_path):
    import asyncio
    cache = Cache(fixture_dir=tmp_path)
    async def good():
        return {"saved": True}
    async def check():
        assert (await cache.get("weather", good))[1] == "live"
        cache.expire(("weather",))
        value, source = await cache.get("weather", _boom)
        assert value == {"saved": True} and source == "stale"
    asyncio.run(check())


def test_endpoint_follows_the_hour_and_the_scale(client):
    day = client.get("/api/cai/areas?metric=heat&scenario=summer_heatwave&hour=15").json()
    night = client.get("/api/cai/areas?metric=heat&scenario=summer_heatwave&hour=3").json()
    assert day["hour"] == 15 and night["hour"] == 3
    assert day["city"]["feels"] > night["city"]["feels"] + 4          # the hour matters now (the old layer was one fixed snapshot)
    assert day["stats"]["median"] > night["stats"]["median"] + 4
    assert day["city"]["level"] in ("high", "extreme")                 # a simulated July afternoon is hot
    assert day["scale"] == "absolute" and day["centre"] == 18.0
    rel = client.get("/api/cai/areas?metric=heat&scenario=summer_heatwave&hour=15&scale=relative").json()
    assert rel["centre"] == rel["reference"] and abs(np.median(rel["cells"]["anomaly"])) < 2
    assert client.get("/api/cai/areas?metric=heat&scale=bogus").status_code == 422


def test_live_values_are_air_temperatures_not_a_summer_snapshot(client):
    """Regression for the root cause: in cool weather the map must not show 30-48 C 'surface' values."""
    h = client.get("/api/cai/areas?metric=heat&scenario=winter_inversion&hour=18").json()
    assert h["stats"]["max"] < 20, h["stats"]
