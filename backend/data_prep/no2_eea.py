"""Hourly NO2 for the Sofia EEA stations -> data/history/no2_reference.csv (+ no2_stations.csv).

    python -m backend.data_prep.no2_eea

Same service and conventions as reference_eea.py (EEA e-reporting download API, dataset 1, `Start` is UTC+1 all year, Validity > 0), but
for NO2, the pollutant road traffic actually moves in Sofia (see docs/TRAFFIC.md). The helpers are imported from reference_eea; the
download is read as data from memory and nothing in it is executed.
Licence: EEA content is CC BY 4.0; credit the EEA and the reporting authority.
"""
import sys

import httpx
import pandas as pd

from . import reference_eea as R

OUT = R.OUT


def main() -> int:
    R.POLLUTANTS = {"NO2": "8"}  # the module-level map is what stations() filters on
    rows, used = [], []
    with httpx.Client(follow_redirects=True) as c:
        st = R.stations(c)
        want = st[st.pollutant == "NO2"].set_index("station")
        print(f"{len(want)} NO2 stations in the Sofia box", file=sys.stderr)
        for u in R.urls(c, "NO2", 1):  # the API takes the pollutant NAME here, not the vocabulary code
            stem = u.rsplit("/", 1)[-1]
            station = stem.split("_")[0].removeprefix("SPO-")
            if station not in want.index or "_00008_" not in stem:
                continue
            d = R.hourly(c, u)
            if d is None or d.empty:
                continue
            d.insert(0, "station", station)
            d.insert(1, "station_type", want.loc[station].station_type)
            rows.append(d)
            used.append((station, want.loc[station].station_type, want.loc[station].lat, want.loc[station].lon, len(d), d.hour_utc.min(), d.hour_utc.max()))
            print(f"{station} ({want.loc[station].station_type}): {len(d)} valid hours", file=sys.stderr)
    if not rows:
        print("no NO2 data downloaded", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    pd.concat(rows).drop_duplicates(["station", "hour_utc"]).to_csv(OUT / "no2_reference.csv", index=False)
    pd.DataFrame(used, columns=["station", "station_type", "lat", "lon", "hours", "first", "last"]).to_csv(OUT / "no2_stations.csv", index=False)
    print(R.ATTRIBUTION, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
