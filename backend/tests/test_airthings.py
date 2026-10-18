"""Airthings pilot stations as extra sensors: parsing, filtering, merge into /api/sensors, and graceful failure."""
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import airthings, main
from backend.cache import Cache

NOW = datetime(2026, 10, 17, 18, 40, tzinfo=timezone.utc)
PINS = [
    {"id": 1, "deviceId": "AT1", "name": "Парк „Хиподрума“ - При повишена влажност е възможно да има отклонения", "latitude": 42.681, "longitude": 23.296},
    {"id": 2, "deviceId": "AT2", "name": "Dragalevtsi", "latitude": 42.629, "longitude": 23.309},
    {"id": 3, "deviceId": "AT3", "name": "Ohrid school", "latitude": 41.136, "longitude": 20.811},  # outside Sofia
    {"id": 4, "deviceId": "AT4", "name": "Old reading", "latitude": 42.70, "longitude": 23.33},
    {"id": 5, "deviceId": "AT5", "name": "No value", "latitude": 42.70, "longitude": 23.34},
]


def series(did, pm25, pm10=None, time="2026-10-17T17:00:00Z"):
    return {"name": "AirThingsAggregateSensorData", "tags": {"deviceId": did, "organization": "SofiaMunicipality"},
            "columns": ["time", "PM2", "PM10"], "values": [[time, pm25, pm10]]}


SERIES = [series("AT1", 1.3, 4.0), series("AT2", 3.99, 6.6), series("AT3", 5.0), series("AT4", 4.0, time="2026-10-17T09:00:00Z"),
          series("AT5", None), series("AT-UNKNOWN", 2.0)]


def test_normalise_joins_filters_and_shapes_records():
    out = airthings.normalise(PINS, SERIES, now=NOW)
    assert [r["id"] for r in out] == ["AT1", "AT2"]  # outside Sofia, stale, null and unknown-station records are dropped
    r = out[0]
    assert r["source"] == "airthings" and r["pm25"] == 1.3 and r["pm10"] == 4.0
    assert r["name"] == "Парк „Хиподрума“"  # the shared humidity disclaimer is cut off
    assert r["timestamp"] == "2026-10-17 17:00:00" and (r["lat"], r["lon"]) == (42.681, 23.296)


@pytest.mark.parametrize("raw,clean", [
    ("Парк „Хиподрума“ - При повишена влажност е възможно да има отклонения", "Парк „Хиподрума“"),
    ("бул. „Александър Стамболийски“, Западен парк -При повишена влажност е възможно", "бул. „Александър Стамболийски“, Западен парк"),
    ("РА „Красна поляна“- При повишена влажност е възможно", "РА „Красна поляна“"),
    ("район „Искър“", "район „Искър“"), ("Station with - a dash", "Station with - a dash"), ("", ""),
])
def test_station_name_cleaning(raw, clean):
    assert airthings._clean_name(raw) == clean


def test_normalise_survives_malformed_and_implausible_records():
    bad = [{"tags": {}}, {"tags": {"deviceId": "AT1"}, "columns": ["time", "PM2"], "values": []}, series("AT1", -3.0), series("AT2", 9999.0),
           series("AT2", "n/a"), series("AT1", 2.0, time="not a time")]
    assert airthings.normalise(PINS, bad, now=NOW) == []
    assert airthings.normalise([], SERIES, now=NOW) == []


def test_fetch_sends_identifying_user_agent_and_both_fields(monkeypatch):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path.endswith("/devices/pins"):
            return httpx.Response(200, json=PINS)
        return httpx.Response(200, json=SERIES)

    real = httpx.AsyncClient
    monkeypatch.setattr(airthings.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(airthings, "datetime", type("D", (datetime,), {"now": classmethod(lambda cls, tz=None: NOW)}))
    import asyncio
    out = asyncio.run(airthings.fetch_sensors())
    assert len(seen) == 2  # one station list + one bulk read, not one request per station
    assert "dishai" in seen[0].headers["user-agent"]
    assert seen[1].url.params["selectFields[0]"] == "PM2" and seen[1].url.params["selectFields[1]"] == "PM10"
    assert [r["id"] for r in out] == ["AT1", "AT2"]


@pytest.fixture()
def client(monkeypatch):
    async def boom():
        raise RuntimeError("offline")

    monkeypatch.setattr(main, "cache", Cache(fixture_dir=main.cache.fixture_dir))
    monkeypatch.setattr(main, "fetch_weather", boom)
    monkeypatch.setattr(main, "fetch_air_quality", boom)
    monkeypatch.setattr(Cache, "_save_fixture", lambda *a, **k: None)
    monkeypatch.delenv("AIRTHINGS_DISABLED", raising=False)
    return TestClient(main.app)


def test_sensors_endpoint_merges_airthings_with_a_source_tag(client, monkeypatch):
    recs = airthings.normalise(PINS, SERIES, now=NOW)

    async def fake():
        return recs

    monkeypatch.setattr(airthings, "fetch_sensors", fake)
    body = client.get("/api/sensors").json()
    at = [s for s in body["sensors"] if s["source"] == "airthings"]
    sc = [s for s in body["sensors"] if s["source"] == "sensor_community"]
    assert {s["id"] for s in at} == {"AT1", "AT2"} and sc, "both networks are present"
    assert body["airthings"]["count"] == 2 and "Airthings" in body["label"]
    assert all(s["pm25"] == s["pm25_raw"] for s in at)  # already calibrated by the project: not corrected a second time
    assert all("district" in s and s["offset_known"] is False for s in at)
    assert body["count"] == len(body["sensors"])
    assert not (main.cache.fixture_dir / "airthings.json").exists()  # licence unknown: memory only


def test_airthings_outage_leaves_the_other_sensors_untouched(client, monkeypatch):
    async def boom():
        raise RuntimeError("site changed")

    monkeypatch.setattr(airthings, "fetch_sensors", boom)
    body = client.get("/api/sensors")
    assert body.status_code == 200
    j = body.json()
    assert j["airthings"]["count"] == 0 and j["airthings"]["source"] == "unavailable"
    assert all(s["source"] == "sensor_community" for s in j["sensors"])


def test_airthings_can_be_switched_off(client, monkeypatch):
    called = []

    async def fake():
        called.append(1)
        return []

    monkeypatch.setattr(airthings, "fetch_sensors", fake)
    monkeypatch.setenv("AIRTHINGS_DISABLED", "1")
    j = client.get("/api/sensors").json()
    assert not called and j["airthings"]["enabled"] is False and j["airthings"]["count"] == 0


def test_demo_scenario_transforms_airthings_like_other_sensors(client, monkeypatch):
    recs = airthings.normalise(PINS, SERIES, now=NOW)

    async def fake():
        return recs

    monkeypatch.setattr(airthings, "fetch_sensors", fake)
    live = {s["id"]: s["pm25"] for s in client.get("/api/sensors").json()["sensors"] if s["source"] == "airthings"}
    demo = {s["id"]: s["pm25"] for s in client.get("/api/sensors?scenario=winter_inversion").json()["sensors"] if s["source"] == "airthings"}
    assert demo and any(demo[i] > live[i] for i in demo) and demo != live  # the simulated winter is dirtier than the live clean day where it pools, cleaner in the parks
