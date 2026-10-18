"""Alerts, fire/smoke rule and web push. No network: forecasts are hand-built, push senders are fakes."""
import json
import math

import pytest
from fastapi.testclient import TestClient

from backend import alerts, fires, main, push
from backend.cache import Cache

NOW = "2026-07-10T09:00"


def _fc(app=None, alert=None, n=48):
    base = "2026-07-10T"
    hours = []
    for i in range(n):
        d, h = divmod(i + 0, 24)
        hours.append({"time": f"2026-07-{10 + d:02d}T{h:02d}:00", "t2m": 20.0, "apparent": (app or {}).get(i, 20.0)})
    return {"hours": hours, "now_index": 9, "generated_at": NOW, "alert": alert or {"level": "good"}, "source": "live"}


def _dust(vals, start_h=9):
    return [{"time": f"2026-07-10T{(start_h + i) % 24:02d}:00" if start_h + i < 24 else f"2026-07-11T{(start_h + i) % 24:02d}:00", "dust": v} for i, v in enumerate(vals)]


# ---- heat -------------------------------------------------------------------------------------------------------------------------------
def test_heat_alert_levels_follow_the_peak():
    warm = {i: 28 for i in range(12, 17)}
    hot = {i: 34 for i in range(12, 17)}
    xtr = {i: 39 for i in range(12, 17)}
    assert [a["level"] for a in alerts.heat_alerts(_fc(warm)["hours"], 9, NOW)] == ["info"]
    assert [a["level"] for a in alerts.heat_alerts(_fc(hot)["hours"], 9, NOW)] == ["watch"]
    a = alerts.heat_alerts(_fc(xtr)["hours"], 9, NOW)
    assert [x["level"] for x in a] == ["warning"] and a[0]["peak"]["value"] == 39 and a[0]["actions"]
    assert alerts.heat_alerts(_fc({12: 35})["hours"], 9, NOW) == []  # one hot hour is not an alert


def test_nothing_to_report_in_mild_weather():
    assert alerts.build_alerts(_fc(), [], None, NOW) == []


# ---- dust -------------------------------------------------------------------------------------------------------------------------------
def test_dust_needs_a_run_and_uses_history_based_thresholds():
    assert alerts.dust_alerts(_dust([5, 25, 5, 5]), NOW) == []  # a single hour
    a = alerts.dust_alerts(_dust([5, 22, 30, 25, 5]), NOW)
    assert len(a) == 1 and a[0]["level"] == "watch" and a[0]["cause"] == ["dust"] and a[0]["peak"]["value"] == 30
    assert alerts.dust_alerts(_dust([5, 25, 45, 41, 5]), NOW)[0]["level"] == "warning"
    assert alerts.dust_alerts([{"time": "2026-07-09T01:00", "dust": 90}, {"time": "2026-07-09T02:00", "dust": 90}], NOW) == []  # the past is not an alert


def test_dust_overlapping_a_pm_alert_is_named_as_its_cause():
    pm = {"level": "warning", "audience": "everyone", "start": "2026-07-10T12:00", "end": "2026-07-10T18:00", "mean_pm25": 31.0, "peak_pm25": 36.0, "probability": 0.8}
    out = alerts.build_alerts(_fc(alert=pm), _dust([5, 5, 30, 35, 30, 5]), None, NOW)
    kinds = [a["kind"] for a in out]
    assert kinds[0] == "pm" and "dust" in kinds
    assert out[0]["cause"] == ["dust"] and "80%" in out[0]["message"]


# ---- fire / smoke -----------------------------------------------------------------------------------------------------------------------
def _fires_at(lat, lon, n=12, frp=20.0):
    return [{"lat": lat + 0.01 * k, "lon": lon, "frp": frp, "conf": "n"} for k in range(n)]


def test_firms_csv_parsing_drops_low_confidence():
    text = "latitude,longitude,bright_ti4,frp,confidence,acq_date\n42.0,23.0,330,12.5,n,2026-07-10\n42.1,23.1,320,3.0,l,2026-07-10\n42.2,23.2,340,40,h,2026-07-10\nbad,row\n"
    got = fires.parse_firms_csv(text)
    assert [round(g["frp"]) for g in got] == [12, 40]
    assert fires.parse_firms_csv("Invalid MAP_KEY") == []


def test_wind_mean_vector_and_bearing():
    assert fires.mean_wind([20, 20], [270, 270]) == pytest.approx((20.0, 270.0), abs=0.1)  # a west wind blows from 270
    s, d = fires.mean_wind([20, 20], [350, 10])
    assert s == pytest.approx(20 * math.cos(math.radians(10)), rel=0.01) and d == pytest.approx(0, abs=0.1) or d == pytest.approx(360, abs=0.1)
    km, b = fires.distance_bearing(42.6977, 22.3219)  # ~82 km due west of Sofia
    assert km == pytest.approx(82, abs=3) and b == pytest.approx(270, abs=1)


def test_smoke_only_when_fires_are_upwind_and_close_enough():
    west = _fires_at(42.7, 21.3)  # about 165 km west
    t = fires.smoke_threat(west, (25.0, 270.0))  # wind from the west
    assert t and t["count"] == 12 and t["direction"] == "west" and 6 < t["arrival_h"] < 7
    assert fires.smoke_threat(west, (25.0, 90.0)) is None        # wind from the east: fires are downwind
    assert fires.smoke_threat(west, (3.0, 270.0)) is None        # almost still air
    assert fires.smoke_threat(_fires_at(42.7, 12.0), (25.0, 270.0)) is None  # 800 km away
    assert fires.smoke_threat(west[:3], (25.0, 270.0)) is None   # too few weak detections
    assert fires.smoke_threat(_fires_at(42.7, 21.3, n=3, frp=100.0), (25.0, 270.0)) is not None  # few but intense


def test_fire_alert_is_indicative_and_names_fire_as_cause():
    t = fires.smoke_threat(_fires_at(42.7, 21.3), (25.0, 270.0))
    a = alerts.fire_alert(t, NOW)
    assert a["kind"] == "fire" and a["level"] == "watch" and a["confidence"] == "inferred" and "not how much" in a["message"] and a["actions"]


# ---- push -------------------------------------------------------------------------------------------------------------------------------
GOOD = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "k", "auth": "a"}}


def test_only_real_push_services_are_accepted(tmp_path):
    p = tmp_path / "s.json"
    for bad in ("http://fcm.googleapis.com/x", "https://169.254.169.254/latest", "https://evil.example.com/push", "https://fcm.googleapis.com.evil.com/x", "ftp://x"):
        with pytest.raises(ValueError):
            push.subscribe({"endpoint": bad, "keys": {"p256dh": "k", "auth": "a"}}, [], "watch", p)
    with pytest.raises(ValueError):
        push.subscribe({"endpoint": GOOD["endpoint"], "keys": {}}, [], "watch", p)
    for ok in ("https://updates.push.services.mozilla.com/wpush/v2/x", "https://web.push.apple.com/x", "https://wns2-par02p.notify.windows.com/x"):
        push.subscribe({"endpoint": ok, "keys": {"p256dh": "k", "auth": "a"}}, [], "watch", p)


def test_dispatch_sends_once_per_alert_and_level_and_respects_choices(tmp_path):
    p = tmp_path / "s.json"
    push.subscribe(GOOD, ["heat"], "watch", p)
    push.subscribe({**GOOD, "endpoint": "https://fcm.googleapis.com/fcm/send/other"}, [], "warning", p)  # all kinds, warnings only
    sent = []
    send = lambda sub, data: sent.append((sub["endpoint"][-5:], json.loads(data)["tag"], json.loads(data)["level"])) or 201
    heat = alerts._alert("heat", "watch", "2026-07-10T12:00", "2026-07-10T18:00", NOW, "Hot", "m", source="s", confidence="forecast")
    dust = alerts._alert("dust", "watch", "2026-07-10T12:00", "2026-07-10T18:00", NOW, "Dust", "m", source="s", confidence="model")
    r = push.dispatch([heat, dust], send, p)
    assert sent == [("d/abc", "heat-2026-07-10", "watch")] and r["sent"] == 1  # the first wants heat only; the second wants warnings only
    assert push.dispatch([heat, dust], send, p)["sent"] == 0                    # the same alert is never sent twice
    worse = {**heat, "level": "warning"}
    assert push.dispatch([worse], send, p)["sent"] == 2                         # an escalation is sent again, to both


def test_gone_subscriptions_are_removed(tmp_path):
    p = tmp_path / "s.json"
    push.subscribe(GOOD, [], "info", p)
    a = alerts._alert("heat", "watch", "2026-07-10T12:00", "2026-07-10T18:00", NOW, "Hot", "m", source="s", confidence="forecast")
    assert push.dispatch([a], lambda s, d: 410, p)["subscriptions"] == 0
    assert push.count(p) == 0


def test_unsubscribe_deletes_everything(tmp_path):
    p = tmp_path / "s.json"
    push.subscribe(GOOD, [], "info", p)
    assert push.unsubscribe(GOOD["endpoint"], p) is True and push.count(p) == 0 and push.unsubscribe(GOOD["endpoint"], p) is False
    assert "fcm" not in p.read_text()


# ---- API --------------------------------------------------------------------------------------------------------------------------------
async def _boom():
    raise RuntimeError("offline")


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "cache", Cache(fixture_dir=main.cache.fixture_dir))
    monkeypatch.setattr(main, "fetch_weather", _boom)
    monkeypatch.setattr(main, "fetch_air_quality", _boom)
    monkeypatch.setattr(main, "fetch_sensors", _boom)
    monkeypatch.setattr(Cache, "_save_fixture", lambda *a, **k: None)
    monkeypatch.setattr(push, "STORE", tmp_path / "subs.json")
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    return TestClient(main.app)


def test_alerts_endpoint_shape_and_demo_scenarios(client):
    j = client.get("/api/alerts?scenario=summer_heatwave").json()
    assert j["alerts"] and all({"id", "kind", "level", "title", "message", "start", "end", "actions", "source", "confidence"} <= set(a) for a in j["alerts"])
    assert j["alerts"][0]["level"] == "warning" and j["alerts"][0]["kind"] == "heat"
    assert j["sources"]["fires"]["enabled"] is False and j["push"]["enabled"] is False
    inv = client.get("/api/alerts?scenario=winter_inversion").json()["alerts"]
    assert inv and inv[0]["kind"] == "pm"
    assert client.get("/api/alerts?scenario=nonsense").status_code in (400, 422)


def test_push_is_off_without_keys_and_never_leaks_the_private_key(client, monkeypatch):
    assert client.get("/api/push/key").json() == {"enabled": False, "public_key": None, "kinds": ["pm", "heat", "dust", "fire", "transport"], "levels": ["info", "watch", "warning"]}
    assert client.post("/api/push/subscribe", json={"subscription": GOOD}).status_code == 503
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "PUBLIC")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "SECRET")
    k = client.get("/api/push/key").json()
    assert k["enabled"] is True and k["public_key"] == "PUBLIC" and "SECRET" not in json.dumps(k)


def test_subscribe_and_unsubscribe_over_http(client, monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "P")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "S")
    r = client.post("/api/push/subscribe", json={"subscription": GOOD, "kinds": ["heat"], "min_level": "warning"})
    assert r.status_code == 200 and r.json()["kinds"] == ["heat"] and push.count() == 1
    assert client.post("/api/push/subscribe", json={"subscription": {"endpoint": "https://evil.example.com/x", "keys": {"p256dh": "k", "auth": "a"}}}).status_code == 422
    assert client.post("/api/push/subscribe", json={"subscription": GOOD, "kinds": ["bogus"]}).status_code == 422
    assert client.post("/api/push/subscribe", json={"subscription": GOOD, "extra": 1}).status_code == 422
    assert client.post("/api/push/unsubscribe", json={"endpoint": GOOD["endpoint"]}).json()["removed"] is True and push.count() == 0
    assert client.post("/api/push/test", json={"endpoint": GOOD["endpoint"]}).status_code == 404
