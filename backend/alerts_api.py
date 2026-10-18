"""/api/alerts and the web-push endpoints. Mounted by backend.main."""
import asyncio
import logging
import time
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from . import alerts as alerts_mod
from . import fires, green_ticket, plumes, push
from .alerts import KINDS, LEVELS
from .score import WATCH_FROM

log = logging.getLogger("inversion_watch")
TEST_COOLDOWN_S = 60


class SubscribeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscription: dict
    kinds: list[Literal["pm", "heat", "dust", "fire", "transport"]] = []
    min_level: Literal["info", "watch", "warning"] = "watch"


class EndpointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str


def make_router(build_forecast, parse_scenario, scenario_query, cache) -> APIRouter:
    r = APIRouter()
    last_test: dict[str, float] = {}

    async def cams_rows() -> tuple[list[dict], str]:
        """Hourly CAMS aerosol fields (PM10, PM2.5, dust, optical depth, CO) for 2 days back and 4 ahead."""
        async def fetch():
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as c:
                resp = await c.get(plumes.CAMS_URL)
                resp.raise_for_status()
                return resp.json()
        try:
            data, src = await cache.get("open_meteo_cams_aerosol", fetch, persist=False)
        except Exception:  # noqa: BLE001
            return [], "unavailable"
        return plumes.rows_from(data), src

    async def fire_threat() -> tuple[dict | None, dict]:
        async def fetch():
            async with httpx.AsyncClient(timeout=25, follow_redirects=True) as c:
                det, wind = await asyncio.gather(fires.fetch_detections(c), fires.fetch_wind(c))
                return {"detections": det, "wind": wind}
        try:
            data, src = await cache.get("firms_viirs", fetch, persist=False)
        except Exception as exc:  # noqa: BLE001
            return None, {"enabled": True, "error": type(exc).__name__}
        threat = fires.smoke_threat(data["detections"], tuple(data["wind"]) if data["wind"] else None)
        return threat, {"enabled": True, "detections": len(data["detections"]), "source": src}

    def stagnant(fc: dict) -> bool:
        """Trapped air in the next 24 h (stagnation score at or above the watch level)."""
        i = fc["now_index"]
        return any(h["score"] >= WATCH_FROM for h in fc["hours"][i : i + 24])

    async def compute(sc: str | None) -> dict:
        fc = await build_forecast(sc)
        plume = None
        if sc:  # demo scenarios are replays: no live dust or fire feed
            rows, dsrc, threat, fsrc = [], "scenario", None, {"enabled": False, "reason": "demo scenario"}
        else:
            (rows, dsrc), (threat, fsrc) = await asyncio.gather(cams_rows(), fire_threat())
            if rows:
                plume = plumes.assess(rows, fc["generated_at"], threat, stagnant(fc))
        dust = [{"time": r["time"], "dust": r["dust"]} for r in rows]
        tickets = [] if sc else green_ticket.announced(fc["generated_at"][:10])
        return {"generated_at": fc["generated_at"], "scenario": sc, "alerts": alerts_mod.build_alerts(fc, dust, threat, fc["generated_at"], plume, tickets),
                "sources": {"forecast": fc["source"], "cams_dust": dsrc, "fires": fsrc}, "push": {"enabled": push.enabled()}, "plume": plume,
                "fire_threat": threat, "green_ticket": tickets}

    @r.get("/api/alerts")
    async def get_alerts(scenario: str | None = scenario_query):
        """Active and upcoming alerts for the next 48 h, most severe first. kinds: pm (PM2.5 episode), heat, dust (CAMS mineral dust), fire (smoke from upwind fires)."""
        return await compute(parse_scenario(scenario))

    @r.get("/api/plumes")
    async def get_plumes(scenario: str | None = scenario_query):
        """Dust, smoke and local-pollution outlook straight from the CAMS forecast: daily PM10 / PM2.5 against the month's usual, whether a day is worse than
        usual, what kind of event it probably is and why, the hourly series, and any green ticket the municipality has announced. Demo scenarios have no live feed."""
        res = await compute(parse_scenario(scenario))
        return {k: res[k] for k in ("generated_at", "scenario", "plume", "fire_threat", "green_ticket", "sources")}

    @r.get("/api/push/key")
    async def push_key():
        k = push.keys()
        return {"enabled": push.enabled(), "public_key": k[0] if k and push.enabled() else None, "kinds": list(KINDS), "levels": list(LEVELS)}

    @r.post("/api/push/subscribe")
    async def push_subscribe(body: SubscribeIn):
        if not push.enabled():
            raise HTTPException(503, "Push notifications are not configured on this server")
        try:
            sid = await asyncio.to_thread(push.subscribe, body.subscription, list(body.kinds), body.min_level)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True, "id": sid, "kinds": body.kinds or list(KINDS), "min_level": body.min_level}

    @r.post("/api/push/unsubscribe")
    async def push_unsubscribe(body: EndpointIn):
        return {"ok": True, "removed": await asyncio.to_thread(push.unsubscribe, body.endpoint)}

    @r.post("/api/push/test")
    async def push_test(body: EndpointIn):
        """Send one test notification to a subscription so a person can see that it works (once a minute per subscription)."""
        if not push.enabled():
            raise HTTPException(503, "Push notifications are not configured on this server")
        now = time.time()
        if now - last_test.get(body.endpoint, 0) < TEST_COOLDOWN_S:
            raise HTTPException(429, "Wait a minute before sending another test")
        row = push._read(push.STORE)["subs"].get(push._id(body.endpoint))
        if not row:
            raise HTTPException(404, "Not subscribed")
        last_test[body.endpoint] = now
        code = await asyncio.to_thread(push.send_webpush, row["sub"], '{"title":"DishAI alerts are on","body":"You will get a message like this when air or heat needs your attention.","tag":"test","level":"info","url":"/"}')
        return {"ok": 200 <= code < 300, "status": code}

    @r.on_event("startup")
    async def _push_loop():
        asyncio.create_task(push.loop(lambda: _active()))

    async def _active() -> list[dict]:
        return (await compute(None))["alerts"]

    return r
