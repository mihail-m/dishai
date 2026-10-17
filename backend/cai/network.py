"""Clean-air network: where do clean routes go, and where is everyone forced through dirty air?

Seeded random OD pairs (40 origins x 10 destinations = 400 pairs). For each pair the fastest and the cleanest
walk (lambda 3, backed off until within +30 % time). Per street segment we count how often it is used by each.
Chokepoints = streets with many fastest-route users and low CAI, ranked by usage * (1 - CAI/100).
"""
import numpy as np

from . import config as C
from .graph import Net, to_xy
from .routing import Router, road_group

# Which municipal measures (Sofia PDUEK) would help, by what is wrong with the street.
MEASURES = {
    "A.4.3": "Blue-green arches and acupuncture",
    "A.4.7": "Street washing",
    "C.1.1": "Low-emission zone",
    "A.4.4": "Tree growth",
}


def measures_for(group: str, traffic: float, green: float, cai: float) -> list[dict]:
    """Thresholds sit relative to the city: traffic term median 3.2 / p90 11.4, greenery median 0.18 / p10 0.01."""
    out = []
    if traffic >= 10:
        out.append(("C.1.1", f"very heavy traffic term ({traffic:.0f}, top ~10 % of streets): restrict the dirtiest vehicles"))
        out.append(("A.4.7", "road dust and resuspension on a high-traffic street"))
    elif traffic >= 4:
        out.append(("A.4.7", f"moderate traffic term ({traffic:.0f}): wash or sweep during dry spells"))
    if green < 0.12:
        out.append(("A.4.4", f"almost no tree cover (greenery {green:.2f}): plant and protect trees"))
        if group in ("major", "collector"):
            out.append(("A.4.3", "arterial with room for a green buffer: blue-green acupuncture"))
    elif green < 0.20:
        out.append(("A.4.4", f"below-median tree cover (greenery {green:.2f}): add or protect trees"))
    if not out:
        out.append(("A.4.7", "low-key street: street washing priority only"))
    return [{"id": i, "label": MEASURES[i], "why": w} for i, w in out]


def _when_cleaner(dose: np.ndarray, time: np.ndarray) -> dict:
    m = dose < -1
    if not m.any():
        return {"n": 0, "median_dose_change_pct": 0.0, "median_time_change_pct": 0.0}
    return {"n": int(m.sum()), "median_dose_change_pct": round(float(np.median(dose[m])), 1), "median_time_change_pct": round(float(np.median(time[m])), 1)}


def compute_network(net: Net, router: Router, cb: np.ndarray, pm: np.ndarray, cai: np.ndarray, feats, rng_seed: int = C.NETWORK_SEED) -> dict:
    od_min, od_max = C.NETWORK_OD_BIKE_M if router.mode == "bike" else (C.NETWORK_OD_MIN_M, C.NETWORK_OD_MAX_M)
    rng = np.random.default_rng(rng_seed)
    xy = net.node_xy
    # origins/destinations from the walkable core: nodes touched by edges, sampled uniformly
    nodes = np.unique(np.concatenate([net.e_u, net.e_v]))
    per_origin = C.NETWORK_N_OD // C.NETWORK_ORIGINS
    n_und = int(net.e_und.max()) + 1
    use_fast = np.zeros(n_und, dtype=np.int32)
    use_clean = np.zeros(n_und, dtype=np.int32)
    lambdas = [C.LAMBDAS["cleanest"], 1.5, 0.75]
    clean_paths: list[np.ndarray] = []
    n_pairs = 0
    saved_time, saved_dose = [], []
    cost0 = router.cost(cb, 0.0)
    costs = [router.cost(cb, lam) for lam in lambdas]
    while n_pairs < C.NETWORK_N_OD:
        o = int(rng.choice(nodes))
        d = np.hypot(xy[nodes, 0] - xy[o, 0], xy[nodes, 1] - xy[o, 1])
        cand = nodes[(d >= od_min) & (d <= od_max)]
        if len(cand) < per_origin:
            continue
        dests = rng.choice(cand, size=per_origin, replace=False)
        _, pred0 = router.tree(o, cost0)
        trees = [router.tree(o, c)[1] for c in costs]
        for dn in dests:
            fe = router.path_edges(pred0, o, int(dn))
            if fe is None or len(fe) == 0:
                continue
            ft = float(router.time_min[fe].sum())
            dose_f = float((pm[fe] * router.time_min[fe]).sum())
            chosen = fe
            for pred in trees:  # cleanest first, back off until within +30 % time and no more dose than the fastest walk
                ce = router.path_edges(pred, o, int(dn))
                if ce is not None and router.time_min[ce].sum() <= ft * (1 + C.ROUTE_MAX_EXTRA) and float((pm[ce] * router.time_min[ce]).sum()) <= dose_f:
                    chosen = ce
                    break
            use_fast[np.unique(net.e_und[fe])] += 1
            use_clean[np.unique(net.e_und[chosen])] += 1
            clean_paths.append(chosen)
            dose_c = float((pm[chosen] * router.time_min[chosen]).sum())
            saved_dose.append(100 * (dose_c / dose_f - 1) if dose_f else 0.0)
            saved_time.append(100 * (router.time_min[chosen].sum() / ft - 1))
            n_pairs += 1
            if n_pairs >= C.NETWORK_N_OD:
                break

    rep = net.und_rep
    seg_cai = cai[rep].astype(float)
    # --- streets (group undirected segments by OSM name) ---
    score = use_fast * (1 - seg_cai / 100.0)
    name_idx = net.e_name[rep]
    chokepoints = []
    named = np.flatnonzero((name_idx >= 0) & (use_fast > 0))
    groups: dict[int, list[int]] = {}
    for s in named:
        groups.setdefault(int(name_idx[s]), []).append(int(s))
    for nm, segs in groups.items():
        segs = np.array(segs)
        tot = float(score[segs].sum())
        if tot <= 0:
            continue
        top = segs[np.argsort(-score[segs])[:25]]
        reps = rep[top]
        # marker sits ON the street (midpoint of its worst segment); an average of positions can fall off a long road
        cx, cy = float(net.e_mid_lon[reps[0]]), float(net.e_mid_lat[reps[0]])
        lens = net.e_len[rep[segs]]
        hw_len: dict[int, float] = {}
        for code, ln in zip(net.e_hw[rep[segs]], lens):
            hw_len[int(code)] = hw_len.get(int(code), 0.0) + float(ln)
        # heaviest road class that makes up >= 15 % of the street's length (sidewalks share the street's name)
        total_len = sum(hw_len.values())
        big = [c for c, l in hw_len.items() if l >= 0.15 * total_len and net.hw_names[c] in C.W_CLASS]
        hw = net.hw_names[max(big or hw_len, key=lambda c: C.W_CLASS.get(net.hw_names[c], 0.0))]
        length = float(net.e_len[rep[segs]].sum())
        mean_cai = float(np.average(seg_cai[segs], weights=net.e_len[rep[segs]]))
        green = float(np.average(feats.G[rep[segs]], weights=net.e_len[rep[segs]]))
        traffic = float(np.average(feats.T[rep[segs]], weights=net.e_len[rep[segs]]))
        chokepoints.append({
            "street": net.names[nm], "road_class": hw, "score": round(tot, 1),
            "max_fast_usage": int(use_fast[segs].max()), "mean_cai": round(mean_cai, 1),
            "length_m": round(length), "lon": round(cx, 5), "lat": round(cy, 5),
            "segments": [[[round(float(a), 5), round(float(b), 5)] for a, b in net.geometry(int(r))] for r in reps[:12]],
            "traffic_term": round(traffic, 2), "greenery": round(green, 2),
            "measures": measures_for(road_group(hw), traffic, green, mean_cai),
        })
    chokepoints.sort(key=lambda c: -c["score"])
    chokepoints = chokepoints[: C.CHOKEPOINT_POOL]

    # --- segment layer: clean-route usage (width) coloured by CAI ---
    keep = np.flatnonzero((use_clean >= 2) | (use_fast >= 2))
    if len(keep) > 14000:
        keep = keep[np.argsort(-(use_clean[keep] + use_fast[keep]))[:14000]]
    # clean channels: well used by the cleanest routes AND cleaner than the city's 60th percentile
    channel_cut = float(np.percentile(seg_cai, C.CHANNEL_CAI_PCTL))
    segments = [{
        "id": int(s), "path": [[round(float(a), 5), round(float(b), 5)] for a, b in net.geometry(int(rep[s]))],
        "usage_clean": int(use_clean[s]), "usage_fast": int(use_fast[s]), "cai": int(seg_cai[s]),
        "channel": bool(use_clean[s] >= C.CHANNEL_MIN_USE and seg_cai[s] >= channel_cut),
    } for s in keep]

    # --- trips for the flow animation: the clean routes that follow the busiest corridors ---
    busy = np.array([use_clean[net.e_und[p]].mean() for p in clean_paths])
    trips = []
    for k in np.argsort(-busy)[:60]:
        trips.append(router.polyline(clean_paths[int(k)]))
    return {
        "n_od": n_pairs, "seed": rng_seed,
        "median_dose_change_pct": round(float(np.median(saved_dose)), 1),
        "median_time_change_pct": round(float(np.median(saved_time)), 1),
        "share_cleaner": round(float(np.mean(np.array(saved_dose) < -1)), 3),
        # typical effect where a cleaner route exists at all (the all-walks median is dominated by pairs with no alternative)
        "when_cleaner": _when_cleaner(np.array(saved_dose), np.array(saved_time)),
        "channel_cai_cut": round(channel_cut, 1), "n_channel_segments": int(sum(1 for x in segments if x["channel"])),
        "segments": segments, "chokepoints": chokepoints, "trips": trips,
        "note": "400 random 1-4.5 km walks; indicative. Chokepoint score = fastest-route usage x (1 - CAI/100).",
    }
