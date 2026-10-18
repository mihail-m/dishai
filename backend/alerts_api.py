"""/api/alerts and the web-push endpoints. Mounted by backend.main."""
import asyncio
import logging
import time
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from . import alerts as alerts_mod
from . import fires, push
from .alerts import KINDS, LEVELS

log = logging.getLogger("inversion_watch")
LAT, LON = fires.LAT0, fires.LON0
DUST_URL = ("https://air-quality-api.open-meteo.com/v1/air-quality"
            f"?latitude={LAT}&longitude={LON}&hourly=dust&forecast_days=3&timezone=Europe%2FSofia")
TEST_COOLDOWN_S = 60


class SubscribeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscription: dict
    kinds: list[Literal["pm", "heat", "dust", "fire"]] = []
    min_level: Literal["info", "watch", "warning"] = "watch"


class EndpointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str


def make_router(build_forecast, parse_scenario, scenario_query, cache) -> APIRouter:
    r = APIRouter()
    last_test: dict[str, float] = {}

    async def dust_series() -> tuple[list[dict], str]:
        async def fetch():
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as c:
                resp = await c.get(DUST_URL)
                resp.raise_for_status()
                return resp.json()
        try:
            data, src = await cache.get("open_meteo_cams_dust", fetch, persist=False)
        except Exception:  # noqa: BLE001
            return [], "unavailable"
        h = data["hourly"]
        return [{"time": t, "dust": v} for t, v in zip(h["time"], h["dust"])], src

    async def fire_threat() -> tuple[dict | None, dict]:
        if not fires.enabled():
            return None, {"enabled": False, "reason": "No FIRMS key (set FIRMS_MAP_KEY). Smoke-from-fire alerts are off."}

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

    async def compute(sc: str | None) -> dict:
        fc = await build_forecast(sc)
        if sc:  # demo scenarios are replays: no live dust or fire feed
            dust, dsrc, threat, fsrc = [], "scenario", None, {"enabled": False, "reason": "demo scenario"}
        else:
            (dust, dsrc), (threat, fsrc) = await asyncio.gather(dust_series(), fire_threat())
        return {"generated_at": fc["generated_at"], "scenario": sc, "alerts": alerts_mod.build_alerts(fc, dust, threat, fc["generated_at"]),
                "sources": {"forecast": fc["source"], "cams_dust": dsrc, "fires": fsrc},
                "push": {"enabled": push.enabled()}}

    @r.get("/api/alerts")
    async def get_alerts(scenario: str | None = scenario_query):
        """Active and upcoming alerts for the next 48 h, most severe first. kinds: pm (PM2.5 episode), heat, dust (CAMS mineral dust), fire (smoke from upwind fires)."""
        return await compute(parse_scenario(scenario))

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
