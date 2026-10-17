"""Walking network as flat numpy arrays (fast to load, vectorised scoring and routing).

    python -m backend.cai.graph        # bake data/net.npz from data/graph.graphml (+ roads.graphml)

The GraphML files are large; the baked net.npz is what the app loads.
"""
import sys
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from . import config as C

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
NET_PATH = DATA / "net.npz"
NET_PATHS = {"walk": DATA / "net.npz", "bike": DATA / "net_bike.npz"}
GRAPHML = {"walk": DATA / "graph.graphml", "bike": DATA / "graph_bike.graphml"}

LON0, LAT0 = 23.35, 42.70
M_LAT = 110_574.0
M_LON = 111_320.0 * np.cos(np.radians(LAT0))


def to_xy(lon, lat):
    """Local equirectangular metres (accurate to <0.3 % over the 30 km extent)."""
    return (np.asarray(lon) - LON0) * M_LON, (np.asarray(lat) - LAT0) * M_LAT


def to_lonlat(x, y):
    return np.asarray(x) / M_LON + LON0, np.asarray(y) / M_LAT + LAT0


@dataclass
class Net:
    node_lon: np.ndarray
    node_lat: np.ndarray
    e_u: np.ndarray  # directed edges, parallel edges collapsed to the shortest
    e_v: np.ndarray
    e_len: np.ndarray
    e_hw: np.ndarray  # index into hw_names
    e_name: np.ndarray  # index into names (-1 = unnamed)
    e_und: np.ndarray  # undirected segment id
    e_geo_off: np.ndarray  # polyline offsets into e_geo
    e_geo: np.ndarray  # (P, 2) lon/lat float32
    e_mid_lon: np.ndarray
    e_mid_lat: np.ndarray
    hw_names: list[str]
    names: list[str]
    road_lon: np.ndarray  # traffic sample points (every 5 m on drivable roads)
    road_lat: np.ndarray
    road_w: np.ndarray
    und_rep: np.ndarray = field(default=None)  # directed edge index representing each undirected segment

    def __post_init__(self):
        if self.und_rep is None:
            rep = np.full(int(self.e_und.max()) + 1, -1, dtype=np.int64)
            # last write wins; any direction is fine for drawing (geometry reversed consistently below)
            rep[self.e_und[::-1]] = np.arange(len(self.e_und))[::-1]
            self.und_rep = rep

    @property
    def n_nodes(self) -> int:
        return len(self.node_lon)

    @property
    def n_edges(self) -> int:
        return len(self.e_u)

    @cached_property
    def node_xy(self) -> np.ndarray:
        x, y = to_xy(self.node_lon, self.node_lat)
        return np.column_stack([x, y])

    @cached_property
    def node_tree(self) -> cKDTree:
        return cKDTree(self.node_xy)

    @cached_property
    def mid_xy(self) -> np.ndarray:
        x, y = to_xy(self.e_mid_lon, self.e_mid_lat)
        return np.column_stack([x, y])

    @cached_property
    def hw_code(self) -> dict[str, int]:
        return {h: i for i, h in enumerate(self.hw_names)}

    def hw_mask(self, *names: str) -> np.ndarray:
        codes = [self.hw_code[n] for n in names if n in self.hw_code]
        return np.isin(self.e_hw, codes)

    def geometry(self, e: int, reverse: bool = False) -> np.ndarray:
        g = self.e_geo[self.e_geo_off[e] : self.e_geo_off[e + 1]]
        return g[::-1] if reverse else g

    def snap(self, lon: float, lat: float) -> tuple[int, float]:
        x, y = to_xy(lon, lat)
        d, i = self.node_tree.query([x, y])
        return int(i), float(d)

    def save(self, path: Path = NET_PATH) -> None:
        np.savez_compressed(
            path,
            **{k: v for k, v in self.__dict__.items() if isinstance(v, np.ndarray)},
            hw_names=np.array(self.hw_names), names=np.array(self.names),
        )

    @classmethod
    def load(cls, path: Path = NET_PATH) -> "Net":
        z = np.load(path, allow_pickle=False)
        kw = {k: z[k] for k in z.files if k not in ("hw_names", "names")}
        return cls(**kw, hw_names=[str(s) for s in z["hw_names"]], names=[str(s) for s in z["names"]])


def _first(v):
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    return v


def _polyline_mid(coords: np.ndarray) -> tuple[float, float]:
    seg = np.hypot(np.diff(coords[:, 0]) * M_LON, np.diff(coords[:, 1]) * M_LAT)
    total = seg.sum()
    if total == 0:
        return float(coords[0, 0]), float(coords[0, 1])
    cum = np.concatenate([[0], np.cumsum(seg)])
    return float(np.interp(total / 2, cum, coords[:, 0])), float(np.interp(total / 2, cum, coords[:, 1]))


def _densify(coords: np.ndarray, spacing: float) -> np.ndarray:
    seg = np.hypot(np.diff(coords[:, 0]) * M_LON, np.diff(coords[:, 1]) * M_LAT)
    total = seg.sum()
    if total == 0:
        return coords[:1]
    cum = np.concatenate([[0], np.cumsum(seg)])
    t = np.arange(spacing / 2, total, spacing)
    if len(t) == 0:
        t = np.array([total / 2])
    return np.column_stack([np.interp(t, cum, coords[:, 0]), np.interp(t, cum, coords[:, 1])])


def build_net(graphml: Path = DATA / "graph.graphml", roads: Path = DATA / "roads.graphml") -> Net:
    import osmnx as ox

    print(f"loading {graphml} ...", file=sys.stderr)
    G = ox.load_graphml(graphml)
    nodes, edges = ox.graph_to_gdfs(G, nodes=True, edges=True)
    nodes = nodes[~nodes.index.duplicated()]
    node_pos = {nid: i for i, nid in enumerate(nodes.index)}
    edges = edges.reset_index().sort_values("length").drop_duplicates(subset=["u", "v"]).reset_index(drop=True)
    edges = edges[edges.u != edges.v].reset_index(drop=True)
    n = len(edges)
    print(f"{len(nodes)} nodes, {n} directed edges after dedup", file=sys.stderr)

    hw = [str(_first(h) or "unclassified") for h in edges["highway"]]
    hw_names = sorted(set(hw))
    hw_idx = {h: i for i, h in enumerate(hw_names)}
    raw_names = [_first(x) if isinstance(_first(x), str) else None for x in (edges["name"] if "name" in edges else [None] * n)]
    names = sorted({x for x in raw_names if x})
    name_idx = {x: i for i, x in enumerate(names)}

    u = np.array([node_pos[x] for x in edges["u"]], dtype=np.int32)
    v = np.array([node_pos[x] for x in edges["v"]], dtype=np.int32)
    und_key = {}
    und = np.empty(n, dtype=np.int32)
    for i, (a, b) in enumerate(zip(u.tolist(), v.tolist())):
        k = (a, b) if a < b else (b, a)
        und[i] = und_key.setdefault(k, len(und_key))

    geo_parts, offsets, mid_lon, mid_lat = [], [0], np.empty(n), np.empty(n)
    drive_pts, drive_w = [], []
    sampled_und: set[int] = set()
    for i, g in enumerate(edges.geometry):
        c = np.asarray(g.coords, dtype=np.float64)[:, :2]
        geo_parts.append(c.astype(np.float32))
        offsets.append(offsets[-1] + len(c))
        mid_lon[i], mid_lat[i] = _polyline_mid(c)
        w = C.W_CLASS.get(hw[i], 0.0)
        # major roads come from roads.graphml; residential/service/unclassified from the walk graph
        if 1.0 <= w < 3.5 and int(und[i]) not in sampled_und:
            sampled_und.add(int(und[i]))
            p = _densify(c, C.TRAFFIC_SPACING_M)
            drive_pts.append(p)
            drive_w.append(np.full(len(p), w * C.TRAFFIC_SPACING_M / 100.0, dtype=np.float32))

    n_minor = sum(len(p) for p in drive_pts)
    if roads.exists():
        RG = ox.load_graphml(roads)
        _, redges = ox.graph_to_gdfs(RG, nodes=True, edges=True)
        seen = set()
        for (ru, rv, _k), row in redges.iterrows():
            key = (min(ru, rv), max(ru, rv))
            if key in seen:
                continue
            seen.add(key)
            w = C.W_CLASS.get(str(_first(row["highway"])), 0.0)
            if w <= 0:
                continue
            p = _densify(np.asarray(row.geometry.coords, dtype=np.float64)[:, :2], C.TRAFFIC_SPACING_M)
            drive_pts.append(p)
            drive_w.append(np.full(len(p), w * C.TRAFFIC_SPACING_M / 100.0, dtype=np.float32))
        print(f"traffic sources: {n_minor} minor-road points + {sum(len(p) for p in drive_pts) - n_minor} major-road points", file=sys.stderr)
    else:
        print("WARNING: roads.graphml missing; major roads are taken from the walk graph only", file=sys.stderr)
        # fall back: use walk-graph major roads too
        for i, g in enumerate(edges.geometry):
            w = C.W_CLASS.get(hw[i], 0.0)
            if w >= 3.5 and int(und[i]) not in sampled_und:
                sampled_und.add(int(und[i]))
                p = _densify(np.asarray(g.coords, dtype=np.float64)[:, :2], C.TRAFFIC_SPACING_M)
                drive_pts.append(p)
                drive_w.append(np.full(len(p), w * C.TRAFFIC_SPACING_M / 100.0, dtype=np.float32))

    pts = np.concatenate(drive_pts) if drive_pts else np.zeros((0, 2))
    return Net(
        node_lon=nodes["x"].to_numpy(dtype=np.float64), node_lat=nodes["y"].to_numpy(dtype=np.float64),
        e_u=u, e_v=v, e_len=edges["length"].to_numpy(dtype=np.float32),
        e_hw=np.array([hw_idx[h] for h in hw], dtype=np.uint8),
        e_name=np.array([name_idx.get(x, -1) if x else -1 for x in raw_names], dtype=np.int32),
        e_und=und, e_geo_off=np.array(offsets, dtype=np.int64), e_geo=np.concatenate(geo_parts),
        e_mid_lon=mid_lon, e_mid_lat=mid_lat, hw_names=hw_names, names=names,
        road_lon=pts[:, 0].astype(np.float32) if len(pts) else np.zeros(0, np.float32),
        road_lat=pts[:, 1].astype(np.float32) if len(pts) else np.zeros(0, np.float32),
        road_w=np.concatenate(drive_w) if drive_w else np.zeros(0, np.float32),
    )


_nets: dict[str, Net] = {}


def get_net(mode: str = "walk") -> Net:
    if mode not in NET_PATHS:
        raise ValueError(f"unknown mode {mode!r}")
    if mode not in _nets:
        path = NET_PATHS[mode]
        if path.exists():
            _nets[mode] = Net.load(path)
        else:
            _nets[mode] = build_net(GRAPHML[mode])
            _nets[mode].save(path)
    return _nets[mode]


def has_mode(mode: str) -> bool:
    return NET_PATHS[mode].exists() or GRAPHML[mode].exists()


if __name__ == "__main__":
    for mode in ("walk", "bike"):
        if not GRAPHML[mode].exists():
            print(f"skip {mode}: {GRAPHML[mode].name} not found", file=sys.stderr)
            continue
        net = build_net(GRAPHML[mode])
        net.save(NET_PATHS[mode])
        print(f"saved {NET_PATHS[mode]} ({NET_PATHS[mode].stat().st_size / 1e6:.1f} MB)", file=sys.stderr)
