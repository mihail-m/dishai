"""Tiny GeoJSON helpers (no shapely dependency)."""
import json
from pathlib import Path
from typing import Iterable

Ring = list[list[float]]


def polygons(geometry: dict) -> list[list[Ring]]:
    """Normalise Polygon/MultiPolygon into a list of polygons (each: outer ring + holes)."""
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    return []


def _in_ring(lon: float, lat: float, ring: Ring) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_geometry(lon: float, lat: float, geometry: dict) -> bool:
    for poly in polygons(geometry):
        if _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, h) for h in poly[1:]):
            return True
    return False


def bbox(geometry: dict) -> tuple[float, float, float, float]:
    pts: Iterable = (p for poly in polygons(geometry) for p in poly[0])
    xs, ys = zip(*((p[0], p[1]) for p in pts))
    return min(xs), min(ys), max(xs), max(ys)


def centroid(geometry: dict) -> tuple[float, float]:
    """Vertex-average of the largest outer ring. Good enough for a label point / zone lookup."""
    largest = max((poly[0] for poly in polygons(geometry)), key=len)
    n = len(largest)
    return sum(p[0] for p in largest) / n, sum(p[1] for p in largest) / n


def load_features(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["features"]
