"""Is the 'cleanest' route really cleaner than the fastest one, or is the difference inside the model's uncertainty? (Phase 5)

Two routes between the same places share most of their streets; what separates them is a few unshared stretches. The difference of
their mean PM2.5 has three sources of error, treated in log1p units (close to relative error at these levels):

  * the city level: it moves both routes the same way, so it cancels in the relative difference and is left out;
  * the unstructured part of each street (measurement noise, micro-scale pollution, the unknown per-sensor offset: `nugget + tau2` from
    the spatial fit). It averages out along a stretch, but only over independent pieces of road, taken to be MICRO_SCALE_M long;
  * the spatially correlated part (psill, range ~12 km): the errors at two places d apart differ by 2 * psill * (1 - exp(-d / range)),
    d being the distance between the centres of the two unshared stretches.

The total is multiplied by the per-season factor that makes the street intervals hold their 80 % (spatial_fit). The routes are called
distinguishable when the difference exceeds Z standard errors (Z = 1.28: a one-sided 90 % statement that the sign is right).
MICRO_SCALE_M is an assumption (the sensors cannot resolve it: pairs closer than 250 m already look like noise, see docs/PM_MODEL.md).
"""
import numpy as np

from .krige import SpatialParams

MICRO_SCALE_M = 150.0
Z = 1.2815515655446004


def compare(net, edges_a: np.ndarray, edges_b: np.ndarray, mean_a: float, mean_b: float, p: SpatialParams, month: int | None = None) -> dict:
    """Route b against route a (a = fastest). mean_* are the routes' mean PM2.5 (ug/m3), edges_* directed edge indices."""
    ua, ub = net.e_und[edges_a], net.e_und[edges_b]
    only_a, only_b = ~np.isin(ua, ub), ~np.isin(ub, ua)
    la, lb = float(net.e_len[edges_a].sum()), float(net.e_len[edges_b].sum())
    sa, sb = float(net.e_len[edges_a][only_a].sum()), float(net.e_len[edges_b][only_b].sum())
    diff = (mean_b - mean_a) / mean_a if mean_a > 0 else 0.0
    out = {"diff_pct": round(100 * diff, 1), "unshared_m": [round(sa), round(sb)]}
    if sa + sb < 1.0 or la <= 0 or lb <= 0:  # identical routes
        return {**out, "sd_pct": 0.0, "distinguishable": False, "reason": "same route"}
    var_ind = (p.nugget + p.tau2) * MICRO_SCALE_M * (sa / la**2 + sb / lb**2)
    if sa > 0 and sb > 0:
        ca, cb = net.mid_xy[edges_a][only_a].mean(axis=0), net.mid_xy[edges_b][only_b].mean(axis=0)
        d = float(np.hypot(*(ca - cb)))
        var_corr = 2.0 * p.psill * (1.0 - np.exp(-d / p.range_m))
    else:
        var_corr = 0.0
    sd = p.scale_for(month) * float(np.sqrt(var_ind + var_corr))
    return {**out, "sd_pct": round(100 * sd, 1), "distinguishable": bool(abs(diff) > Z * sd), "z": round(abs(diff) / sd, 2) if sd > 0 else None}


def summarise(per_route: dict[str, dict]) -> dict:
    """The banner-level verdict: is any alternative to the fastest route distinguishable from it?"""
    better = [k for k, v in per_route.items() if v["distinguishable"] and v["diff_pct"] < 0]
    return {"routes": per_route, "cleaner_exists": bool(better), "better": better,
            "message": None if better else "All routes have about the same air right now: the differences are smaller than the model can resolve."}
