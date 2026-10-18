"""CAI, routing, network and validation endpoints (PLAN2). Mounted by backend.main."""
import asyncio
import base64
import logging
import struct
import time
import uuid
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Response

from . import google_routes, traffic
from .advice import _pollen_level
from . import scenario as demo
from .cai import config as C
from .cai.features import Features, compute_features
from .cai import heat
from .cai.areas import area_cells, summarize
from .cai.graph import Net, get_net, has_mode
from .cai.names import Namer, translit
from .cai.network import compute_network
from .cai import route_confidence, trust
from .cai.routing import Router, RouteError, douglas_peucker, road_group
from .cai.score import CaiModel, HourCtx
from .cai.validate import leave_one_out, leave_one_out_ml

log = logging.getLogger("inversion_watch")
MODEL_TTL = 300

CLASS_CODE = {"major": 0, "collector": 1, "local": 2, "path": 3}
Mode = Literal["walk", "bike"]
Species = Literal["alder", "birch", "grass", "mugwort", "olive", "ragweed"]


def make_router(build_forecast, build_sensors, parse_scenario, scenario_query, build_sensors_24h=None, district_at=None,
                refresh_sources=None, now=None) -> APIRouter:
    r = APIRouter()
    district_at = district_at or (lambda lon, lat: None)
    static: dict = {}
    models: dict[tuple, tuple[float, dict, CaiModel]] = {}
    networks: dict[tuple, dict] = {}
    snapshots: dict[str, dict] = {}
    locks = {"static": asyncio.Lock(), "model": asyncio.Lock(), "network": asyncio.Lock()}
    snapshot_lock = asyncio.Lock()

    async def get_static(mode: str = "walk") -> tuple[Net, Features, Router]:
        if mode == "bike" and not has_mode("bike"):
            raise HTTPException(503, "The cycling network has not been baked yet (make graph/bake with --bike)")
        async with locks["static"]:
            if ("v", mode) not in static:
                def build():
                    net = get_net(mode)
                    return net, compute_features(net), Router(net, mode)
                static[("v", mode)] = await asyncio.to_thread(build)
                static[("namer", mode)] = await asyncio.to_thread(Namer, static[("v", mode)][0], district_at)
        return static[("v", mode)]

    def demo_scale(net: Net, sc: str | None):
        """Demo scenarios with a geography (backend/scenario.py) scale the street PM field by place; live data never does."""
        return demo.demo_geography_array(net.e_mid_lat, net.e_mid_lon, sc) if demo.has_geography(sc) else None

    async def get_model(scenario: str | None, mode: str = "walk", forecast_id: str | None = None) -> tuple[CaiModel, dict, Router, Net]:
        net, feats, router = await get_static(mode)
        async with locks["model"]:
            if forecast_id:
                snapshot = snapshots.get(forecast_id)
                if not snapshot or snapshot["forecast"]["scenario"] != scenario:
                    raise HTTPException(409, "Forecast snapshot expired. Refresh the forecast.")
                if mode not in snapshot["models"]:
                    fc, sens = snapshot["forecast"], snapshot["sensors"]
                    snapshot["models"][mode] = await asyncio.to_thread(CaiModel, net, feats, fc["hours"], fc["now_index"], sens.get("model_sensors", sens["sensors"]), True, demo_scale(net, scenario))
                return snapshot["models"][mode], snapshot["forecast"], router, net
            hit = models.get((scenario, mode))
            if hit and time.time() - hit[0] < MODEL_TTL:
                return hit[2], hit[1], router, net
            fc = await build_forecast(scenario)
            sens = await build_sensors(scenario)
            model = await asyncio.to_thread(CaiModel, net, feats, fc["hours"], fc["now_index"], sens.get("model_sensors", sens["sensors"]), True, demo_scale(net, scenario))
            models[(scenario, mode)] = (time.time(), fc, model)
            return model, fc, router, net

    def hour_of(h: int | None, fc: dict) -> int:
        n = len(fc["hours"])
        return fc["now_index"] if h is None else max(0, min(int(h), n - 1))

    async def forecast_window(sc: str | None, refresh: bool = False):
        # Publish a complete immutable snapshot. A failed refresh leaves existing
        # snapshots usable, including routes that are still being preloaded.
        async with snapshot_lock:
            clock_hour = now().strftime("%Y-%m-%dT%H:00") if now else None
            if not refresh:
                for snapshot in reversed(list(snapshots.values())):
                    payload = snapshot["payload"]
                    if (payload["forecast"]["scenario"] == sc and time.time() - snapshot["created"] < MODEL_TTL
                            and (clock_hour is None or payload["forecast"]["hours"][0]["time"] == clock_hour)):
                        return payload
            elif refresh_sources:
                refresh_sources()
            fc, sens = await asyncio.gather(build_forecast(sc), build_sensors(sc))
            start = fc["now_index"]
            # Demo scenarios are hand-built 24 h replays; live data gets up to WINDOW_HOURS, and 24 is the floor when the feed (or an
            # offline fixture) is shorter.
            n_win = 24 if sc else min(C.WINDOW_HOURS, len(fc["hours"]) - start)
            indices = list(range(start, start + n_win))
            if n_win < 24 or (clock_hour and fc["hours"][start]["time"] != clock_hour):
                raise HTTPException(503, "A complete forecast for the next 24 hours is unavailable. Try refreshing again.")
            net, feats, _ = await get_static()

            def prepare():
                model = CaiModel(net, feats, fc["hours"], start, sens.get("model_sensors", sens["sensors"]), True, demo_scale(net, sc))
                values = []
                for i in indices:
                    h = model.hour(i)
                    values.append({"hour": i, "time": h["ctx"].time, "scenario": sc,
                                   "cai_base64": base64.b64encode(h["cai"][net.und_rep].tobytes()).decode("ascii"),
                                   "background": round(h["B"], 1), "bias": round(model.bias, 2),
                                   "heat_weight": round(h["ctx"].g, 2), "stagnation": round(h["ctx"].stagnation, 1)})
                return model, values

            model, values = await asyncio.to_thread(prepare)
            ident = uuid.uuid4().hex
            payload = {"id": ident, "forecast": {**fc, "hours": fc["hours"][start:start + n_win], "now_index": 0},
                       "hours": indices, "values": values, "sensors": {k: v for k, v in sens.items() if k != "model_sensors"}}
            snapshots[ident] = {"created": time.time(), "forecast": fc, "sensors": sens, "models": {"walk": model}, "payload": payload}
            # Bound memory while allowing an old tab/refresh to finish safely.
            while len(snapshots) > 4:
                snapshots.pop(next(iter(snapshots)))
            if refresh:
                models.clear()
            return payload

    @r.get("/api/forecast/window")
    async def get_forecast_window(scenario: str | None = scenario_query):
        return await forecast_window(parse_scenario(scenario))

    @r.post("/api/forecast/refresh")
    async def refresh_forecast_window(scenario: str | None = scenario_query):
        return await forecast_window(parse_scenario(scenario), refresh=True)

    # ---------------------------------------------------------------------------------------
    @r.get("/api/cai/geometry")
    async def geometry():
        """Static street geometry, binary: u32 n, u32 P, u32 offsets[n+1], f32 lonlat[2P], u8 class[n]."""
        if "geom" not in static:
            net, _, _ = await get_static("walk")
            rep = net.und_rep
            parts = [net.geometry(int(e)) for e in rep]
            offs = np.concatenate([[0], np.cumsum([len(p) for p in parts])]).astype("<u4")
            coords = np.concatenate(parts).astype("<f4")
            cls = np.array([CLASS_CODE[road_group(net.hw_names[int(net.e_hw[e])])] for e in rep], dtype="u1")
            static["geom"] = struct.pack("<II", len(rep), len(coords)) + offs.tobytes() + coords.tobytes() + cls.tobytes()
        return Response(static["geom"], media_type="application/octet-stream", headers={"Cache-Control": "public, max-age=3600"})

    @r.get("/api/cai/values")
    async def values(hour: int | None = None, scenario: str | None = scenario_query):
        model, fc, _, net = await get_model(parse_scenario(scenario))
        i = hour_of(hour, fc)
        h = model.hour(i)
        rep = net.und_rep
        return {
            "hour": i, "time": h["ctx"].time, "scenario": parse_scenario(scenario),
            "cai": h["cai"][rep].tolist(), "pm25": np.round(h["pm"][rep], 1).tolist(),
            "background": round(h["B"], 1), "bias": round(model.bias, 2),
            "heat_weight": round(h["ctx"].g, 2), "stagnation": round(h["ctx"].stagnation, 1),
        }

    @r.get("/api/cai/edge")
    async def edge(id: int, hour: int | None = None, scenario: str | None = scenario_query, forecast_id: str | None = None):
        model, fc, _, net = await get_model(parse_scenario(scenario), forecast_id=forecast_id)
        if not 0 <= id < len(net.und_rep):
            raise HTTPException(404, "unknown segment")
        e = int(net.und_rep[id])
        out = model.explain(e, hour_of(hour, fc))
        out.update({
            "id": id, **static[("namer", "walk")].edge(e),
            "road_class": net.hw_names[int(net.e_hw[e])], "length_m": round(float(net.e_len[e]), 1),
            "geometry": [[round(float(a), 5), round(float(b), 5)] for a, b in net.geometry(e)],  # so the UI can highlight it
            # measured on the arterial corridor that runs along this street (None elsewhere); observed, not part of the PM2.5 estimate
            "observed_traffic": traffic.edge_reading(net, e) if not parse_scenario(scenario) else None,
        })
        return out

    @r.get("/api/cai")
    async def cai(hour: int | None = None, bbox: str | None = None, limit: int = Query(20000, le=60000), scenario: str | None = scenario_query):
        """Segment GeoJSON (LineStrings) with CAI and the PM decomposition. bbox = west,south,east,north."""
        model, fc, _, net = await get_model(parse_scenario(scenario))
        i = hour_of(hour, fc)
        h = model.hour(i)
        rep = net.und_rep
        sel = np.arange(len(rep))
        if bbox:
            try:
                w, s, e, n = (float(x) for x in bbox.split(","))
            except ValueError as exc:
                raise HTTPException(422, "bbox must be west,south,east,north") from exc
            lon, lat = net.e_mid_lon[rep], net.e_mid_lat[rep]
            sel = np.flatnonzero((lon >= w) & (lon <= e) & (lat >= s) & (lat <= n))
        rank = np.array([CLASS_CODE[road_group(net.hw_names[int(net.e_hw[rep[k]])])] for k in sel])
        sel = sel[np.argsort(rank, kind="stable")][:limit]
        feats = []
        for k in sel:
            e_ = int(rep[k])
            ex = model.explain(e_, i)
            feats.append({
                "type": "Feature",
                "properties": {"id": int(k), "cai": ex["cai"], "pm25": ex["pm25"], **ex["components_ugm3"], "heat_points": ex["heat_points"],
                               "road_class": net.hw_names[int(net.e_hw[e_])]},
                "geometry": {"type": "LineString", "coordinates": [[round(float(a), 5), round(float(b), 5)] for a, b in net.geometry(e_)]},
            })
        return {"type": "FeatureCollection", "meta": {"hour": i, "time": h["ctx"].time, "count": len(feats), "scenario": parse_scenario(scenario)}, "features": feats}

    def _extremes(cells: dict, mode: str, higher_is_better: bool, key: str = "mean", n: int = 5) -> dict:
        """Name the n best and n worst areas (cells with >= 600 m of street, so tiny fringes do not win)."""
        idx = [i for i, m in enumerate(cells["street_m"]) if m >= 600]
        order = sorted(idx, key=lambda i: cells[key][i], reverse=True)
        best, worst = (order[:n], order[::-1][:n]) if higher_is_better else (order[::-1][:n], order[:n])
        namer = static[("namer", mode)]
        row = lambda i: {"lon": cells["lon"][i], "lat": cells["lat"][i], "value": cells[key][i], "street_m": cells["street_m"][i],
                         "place": namer.point(cells["lat"][i], cells["lon"][i])}
        return {"best": [row(i) for i in best], "worst": [row(i) for i in worst]}

    def _heat_areas(net: Net, fc: dict, i: int, mode: str, cell: float, scale: str, sc: str | None) -> dict:
        """Feels-like temperature per area cell for forecast hour `i`: the city's feels-like plus the learned local offset of each street."""
        hf = heat.get(net)
        hr = fc["hours"][i]
        city_feels = float(hr["apparent"] if hr.get("apparent") is not None else hr["t2m"])
        city_air = float(hr["t2m"])
        off = hf.offsets(hr)
        off = np.zeros(len(net.e_len), dtype="float32") if off is None else off
        cells = area_cells(net, city_feels + off.astype(float), {"air": city_air + off, "offset": off}, cell)
        if scale == "relative":
            centre, span = city_feels, max(1.5, float(np.percentile(np.abs(cells["offset"]), 95)))
            anomaly = [round(v - centre, 1) for v in cells["mean"]]
            thr = {"hot": round(float(np.percentile(anomaly, 90)), 1), "cool": round(float(np.percentile(anomaly, 10)), 1)}
        else:  # the diverging palette is centred on a mild 18 C and reaches full colour 20 degrees either side
            centre, span = heat.ABS_CENTRE, heat.ABS_SPAN
            anomaly = [round(v - centre, 1) for v in cells["mean"]]
            thr = {"hot": span, "cool": -span}
        cells["anomaly"] = anomaly
        stats = summarize(cells["mean"])
        return {
            "metric": "heat", "mode": mode, "cell_m": cell, "unit": "°C", "hour": i, "time": hr["time"], "scenario": sc, "scale": scale,
            "reference": round(city_feels, 1), "centre": centre, "span": span, "thresholds": thr, "stats": stats, "cells": cells,
            "extremes": _extremes(cells, mode, higher_is_better=False),
            "city": {"feels": round(city_feels, 1), "air": round(city_air, 1), "level": heat.level_for(stats["max"]), "city_level": heat.level_for(city_feels)},
            "model": hf.available, "validation": hf.meta() if hf.available else None,
            "note": ("Feels-like temperature: the city forecast for this hour plus how much warmer or cooler each place usually is in this weather "
                     "(built-up areas stay warm at night, higher ground is cooler), learned from about 220 citizen temperature sensors and tested on sensors it never saw. "
                     "Differences between places are small compared with the weather itself."
                     if hf.available else "Local temperature model not available: every place shows the city forecast."),
        }

    # ---------------------------------------------------------------------------------------
    @r.get("/api/cai/areas")
    async def areas(hour: int | None = None, mode: Mode = "walk", metric: Literal["air", "heat"] = "air",
                    heat_scale: Literal["absolute", "relative"] = Query("absolute", alias="scale"),
                    cell: int = Query(int(C.AREA_CELL_M), ge=100, le=1000), scenario: str | None = scenario_query, forecast_id: str | None = None):
        """Area cells (square, `cell` m) with the length-weighted mean of the streets inside.
        metric=air: Clean Air Index (and modelled PM2.5) for the hour. metric=heat: feels-like temperature for the hour (city forecast + learned local offset, backend/ml/heat_field.py);
        scale=absolute centres the colour on 18 C, scale=relative on the city value of the hour."""
        sc = parse_scenario(scenario)
        model, fc, _, net = await get_model(sc, mode, forecast_id)
        i = hour_of(hour, fc)
        if metric == "heat":
            return await asyncio.to_thread(_heat_areas, net, fc, i, mode, float(cell), heat_scale, sc)
        h = model.hour(i)
        cells = await asyncio.to_thread(area_cells, net, h["cai"].astype(float), {"pm25": h["pm"]}, float(cell))
        cut = {"clean": round(float(np.percentile(cells["mean"], 90)), 1), "dirty": round(float(np.percentile(cells["mean"], 10)), 1)}
        return {"metric": "air", "mode": mode, "cell_m": cell, "unit": "CAI", "hour": i, "time": h["ctx"].time, "scenario": sc,
                "thresholds": cut, "stats": summarize(cells["mean"]), "cells": cells, "extremes": _extremes(cells, mode, higher_is_better=True),
                "note": "Length-weighted mean Clean Air Index of the streets in each cell. Indicative screening model."}

    # ---------------------------------------------------------------------------------------
    def _pt(s: str, name: str) -> tuple[float, float]:
        try:
            a, b = (float(x) for x in s.split(","))
        except ValueError as exc:
            raise HTTPException(422, f"{name} must be 'lat,lng'") from exc
        if not (-90 <= a <= 90 and -180 <= b <= 180):
            raise HTTPException(422, f"{name} out of range")
        return a, b

    def _score_ext(router: Router, pts_latlng, h) -> dict:
        arr = np.array([(lng, lat) for lat, lng in pts_latlng])
        return router.score_polyline(arr, h["pm"], h["cai"], h["B"])

    @r.get("/api/route")
    async def route(
        from_: str = Query(alias="from"), to: str = Query(...), lam: float | None = Query(None, alias="lambda", ge=0, le=3),
        hour: int | None = None, mode: Mode = "walk", scenario: str | None = scenario_query,
        forecast_id: str | None = None, include_google: bool = True,
        allergy: bool = False, species: list[Species] = Query([]),
    ):
        o, d = _pt(from_, "from"), _pt(to, "to")
        model, fc, router, net = await get_model(parse_scenario(scenario), mode, forecast_id)
        i = hour_of(hour, fc)
        h = model.hour(i)
        G = static[("v", mode)][1].G
        cb, allergy_info = h["cr"], None
        if allergy:  # experimental: add the user's pollen level times the street's greenery to the burden (see C.POLLEN_GREEN_WEIGHT)
            pol = (fc["hours"][i].get("pollen") or {})
            level = max((_pollen_level(k, v) for k, v in pol.items() if v is not None and (not species or k in species)), default=0)
            cb = h["cr"] + C.POLLEN_GREEN_WEIGHT * level * G
            allergy_info = {"level": level, "weight": C.POLLEN_GREEN_WEIGHT, "species": sorted(set(species)),
                            "note": "Experimental: streets with more greenery count as worse in proportion to the pollen level. Greenery is only a proxy for pollen sources."}
        try:
            res = await asyncio.to_thread(router.routes, o, d, cb, h["pm"], h["cai"], lam)
        except RouteError as exc:
            raise HTTPException(422, str(exc)) from exc
        routes = {k: {kk: vv for kk, vv in v.items() if kk != "edges"} for k, v in res.items()}
        for k, v in res.items():  # length-weighted greenery along the route, so the allergy trade-off is visible
            ln = net.e_len[v["edges"]].astype(float)
            routes[k]["mean_green_pct"] = round(100 * float((G[v["edges"]] * ln).sum() / ln.sum()), 1) if ln.sum() else 0.0
        confidence = None  # is any alternative really cleaner than the fastest route, given the street-level uncertainty?
        if getattr(model, "spatial", None) and "fastest" in res and not allergy:
            month = int(h["ctx"].time[5:7]) if h["ctx"].time[5:7].isdigit() else None
            confidence = route_confidence.summarise({
                k: route_confidence.compare(net, res["fastest"]["edges"], v["edges"], routes["fastest"]["mean_pm25"], v["mean_pm25"], model.spatial, month)
                for k, v in res.items() if k != "fastest"})
        clean = routes["cleanest"]["polyline"]

        out = {
            "from": o, "to": d, "hour": i, "time": h["ctx"].time, "scenario": parse_scenario(scenario), "mode": mode,
            "from_label": static[("namer", mode)].point(*o), "to_label": static[("namer", mode)].point(*d),
            "routes": routes, "confidence": confidence, "allergy": allergy_info, "google_status": "no_key", "google_baseline": None,
            "google_available": mode == "walk" and google_routes.enabled(),
            "snapped": {"used": False, "polyline": clean, "eta_min": None, "waypoints": []},
            "open_in_google_maps": google_routes.maps_url(o, d),
            "note": "Indicative: modelled PM2.5; " + ("cycling at 15 km/h with an assumed 2x breathing rate" if mode == "bike" else "walking at 4.8 km/h") + "; routes follow OpenStreetMap data.",
        }
        wp = douglas_peucker(np.array(clean), C_MAX_WAYPOINTS + 2)[1:-1]
        wp_latlng = [(float(p[1]), float(p[0])) for p in wp]
        out["snapped"]["waypoints"] = [[a, b] for a, b in wp_latlng]
        out["open_in_google_maps_clean"] = google_routes.maps_url(o, d, wp_latlng)
        if mode == "bike":
            out["google_status"] = "n/a"  # Routes API cycling is a higher billing tier: not used
        elif include_google and google_routes.enabled():
            base = await google_routes.walk(o, d)
            if base:
                try:
                    sc = _score_ext(router, base["polyline"], h)
                    sc.update({
                        "eta_min": round(base["duration_s"] / 60, 1), "polyline": [[lng, lat] for lat, lng in base["polyline"]],
                        "dose_change_pct": round(100 * (sc["dose"] / routes["fastest"]["dose"] - 1), 1) if routes["fastest"]["dose"] else 0.0,
                    })
                    out["google_baseline"], out["google_status"] = sc, "ok"
                except RouteError:
                    out["google_status"] = "error"
            else:
                out["google_status"] = "error"
            snapped = await google_routes.walk(o, d, wp_latlng)
            if snapped:  # on any error we keep our own polyline and snapped.used = False
                out["snapped"] = {**out["snapped"], "used": True, "eta_min": round(snapped["duration_s"] / 60, 1),
                                  "polyline": [[lng, lat] for lat, lng in snapped["polyline"]]}
        return out

    # ---------------------------------------------------------------------------------------
    @r.get("/api/network")
    async def network(hour: int | None = None, mode: Mode = "walk", scenario: str | None = scenario_query, forecast_id: str | None = None):
        sc = parse_scenario(scenario)
        model, fc, router, net = await get_model(sc, mode, forecast_id)
        i = hour_of(hour, fc)
        key = (model, i, mode)
        async with locks["network"]:
            if key not in networks:
                h = model.hour(i)
                feats = static[("v", mode)][1]
                networks[key] = await asyncio.to_thread(compute_network, net, router, h["cr"], h["pm"], h["cai"], feats)
            cached = networks[key]
            while len(networks) > 48:
                networks.pop(next(iter(networks)))
        if "named" not in cached:
            kept = []
            for c in cached["chokepoints"]:  # Latin name for the UI, Cyrillic kept, plus the district
                c["street_bg"], c["street"] = c["street"], translit(c["street"])
                c["district"] = district_at(c["lon"], c["lat"])
                if c["district"]:  # the walkable bbox spills into neighbouring municipalities Sofia cannot act on
                    kept.append(c)
            cached["chokepoints"] = kept[: C.CHOKEPOINT_TOP]
            for rank, c in enumerate(cached["chokepoints"], start=1):
                c["rank"] = rank
            cached["named"] = True
        out = dict(cached)
        out.update({"hour": i, "time": model.ctxs[i].time, "scenario": sc, "mode": mode})
        return out

    def _daily_ctx(model: CaiModel, now_idx: int) -> HourCtx:
        """Context for a 24 h-mean target: today's mean CAMS / stagnation."""
        cs = model.ctxs[: now_idx + 1]
        cams = [c.cams for c in cs if c.cams is not None]
        weekend = cs[-1].weekend
        return HourCtx(
            time="24 h mean", cams=float(np.mean(cams)) if cams else None, stagnation=float(np.mean([c.stagnation for c in cs])),
            apparent=float(np.mean([c.apparent for c in cs])), daylight=0.3, hour=12, weekend=weekend,
        )

    @r.get("/api/validation")
    async def validation(scenario: str | None = scenario_query):
        """Leave-one-out validation against outdoor citizen sensors: latest snapshot and 24 h means."""
        sc = parse_scenario(scenario)
        model, fc, _, _ = await get_model(sc)
        ctx = model.ctxs[fc["now_index"]]
        out = await asyncio.to_thread(leave_one_out, model.feats, model.sensors, ctx)
        out["ml"] = await asyncio.to_thread(leave_one_out_ml, model)
        out["trust"] = trust.summary(getattr(model, "ml", None))
        out["scenario"] = sc
        out["daily_mean"] = None
        if sc:
            out["warning"] = "Demo scenario: sensor values are simulated from live positions, so this is not a real validation."
        elif build_sensors_24h:
            try:
                s24 = await build_sensors_24h()
                dctx = _daily_ctx(model, fc["now_index"])
                daily = await asyncio.to_thread(leave_one_out, model.feats, s24, dctx)
                out["daily_mean"] = daily
            except Exception as exc:  # noqa: BLE001
                log.warning("24 h validation unavailable: %r", exc)
        return out

    @r.get("/api/traffic")
    async def traffic_state():
        """Corridors with live and typical travel-time index, the measured hourly profile and request counts."""
        if traffic.enabled():
            net, _, _ = await get_static()
            traffic.kick(net)  # background refresh; this response shows what is known now
        return traffic.snapshot()

    @r.get("/api/traffic/incidents")
    async def traffic_incidents():
        return await traffic.incidents()

    @r.get("/api/traffic/tiles/{z}/{x}/{y}.png")
    async def traffic_tile(z: int, x: int, y: int):
        t = await traffic.flow_tile(z, x, y)
        if t is None:
            raise HTTPException(404, "No traffic tile")
        return Response(content=t[0], media_type=t[1], headers={"Cache-Control": "public, max-age=120"})

    async def warmup():
        try:
            if traffic.enabled():
                traffic.load_saved_profile()
            net, _, _ = await get_static()
            traffic.kick(net)
            await get_model(None)
            log.info("CAI engine warm")
        except Exception as exc:  # noqa: BLE001
            log.warning("CAI warm-up failed: %r", exc)

    r.warmup = warmup  # type: ignore[attr-defined]
    return r


C_MAX_WAYPOINTS = google_routes.MAX_INTERMEDIATES
