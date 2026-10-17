"""Is street-level detail supported by the sensors, or only neighbourhood-level? (Phase 5)

    python -m backend.ml.street_vs_area

Same held-out protocol as spatial_fit (whole weeks held out, every hidden sensor predicted from the others at that hour, city level
taken as known), then two questions asked of the predictions:

  A. Does a map made of 250 m - 2 km cells rank sensors as well as the street-level prediction? For each hidden sensor the kriged
     residual is taken at its own position (street level) and averaged over the grid square that contains it (a 3 x 3 grid of points),
     both without that sensor; same street trend added to both. If cells do as well, street detail adds nothing a cell map does not show.
  B. Can anything rank streets that are CLOSE to each other? For pairs of sensors in the same hour at 0-250 m, 250-500 m, 0.5-1 km and
     1-2 km: how often does the order of the traffic+terrain trend match the order of the readings (0.5 = chance)? The trend uses no
     sensor reading, so the pair test has no leakage. Kriged corrections are smooth over these distances and cannot order close pairs.
"""
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.model_selection import GroupKFold

from . import spatial_fit as S

CELLS = (250, 500, 1000, 2000)
PAIR_BINS = [(0, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 5000)]


def hour_rho(df: pd.DataFrame, col: str) -> float:
    r = [spearmanr(g[col], g.obs).statistic for _, g in df.groupby("t") if len(g) >= 20 and g[col].nunique() > 1]
    return float(np.nanmean(r)) if r else float("nan")


def cell_field(d, t: int, p, off, post, sizes) -> dict | None:
    """Kriged residual at each hidden sensor's own position (street level) and averaged over the cell (grid square of `size` m) that
    contains it, both computed WITHOUT that sensor. The cell average is the mean of the field at a 3 x 3 grid of points in the cell,
    which is what a cell map built from the same field shows. Returns arrays over the sensors present at hour t."""
    pres = np.flatnonzero(np.isfinite(d.U[:, t]))
    if len(pres) < S.MIN_SENSORS_PER_HOUR:
        return None
    xy = d.xy[pres]
    e = d.E0[pres, t] - off[pres]
    noise = p.nugget + post[pres]
    own = np.arange(len(pres))
    W, _ = S.kriging_weights(xy, xy, noise, p, k=S.KN, exclude=own)
    out = {"pres": pres, "base_u": d.L[t] + d.dev[pres, t], "street": W @ e, "e0": d.E0[pres, t]}
    g = (np.arange(3) + 0.5) / 3
    for size in sizes:
        x0, y0 = np.floor(xy[:, 0] / size) * size, np.floor(xy[:, 1] / size) * size
        pts = np.column_stack([(x0[:, None] + g[None, :] * size)[:, :, None].repeat(3, 2).ravel(), (y0[:, None] + g[None, :] * size)[:, None, :].repeat(3, 1).ravel()])
        Wc, _ = S.kriging_weights(pts, xy, noise, p, k=S.KN, exclude=np.repeat(own, 9))
        out[f"cell_{size}"] = (Wc @ e).reshape(-1, 9).mean(axis=1)
    return out


def main() -> int:
    rng = np.random.default_rng(S.SEED)
    d = S.Data()
    allcols = np.arange(len(d.times))
    parts, pair_rows = [], []
    for k, (tr, te) in enumerate(GroupKFold(5).split(allcols, groups=d.week)):
        p, o, _ = S.fit_params(d, tr, rng)
        p.sd_scale = S.fit_sd_scale(d, p, o["offset"], o["post_var"], tr, rng)
        hrs = S.pick_eval_hours(d, te, rng)
        df = S.score_hours(d, p, o["offset"], o["post_var"], hrs)
        df["scale"] = np.where(np.isin(d.month[df.t.to_numpy()], S.HEATING), p.sd_scale["heating"], p.sd_scale["summer"])
        # the same hidden-sensor predictions with the field averaged over cells (a subset of the hours: it is the slow part)
        cells = []
        for t in rng.choice(hrs, min(len(hrs), 300), replace=False):
            r = cell_field(d, t, p, o["offset"], o["post_var"], CELLS)
            if r is None:
                continue
            cells.append(pd.DataFrame({"t": t, "sensor": r["pres"], "obs": np.expm1(d.U[r["pres"], t]), "base_u": r["base_u"], "street_k": r["street"],
                                       **{f"cell_{s}_k": r[f"cell_{s}"] for s in CELLS}}))
        parts.append(df)
        pair_rows.append(pd.concat(cells, ignore_index=True))
        print(f"fold {k}: {len(df):,} predictions, {sum(len(c) for c in cells):,} with cell averages", file=sys.stderr)
    ev = pd.concat(parts, ignore_index=True)
    cv = pd.concat(pair_rows, ignore_index=True)
    for df_ in (ev, cv):
        df_["city"] = d.city[df_.t.to_numpy()]
        df_["month"] = d.month[df_.t.to_numpy()]
        xy = d.xy[df_.sensor.to_numpy()]
        df_["x"], df_["y"] = xy[:, 0], xy[:, 1]
    ev["inside"] = (ev.z.abs() <= S.Z80 * ev.scale).astype(float)
    ev["trend"] = ev.level_trend
    cv["trend_only"] = np.expm1(cv.base_u)
    cv["street"] = np.expm1(cv.base_u + cv.street_k)
    for size in CELLS:
        cv[f"cell_{size}"] = np.expm1(cv.base_u + cv[f"cell_{size}_k"])
    res = {}
    for name, fn in (("heating, city > 15", lambda q: np.isin(q.month, S.HEATING) & (q.city > 15)),
                     ("heating, city > 25", lambda q: np.isin(q.month, S.HEATING) & (q.city > 25)),
                     ("heating, all", lambda q: np.isin(q.month, S.HEATING))):
        e_, c_ = ev[fn(ev)], cv[fn(cv)]
        row = {"hours": int(e_.t.nunique()), "rows": int(len(e_)), "coverage80": round(float(e_.inside.mean()), 3),
               "cell_hours": int(c_.t.nunique()), "trend_only": round(hour_rho(c_, "trend_only"), 3), "street_level": round(hour_rho(c_, "street"), 3)}
        for size in CELLS:
            row[f"cell_{size}m"] = round(hour_rho(c_, f"cell_{size}"), 3)
        res[name] = row
        print(f"\n[{name}] {row['hours']} hours scored ({row['cell_hours']} with cell averages), 80% interval coverage {row['coverage80']}", file=sys.stderr)
        print(f"   within-hour Spearman at hidden sensors: trend only {row['trend_only']} | street level {row['street_level']} | "
              + " | ".join(f"{s} m cells {row[f'cell_{s}m']}" for s in CELLS), file=sys.stderr)

    # B. close pairs: does the trend order them?
    print("\nB. share of sensor pairs (same hour, heating season, city > 15) whose terrain-trend order matches the reading order", file=sys.stderr)
    sub = ev[np.isin(ev.month, S.HEATING) & (ev.city > 15)]
    hours = sub.t.unique()
    hours = rng.choice(hours, min(len(hours), 250), replace=False)
    xs, ys = d.xy[:, 0], d.xy[:, 1]
    out = {b: [0, 0] for b in PAIR_BINS}
    for t in hours:
        g = sub[sub.t == t]
        n = len(g)
        if n < 20:
            continue
        gx, gy = xs[g.sensor.to_numpy()], ys[g.sensor.to_numpy()]
        i, j = np.triu_indices(n, 1)
        dist = np.hypot(gx[i] - gx[j], gy[i] - gy[j])
        dt = g.trend.to_numpy()[i] - g.trend.to_numpy()[j]
        do = np.log1p(g.obs.to_numpy()[i]) - np.log1p(g.obs.to_numpy()[j])
        ok = (np.abs(dt) > 1e-3) & (np.abs(do) > 0.02)  # ties carry no information
        for lo, hi in PAIR_BINS:
            s = ok & (dist >= lo) & (dist < hi)
            out[(lo, hi)][0] += int((np.sign(dt[s]) == np.sign(do[s])).sum())
            out[(lo, hi)][1] += int(s.sum())
    pairs = {}
    for (lo, hi), (hit, n) in out.items():
        share = hit / max(n, 1)
        se = (share * (1 - share) / max(n, 1)) ** 0.5
        pairs[f"{lo}-{hi} m"] = {"pairs": n, "concordance": round(share, 3), "se_if_independent": round(se, 4)}
        print(f"   {lo:4d}-{hi:4d} m: {n:>9,} pairs, concordance {share:.3f}  (pairs share sensors and hours, so the real SE is larger than {se:.4f})", file=sys.stderr)
    res["close_pairs"] = pairs
    import json
    (S.DATA / "street_vs_area.json").write_text(json.dumps(res, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
