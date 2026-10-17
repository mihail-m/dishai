import asyncio
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import main, waqi
from backend.cache import Cache

REAL = httpx.AsyncClient
TOKEN = "SECRET-TOKEN-0123456789abcdef0123456789abcd"


def test_pm10_aqi_to_ugm3():
    assert waqi.pm10_aqi_to_ugm3(0) == 0 and waqi.pm10_aqi_to_ugm3(50) == 54.0
    assert waqi.pm10_aqi_to_ugm3(12) == pytest.approx(13.0, abs=0.1)  # 12/50 * 54
    assert waqi.pm10_aqi_to_ugm3(100) == 154.0 and waqi.pm10_aqi_to_ugm3(None) is None
    assert waqi.pm10_aqi_to_ugm3(75) == pytest.approx(103.5, abs=0.1)  # 55 + 24 * 99 / 49


def test_station_name_parsing():
    assert waqi._names("Druzhba, Sofia, Bulgaria (Дружба, Столична)") == ("Druzhba", "Дружба")
    assert waqi._names("Plain")[1] is None


def fake_waqi(monkeypatch, handler):
    monkeypatch.setattr(waqi.httpx, "AsyncClient", lambda **kw: REAL(transport=httpx.MockTransport(handler), **kw))


def standard_handler(seen=None):
    def handler(req: httpx.Request):
        if seen is not None:
            seen.append(req)
        if req.url.path.startswith("/map/bounds"):
            return httpx.Response(200, json={"status": "ok", "data": [
                {"uid": 1, "lat": 42.65, "lon": 23.40, "aqi": "17", "station": {"name": "Druzhba, Sofia, Bulgaria (Дружба, Столична)"}},
                {"uid": 2, "lat": 42.73, "lon": 23.30, "aqi": "21", "station": {"name": "Nadezhda, Sofia, Bulgaria (Надежда, Столична)"}},
                {"uid": 3, "lat": 42.60, "lon": 23.20, "aqi": "5", "station": {"name": "Broken, Sofia"}},
            ]})
        uid = int(req.url.path.split("@")[1].strip("/"))
        if uid == 3:
            return httpx.Response(500)
        return httpx.Response(200, json={"status": "ok", "data": {"time": {"iso": "2026-10-17T13:00:00+03:00"},
                                                                     "iaqi": {"pm10": {"v": 17 if uid == 1 else 12}, "no2": {"v": 8.5}, "o3": {"v": 16.3}}}})
    return handler


def test_fetch_stations_sends_bbox_and_token_as_params(monkeypatch):
    """Regression: httpx drops a query string embedded in the path when params= is given, so latlng was lost."""
    seen: list[httpx.Request] = []
    monkeypatch.setenv("WAQI_TOKEN", TOKEN)
    fake_waqi(monkeypatch, standard_handler(seen))
    out = asyncio.run(waqi.fetch_stations())
    bounds = [r for r in seen if r.url.path.startswith("/map/bounds")][0]
    assert bounds.url.params["latlng"] == "42.55,23.10,42.85,23.60" and bounds.url.params["token"] == TOKEN
    assert [s["name"] for s in out] == ["Druzhba", "Nadezhda"]  # the station whose feed failed is skipped
    assert out[0]["pm10"] == pytest.approx(18.4, abs=0.1) and out[0]["name_bg"] == "Дружба" and out[0]["no2_aqi"] == 8.5


@pytest.mark.parametrize("status,expect", [(401, "HTTP 401"), (503, "HTTP 503")])
def test_errors_never_contain_the_token(monkeypatch, caplog, status, expect):
    monkeypatch.setenv("WAQI_TOKEN", TOKEN)
    fake_waqi(monkeypatch, lambda req: httpx.Response(status))
    with caplog.at_level(logging.DEBUG), pytest.raises(waqi.WaqiError) as e:
        asyncio.run(waqi.fetch_stations())
    assert str(e.value) == expect and TOKEN not in str(e.value) and TOKEN not in caplog.text


def test_api_refusal_and_connection_errors_are_sanitised(monkeypatch):
    monkeypatch.setenv("WAQI_TOKEN", TOKEN)
    fake_waqi(monkeypatch, lambda req: httpx.Response(200, json={"status": "error", "data": "Invalid key"}))
    with pytest.raises(waqi.WaqiError) as e:
        asyncio.run(waqi.fetch_stations())
    assert TOKEN not in str(e.value)

    def boom(req):
        raise httpx.ConnectError(f"cannot reach {req.url}")  # message embeds the URL, hence the token

    fake_waqi(monkeypatch, boom)
    with pytest.raises(waqi.WaqiError) as e:
        asyncio.run(waqi.fetch_stations())
    assert str(e.value) == "ConnectError" and TOKEN not in str(e.value)


def test_no_token_means_no_request(monkeypatch):
    monkeypatch.delenv("WAQI_TOKEN", raising=False)
    assert not waqi.enabled()
    with pytest.raises(waqi.WaqiError):
        asyncio.run(waqi.fetch_stations())


# ---- sensor check --------------------------------------------------------------------------------------
def test_sensor_check_ratio_and_minimum_sensors():
    st = [{"name": "A", "lat": 42.70, "lon": 23.30, "pm10": 20.0}, {"name": "B", "lat": 42.60, "lon": 23.50, "pm10": 10.0},
          {"name": "NoPM10", "lat": 42.7, "lon": 23.3, "pm10": None}]
    near_a = [{"lat": 42.701, "lon": 23.301, "pm10": 8.0}, {"lat": 42.699, "lon": 23.299, "pm10": 10.0}, {"lat": 42.700, "lon": 23.302, "pm10": 12.0}]
    far = [{"lat": 42.90, "lon": 23.60, "pm10": 99.0}]
    out = main.sensor_check(st, near_a + far)
    assert [r["station"] for r in out["rows"]] == ["A"]  # B has no sensors nearby, NoPM10 is skipped
    assert out["rows"][0]["ratio"] == 0.5 and out["median_ratio"] == 0.5 and "lower" in out["verdict"]
    assert main.sensor_check(st, [])["median_ratio"] is None


# ---- endpoint ------------------------------------------------------------------------------------------
@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main, "cache", Cache(fixture_dir=main.cache.fixture_dir))
    monkeypatch.setattr(main, "fetch_weather", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    return TestClient(main.app)


def test_reference_disabled_without_token(client, monkeypatch):
    monkeypatch.delenv("WAQI_TOKEN", raising=False)
    r = client.get("/api/reference").json()
    assert r["enabled"] is False and r["stations"] == [] and "WAQI_TOKEN" in r["reason"] and r["attribution"]


def test_reference_hidden_in_demo_scenario(client, monkeypatch):
    monkeypatch.setenv("WAQI_TOKEN", TOKEN)
    r = client.get("/api/reference?scenario=winter_inversion").json()
    assert r["enabled"] is False and r["reason"].startswith("Demo")


def test_reference_enabled_in_memory_only_and_token_not_leaked(client, monkeypatch):
    monkeypatch.setenv("WAQI_TOKEN", TOKEN)
    fake_waqi(monkeypatch, standard_handler())
    r = client.get("/api/reference")
    body = r.json()
    assert body["enabled"] is True and len(body["stations"]) == 2 and body["pm25_available"] is False
    assert "Sofia Municipality" in body["attribution"] and TOKEN not in r.text
    assert not (main.cache.fixture_dir / "waqi_stations.json").exists()  # terms forbid archiving: nothing is written to disk
    assert "sensor_check" in body


def test_reference_upstream_failure_degrades_gracefully(client, monkeypatch):
    monkeypatch.setenv("WAQI_TOKEN", TOKEN)
    fake_waqi(monkeypatch, lambda req: httpx.Response(502))
    r = client.get("/api/reference")
    assert r.status_code == 200 and r.json()["enabled"] is False and TOKEN not in r.text
