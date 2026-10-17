"""Clean-air walking router on the baked network.

    time_e  = length / 4.8 km/h (steps x1.5; motorways removed, trunk roads heavily penalised)
    dose_e  = PM(e,t) * time_e                 [ug.min/m3]
    cost_e  = time_e * (1 + lambda * cost_burden(e,t) * K)

Three routes: fastest (lambda 0), balanced (1), cleanest (3), each capped at +30 % time over the fastest
by bisecting lambda downwards.
"""
from dataclasses import dataclass

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from . import config as C
from .graph import Net, to_xy

M_PER_MIN = C.WALK_KMH * 1000 / 60  # walking default; Router instances carry their own


class RouteError(ValueError):
    pass


def road_group(hw: str) -> str:
    if hw in ("motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link"):
        return "major"
    if hw in ("secondary", "secondary_link", "tertiary", "tertiary_link"):
        return "collector"
    if hw in ("residential", "unclassified", "service", "living_street"):
        return "local"
    return "path"


@dataclass
class Path:
    edges: np.ndarray
    lam: float


class Router:
    def __init__(self, net: Net, mode: str = "walk"):
        self.net, self.mode = net, mode
        self.m_per_min = (C.BIKE_KMH if mode == "bike" else C.WALK_KMH) * 1000 / 60
        self.breathing = C.BREATHING[mode]
        steps = net.hw_mask("steps")
        # walking: steps are slow; cycling: steps are not rideable, so they are removed
        self.time_min = (net.e_len / self.m_per_min * np.where(steps & (mode == "walk"), C.STEPS_FACTOR, 1.0)).astype("float32")
        self.blocked = net.hw_mask("motorway", "motorway_link") | (steps if mode == "bike" else False)
        self.penalty = np.where(net.hw_mask("trunk", "trunk_link"), 6.0, 1.0).astype("float32")
        self.allowed = np.flatnonzero(~self.blocked)
        order = np.argsort(net.e_u.astype(np.int64) * net.n_nodes + net.e_v)
        self._pair_key = (net.e_u.astype(np.int64) * net.n_nodes + net.e_v)[order]
        self._pair_edge = order
        self._edge_pts = None

    # ---- shortest paths ---------------------------------------------------------------------
    def _graph(self, cost: np.ndarray) -> csr_matrix:
        a = self.allowed
        return csr_matrix((cost[a].astype("float64"), (self.net.e_u[a], self.net.e_v[a])), shape=(self.net.n_nodes,) * 2)

    def cost(self, cb: np.ndarray, lam: float) -> np.ndarray:
        return self.time_min * self.penalty * (1.0 + lam * cb * C.ROUTE_K)

    def tree(self, src: int, cost: np.ndarray):
        return dijkstra(self._graph(cost), directed=True, indices=src, return_predecessors=True)

    def _edge_of(self, u: np.ndarray, v: np.ndarray) -> np.ndarray:
        k = u.astype(np.int64) * self.net.n_nodes + v
        return self._pair_edge[np.searchsorted(self._pair_key, k)]

    def path_edges(self, pred: np.ndarray, src: int, dst: int) -> np.ndarray | None:
        if dst != src and pred[dst] < 0:
            return None
        nodes = [dst]
        while nodes[-1] != src:
            nodes.append(int(pred[nodes[-1]]))
        nodes = np.array(nodes[::-1])
        return self._edge_of(nodes[:-1], nodes[1:]) if len(nodes) > 1 else np.zeros(0, dtype=np.int64)

    # ---- statistics ---------------------------------------------------------------------------
    def stats(self, edges: np.ndarray, pm: np.ndarray, cai: np.ndarray) -> dict:
        t = self.time_min[edges].astype(float)
        total_t = float(t.sum())
        dose = float((pm[edges] * t).sum()) * self.breathing  # dose = PM x minutes x relative breathing rate
        return {
            "time_min": round(total_t, 2),
            "distance_m": round(float(self.net.e_len[edges].sum()), 1),
            "mean_pm25": round(dose / self.breathing / total_t, 2) if total_t else 0.0,
            "dose": round(dose, 1),
            "mean_cai": round(float((cai[edges] * t).sum() / total_t), 1) if total_t else 0.0,
            "steps_m": round(float(self.net.e_len[edges][self.net.hw_mask("steps")[edges]].sum()), 1),
        }

    def polyline(self, edges: np.ndarray) -> list[list[float]]:
        net, out = self.net, []
        for e in edges:
            g = net.geometry(int(e))
            if len(g) == 0:
                continue
            ux, uy = net.node_lon[net.e_u[e]], net.node_lat[net.e_u[e]]
            if np.hypot(g[0, 0] - ux, g[0, 1] - uy) > np.hypot(g[-1, 0] - ux, g[-1, 1] - uy):
                g = g[::-1]
            out.extend([[round(float(x), 5), round(float(y), 5)] for x, y in g])
        dedup = [p for i, p in enumerate(out) if i == 0 or p != out[i - 1]]
        return dedup

    # ---- routes ---------------------------------------------------------------------------------
    def snap(self, lat: float, lon: float, label: str) -> int:
        node, d = self.net.snap(lon, lat)
        if d > C.SNAP_MAX_M:
            raise RouteError(f"{label} is {d:.0f} m from the walking network (max {C.SNAP_MAX_M:.0f} m)")
        return node

    def capped(self, src: int, dst: int, cb: np.ndarray, lam: float, fastest_time: float) -> Path | None:
        """Largest lambda <= lam whose route is within +30 % of the fastest time."""
        limit = fastest_time * (1 + C.ROUTE_MAX_EXTRA)
        lo, hi = 0.0, lam
        best: Path | None = None
        for _ in range(7):
            _, pred = self.tree(src, self.cost(cb, hi))
            edges = self.path_edges(pred, src, dst)
            if edges is not None and self.time_min[edges].sum() <= limit + 1e-6:
                best = Path(edges, hi)
                if hi == lam or hi - lo < 0.05:
                    break
                lo, hi = hi, (hi + lam) / 2  # try to go cleaner again (only when we backed off before)
            else:
                hi = (lo + hi) / 2
        return best

    def routes(self, origin: tuple[float, float], dest: tuple[float, float], cb, pm, cai, chosen_lambda: float | None = None) -> dict:
        src, dst = self.snap(*origin, "origin"), self.snap(*dest, "destination")
        _, pred = self.tree(src, self.cost(cb, 0.0))
        fast_edges = self.path_edges(pred, src, dst)
        if fast_edges is None:
            raise RouteError("no walking route between these points")
        fast = self.stats(fast_edges, pm, cai)
        ft = fast["time_min"]
        out = {"fastest": {**fast, "lambda_used": 0.0, "polyline": self.polyline(fast_edges), "edges": fast_edges}}
        wanted = dict(C.LAMBDAS)
        if chosen_lambda is not None:
            wanted["chosen"] = float(chosen_lambda)
        for name, lam in wanted.items():
            if name == "fastest":
                continue
            p = self.capped(src, dst, cb, lam, ft) if lam > 0 else Path(fast_edges, 0.0)
            p = p or Path(fast_edges, 0.0)
            s = self.stats(p.edges, pm, cai)
            # The cost over-weights contrast relative to dose; if a detour still ends up with more dose than walking
            # straight, ease the setting off until it does not.
            while s["dose"] > fast["dose"] and p.lam > 0.05:
                p = (self.capped(src, dst, cb, p.lam / 2, ft) if p.lam / 2 > 0.05 else None) or Path(fast_edges, 0.0)
                s = self.stats(p.edges, pm, cai)
            out[name] = {**s, "lambda_used": round(p.lam, 2), "polyline": self.polyline(p.edges), "edges": p.edges}
        for r in out.values():
            r["dose_change_pct"] = round(100 * (r["dose"] / fast["dose"] - 1), 1) if fast["dose"] else 0.0
            r["time_change_pct"] = round(100 * (r["time_min"] / ft - 1), 1) if ft else 0.0
        return out

    # ---- scoring an external polyline (Google baseline) ----------------------------------------------
    def _edge_points(self):
        if self._edge_pts is None:
            net, xs, ys, ids = self.net, [], [], []
            reps = net.und_rep
            for e in reps:
                g = net.geometry(int(e))
                x, y = to_xy(g[:, 0].astype(np.float64), g[:, 1].astype(np.float64))
                seg = np.hypot(np.diff(x), np.diff(y))
                cum = np.concatenate([[0], np.cumsum(seg)])
                tt = np.arange(0, cum[-1] + 1e-6, 10.0) if cum[-1] > 0 else np.array([0.0])
                xs.append(np.interp(tt, cum, x))
                ys.append(np.interp(tt, cum, y))
                ids.append(np.full(len(tt), e))
            self._edge_pts = (cKDTree(np.column_stack([np.concatenate(xs), np.concatenate(ys)])), np.concatenate(ids))
        return self._edge_pts

    def score_polyline(self, lonlat: np.ndarray, pm: np.ndarray, cai: np.ndarray, background: float) -> dict:
        """Time and dose for an arbitrary polyline: sample every 10 m, snap to the nearest edge within 25 m."""
        x, y = to_xy(lonlat[:, 0], lonlat[:, 1])
        seg = np.hypot(np.diff(x), np.diff(y))
        cum = np.concatenate([[0], np.cumsum(seg)])
        total = float(cum[-1])
        if total == 0:
            raise RouteError("empty polyline")
        spacing = 10.0
        t = np.arange(spacing / 2, total, spacing)
        if len(t) == 0:
            t = np.array([total / 2])
        px, py = np.interp(t, cum, x), np.interp(t, cum, y)
        tree, ids = self._edge_points()
        d, idx = tree.query(np.column_stack([px, py]), distance_upper_bound=C.GOOGLE_SNAP_M)
        hit = np.isfinite(d)
        e = ids[np.where(hit, idx, 0)]
        step_m = total / len(t)
        minutes = step_m / self.m_per_min
        pm_pts = np.where(hit, pm[e], background)
        cai_pts = np.where(hit, cai[e], np.nan)
        dose = float((pm_pts * minutes).sum())
        t_total = minutes * len(t)
        return {
            "time_min": round(t_total, 2), "distance_m": round(total, 1),
            "mean_pm25": round(dose / t_total, 2), "dose": round(dose * self.breathing, 1),
            "mean_cai": round(float(np.nanmean(cai_pts)), 1) if hit.any() else None,
            "snapped_share": round(float(hit.mean()), 3),
        }


def douglas_peucker(lonlat: np.ndarray, max_points: int) -> np.ndarray:
    """Simplify a polyline to at most `max_points` vertices (including both ends) by growing the tolerance."""
    if len(lonlat) <= max_points:
        return lonlat
    x, y = to_xy(lonlat[:, 0], lonlat[:, 1])
    pts = np.column_stack([x, y])

    def simplify(tol: float) -> list[int]:
        keep = {0, len(pts) - 1}
        stack = [(0, len(pts) - 1)]
        while stack:
            a, b = stack.pop()
            if b <= a + 1:
                continue
            ab = pts[b] - pts[a]
            L = np.hypot(*ab)
            seg = pts[a + 1 : b] - pts[a]
            dist = np.abs(ab[0] * seg[:, 1] - ab[1] * seg[:, 0]) / L if L > 0 else np.hypot(seg[:, 0], seg[:, 1])
            k = int(np.argmax(dist))
            if dist[k] > tol:
                m = a + 1 + k
                keep.add(m)
                stack += [(a, m), (m, b)]
        return sorted(keep)

    lo, hi = 0.0, 5000.0
    best = [0, len(pts) - 1]
    for _ in range(30):
        mid = (lo + hi) / 2
        kept = simplify(mid)
        if len(kept) <= max_points:
            best, hi = kept, mid
        else:
            lo = mid
    return lonlat[best]
