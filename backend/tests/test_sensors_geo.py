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


def _rec(i, dlat, pm, dlon=0.0):
    return {"id": i, "lat": 42.70 + dlat, "lon": 23.30 + dlon, "pm25": pm}


def test_one_sensor_far_above_agreeing_neighbours_is_dropped():
    from backend.sensors import drop_isolated_outliers
    recs = [_rec(1, 0.00, 4), _rec(2, 0.01, 5), _rec(3, 0.02, 4), _rec(4, -0.01, 5), _rec(9, 0.005, 120)]
    kept, dropped = drop_isolated_outliers(recs)
    assert [r["id"] for r in dropped] == [9]
    assert {r["id"] for r in kept} == {1, 2, 3, 4}


def test_a_local_plume_seen_by_several_sensors_is_kept():
    from backend.sensors import drop_isolated_outliers
    recs = [_rec(1, 0.00, 40), _rec(2, 0.01, 45), _rec(3, 0.02, 38), _rec(4, -0.01, 42), _rec(9, 0.005, 60)]
    assert drop_isolated_outliers(recs)[1] == []


def test_quiet_air_and_sparse_areas_never_trip_the_rule():
    from backend.sensors import drop_isolated_outliers
    quiet = [_rec(1, 0.00, 1), _rec(2, 0.01, 1), _rec(3, 0.02, 1), _rec(9, 0.005, 6)]  # 6x the median but only 5 ug/m3 above it
    assert drop_isolated_outliers(quiet)[1] == []
    sparse = [_rec(1, 0.00, 4), _rec(9, 0.30, 200)]  # nothing within 5 km to compare against
    assert drop_isolated_outliers(sparse)[1] == []


def test_winter_demo_has_a_dirty_pool_and_cleaner_ground_around_it():
    from backend.scenario import demo_geography, scenario_pm25
    assert demo_geography(42.690, 23.345, "winter_inversion") > 1.4      # the smoke pool
    assert demo_geography(42.665, 23.335, "winter_inversion") < 0.5      # the cleaner way south of the centre
    assert demo_geography(42.69, 23.34, "summer_heatwave") == 1.0         # other scenarios keep their uniform air
    assert demo_geography(None, None, "winter_inversion") == 1.0          # no position, no change
    assert scenario_pm25(4.0, "winter_inversion", 42.690, 23.345) > scenario_pm25(4.0, "winter_inversion", 42.665, 23.335) * 1.5
