"""Local temperature field: the model's physics, and the endpoint's hour/weather dependence."""
import numpy as np
import pytest

from backend.cai import heat
from backend.ml import heat_field as HF


def _model():
    """A hand-made model: dense building height warms calm clear nights, higher ground is cooler by day. No file needed."""
    n_b, n_s = len(HF.BASIS), len(HF.STATIC)
    coef = np.zeros(n_b * (1 + n_s))
    coef[(1 + HF.STATIC.index("ghs_height_1000")) * n_b + HF.BASIS.index("calm_clear_night")] = 2.0
    coef[(1 + HF.STATIC.index("elev")) * n_b + HF.BASIS.index("sun")] = -1.0
    return {"mu": np.array([600.0, 0.0, 5.0]), "sd": np.array([100.0, 30.0, 4.0]), "coef": coef}


def test_basis_separates_day_night_and_calm():
    b = HF.basis([700.0, 0.0, 0.0], [10, 0, 100], [4, 2, 30])
    names = HF.BASIS
    assert b[0, names.index("sun")] > 0.8 and b[0, names.index("night")] == 0
    assert b[1, names.index("calm_clear_night")] > 0.7  # clear and nearly calm night
    assert b[2, names.index("calm_clear_night")] == 0   # cloudy and windy night: no pooling or heat-island boost


def test_built_up_places_warmer_on_calm_clear_nights_only():
    m = _model()
    dense, sparse = np.array([[600, 0, 20.0]]), np.array([[600, 0, 0.0]])
    gap = lambda rad, cloud, wind: float(HF.predict_offset(m, dense, rad, cloud, wind)[0] - HF.predict_offset(m, sparse, rad, cloud, wind)[0])
    assert gap(0, 0, 2) > 1.5          # calm clear night: heat island
    assert abs(gap(0, 90, 25)) < 0.2   # overcast and windy: no difference
    assert abs(gap(700, 10, 5)) < 0.5  # a sunny afternoon in this hand-made model has no building term


def test_higher_ground_cooler_in_sun():
    m = _model()
    low, high = np.array([[550, 0, 5.0]]), np.array([[900, 0, 5.0]])
    assert HF.predict_offset(m, high, 700, 10, 5)[0] < HF.predict_offset(m, low, 700, 10, 5)[0] - 1.0


def test_missing_features_fall_back_to_the_average_place():
    m = _model()
    assert HF.predict_offset(m, np.array([[np.nan, np.nan, np.nan]]), 300, 50, 8)[0] == pytest.approx(
        HF.predict_offset(m, np.array([m["mu"]]), 300, 50, 8)[0])


def test_heat_levels_use_the_advice_bands():
    assert [heat.level_for(v) for v in (10, 27, 33, 40)] == ["none", "caution", "high", "extreme"]
