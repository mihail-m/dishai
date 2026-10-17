"""Does proximity to construction sites predict PM2.5 on sensors? A reproducible check, not part of the app.

    PYTHONPATH=. .venv/bin/python -m backend.ml.construction_check

Needs data/construction.geojson (make construction) and data/history/. Tests, at sensor level: radius (50-500 m), site size and
kind, recent check_date, conditions where dust should show (workday daytime, dry, windy), PM10/PM2.5 on the few days of PM10
history, and a design where each sensor is its own control (workday vs weekend at the same hours).
"""
import json, numpy as np, pandas as pd, warnings
warnings.filterwarnings("ignore")
from scipy.stats import spearmanr
from shapely.geometry import shape, Point
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree
from pyproj import Transformer
from . import train as t
tr=Transformer.from_crs(4326,32635,always_xy=True).transform
feats=json.load(open("data/construction.geojson"))["features"]
sites=[]
for f in feats:
    g=transform(tr,shape(f["geometry"])); p=f["properties"]
    lv = p.get("building:levels") or p.get("construction:building:levels") or ""
    sites.append(dict(g=g,area=g.area,kind="building" if p.get("building")=="construction" else "land",check=p.get("check_date"),dated=bool(p.get("start_date") or p.get("opening_date")),
                      levels=float(lv) if lv.replace(".","",1).isdigit() else np.nan))
S=pd.DataFrame(sites)
print("areas m2 by kind:\n",S.groupby("kind").area.describe(percentiles=[.5,.9]).round(0).to_string())
print("check_date >= 2024:",(S.check.fillna("0")>="2024").sum(),"| dated:",S.dated.sum())

df,_=t.build_table(np.random.default_rng(7)); df=df.reset_index(drop=True)
loc=df.groupby("sensor")[["lat","lon"]].median()
pts=[transform(tr,Point(lo,la)) for la,lo in zip(loc.lat,loc.lon)]
def share(sel,R):
    u=unary_union([s for s in S[sel].g]) if sel.any() else None
    return np.array([ (p.buffer(R).intersection(u).area/(np.pi*R*R)) if u is not None else 0 for p in pts])
def nearest(sel):
    u=unary_union(list(S[sel].g)); return np.array([p.distance(u) for p in pts])
df["hm"]=df.groupby("time").pm.transform("median"); df["lr"]=np.log1p(df.pm)-np.log1p(df.hm)
sm=df.groupby("sensor").lr.mean().loc[loc.index]
st=df.groupby("sensor")[["T","elev"]].first().loc[loc.index]
from sklearn.linear_model import LinearRegression
X=np.column_stack([np.log1p(st["T"]),st.elev.fillna(st.elev.median())]); res=sm-LinearRegression().fit(X,sm).predict(X)
def rep(name,v,pos=True):
    v=pd.Series(v,index=loc.index); r=spearmanr(v,sm).statistic; pr=spearmanr(v,res).statistic
    rng=np.random.default_rng(0); b=[spearmanr(v.iloc[i],sm.iloc[i]).statistic for i in (rng.integers(0,len(v),len(v)) for _ in range(300))]
    print(f"{name:34s} n_near={int((v>0).sum()):3d}  rho={r:+.3f} [{np.percentile(b,2.5):+.2f},{np.percentile(b,97.5):+.2f}]  partial={pr:+.3f}")
allm=np.ones(len(S),bool)
print("\nsensor-level rank correlation with persistent PM2.5 offset (95% bootstrap CI):")
for R in (50,100,150,300,500): rep(f"share within {R} m (all sites)",share(allm,R))
big=(S.area>=2000).to_numpy(); small=~big
rep("share 150 m, sites >= 2000 m2",share(big,150)); rep("share 150 m, sites < 2000 m2",share(small,150))
rep("share 150 m, buildings only",share((S.kind=="building").to_numpy(),150)); rep("share 150 m, land only",share((S.kind=="land").to_numpy(),150))
rec=(S.check.fillna("0")>="2024").to_numpy(); rep("share 300 m, checked since 2024",share(rec,300))
fresh=(S.check.isna()|(S.check.fillna("9999")>="2023")).to_numpy()  # drop sites whose last check is older than 2023
print(f"  stale (checked before 2023): {(~fresh).sum()} of {len(S)}")
rep("share 150 m, not stale",share(fresh,150)); rep("share 300 m, not stale",share(fresh,300))
try:  # Sentinel-2 activity per site (make construction-activity); the order matches construction.geojson
    act = pd.read_csv("data/construction_activity.csv")
    nd, ndp = act.ndvi_recent.to_numpy(), act.ndvi_previous.to_numpy()
    print(f"  Sentinel-2 scored {int(np.isfinite(nd).sum())} of {len(act)} sites")
    for label, m in (("bare now (NDVI<0.2)", nd < 0.2), ("cleared since last year", ((ndp - nd) > 0.15) & (nd < 0.3)),
                     ("bare in both summers", (nd < 0.2) & (ndp < 0.2)), ("green now (NDVI>=0.3), control", nd >= 0.3)):
        m = np.nan_to_num(m.astype(float)).astype(bool)
        for R in (150, 300): rep(f"share {R} m, {label}", share(m, R))
except OSError:
    pass
tall=(S.levels>=4).to_numpy(); rep("share 300 m, buildings >= 4 levels",share(tall,300))
rep("-log distance to nearest site",-np.log1p(nearest(allm)))
# conditions where dust should show: workday daytime, dry
d=df.copy(); d["hr"]=d.time.dt.hour; d["wd"]=d.time.dt.weekday<5
c150=pd.Series(share(allm,150),index=loc.index); d["c"]=c150.loc[d.sensor].to_numpy()
print("\nwithin-hour rank correlation of 150 m share, by condition:")
for name,sel in {"all":d.hr>=0,"workday 9-17":d.wd&d.hr.between(9,17),"workday 9-17 & dry (rh<55)":d.wd&d.hr.between(9,17)&(d.rh<55),"workday 9-17 & dry & wind>10":d.wd&d.hr.between(9,17)&(d.rh<55)&(d.wind10>10),"weekend 9-17":(~d.wd)&d.hr.between(9,17),"night 0-5":d.hr<=5}.items():
    print(f"  {name:32s} rows {int(sel.sum()):7d}  rho {t.within_hour_rho(d[sel],d.c[sel].to_numpy()):+.3f}")
# PM10 from the partial re-download
p=pd.read_csv("data/history/sensors_pm10_partial.csv",parse_dates=["hour_utc"]); p=p.dropna(subset=["pm10"])
p=p[p.sensor.isin(loc.index)]; p["wd"]=p.hour_utc.dt.weekday<5; p["hr"]=(p.hour_utc+pd.Timedelta(hours=3)).dt.hour
day=p[p.wd&p.hr.between(9,17)]; night=p[p.hr<=5]
for nm,q in (("workday daytime",day),("night",night)):
    g=q.groupby("sensor").agg(pm25=("pm25","median"),pm10=("pm10","median")); g["ratio"]=g.pm10/g.pm25.clip(lower=1)
    v=c150.loc[g.index]; near=v>0.05
    print(f"PM10/PM2.5 {nm:16s} sensors near sites (>5%) {g.ratio[near].median():.2f} (n={near.sum()}) vs others {g.ratio[~near].median():.2f} (n={(~near).sum()}); rho(share, ratio)={spearmanr(v,g.ratio).statistic:+.3f}; days={q.hour_utc.dt.date.nunique()}")

print("\n=== each sensor as its own control: workday vs weekend, same hours (9-17) ===")
d["lr"]=d.lr
w=d[d.hr.between(9,17)].copy()
a=w[w.wd].groupby("sensor").lr.agg(["mean","size"]); b=w[~w.wd].groupby("sensor").lr.agg(["mean","size"])
j=a.join(b,lsuffix="_wd",rsuffix="_we").dropna(); j=j[(j.size_wd>200)&(j.size_we>40)]
j["diff"]=j.mean_wd-j.mean_we
T=np.log1p(st["T"]).loc[j.index]
from sklearn.linear_model import LinearRegression
for R in (100,150,300):
    c=pd.Series(share(allm,R),index=loc.index).loc[j.index]
    r=spearmanr(c,j["diff"]).statistic
    Z=np.column_stack([T]); resid=j["diff"]-LinearRegression().fit(Z,j["diff"]).predict(Z)
    pr=spearmanr(c,resid).statistic
    near=c>0.05
    rng=np.random.default_rng(1); idx=[rng.integers(0,len(j),len(j)) for _ in range(500)]
    bs=[ (j["diff"].values[i][near.values[i]].mean()-j["diff"].values[i][~near.values[i]].mean()) for i in idx if near.values[i].any() and (~near.values[i]).any()]
    print(f"R={R:3d} m: sensors {len(j)}, near {int(near.sum())}; rho(share, workday-weekend diff)={r:+.3f}, partial|traffic={pr:+.3f}; mean diff near-far={j['diff'][near].mean()-j['diff'][~near].mean():+.3f} log-units [{np.percentile(bs,2.5):+.3f},{np.percentile(bs,97.5):+.3f}]")
print("overall workday-minus-weekend (log units):",round(j["diff"].mean(),3))
