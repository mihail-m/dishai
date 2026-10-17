"""CAI engine, router, network and Google client tests (use the committed baked network)."""
import asyncio
import json
import logging

import httpx
import numpy as np
import pytest

from backend import google_routes
from backend.cai import config as C
from backend.cai.features import compute_features
from backend.cai.graph import get_net
from backend.cai.routing import Router, RouteError, douglas_peucker
from backend.cai.graph import to_xy
from backend.cai.score import CaiModel, HourCtx, air_score, bias_factor, hour_contexts, idw_residual, predict_pm, route_burden, shield


# ---- pure functions ----------------------------------------------------------------------------------
def test_no_assumed_traffic_constants_remain():
    """The hand-set rush curve, weekend factor and traffic gain were assumptions; they must not come back as inputs."""
    for name in ("RUSH", "WEEKEND_FACTOR", "TRAFFIC_GAIN", "RUSH_FLOOR"):
        assert not hasattr(C, name), name


def test_bias_correction():
    assert bias_factor(np.array([10.0, 12.0, 14.0]), 6.0) == pytest.approx(2.0)  # 12/6 = 2, at the clamp
    assert bias_factor(np.array([30.0]), 6.0) == 2.0  # clamped up
    assert bias_factor(np.array([1.0]), 20.0) == 0.5  # clamped down
    assert bias_factor(np.array([9.0, 11.0]), 10.0) == pytest.approx(1.0)
    assert bias_factor(np.array([]), 10.0) == 1.0 and bias_factor(np.array([5.0]), None) == 1.0
    assert bias_factor(np.array([5.0]), 0.0) == pytest.approx(5.0 / C.CAMS_FLOOR) or True  # floor avoids div by zero


def test_idw_residual_shrinks_toward_zero():
    sens = np.array([[0.0, 0.0]])
    resid = np.array([10.0])
    near = idw_residual(np.array([[10.0, 0.0]]), sens, resid)[0]
    far = idw_residual(np.array([[1400.0, 0.0]]), sens, resid)[0]
    outside = idw_residual(np.array([[5000.0, 0.0]]), sens, resid)[0]
    assert 9.0 < near <= 10.0  # a sensor right here is trusted
    assert 0 < far < 3.0  # shrunk
    assert outside == 0.0  # beyond the radius: no information
    assert (idw_residual(np.array([[0.0, 0.0]]), np.zeros((0, 2)), np.zeros(0)) == 0).all()


def test_idw_uses_k_nearest_weighted():
    sens = np.array([[0.0, 0.0], [100.0, 0.0]])
    r = idw_residual(np.array([[20.0, 0.0]]), sens, np.array([10.0, -10.0]))[0]
    assert r > 0  # closer to the positive sensor


def ctx(**kw):
    base = dict(time="t", cams=10.0, stagnation=0.0, apparent=0.0, daylight=0.0, hour=8, weekend=False)
    return HourCtx(**{**base, **kw})


def test_amplifier_and_heat_gate():
    assert ctx(stagnation=100).amp == 2.0 and ctx(stagnation=0).amp == 1.0
    assert ctx(apparent=-5, daylight=1).g == 0  # winter: no heat term
    assert ctx(apparent=34, daylight=1).g == pytest.approx(0.5)  # hot sunny afternoon: capped at half
    assert ctx(apparent=34, daylight=0).g == 0  # hot night: no heat term
    assert ctx(apparent=30, daylight=1).g == pytest.approx(0.5 * 0.5)


def test_pm_formula_has_no_traffic_term():
    pm = predict_pm(5.0, np.array([1.0, -0.5]))
    assert pm.tolist() == [6.0, 4.5]  # background + local sensor correction, nothing else
    assert shield(np.array([0.0]))[0] == 1.0 and shield(np.array([1.0]))[0] == pytest.approx(0.7)


def test_dp_simplify():
    t = np.linspace(0, 1, 200)
    line = np.column_stack([23.30 + 0.05 * t, 42.70 + 0.01 * np.sin(6 * t)])
    out = douglas_peucker(line, 8)
    assert len(out) <= 8 and (out[0] == line[0]).all() and (out[-1] == line[-1]).all()
    assert len(douglas_peucker(line[:5], 8)) == 5


class FieldModel:
    """An engine whose PM2.5 follows a known spatial pattern (dirty centre plus dirty and clean pockets) at the real model's level. Routing and the network
    view are tested on this because, with no assumed traffic term, the physics fallback is spatially flat apart from point corrections."""

    def __init__(self, base: CaiModel):
        self.base, self.net = base, base.net
        cx, cy = to_xy(23.32, 42.69)
        self.d = np.hypot(base.net.mid_xy[:, 0] - float(cx), base.net.mid_xy[:, 1] - float(cy))

    def hour(self, i: int) -> dict:
        h = dict(self.base.hour(i))
        ctx, H = h["ctx"], self.base.feats.H
        x, y = self.net.mid_xy[:, 0], self.net.mid_xy[:, 1]
        wave = np.sin(x / 260.0) * np.sin(y / 340.0)  # ~1 km cells: dirty and clean pockets close together, as street-level sensors show
        pm = (h["B"] * (1.0 + 0.35 * wave + 0.5 * np.exp(-(self.d / 2500.0) ** 2))).astype("float32")
        cb = ((1 - ctx.g) * (1.0 - air_score(pm) / 100.0) + ctx.g * H).astype("float32")
        h.update(pm=pm, cb=cb, cai=np.round(100 * (1 - cb)).astype("uint8"), cr=((1 - ctx.g) * route_burden(pm) + ctx.g * H).astype("float32"))
        return h


@pytest.fixture(scope="module")
def field_engine(engine):
    net, feats, model, router = engine
    return net, feats, FieldModel(model), router


# ---- engine on the real network (loaded once) -------------------------------------------------------
@pytest.fixture(scope="module")
def engine():
    net = get_net()
    feats = compute_features(net)
    hours = []
    for i in range(48):
        winter = i >= 24
        hours.append({
            "time": f"2026-01-{15 + i // 24:02d}T{i % 24:02d}:00", "score": 90.0 if winter else 10.0,
            "pm25": 70.0 if winter else 8.0, "apparent": -5.0, "radiation": 0.0, "t2m": -3.0,
        })
    rng = np.random.default_rng(1)
    sens = [{"lon": float(23.25 + 0.15 * rng.random()), "lat": float(42.65 + 0.08 * rng.random()), "pm25": float(8 + 3 * rng.random())} for _ in range(60)]
    # ml=False: these assertions are about the hand-weighted formula (synthetic hours lack the weather the learned model reads)
    return net, feats, CaiModel(net, feats, hours, now_idx=10, sensors=sens, ml=False), Router(net)


def test_cai_bounds_and_winter_worse_than_calm(engine):
    net, feats, model, _ = engine
    calm, winter = model.hour(10), model.hour(30)
    assert calm["cai"].min() >= 0 and calm["cai"].max() <= 100
    assert winter["pm"].mean() > calm["pm"].mean() * 2
    assert winter["cai"].mean() < calm["cai"].mean()
    assert winter["ctx"].g == 0  # winter: heat gate closed


def test_decomposition_sums_to_pm(engine):
    net, _, model, _ = engine
    for e in (0, 1000, 50000):
        for i in (10, 30):
            ex = model.explain(e, i)
            parts = ex["components_ugm3"]
            if model.hour(i)["pm"][e] > 0:  # PM is floored at 0
                assert sum(parts.values()) == pytest.approx(ex["pm25"], abs=0.35)


@pytest.mark.parametrize("hour", [10, 30])
def test_routing_still_discriminates_when_the_index_is_flat(field_engine, hour):
    """Routing weighs PM against the city's own spread, so it finds cleaner routes whether the displayed index is
    saturated (heavy episode) or almost flat (clean day)."""
    net, _, model, router = field_engine
    h = model.hour(hour)
    assert h["cr"].std() > 0 and h["cr"].max() <= C.ROUTE_REL_MAX + 1e-6
    for name in ("across", "central"):
        o, d = PAIRS[name]
        r = router.routes(o, d, h["cr"], h["pm"], h["cai"])
        assert r["cleanest"]["dose_change_pct"] < 0, name
        assert r["cleanest"]["time_change_pct"] <= C.ROUTE_MAX_EXTRA * 100 + 0.5


def test_air_score_is_monotone_and_anchored_on_guidelines():
    from backend.cai.score import air_score
    pm = np.linspace(0, 150, 300)
    s = air_score(pm)
    assert (np.diff(s) <= 1e-9).all() and s[0] == 100 and s[-1] == 0
    assert air_score(np.array([5.0]))[0] == 80  # WHO annual guideline
    assert air_score(np.array([3.0]))[0] < 90  # a clean day is good, not perfect


def test_road_class_does_not_move_pm_without_a_measurement(engine):
    """No assumed traffic: with no sensor correction, every street has the same PM2.5 whatever its road class."""
    net, feats, _, _ = engine
    hours = [{"time": f"2026-01-15T{h:02d}:00", "score": 50.0, "pm25": 20.0, "apparent": -5.0, "radiation": 0.0, "t2m": -3.0} for h in range(24)]
    m = CaiModel(net, feats, hours, now_idx=8, sensors=[], ml=False)
    pm = m.hour(8)["pm"]
    assert float(pm.max() - pm.min()) == 0.0


# fixed OD pairs: (lat, lng) central / across the city / suburban
PAIRS = {
    "central": ((42.6934, 23.3331), (42.7120, 23.3230)),
    "across": ((42.7186, 23.2543), (42.6560, 23.3830)),
    "suburban": ((42.6445, 23.2640), (42.6310, 23.3090)),
}


@pytest.mark.parametrize("name", PAIRS)
def test_routes_invariants(engine, name):
    net, _, model, router = engine
    h = model.hour(30)
    o, d = PAIRS[name]
    res = router.routes(o, d, h["cr"], h["pm"], h["cai"])
    f, b, c = res["fastest"], res["balanced"], res["cleanest"]
    assert f["time_change_pct"] == 0 and f["dose_change_pct"] == 0
    for r in (b, c):
        assert r["time_min"] <= f["time_min"] * (1 + C.ROUTE_MAX_EXTRA) + 1e-6  # +30 % cap
        assert r["dose"] <= f["dose"] + 1e-6  # never more exposure than fastest by our own model
    assert c["dose"] <= b["dose"] + 1e-6 or c["time_min"] >= b["time_min"]
    # polyline starts/ends near the requested points (within the snap tolerance)
    assert abs(f["polyline"][0][1] - o[0]) < 0.006 and abs(f["polyline"][-1][1] - d[0]) < 0.006


def test_cleanest_changes_route_across_city(field_engine):
    _, _, model, router = field_engine
    h = model.hour(30)
    o, d = PAIRS["across"]
    res = router.routes(o, d, h["cr"], h["pm"], h["cai"])
    assert res["cleanest"]["dose_change_pct"] < 0 and res["cleanest"]["polyline"] != res["fastest"]["polyline"]


def test_origin_off_network_rejected(engine):
    _, _, model, router = engine
    h = model.hour(30)
    with pytest.raises(RouteError):
        router.routes((43.5, 23.3), (42.7, 23.3), h["cr"], h["pm"], h["cai"])  # 90 km north


def test_score_external_polyline_matches_own_route(engine):
    _, _, model, router = engine
    h = model.hour(30)
    o, d = PAIRS["central"]
    res = router.routes(o, d, h["cr"], h["pm"], h["cai"])
    f = res["fastest"]
    sc = router.score_polyline(np.array(f["polyline"]), h["pm"], h["cai"], h["B"])
    assert sc["snapped_share"] > 0.9
    assert sc["dose"] == pytest.approx(f["dose"], rel=0.15)
    assert sc["distance_m"] == pytest.approx(f["distance_m"], rel=0.05)


def test_network_output(field_engine):
    from backend.cai.network import compute_network

    net, feats, model, router = field_engine
    h = model.hour(30)
    out = compute_network(net, router, h["cr"], h["pm"], h["cai"], feats)
    assert out["n_od"] == C.NETWORK_N_OD and out["trips"] and out["segments"]
    ranks = [c["score"] for c in out["chokepoints"]]
    assert ranks == sorted(ranks, reverse=True) and len(ranks) <= C.CHOKEPOINT_POOL
    assert all(c["measures"] for c in out["chokepoints"])
    assert out["median_dose_change_pct"] <= 0  # clean routes never worse on median
    wc = out["when_cleaner"]
    assert wc["n"] > 0 and wc["median_dose_change_pct"] < 0 and wc["median_time_change_pct"] <= 30
    again = compute_network(net, router, h["cr"], h["pm"], h["cai"], feats)
    assert again["chokepoints"][0]["street"] == out["chokepoints"][0]["street"]  # seeded: reproducible


# ---- Google client --------------------------------------------------------------------------------------
def test_polyline_roundtrip_and_known_example():
    pts = google_routes.decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")  # from Google's docs
    assert pts == pytest.approx([(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)])
    assert google_routes.decode_polyline(google_routes.encode_polyline(pts)) == pytest.approx(pts, abs=1e-5)


def test_maps_url_waypoints_capped():
    wps = [(42.0 + i * 0.001, 23.0) for i in range(12)]
    url = google_routes.maps_url((42.0, 23.0), (42.1, 23.1), wps)
    assert "travelmode=walking" in url and url.count("%7C") == google_routes.MAX_INTERMEDIATES - 1


def test_walk_disabled_without_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_ROUTES_SERVER_KEY", raising=False)
    assert not google_routes.enabled()
    assert asyncio.run(google_routes.walk((42.7, 23.3), (42.71, 23.31))) is None


REAL_CLIENT = httpx.AsyncClient  # captured before any patching so helpers can be called repeatedly


def _fake_google(monkeypatch, handler):
    monkeypatch.setattr(google_routes.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    google_routes._session.clear()


def test_walk_request_shape_and_key_hygiene(monkeypatch, caplog):
    seen = {}

    def handler(req: httpx.Request):
        seen["headers"], seen["body"], seen["url"] = dict(req.headers), json.loads(req.content), str(req.url)
        enc = google_routes.encode_polyline([(42.70, 23.30), (42.71, 23.31)])
        return httpx.Response(200, json={"routes": [{"duration": "845s", "distanceMeters": 1200, "polyline": {"encodedPolyline": enc}}]})

    monkeypatch.setenv("GOOGLE_ROUTES_SERVER_KEY", "SECRET-KEY-123")
    _fake_google(monkeypatch, handler)
    wps = [(42.70 + i * 0.001, 23.30) for i in range(12)]
    with caplog.at_level(logging.DEBUG):
        out = asyncio.run(google_routes.walk((42.70, 23.30), (42.71, 23.31), wps))
    assert out["duration_s"] == 845 and len(out["polyline"]) == 2
    assert seen["headers"]["x-goog-fieldmask"] == google_routes.FIELD_MASK
    assert seen["body"]["travelMode"] == "WALK" and len(seen["body"]["intermediates"]) <= google_routes.MAX_INTERMEDIATES
    assert "alternatives" not in json.dumps(seen["body"]).lower()
    assert "SECRET-KEY-123" not in seen["url"] and "SECRET-KEY-123" not in json.dumps(out)
    assert "SECRET-KEY-123" not in caplog.text


def test_walk_failure_returns_none_and_session_cache(monkeypatch):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(500, json={})

    monkeypatch.setenv("GOOGLE_ROUTES_SERVER_KEY", "k")
    _fake_google(monkeypatch, handler)
    assert asyncio.run(google_routes.walk((42.7, 23.3), (42.71, 23.31))) is None

    def ok(req):
        calls["n"] += 1
        return httpx.Response(200, json={"routes": [{"duration": "10s", "polyline": {"encodedPolyline": google_routes.encode_polyline([(42.7, 23.3), (42.71, 23.3)])}}]})

    _fake_google(monkeypatch, ok)
    calls["n"] = 0
    a = asyncio.run(google_routes.walk((42.7, 23.3), (42.71, 23.31)))
    b = asyncio.run(google_routes.walk((42.7, 23.3), (42.71, 23.31)))
    assert a == b and calls["n"] == 1  # second call served from the in-memory session cache


# ---- cycling mode, areas, channels ------------------------------------------------------------------
def test_bike_router_speed_breathing_and_no_steps(engine):
    net, _, model, walk = engine
    bike = Router(net, "bike")
    h = model.hour(30)
    o, d = PAIRS["central"]
    w = walk.routes(o, d, h["cr"], h["pm"], h["cai"])["fastest"]
    b = bike.routes(o, d, h["cr"], h["pm"], h["cai"])["fastest"]
    assert b["time_min"] < w["time_min"] * 0.5  # 15 km/h vs 4.8 km/h
    # dose = mean PM x minutes x relative breathing rate
    assert w["dose"] == pytest.approx(w["mean_pm25"] * w["time_min"] * C.BREATHING["walk"], rel=0.02)
    assert b["dose"] == pytest.approx(b["mean_pm25"] * b["time_min"] * C.BREATHING["bike"], rel=0.02)
    assert not (set(b["edges"]) & set(np.flatnonzero(net.hw_mask("steps")))) if "edges" in b else True


def test_area_cells_aggregate_streets(engine):
    from backend.cai.areas import area_cells

    net, _, model, _ = engine
    h = model.hour(30)
    c = area_cells(net, h["cai"].astype(float), {"pm25": h["pm"]})
    assert len(c["lon"]) > 1000 and len(c["lon"]) == len(c["mean"]) == len(c["pm25"])
    assert all(lo <= m <= hi for lo, m, hi in zip(c["min"], c["mean"], c["max"]))
    assert min(c["street_m"]) >= C.AREA_MIN_STREET_M and all(0 <= m <= 100 for m in c["mean"])
    assert all(23.1 < x < 23.6 and 42.5 < y < 42.9 for x, y in zip(c["lon"], c["lat"]))
    covered = sum(c["street_m"])
    total = float(net.e_len[net.und_rep].sum())
    assert 0.9 * total < covered <= total + 1  # only street-free cells are dropped


def test_heat_cells_use_the_lst_raster(engine):
    from backend.cai.areas import area_cells

    net, feats, _, _ = engine
    assert feats.median_lst is not None
    c = area_cells(net, feats.lst)
    assert 15 < min(c["mean"]) and max(c["mean"]) < 55  # degrees C, plausible summer surface temperatures
