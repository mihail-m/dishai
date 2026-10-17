"""Does low-rise housing (a proxy for solid-fuel home heating) explain PM2.5 on sensors? Reproducible check, not part of the app.

    PYTHONPATH=. .venv/bin/python -m backend.ml.heating_check

Needs data/lowrise.tif and data/buildings.tif (make heating), the sensor history and the spatial offsets (make spatial).
Design, in the order the Phase 4 brief asks for it (the buffer is chosen on the first test, the others follow):
  1. sensor level: Spearman between the feature (250 / 500 / 1000 m buffers) and each sensor's persistent offset
  2. within-sensor contrast: heating-season nights (Dec-Feb, 22-06 local) minus summer nights (Jun-Aug), per sensor, against the feature.
     The sensor is its own control, so calibration and siting offsets cancel; a heating source shows up as a bigger winter-night excess
  3. what the model needs: within-hour Spearman on HELD-OUT sensors in heating-season hours, traffic + terrain vs traffic + terrain +
     heating (and heating x stagnation), sensor-grouped CV repeated over three fold seeds. Acceptance: +0.05 or it stays context.
"""
import warnings

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import HuberRegressor, LinearRegression
from sklearn.model_selection import GroupKFold

from ..cai.features import _load_raster
from ..cai.graph import DATA
from ..cai.krige import get_params
from . import train as T
from .pm_model import STATIC_COLS

warnings.filterwarnings("ignore")
BUFFERS = (250, 500, 1000)
HEATING_M, SUMMER_M = (12, 1, 2), (6, 7, 8)
ALPHA = 3e5  # the shrinkage the street model uses
ACCEPT = 0.05


def feature_at(lon, lat, kind: str, R: int) -> np.ndarray:
    low = _load_raster(DATA / "lowrise.tif", buffer_m=R)
    allb = _load_raster(DATA / "buildings.tif", buffer_m=R)
    a, b = low.sample(lon, lat), allb.sample(lon, lat)
    return {"lowrise": a, "fraction": a / np.maximum(b, 0.01)}[kind]


def rho(x, y) -> float:
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    return float(spearmanr(x[ok], y[ok]).statistic)


def boot_ci(x, y, n=400, seed=0):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    rng = np.random.default_rng(seed)
    idx = [rng.integers(0, len(x), len(x)) for _ in range(n)]
    return np.percentile([spearmanr(x[i], y[i]).statistic for i in idx], [2.5, 97.5])


def design(Z, S):
    return np.hstack([Z, Z * (S / 100.0)[:, None]])


def cv_rho(df: pd.DataFrame, cols: list[str], seed: int, heating_only=True, min_city=None) -> float:
    """Held-out within-hour Spearman of the street deviation predicted from per-sensor columns `cols` (z-scored on training sensors)."""
    oof = np.zeros(len(df))
    for tr, te in GroupKFold(5, shuffle=True, random_state=seed).split(df, groups=df.sensor):
        d = df.iloc[tr]
        one = d.groupby("sensor")[cols].first()
        mu, sd = one.mean().to_numpy(), one.std().to_numpy()
        z = lambda x: np.nan_to_num((x[cols].to_numpy(dtype=float) - mu) / sd)
        s = d.sample(min(len(d), 150_000), random_state=T.SEED)
        y = np.log1p(s.pm.to_numpy()) - np.log1p(T.hour_median(df).loc[s.index].to_numpy())
        m = HuberRegressor(alpha=ALPHA, fit_intercept=False, epsilon=1.35, max_iter=200).fit(design(z(s), s.stagnation.to_numpy()), y)
        t = df.iloc[te]
        oof[te] = np.clip(design(z(t), t.stagnation.to_numpy()) @ m.coef_, -0.7, 0.7)
    sel = np.isin(df.time.dt.month, [10, 11, 12, 1, 2, 3]) if heating_only else np.ones(len(df), bool)
    if min_city is not None:
        sel = sel & (T.hour_median(df).to_numpy() > min_city)
    return T.within_hour_rho(df[sel], oof[sel])


def main() -> int:
    df, _ = T.build_table(np.random.default_rng(T.SEED))
    df = df.reset_index(drop=True)
    loc = df.groupby("sensor")[["lat", "lon"]].median()
    params = get_params()
    off = pd.Series({s: params.offsets.get(int(s)) for s in loc.index}) if params else pd.Series(dtype=float)
    df["hm"] = df.groupby("time").pm.transform("median")
    df["lr"] = np.log1p(df.pm) - np.log1p(df.hm)
    raw_off = df.groupby("sensor").lr.mean().loc[loc.index]
    st = df.groupby("sensor")[["T", "elev"]].first().loc[loc.index]
    X = np.column_stack([np.log1p(st["T"]), st.elev.fillna(st.elev.median())])
    resid_off = raw_off - LinearRegression().fit(X, raw_off).predict(X)  # offset with traffic and terrain taken out

    nan_s = int(np.isnan(feature_at(loc.lon.to_numpy(), loc.lat.to_numpy(), "lowrise", 500)).sum())
    print(f"{len(loc)} sensors ({nan_s} fall outside the raster and are left out of the sensor-level tests); low-rise share around sensors, median by buffer: "
          f"{ {R: round(float(np.nanmedian(feature_at(loc.lon.to_numpy(), loc.lat.to_numpy(), 'lowrise', R))), 3) for R in BUFFERS} }\n")

    # 1. sensor level
    print("1. rank correlation with the persistent sensor offset (95% bootstrap CI)")
    feats = {}
    for kind in ("lowrise", "fraction"):
        for R in BUFFERS:
            v = pd.Series(feature_at(loc.lon.to_numpy(), loc.lat.to_numpy(), kind, R), index=loc.index)
            feats[f"{kind}_{R}"] = v
            r1, r2 = rho(v, raw_off), rho(v, resid_off)
            lo, hi = boot_ci(v.to_numpy(), raw_off.to_numpy())
            print(f"   {kind:9s} {R:4d} m: rho {r1:+.3f} [{lo:+.2f},{hi:+.2f}]   after traffic+terrain {r2:+.3f}")
    for nm, v in (("traffic", np.log1p(st["T"])), ("terrain height", st.elev)):
        print(f"   (for scale) {nm}: rho {rho(v.fillna(v.median()), raw_off):+.3f}")

    # 2. within-sensor contrast
    print("\n2. winter night minus summer night per sensor (log units), against the feature")
    t = df.time
    night = (t.dt.hour >= 22) | (t.dt.hour < 6)
    w = df[night & t.dt.month.isin(HEATING_M)].groupby("sensor").lr.agg(["mean", "size"])
    s = df[night & t.dt.month.isin(SUMMER_M)].groupby("sensor").lr.agg(["mean", "size"])
    j = w.join(s, lsuffix="_w", rsuffix="_s").dropna()
    j = j[(j.size_w >= 60) & (j.size_s >= 60)]
    j["diff"] = j.mean_w - j.mean_s
    print(f"   {len(j)} sensors with >= 60 winter and summer night hours; mean contrast {j['diff'].mean():+.3f} (SD {j['diff'].std():.2f})")
    contrast = {}
    for name, v in feats.items():
        v = v.loc[j.index]
        r = rho(v, j["diff"])
        lo, hi = boot_ci(v.to_numpy(), j["diff"].to_numpy(), 300)
        hi_q, lo_q = v >= v.quantile(0.75), v <= v.quantile(0.25)
        gap = j["diff"][hi_q].mean() - j["diff"][lo_q].mean()
        contrast[name] = r
        print(f"   {name:14s} rho {r:+.3f} [{lo:+.2f},{hi:+.2f}]   top-quartile minus bottom-quartile contrast {gap:+.3f}")

    # 3. what the model needs
    print("\n3. held-out within-hour Spearman, heating-season hours (sensor-grouped 5-fold, 3 repeats)")
    best = max(feats, key=lambda k: abs(contrast.get(k, 0)))
    base_cols = ["T", "elev"]
    out = {}
    for label, extra in (("traffic + terrain (baseline)", []), *((f"+ {k}", [k]) for k in ("lowrise_250", "lowrise_500", "lowrise_1000", "fraction_500"))):
        d = df.copy()
        for k in extra:
            d[k] = feats[k].loc[d.sensor].to_numpy()
        vals = {sel: [cv_rho(d, base_cols + extra, seed, True, thr) for seed in (1, 2, 3)] for sel, thr in (("heating", None), ("heating, city > 15", 15.0))}
        out[label] = vals
    b0 = {k: np.mean(v) for k, v in out["traffic + terrain (baseline)"].items()}
    for label, vals in out.items():
        print(f"   {label:30s} " + "   ".join(f"{k}: {np.mean(v):.3f} (+/-{np.std(v):.3f}, gain {np.mean(v) - b0[k]:+.3f})" for k, v in vals.items()))
    gain = max(np.mean(v["heating"]) - b0["heating"] for k, v in out.items() if k != "traffic + terrain (baseline)")
    print(f"\n   best gain in heating-season hours: {gain:+.3f}; acceptance bar +{ACCEPT:.2f} -> {'ENTERS the model' if gain >= ACCEPT else 'stays context only'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
