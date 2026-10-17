"""Download the osmnx walking graph and cache it to data/graph.graphml (and with --roads, the major-road
graph to data/roads.graphml). GraphML is large; `python -m backend.cai.graph` bakes a compact data/net.npz.

    python -m backend.data_prep.graph_dl            # full bbox (42.60,23.15)-(42.80,23.55)
    python -m backend.data_prep.graph_dl --central  # smaller central bbox, faster
"""
import argparse
import sys
import time
from pathlib import Path

import osmnx as ox

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "graph.graphml"
FULL = (23.15, 42.60, 23.55, 42.80)  # west, south, east, north
CENTRAL = (23.25, 42.65, 23.42, 42.74)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--central", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--roads", action="store_true", help="download major roads (motorway..tertiary) for the traffic term")
    ap.add_argument("--bike", action="store_true", help="download the cycling network (cycleways + bikeable roads) -> use with --out data/graph_bike.graphml")
    args = ap.parse_args()
    bbox = CENTRAL if args.central else FULL
    ox.settings.requests_timeout = 240
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(ROOT / "data" / ".osmnx_cache")
    last = None
    for attempt in range(1, 5):
        try:
            print(f"downloading walk graph bbox={bbox} attempt {attempt}", file=sys.stderr)
            if args.roads:
                G = ox.graph_from_bbox(
                    bbox, simplify=True, retain_all=True,
                    custom_filter='["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link)$"]',
                )
            else:
                G = ox.graph_from_bbox(bbox, network_type="bike" if args.bike else "walk", simplify=True, retain_all=False)
            ox.save_graphml(G, args.out)
            print(f"saved {args.out}: {len(G.nodes)} nodes, {len(G.edges)} edges", file=sys.stderr)
            return 0
        except Exception as exc:  # noqa: BLE001
            last = exc
            wait = 5 * 2**attempt
            print(f"  failed: {exc!r}; retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    print(f"giving up: {last!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
