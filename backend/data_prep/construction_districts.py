"""Share of each Sofia district covered by OSM construction sites -> public/data/construction_districts.json

Precomputed (needs shapely) so the runtime image does not: the "For you" advice cards read this file.

    python -m backend.data_prep.construction_districts
"""
import json
import sys

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

from .stac_common import DATA, ROOT

TO_UTM = Transformer.from_crs(4326, 32634, always_xy=True).transform


def main() -> int:
    districts = json.load(open(ROOT / "public" / "data" / "districts.geojson"))["features"]
    sites = [transform(TO_UTM, shape(f["geometry"])) for f in json.load(open(DATA / "construction.geojson"))["features"]]
    union = unary_union(sites)
    tree = STRtree(sites)
    rows = {}
    for f in districts:
        g = transform(TO_UTM, shape(f["geometry"]))
        covered = union.intersection(g).area
        n = sum(1 for i in tree.query(g) if sites[i].centroid.within(g))
        rows[f["properties"]["name"]] = {"share_pct": round(100 * covered / g.area, 2), "n_sites": n, "area_km2": round(g.area / 1e6, 1)}
    shares = sorted(r["share_pct"] for r in rows.values())
    out = {"districts": rows, "high_from_pct": shares[int(len(shares) * 2 / 3)],  # top third of districts
           "source": "OpenStreetMap landuse/building=construction, mapped sites only; not dated, may include finished sites"}
    (ROOT / "public" / "data" / "construction_districts.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    top = sorted(rows.items(), key=lambda kv: -kv[1]["share_pct"])[:5]
    print(f"{len(rows)} districts; top third from {out['high_from_pct']}%; highest:", [(k, v["share_pct"], v["n_sites"]) for k, v in top], file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
