#!/usr/bin/env python3
"""Build public/data/districts.geojson (+ lez_illustrative.geojson).

Tries Overpass (OSM admin_level 6 in the Sofia bbox: the 24 city districts; level 9 is
neighbourhoods, which is too fine) with retries and exponential backoff across
two mirrors. If that fails, writes a hand-made rough 8-zone grid, every feature marked
"approx": true. Run once at build time and commit the output; the app never calls Overpass.

    python scripts/fetch_districts.py            # try Overpass, fall back to the grid
    python scripts/fetch_districts.py --offline  # skip Overpass, write the grid
"""
import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "public" / "data"
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
BBOX = (42.55, 23.10, 42.85, 23.60)  # south, west, north, east
QUERY = """[out:json][timeout:60];
rel["boundary"="administrative"]["admin_level"="6"]({s},{w},{n},{e});
out geom;"""

# OSM name:en spellings are inconsistent; use the standard English forms of the 24 district names.
NAME_FIX = {"Nadejda": "Nadezhda", "Kremikovci": "Kremikovtsi", "Sredec": "Sredets", "Vazrajdane": "Vazrazhdane",
            "Krasna poliana": "Krasna Polyana", "Ovcha kupel": "Ovcha Kupel", "Krasno selo": "Krasno Selo"}

# Vitosha foothill reference point; closer to it means higher ground, so weaker pollution trapping.
VITOSHA = (42.57, 23.27)


def elevation_factor(lat: float, lon: float) -> float:
    """Rough proxy: basin floor ~1.15, Vitosha foothills ~0.75."""
    d_km = math.hypot((lat - VITOSHA[0]) * 111.0, (lon - VITOSHA[1]) * 82.0)
    closeness = max(0.0, min(1.0, (22.0 - d_km) / 22.0)) ** 1.5
    return round(1.15 - 0.40 * closeness, 2)


# ---- Overpass path --------------------------------------------------------------------------


def post_overpass(url: str, query: str, timeout: int = 90) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "inversion-watch-hackathon/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_overpass(attempts_per_mirror: int = 3) -> dict | None:
    q = QUERY.format(s=BBOX[0], w=BBOX[1], n=BBOX[2], e=BBOX[3])
    for url in MIRRORS:
        for i in range(attempts_per_mirror):
            try:
                print(f"Overpass {url} attempt {i + 1}", file=sys.stderr)
                return post_overpass(url, q)
            except Exception as exc:  # noqa: BLE001
                wait = 2 ** (i + 1)
                print(f"  failed: {exc!r}; backing off {wait}s", file=sys.stderr)
                time.sleep(wait)
    return None


def stitch(ways: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Join way fragments (lon, lat) into closed rings."""
    ways = [w[:] for w in ways if len(w) > 1]
    rings = []
    while ways:
        ring = ways.pop(0)
        progress = True
        while progress and ring[0] != ring[-1]:
            progress = False
            for i, w in enumerate(ways):
                if w[0] == ring[-1]:
                    ring += w[1:]
                elif w[-1] == ring[-1]:
                    ring += w[::-1][1:]
                elif w[-1] == ring[0]:
                    ring = w[:-1] + ring
                elif w[0] == ring[0]:
                    ring = w[::-1][:-1] + ring
                else:
                    continue
                ways.pop(i)
                progress = True
                break
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring)
    return rings


def overpass_to_features(payload: dict) -> list[dict]:
    feats = []
    for el in payload.get("elements", []):
        if el.get("type") != "relation":
            continue
        tags = el.get("tags", {})
        outer = [
            [(p["lon"], p["lat"]) for p in m["geometry"]]
            for m in el.get("members", [])
            if m.get("role") == "outer" and m.get("type") == "way" and m.get("geometry")
        ]
        rings = stitch(outer)
        if not rings:
            continue
        name = tags.get("name:en") or tags.get("int_name") or tags.get("name") or f"Relation {el['id']}"
        name = NAME_FIX.get(name, name)
        pts = [p for r in rings for p in r]
        clon = sum(p[0] for p in pts) / len(pts)
        clat = sum(p[1] for p in pts) / len(pts)
        feats.append(
            {
                "type": "Feature",
                "properties": {
                    "name": name,
                    "name_bg": tags.get("name"),
                    "osm_id": el["id"],
                    "elevation_factor": elevation_factor(clat, clon),
                    "approx": False,
                },
                "geometry": {"type": "MultiPolygon", "coordinates": [[[[round(p[0], 4), round(p[1], 4)] for p in r]] for r in rings]},
            }
        )
    return feats


# ---- Hand-made fallback ---------------------------------------------------------------------

A, B, C, D, E = 23.20, 23.28, 23.34, 23.38, 23.46
R0, R1, R2, R3 = 42.60, 42.66, 42.70, 42.76

FALLBACK = [
    # name, (west, south, east, north), elevation factor (basin floor > 1, foothills < 1)
    ("Vitosha", (A, R0, C, R1), 0.75),
    ("Lyulin", (A, R1, B, R3), 0.90),
    ("Center", (B, R1, C, R2), 1.05),
    ("Nadezhda", (B, R2, D, R3), 1.10),
    ("Studentski", (C, R0, D, R1), 0.95),
    ("Slatina", (C, R1, D, R2), 1.08),
    ("Mladost", (D, R0, E, R1), 0.95),
    ("Iskar", (D, R1, E, R3), 1.10),
]


def rect(w: float, s: float, e: float, n: float) -> list[list[list[float]]]:
    return [[[w, s], [e, s], [e, n], [w, n], [w, s]]]


def fallback_features() -> list[dict]:
    return [
        {
            "type": "Feature",
            "properties": {"name": name, "elevation_factor": f, "approx": True},
            "geometry": {"type": "Polygon", "coordinates": rect(*box)},
        }
        for name, box, f in FALLBACK
    ]


def lez_features() -> list[dict]:
    label = "Low-emission zone (illustrative, not official)"

    def poly(name, zone, cx, cy, rx, ry, n=28):
        ring = [
            [round(cx + rx * math.cos(2 * math.pi * i / n), 5), round(cy + ry * math.sin(2 * math.pi * i / n), 5)]
            for i in range(n)
        ]
        ring.append(ring[0])
        return {
            "type": "Feature",
            "properties": {"name": name, "zone": zone, "label": label, "illustrative": True},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        }

    return [
        poly("LEZ outer", "outer", 23.322, 42.690, 0.060, 0.034),
        poly("LEZ inner", "inner", 23.322, 42.694, 0.028, 0.016),
    ]


def write(path: Path, feats: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False))
    print(f"wrote {path} ({len(feats)} features)", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="skip Overpass, write the fallback grid")
    args = ap.parse_args()

    feats: list[dict] = []
    if not args.offline:
        payload = fetch_overpass()
        if payload:
            feats = overpass_to_features(payload)
    if len(feats) < 6:
        print("Overpass unavailable or too few districts; writing the rough 8-zone fallback", file=sys.stderr)
        feats = fallback_features()
    write(OUT_DIR / "districts.geojson", feats)
    write(OUT_DIR / "lez_illustrative.geojson", lez_features())


if __name__ == "__main__":
    main()
