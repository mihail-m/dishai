from datetime import datetime, timezone

from backend import geo
from backend.sensors import fresh_only, normalise


def rec(sid, p2, indoor=0, ts="2026-10-17 10:00:00", lat="42.7", lon="23.3", p1="5"):
    vals = [{"value_type": "P1", "value": p1}]
    if p2 is not None:
        vals.append({"value_type": "P2", "value": p2})
    return {
        "id": sid * 10,
        "timestamp": ts,
        "sensor": {"id": sid},
        "location": {"latitude": lat, "longitude": lon, "indoor": indoor},
        "sensordatavalues": vals,
    }


def test_normalise_filters():
    raw = [
        rec(1, "12.5"),
        rec(2, "9", indoor=1),  # indoor
        rec(3, None),  # no P2 (not a PM sensor)
        rec(4, "900"),  # implausible
        rec(5, "abc"),  # junk
        rec(6, "7", lat="x"),  # bad location
    ]
    out = normalise(raw)
    assert [r["id"] for r in out] == [1]
    assert out[0]["pm25"] == 12.5 and out[0]["lat"] == 42.7


def test_normalise_keeps_latest_per_sensor():
    raw = [rec(1, "10", ts="2026-10-17 10:00:00"), rec(1, "20", ts="2026-10-17 10:05:00"), rec(1, "5", ts="2026-10-17 09:00:00")]
    out = normalise(raw)
    assert len(out) == 1 and out[0]["pm25"] == 20


def test_fresh_only():
    now = datetime(2026, 10, 17, 10, 30, tzinfo=timezone.utc)
    recs = [{"timestamp": "2026-10-17 10:10:00"}, {"timestamp": "2026-10-17 09:40:00"}, {"timestamp": None}]
    assert len(fresh_only(recs, now=now)) == 1


def test_point_in_geometry_with_hole():
    poly = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]}
    assert geo.point_in_geometry(1, 1, poly)
    assert not geo.point_in_geometry(5, 5, poly)  # in the hole
    assert not geo.point_in_geometry(11, 5, poly)
