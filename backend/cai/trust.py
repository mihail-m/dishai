"""'How far to trust this' summary for /api/validation and the validation card: the measured numbers of each stage, read from the files
the fits wrote (calibration, spatial, model, street-vs-area). Nothing is recomputed here; missing pieces are simply absent."""
import json

from ..ml import calibration as calibration_mod
from ..ml.pm_model import PmModel
from . import krige
from .graph import DATA

STREET_VS_AREA = DATA / "street_vs_area.json"


def summary(model: PmModel | None = None) -> dict:
    out: dict = {}
    cal = calibration_mod.get("pm25")
    if cal is not None and cal.meta:
        m = cal.meta
        city = m.get("city_check") or {}
        out["calibration"] = {
            "reference": "EEA station Hipodruma (BG0050A), hourly PM2.5", "rows": m.get("rows"), "sensors": m.get("sensors"),
            "raw_mae": city.get("mae_raw", m.get("raw_mae")), "calibrated_mae": city.get("mae_cal"), "mae_reduction_pct": city.get("mae_reduction_pct"),
            "ratio_by_rh": {k: v["median_ratio"] for k, v in (city.get("ratio_by_rh") or {}).items()},
            "typical_error_pct": round(100 * cal.sigma_log), "one_reference_station": True,
        }
    sp = krige.get_params()
    if sp is not None and sp.meta:
        ev = sp.meta.get("eval") or {}
        vg = (sp.meta.get("variograms") or {}).get("all") or {}
        pick = lambda k: {m: ev[k][m] for m in ("hours", "coverage80", "idw_current", "krige_offsets_removed", "level_trend") if k in ev and m in ev[k]} if k in ev else None
        out["spatial"] = {
            "offset_sd_pct": round(100 * (2.718281828 ** sp.meta.get("offset_sd_log", 0) - 1)), "offset_stability_r": sp.meta.get("offset_stability_r"),
            "range_km": round(vg["range_m"] / 1000, 1) if vg else None, "nugget_share_pct": round(100 * vg["nugget"] / (vg["nugget"] + vg["psill"])) if vg else None,
            "polluted_heating": pick("polluted_heating"), "heating": pick("heating"), "summer": pick("summer"), "all": pick("all"),
        }
    if STREET_VS_AREA.exists():
        try:
            out["street_vs_area"] = json.loads(STREET_VS_AREA.read_text())
        except (OSError, ValueError):
            pass
    a = (model.meta.get("alert") if model is not None else None) or {}
    if a:
        bt = a.get("backtest", {}).get("test", {})
        out["alert"] = {"p_red": a.get("p_red"), "p_watch": a.get("p_watch"), "selected_on": a.get("selected_on"),
                        "test_period": {k: {m: v.get(m) for m in ("alerts", "pod", "far", "peirce", "missed_episodes")} for k, v in bt.items()}}
    return out
