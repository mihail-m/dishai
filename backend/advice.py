"""Action cards. Every card names the municipal plan (PDUEK) measure it implements."""

PROFILES = ("driver", "wood_heater", "vulnerable")
# Personalisation toggles, layered on top of the profile. Each one adds cards; none is stored server-side.
TOGGLES = ("pollen_allergy", "asthma", "heat_sensitive", "outdoor_exercise", "kids_or_elderly")

POLLEN_TYPES = ("alder", "birch", "grass", "mugwort", "olive", "ragweed")
POLLEN_LABEL = {"alder": "Alder", "birch": "Birch", "grass": "Grass", "mugwort": "Mugwort", "olive": "Olive", "ragweed": "Ragweed"}
# (moderate from, high from) in grains/m3; approximate bands per pollen family, indicative only
POLLEN_BANDS = {
    "alder": (15, 90), "birch": (15, 90), "olive": (15, 90),
    "grass": (5, 20),
    "mugwort": (10, 50), "ragweed": (10, 50),
}
# feels-like temperature (deg C): caution / high / extreme
HEAT_BANDS = (27.0, 32.0, 38.0)
HEAT_LEVELS = ("none", "caution", "high", "extreme")
POLLEN_LEVELS = ("none", "low", "moderate", "high")

GRANT_NOTE = "Up to about 10,000 EUR per household under the plan; final terms may change."

MEASURES = {
    "PUGM": "PUGM green ticket",
    "C.1.1": "C.1.1 low-emission zone",
    "C.3.2": "C.3.2 home renovation",
    "A.5.4": "A.5.4 climate shelter",
    "A.3.4": "A.3.4 early warning",
    "A.3.3": "A.3.3 self-cleaning potential",
    "A.4.7": "A.4.7 street washing",
}


def _measure(key: str) -> dict:
    return {"id": key, "label": MEASURES[key]}


def _fmt_window(w: dict | None) -> str:
    return f"{w['start_hhmm']} to {w['end_hhmm']}" if w else "the coming hours"


def heat_summary(hours: list[dict]) -> dict:
    """Heat stress over the given hours (next 24 h), from feels-like temperature."""
    app = [(h["time"], h.get("apparent") if h.get("apparent") is not None else h["t2m"]) for h in hours]
    if not app:
        return {"level": "none", "peak_c": None, "peak_hhmm": None, "start_hhmm": None, "end_hhmm": None, "warm_night": False}
    peak_t, peak = max(app, key=lambda x: x[1])
    level = HEAT_LEVELS[sum(peak >= b for b in HEAT_BANDS)]
    # the contiguous run of warm hours around the peak (the slice can span two afternoons, so not first/last overall)
    pk = max(range(len(app)), key=lambda k: app[k][1])
    lo = hi = pk
    while lo > 0 and app[lo - 1][1] >= HEAT_BANDS[0]:
        lo -= 1
    while hi < len(app) - 1 and app[hi + 1][1] >= HEAT_BANDS[0]:
        hi += 1
    hot = app[lo : hi + 1] if peak >= HEAT_BANDS[0] else []
    night_min = [h["t2m"] for h in hours if h["time"][11:13] >= "22" or h["time"][11:13] < "06"]
    return {
        "level": level,
        "peak_c": round(peak, 1),
        "peak_hhmm": peak_t[11:16],
        "start_hhmm": hot[0][0][11:16] if hot else None,
        "end_hhmm": hot[-1][0][11:16] if hot else None,
        "warm_night": bool(night_min) and min(night_min) >= 20.0,  # "tropical night"
    }


def _pollen_level(kind: str, grains: float) -> int:
    mod, high = POLLEN_BANDS[kind]
    return 3 if grains >= high else 2 if grains >= mod else 1 if grains >= 1 else 0


def pollen_summary(hours: list[dict]) -> dict:
    """Peak pollen per type over the given hours. available=False when the feed has no pollen values at all."""
    peaks: dict[str, tuple[float, str]] = {}
    seen = False
    for h in hours:
        for k, v in (h.get("pollen") or {}).items():
            if v is None or k not in POLLEN_BANDS:
                continue
            seen = True
            if v > peaks.get(k, (0.0, ""))[0]:
                peaks[k] = (v, h["time"])
    if not seen:
        return {"available": False, "level": "none", "types": [], "peak_hhmm": None}
    types = sorted(
        ({"type": k, "label": POLLEN_LABEL[k], "grains": round(v, 1), "level": POLLEN_LEVELS[_pollen_level(k, v)], "peak_hhmm": t[11:16]} for k, (v, t) in peaks.items()),
        key=lambda x: (-POLLEN_LEVELS.index(x["level"]), -x["grains"]),
    )
    types = [t for t in types if t["level"] != "none"]
    top = types[0] if types else None
    return {"available": True, "level": top["level"] if top else "none", "types": types[:3], "peak_hhmm": top["peak_hhmm"] if top else None}


def activity_window(hours: list[dict], scores: list[float], pollen_allergy: bool) -> dict | None:
    """Best two-hour slot between 06:00 and 21:00 for being outside: low stagnation, low heat, low pollen if allergic."""
    pen = []
    for h, sc in zip(hours, scores):
        app = h.get("apparent") if h.get("apparent") is not None else h["t2m"]
        p = sc + 4.0 * max(0.0, app - 24.0)
        if pollen_allergy and h.get("pollen"):
            p += 12.0 * max((_pollen_level(k, v) for k, v in h["pollen"].items() if v is not None and k in POLLEN_BANDS), default=0)
        pen.append(p)
    cands = [i for i in range(len(hours) - 1) if 6 <= int(hours[i]["time"][11:13]) <= 19]
    if not cands:
        return None
    i = min(cands, key=lambda k: pen[k] + pen[k + 1])
    start, end = hours[i]["time"], hours[i + 1]["time"]
    end_h = int(end[11:13]) + 1
    return {"start_hhmm": start[11:16], "end_hhmm": f"{end_h % 24:02d}:00", "penalty": round((pen[i] + pen[i + 1]) / 2, 1)}


def conditions(hours: list[dict], scores: list[float], pollen_allergy: bool = False) -> dict:
    """What the cards need besides the stagnation level: heat, pollen and the best time to be outside."""
    return {
        "heat": heat_summary(hours),
        "pollen": pollen_summary(hours),
        "activity": activity_window(hours, scores, pollen_allergy),
    }


def _profile_cards(profile: str, level: str, alert: dict | None, best: dict | None, zone: dict) -> list[dict]:
    """level: good | watch | warning for the chosen district over the next 24 h."""
    risky = level in ("watch", "warning")
    window = _fmt_window(alert)
    best_txt = f"{best['start_hhmm']} to {best['end_hhmm']}" if best else "early afternoon"
    cards: list[dict] = []

    if profile == "driver":
        cards.append(
            {
                "id": "green_ticket",
                "title": "Leave the car at home: take the green ticket",
                "body": (
                    f"Public transport on the green ticket cuts traffic exactly when the air is trapped ({window})."
                    if risky
                    else "Air is mixing well, but the green ticket is the standing low-emission way to get around."
                ),
                "measure": _measure("PUGM"),
                "severity": "action" if level == "warning" else "info",
            }
        )
        zone_txt = {
            "inner": "Your district centre falls inside the illustrative inner low-emission zone. Check vehicle class rules before driving in.",
            "outer": "Your district centre falls inside the illustrative outer low-emission zone. Check vehicle class rules before driving in.",
            None: "Your district centre is outside the illustrative low-emission zones, but trips into the centre may cross one.",
        }[zone["zone"]]
        cards.append(
            {
                "id": "lez_check",
                "title": "Check the low-emission zone before you drive in",
                "body": zone_txt + " Zone polygons are illustrative, not official.",
                "measure": _measure("C.1.1"),
                "severity": "action" if zone["zone"] and risky else "info",
            }
        )
        cards.append(
            {
                "id": "shift_trips",
                "title": "Shift non-essential trips out of the worst hours",
                "body": f"Worst trapping is expected {window}. Best mixing: {best_txt}."
                if risky
                else "No trapping expected in the next 24 h. We will warn you ahead of the next episode.",
                "measure": _measure("A.3.4"),
                "severity": "info",
            }
        )

    elif profile == "wood_heater":
        cards.append(
            {
                "id": "hold_fire",
                "title": "Hold off on solid-fuel burning during the trapped window",
                "body": (
                    f"Smoke from wood and coal stays near the ground when an inversion sits over the basin ({window}). "
                    "If you have another heat source, use it; if not, burn dry wood only and keep the fire small."
                    if risky
                    else "Dispersion is fine right now. Early warnings tell you in advance when to switch heat sources."
                ),
                "measure": _measure("A.3.4"),
                "severity": "action" if level == "warning" else "info",
            }
        )
        zone_txt = {
            "inner": "Inner zone (illustrative): the strictest rules would apply here.",
            "outer": "Outer zone (illustrative): zone rules would apply here.",
            None: "Outside the illustrative zones: no zone rules apply in this POC.",
        }[zone["zone"]]
        cards.append(
            {
                "id": "zone_lookup",
                "title": "Zone check for your district",
                "body": zone_txt + " Polygons are illustrative until the municipality publishes official geometry.",
                "measure": _measure("C.1.1"),
                "severity": "info",
                "zone": zone,
            }
        )
        cards.append(
            {
                "id": "renovation",
                "title": "Replace the stove: check home renovation support",
                "body": f"Energy-poor households can get support to renovate and switch heating. {GRANT_NOTE}",
                "measure": _measure("C.3.2"),
                "severity": "action",
                "cta": "eligibility_wizard",
            }
        )

    elif profile == "vulnerable":
        cards.append(
            {
                "id": "stay_in",
                "title": "Limit time outdoors and keep exertion low in the trapped window",
                "body": (
                    f"Fine particles are highest {window}. Keep windows closed then, avoid outdoor exercise, and keep rescue medication to hand."
                    if risky
                    else "Air is mixing well. Normal activity is fine; check back before evening."
                ),
                "measure": _measure("A.3.4"),
                "severity": "action" if level == "warning" else "info",
            }
        )
        cards.append(
            {
                "id": "ventilate",
                "title": f"Air the home when the air mixes best ({best_txt})",
                "body": "The atmosphere clears itself most in the early afternoon. Ventilate briefly then, not at night or during rush hour.",
                "measure": _measure("A.3.3"),
                "severity": "info",
            }
        )
        cards.append(
            {
                "id": "climate_shelter",
                "title": "Know your nearest climate shelter",
                "body": "Municipal climate shelters offer a cleaner, conditioned space. Locations come from the municipal list (not included in this POC).",
                "measure": _measure("A.5.4"),
                "severity": "action" if level == "warning" else "info",
            }
        )
    return cards[:4]


def _heat_window(heat: dict) -> str:
    return f"{heat['start_hhmm']} to {heat['end_hhmm']}" if heat["start_hhmm"] else "the afternoon"


def _personal_cards(
    level: str, alert: dict | None, best: dict | None, cond: dict, toggles: frozenset[str]
) -> tuple[list[dict], list[dict]]:
    """Heat, pollen and toggle cards. Returns (urgent, calm): urgent ones go before the profile cards, calm ones after."""
    heat, pollen, act = cond["heat"], cond["pollen"], cond["activity"]
    hot = HEAT_LEVELS.index(heat["level"])  # 0 none, 1 caution, 2 high, 3 extreme
    pol = POLLEN_LEVELS.index(pollen["level"])  # 0 none, 1 low, 2 moderate, 3 high
    risky = level in ("watch", "warning")
    window = _fmt_window(alert)
    best_txt = f"{best['start_hhmm']} to {best['end_hhmm']}" if best else "early afternoon"
    sensitive = "heat_sensitive" in toggles
    carer = "kids_or_elderly" in toggles
    cards: list[dict] = []

    # --- heat: everyone sees it from "high"; heat-sensitive users and carers see it from "caution" and get a status line otherwise
    if hot >= 2 or ((sensitive or carer) and hot >= 1):
        tips = f"Peak feels-like {heat['peak_c']:.0f}°C at {heat['peak_hhmm']}. Keep to the shade and drink water before you are thirsty."
        if sensitive:
            tips += " Avoid exertion in the hot hours, keep medication out of the sun, and rest somewhere cool."
        if heat["warm_night"]:
            tips += " Nights stay above 20°C, so the home will not cool down: ventilate after dark."
        cards.append(
            {
                "id": "heat",
                "title": f"{'Extreme heat' if hot == 3 else 'Heat'} {_heat_window(heat)}: plan around it",
                "body": tips,
                "measure": _measure("A.5.4"),
                "severity": "action" if hot >= 2 else "info",
            }
        )
        if sensitive and hot >= 2:
            cards.append(
                {
                    "id": "heat_shelter",
                    "title": "Know where to cool down",
                    "body": "Municipal climate shelters are conditioned public spaces for hot days. Locations come from the municipal list (not included in this POC).",
                    "measure": _measure("A.5.4"),
                    "severity": "info",
                }
            )
    elif sensitive:
        cards.append(
            {
                "id": "heat",
                "title": "No heat stress expected in the next 24 h",
                "body": f"Peak feels-like is {heat['peak_c']:.0f}°C at {heat['peak_hhmm']}. We will flag it ahead of the next hot spell."
                if heat["peak_c"] is not None
                else "No temperature data right now.",
                "measure": _measure("A.3.4"),
                "severity": "info",
            }
        )

    # --- pollen: everyone sees "high"; allergic or asthmatic users see it from "low" and a status line otherwise
    allergic = "pollen_allergy" in toggles
    asthma = "asthma" in toggles
    names = ", ".join(t["label"].lower() for t in pollen["types"])
    if pol >= 3 or ((allergic or asthma) and pol >= 1):
        tips = f"Mainly {names}, highest around {pollen['peak_hhmm']}."
        if pol >= 2:
            tips += " Keep windows closed around then, shower and change clothes after being out, and use your usual allergy medication as your doctor advised."
        else:
            tips += " Low levels: most people notice little, but sensitive noses may react."
        cards.append(
            {
                "id": "pollen",
                "title": f"{POLLEN_LEVELS[pol].capitalize()} pollen today",
                "body": tips,
                "measure": _measure("A.3.4"),
                "severity": "action" if pol >= 3 else "info",
            }
        )
    elif allergic:
        cards.append(
            {
                "id": "pollen",
                "title": "Pollen: none in the forecast" if pollen["available"] else "Pollen: no data right now",
                "body": "The pollen forecast shows no measurable pollen for the next 24 h. Outside the season it is usually zero."
                if pollen["available"]
                else "The pollen feed returned nothing for Sofia. It only covers the pollen season.",
                "measure": _measure("A.3.4"),
                "severity": "info",
            }
        )

    # --- asthma: combines every trigger we can see
    if asthma:
        triggers = []
        if risky:
            triggers.append(f"trapped air ({window})")
        if hot >= 2:
            triggers.append(f"heat (to {heat['peak_hhmm']})")
        if pol >= 2:
            triggers.append(f"{POLLEN_LEVELS[pol]} pollen")
        cards.append(
            {
                "id": "asthma_air",
                "title": "Asthma: carry your reliever today" if triggers else "Asthma: no major triggers expected",
                "body": (
                    "Triggers in the next 24 h: " + ", ".join(triggers) + ". Keep the reliever inhaler with you, avoid hard outdoor exercise then, "
                    "and follow your asthma action plan."
                )
                if triggers
                else "Air is mixing, it is not hot and pollen is low. Normal activity is fine; check back before evening.",
                "measure": _measure("A.3.4"),
                "severity": "action" if triggers and (level == "warning" or hot >= 3 or pol >= 3) else "info",
            }
        )

    # --- children and older people
    if carer:
        lines = []
        if risky:
            lines.append(f"Plan outdoor play and walks for {best_txt}, not during {window}.")
        if hot >= 1:
            lines.append(f"Feels like {heat['peak_c']:.0f}°C at {heat['peak_hhmm']}: keep them hydrated and out of the midday sun, and never leave anyone in a parked car.")
        if pol >= 2:
            lines.append("Pollen is up: rinse faces and hands after outdoor play.")
        if hot >= 2 or risky:
            lines.append("Check in on older neighbours when it is hot or the air is bad.")
        cards.append(
            {
                "id": "care_check",
                "title": "Children and older relatives: today's plan" if lines else "Children and older relatives: all clear",
                "body": " ".join(lines) if lines else "Air, heat and pollen are all fine for normal outdoor time.",
                "measure": _measure("A.3.4"),
                "severity": "action" if level == "warning" or hot >= 3 else "info",
            }
        )

    # --- outdoor exercise: one combined best slot
    if "outdoor_exercise" in toggles:
        why = []
        if risky:
            why.append("avoids the trapped air")
        if hot >= 1:
            why.append("dodges the afternoon heat")
        if "pollen_allergy" in toggles and pol >= 1:
            why.append("keeps pollen low")
        cards.append(
            {
                "id": "exercise_window",
                "title": f"Best time to exercise outdoors: {act['start_hhmm']} to {act['end_hhmm']}" if act else "Best time to exercise outdoors",
                "body": (
                    ("This slot " + ", ".join(why) + ". " if why else "Air, heat and pollen are all fine, so this is simply the best slot. ")
                    + "Take an easy pace if you are close to a busy road: traffic fumes are worse at the kerb."
                )
                if act
                else "No daytime hours left in the next 24 h.",
                "measure": _measure("A.3.3"),
                "severity": "info",
            }
        )

    urgent = [c for c in cards if c["severity"] == "action"]
    calm = [c for c in cards if c["severity"] != "action"]
    return urgent, calm


def build_cards(
    profile: str,
    level: str,
    alert: dict | None,
    best: dict | None,
    zone: dict,
    cond: dict | None = None,
    toggles: tuple[str, ...] | frozenset[str] = (),
) -> list[dict]:
    """Profile cards plus heat, pollen and toggle cards. Urgent extras lead; calm extras trail the profile cards."""
    base = _profile_cards(profile, level, alert, best, zone)
    if cond is None:
        return base
    urgent, calm = _personal_cards(level, alert, best, cond, frozenset(toggles))
    return urgent + base + calm
