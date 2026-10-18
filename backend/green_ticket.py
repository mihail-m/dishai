"""Sofia's "green ticket" (зелен билет): a reduced-price all-day public-transport ticket the municipality announces on bad-air days.

There is no published feed for it. The municipality decides case by case, usually a day ahead, on a national meteorological forecast of conditions that
hold fine particles near the ground or on measured exceedances, and announces it through its website, the Urban Mobility Centre and the press (BTA,
BNT and Radio Bulgaria reported the January 2025 and winter 2025/26 ones). So the app reads announcements from a small file that a person updates:

    python -m backend.green_ticket add 2026-01-09 --source https://... [--note "free buffer parking too"]
    python -m backend.green_ticket remove 2026-01-09
    python -m backend.green_ticket list

data/green_ticket.json = {"entries": [{"date": "YYYY-MM-DD", "source": "url", "note": "..."}]}. The app never invents an announcement: with no entry it says
that none is announced. The price and terms change between announcements, so they are not stored; the entry's note and link carry them.
"""
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from .cai.graph import DATA

STORE = DATA / "green_ticket.json"


def _read(path: Path) -> list[dict]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("entries", [])
    except (OSError, ValueError):
        return []
    out = []
    for e in rows if isinstance(rows, list) else []:
        try:
            datetime.strptime(e["date"], "%Y-%m-%d")
            out.append({"date": e["date"], "source": str(e.get("source") or "")[:300], "note": str(e.get("note") or "")[:300]})
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(out, key=lambda e: e["date"])


def announced(today: str, days_ahead: int = 3, path: Path = STORE) -> list[dict]:
    """Entries for today and the next `days_ahead` days."""
    end = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    return [e for e in _read(path) if today <= e["date"] <= end]


def add(day: str, source: str = "", note: str = "", path: Path = STORE) -> None:
    datetime.strptime(day, "%Y-%m-%d")
    rows = [e for e in _read(path) if e["date"] != day] + [{"date": day, "source": source, "note": note}]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"entries": sorted(rows, key=lambda e: e["date"])}, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def remove(day: str, path: Path = STORE) -> bool:
    rows = _read(path)
    keep = [e for e in rows if e["date"] != day]
    if len(keep) == len(rows):
        return False
    path.write_text(json.dumps({"entries": keep}, indent=1, ensure_ascii=False), encoding="utf-8")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("date")
    a.add_argument("--source", default="")
    a.add_argument("--note", default="")
    sub.add_parser("list")
    r = sub.add_parser("remove")
    r.add_argument("date")
    args = ap.parse_args()
    if args.cmd == "add":
        add(args.date, args.source, args.note)
    elif args.cmd == "remove":
        print("removed" if remove(args.date) else "no such entry")
    else:
        for e in _read(STORE):
            print(e["date"], e["source"], e["note"])


if __name__ == "__main__":
    main()
