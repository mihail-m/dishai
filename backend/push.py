"""Web Push notifications for alerts (backend/alerts.py).

Privacy: a subscription is the browser's push endpoint and keys (needed to send anything at all) plus which kinds of alert and which minimum level
the person chose. No location, name, e-mail or district is stored. The file data/push_subs.json is git-ignored; unsubscribing deletes the row.

Setup, once:   python -m backend.push --gen-keys     and paste the three lines into .env (VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT).
Without the keys (or without the pywebpush package) push is simply off and the app falls back to notifications while the page is open.

Only real push services are accepted as endpoints: the server makes an HTTPS request to whatever endpoint it is given, so an open endpoint would let
anyone aim it at an arbitrary address.
"""
import argparse
import asyncio
import base64
import json
import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from .alerts import KINDS, LEVELS, rank
from .cai.graph import DATA

log = logging.getLogger("inversion_watch")
STORE = DATA / "push_subs.json"
MAX_SUBSCRIPTIONS = 5000
ALLOWED_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com", ".push.apple.com",
                 ".notify.windows.com", ".push.services.mozilla.com")
_lock = threading.Lock()


def keys() -> tuple[str, str, str] | None:
    pub, priv = os.environ.get("VAPID_PUBLIC_KEY"), os.environ.get("VAPID_PRIVATE_KEY")
    if not (pub and priv):
        return None
    return pub, priv, os.environ.get("VAPID_SUBJECT", "mailto:admin@example.org")


def enabled() -> bool:
    try:
        import pywebpush  # noqa: F401
    except ImportError:
        return False
    return keys() is not None


def endpoint_ok(endpoint: str) -> bool:
    u = urlparse(endpoint)
    host = u.hostname or ""
    return u.scheme == "https" and len(endpoint) < 1000 and any(host == h.lstrip(".") or (h.startswith(".") and host.endswith(h)) for h in ALLOWED_HOSTS)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"subs": {}, "sent": {}}


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _id(endpoint: str) -> str:
    return base64.urlsafe_b64encode(endpoint.encode())[-40:].decode()


def subscribe(sub: dict, kinds: list[str], min_level: str, path: Path = STORE) -> str:
    """Add or update a subscription. `sub` is the browser's PushSubscription.toJSON(). Returns its id."""
    ep, k = sub.get("endpoint", ""), sub.get("keys") or {}
    if not endpoint_ok(ep) or not k.get("p256dh") or not k.get("auth"):
        raise ValueError("not a valid web push subscription")
    kinds = [x for x in kinds if x in KINDS] or list(KINDS)
    if min_level not in LEVELS:
        min_level = "watch"
    with _lock:
        d = _read(path)
        if _id(ep) not in d["subs"] and len(d["subs"]) >= MAX_SUBSCRIPTIONS:
            raise ValueError("too many subscriptions")
        d["subs"][_id(ep)] = {"sub": {"endpoint": ep, "keys": {"p256dh": k["p256dh"], "auth": k["auth"]}}, "kinds": kinds, "min_level": min_level, "created": int(time.time())}
        _write(path, d)
    return _id(ep)


def unsubscribe(endpoint: str, path: Path = STORE) -> bool:
    with _lock:
        d = _read(path)
        gone = d["subs"].pop(_id(endpoint), None) is not None
        d["sent"].pop(_id(endpoint), None)
        _write(path, d)
    return gone


def wants(row: dict, alert: dict) -> bool:
    return alert["kind"] in row["kinds"] and rank(alert["level"]) >= rank(row["min_level"])


def payload(alert: dict) -> str:
    return json.dumps({"title": alert["title"], "body": alert["message"], "tag": alert["id"], "level": alert["level"], "url": "/"})


def send_webpush(sub: dict, data: str) -> int:
    """Send one notification (blocking). Returns the HTTP status; 404 / 410 mean the subscription is gone."""
    from pywebpush import WebPushException, webpush

    pub, priv, subject = keys()  # type: ignore[misc]
    try:
        r = webpush(subscription_info=sub, data=data, vapid_private_key=priv, vapid_claims={"sub": subject}, ttl=6 * 3600, timeout=15)
        return int(getattr(r, "status_code", 201))
    except WebPushException as exc:
        return int(getattr(exc.response, "status_code", 0) or 0)


def dispatch(alerts: list[dict], send=send_webpush, path: Path = STORE) -> dict:
    """Send each alert once per (alert id, level) to every subscription that asked for it. Drops subscriptions the push service reports gone."""
    sent = failed = 0
    with _lock:
        d = _read(path)
        for sid, row in list(d["subs"].items()):
            seen = set(d["sent"].setdefault(sid, []))
            for a in alerts:
                key = f"{a['id']}:{a['level']}"
                if key in seen or not wants(row, a):
                    continue
                code = send(row["sub"], payload(a))
                if code in (404, 410):
                    d["subs"].pop(sid, None)
                    d["sent"].pop(sid, None)
                    failed += 1
                    break
                if 200 <= code < 300:
                    seen.add(key)
                    sent += 1
                else:
                    failed += 1
            else:
                d["sent"][sid] = sorted(seen)[-200:]
        _write(path, d)
    return {"sent": sent, "failed": failed, "subscriptions": len(d["subs"])}


def count(path: Path = STORE) -> int:
    return len(_read(path)["subs"])


async def loop(compute_alerts, every_s: int = 600) -> None:
    """Background task: re-evaluate the alerts and push new ones. Never raises."""
    while True:
        try:
            if enabled() and count():
                r = await asyncio.to_thread(dispatch, await compute_alerts())
                if r["sent"] or r["failed"]:
                    log.info("push: %s", r)
        except Exception as exc:  # noqa: BLE001
            log.warning("push loop: %r", exc)
        await asyncio.sleep(every_s)


def generate_keys() -> None:
    from cryptography.hazmat.primitives import serialization as s
    from py_vapid import Vapid

    v = Vapid()
    v.generate_keys()
    b64 = lambda x: base64.urlsafe_b64encode(x).rstrip(b"=").decode()
    print(f"VAPID_PUBLIC_KEY={b64(v.public_key.public_bytes(s.Encoding.X962, s.PublicFormat.UncompressedPoint))}")
    print(f"VAPID_PRIVATE_KEY={b64(v.private_key.private_numbers().private_value.to_bytes(32, 'big'))}")
    print("VAPID_SUBJECT=mailto:you@example.org")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen-keys", action="store_true")
    if ap.parse_args().gen_keys:
        generate_keys()
