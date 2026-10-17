"""Human-readable place names for graph segments.

- Bulgarian street names are transliterated with the official Streamlined System (Latin), keeping the Cyrillic.
- Unnamed segments (footpaths, sidewalks) take the nearest named street within 80 m, flagged as "near".
- Any point gets "near <street>, <district>" so routes show places instead of raw coordinates.
"""
import numpy as np
from scipy.spatial import cKDTree

from .graph import Net, to_xy

_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h",
    "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sht", "ъ": "a", "ь": "y", "ю": "yu", "я": "ya",
}
_ABBR = {"бул.": "Blvd", "ул.": "St", "пл.": "Sq", "ж.к.": "zh.k.", "кв.": "kv.", "бул": "Blvd", "ул": "St"}


def _word(w: str) -> str:
    if w in _ABBR:
        return _ABBR[w]
    if w.lower() == "българия":  # the one official exception to ъ -> a
        return "Bulgaria"
    low = w.lower()
    if low.endswith("ия") and len(low) > 2:  # Streamlined System: word-final -ия -> -ia
        low_t = "".join(_MAP.get(ch, ch) for ch in low[:-2]) + "ia"
    else:
        low_t = "".join(_MAP.get(ch, ch) for ch in low)
    return low_t[:1].upper() + low_t[1:] if w[:1].isupper() else low_t


def translit(name: str | None) -> str | None:
    if not name:
        return name
    return " ".join(_word(w) for w in name.split())


class Namer:
    def __init__(self, net: Net, district_at):
        self.net, self.district_at = net, district_at
        rep = net.und_rep
        named = np.flatnonzero(net.e_name[rep] >= 0)
        self._named_edges = rep[named]
        x, y = to_xy(net.e_mid_lon[self._named_edges], net.e_mid_lat[self._named_edges])
        self._tree = cKDTree(np.column_stack([x, y]))

    def _nearest(self, lon: float, lat: float, max_m: float):
        x, y = to_xy(lon, lat)
        d, i = self._tree.query([x, y], distance_upper_bound=max_m)
        if not np.isfinite(d):
            return None
        e = int(self._named_edges[i])
        return self.net.names[int(self.net.e_name[e])], float(d)

    def edge(self, e: int) -> dict:
        """Street and district for a directed edge; unnamed edges borrow the nearest named street."""
        net = self.net
        lon, lat = float(net.e_mid_lon[e]), float(net.e_mid_lat[e])
        district = self.district_at(lon, lat)
        if net.e_name[e] >= 0:
            bg, near = net.names[int(net.e_name[e])], False
        else:
            hit = self._nearest(lon, lat, 80.0)
            bg, near = (hit[0], True) if hit else (None, False)
        return {"street": translit(bg), "street_bg": bg, "near_street": near, "district": district}

    def point(self, lat: float, lon: float) -> str:
        """'near Blvd X, District' for a clicked or searched point."""
        district = self.district_at(lon, lat)
        hit = self._nearest(lon, lat, 150.0)
        street = translit(hit[0]) if hit else None
        parts = [f"near {street}" if street else None, district]
        return ", ".join(p for p in parts if p) or f"{lat:.4f}, {lon:.4f}"
