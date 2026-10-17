"""Do sensor offsets estimated from a window around the date predict better than offsets from the whole history? (they drift over months and years)

    PYTHONPATH=. .venv/bin/python -m backend.ml.offset_window_eval [--folds 0 1] [--windows 45 90 180 365]

The Kaggle 2017-2019 data showed that a sensor's offset is stable for months (correlation 0.73-0.87 from one quarter to the next) and fades over
years (about 0.5 after a year, 0.1 after five). spatial_fit estimates one offset per sensor from the whole 2-year history. Here the offsets used for a
test hour come only from training hours within +-W days of it (whole weeks held out as usual), everything else as in spatial_fit's evaluation.
Hidden-sensor predictions, same protocol: median abs error and within-hour Spearman against the raw readings.
"""
import argparse
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.model_selection import GroupKFold

from . import spatial_fit as S

HEATING = S.HEATING


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", type=int, nargs="+", default=[0, 1])
    ap.add_argument("--windows", type=int, nargs="+", default=[45, 90, 180, 365])
    ap.add_argument("--past-only", action="store_true", help="deployment-like: offsets from the W days BEFORE the hour only (the app cannot see the future)")
    a = ap.parse_args()
    rng = np.random.default_rng(S.SEED)
    d = S.Data()
    allcols = np.arange(len(d.times))
    day = np.array((d.times - d.times.min()).days)
    folds = list(GroupKFold(5).split(allcols, groups=d.week))
    rows = []
    for k in a.folds:
        tr, te = folds[k]
        p, o_glob, _ = S.fit_params(d, tr, rng)
        p.sd_scale = S.fit_sd_scale(d, p, o_glob["offset"], o_glob["post_var"], tr, rng)
        hrs = S.pick_eval_hours(d, te, rng)
        variants = {"global": None, **{(f"past {w} d" if a.past_only else f"+-{w} d"): w for w in a.windows}}
        for name, w in variants.items():
            if w is None:
                df = S.score_hours(d, p, o_glob["offset"], o_glob["post_var"], hrs)
            else:
                parts = []
                for wk in np.unique(d.week[hrs]):
                    mid = day[hrs[d.week[hrs] == wk]].mean()
                    near = tr[(day[tr] <= mid) & (day[tr] >= mid - w)] if a.past_only else tr[np.abs(day[tr] - mid) <= w]
                    if len(near) < 200:
                        near = tr
                    o = S.estimate_offsets(d.E0, near)
                    parts.append(S.score_hours(d, p, o["offset"], o["post_var"], hrs[d.week[hrs] == wk]))
                df = pd.concat(parts, ignore_index=True)
            df["variant"], df["fold"] = name, k
            df["month"], df["city"] = d.month[df.t.to_numpy()], d.city[df.t.to_numpy()]
            rows.append(df)
            print(f"fold {k} {name}: {len(df):,} predictions", file=sys.stderr)
    ev = pd.concat(rows, ignore_index=True)
    out = {}
    for cut, m in {"all": np.ones(len(ev), bool), "heating": np.isin(ev.month, HEATING), "polluted heating (city>15)": np.isin(ev.month, HEATING) & (ev.city > 15)}.items():
        for v, g in ev[m].groupby("variant", sort=False):
            r = [spearmanr(x.krige_offsets_removed, x.obs).statistic for _, x in g.groupby("t") if len(x) >= 20 and x.krige_offsets_removed.nunique() > 1]
            out[(cut, v)] = {"median_abs_error": round(float((g.krige_offsets_removed - g.obs).abs().median()), 3), "within_hour_spearman": round(float(np.nanmean(r)), 3)}
    print(f"\nfolds {a.folds}:\n" + pd.DataFrame(out).T.to_string(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
