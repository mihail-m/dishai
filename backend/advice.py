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


def pollen_summary(hours: list[dict], only: frozenset[str] | set[str] | None = None) -> dict:
    """Peak pollen per type over the given hours. available=False when the feed has no pollen values at all.
    `only` limits it to the user's own trigger species."""
    peaks: dict[str, tuple[float, str]] = {}
    seen = False
    for h in hours:
        for k, v in (h.get("pollen") or {}).items():
            if v is None or k not in POLLEN_BANDS or (only and k not in only):
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


def activity_window(hours: list[dict], scores: list[float], pollen_allergy: bool, only: frozenset[str] | set[str] | None = None) -> dict | None:
    """Best two-hour slot between 06:00 and 21:00 for being outside: low stagnation, low heat, low pollen if allergic."""
    pen = []
    for h, sc in zip(hours, scores):
        app = h.get("apparent") if h.get("apparent") is not None else h["t2m"]
        p = sc + 4.0 * max(0.0, app - 24.0)
        if pollen_allergy and h.get("pollen"):
            p += 12.0 * max((_pollen_level(k, v) for k, v in h["pollen"].items() if v is not None and k in POLLEN_BANDS and (not only or k in only)), default=0)
        pen.append(p)
    cands = [i for i in range(len(hours) - 1) if 6 <= int(hours[i]["time"][11:13]) <= 19]
    if not cands:
        return None
    i = min(cands, key=lambda k: pen[k] + pen[k + 1])
    start, end = hours[i]["time"], hours[i + 1]["time"]
    end_h = int(end[11:13]) + 1
    return {"start_hhmm": start[11:16], "end_hhmm": f"{end_h % 24:02d}:00", "penalty": round((pen[i] + pen[i + 1]) / 2, 1)}


def conditions(hours: list[dict], scores: list[float], pollen_allergy: bool = False, species: tuple[str, ...] | list[str] = ()) -> dict:
    """What the cards need besides the stagnation level: heat, pollen, UV / ozone / dust and the best time to be outside.
    For an allergic user who named trigger species, pollen and the outdoor slot count only those species."""
    from . import pollen as pollen_mod  # pollen imports this module's constants

    only = frozenset(s for s in species if s in POLLEN_BANDS) if pollen_allergy else frozenset()
    return {
        "heat": heat_summary(hours),
        "pollen": {**pollen_summary(hours, only or None), "triggers": sorted(only)},
        "activity": activity_window(hours, scores, pollen_allergy, only or None),
        "environment": pollen_mod.environment_summary(hours),
    }


def construction_context(data: dict | None, district: str) -> dict | None:
    """Construction dust context for a district, or None when the data is missing or the district is not among the busiest third."""
    row = (data or {}).get("districts", {}).get(district)
    if not row or row["share_pct"] < data["high_from_pct"] or row["n_sites"] < 10:
        return None
    return {"share_pct": row["share_pct"], "n_sites": row["n_sites"]}


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


def _season_cards(season: list[dict], triggers: set[str], pollen: dict, pol: int) -> list[dict]:
    """For allergic users: when the next trigger season usually starts, and which foods the active species may cross-react with."""
    from . import pollen as pollen_mod

    mine = [s for s in season if not triggers or s["type"] in triggers]
    cards = []
    soon = sorted((s for s in mine if s["status"] == "upcoming" and s["in_days"] <= 45), key=lambda s: s["in_days"])
    active = [s for s in mine if s["status"] == "in_season"]
    if soon and pol < 2:
        s = soon[0]
        cards.append({
            "id": "pollen_season",
            "title": f"{POLLEN_LABEL[s['type']]} season usually starts in about {s['in_days']} days" if s["in_days"] > 1 else f"{POLLEN_LABEL[s['type']]} season usually starts about now",
            "body": f"In the model history for Sofia (2022 to 2025) {POLLEN_LABEL[s['type']].lower()} pollen typically reaches the season threshold around {s['start']} "
                    f"and fades by {s['end']}. Start your usual medication when your doctor advises, a little before the season. Dates are modelled and vary by year.",
            "measure": None, "severity": "info",
        })
    elif active and pol == 0:
        names = ", ".join(POLLEN_LABEL[s["type"]].lower() for s in active)
        cards.append({
            "id": "pollen_season",
            "title": f"In season for {names}, but the forecast is quiet",
            "body": "It is the usual season, yet the model shows little pollen in the next 24 h. Rain, cool weather or a modelling gap can do that: stay watchful if you react.",
            "measure": None, "severity": "info",
        })
    now_types = [t["type"] for t in pollen.get("types", []) if triggers == set() or t["type"] in triggers][:2]
    if now_types:
        foods = []
        for k in now_types:
            foods.append(f"{POLLEN_LABEL[k]}: {', '.join(pollen_mod.SPECIES[k]['foods'][:6])}")
        cards.append({
            "id": "cross_reaction",
            "title": "Some foods can itch while this pollen is up",
            "body": "Some people with pollen allergy get an itchy mouth or throat from certain raw fruit, vegetables and nuts (oral allergy syndrome). Often reported with "
                    + "; ".join(foods) + ". Cooking usually removes it. Not everyone is affected; ask your allergist.",
            "measure": None, "severity": "info",
        })
    return cards


def _environment_cards(env: dict, toggles: frozenset[str], asthma: bool, carer: bool, sensitive: bool) -> list[dict]:
    from . import pollen as pollen_mod

    cards = []
    uv, oz, du = env.get("uv"), env.get("ozone"), env.get("dust")
    outdoors = "outdoor_exercise" in toggles
    if uv and (uv["level"] >= 2 or (uv["level"] >= 1 and (outdoors or carer or sensitive))):
        lv = pollen_mod.UV_LEVELS[uv["level"]]
        cards.append({
            "id": "uv", "measure": None, "severity": "action" if uv["level"] >= 3 else "info",
            "title": f"UV index {uv['peak']:.0f} ({lv}) around {uv['peak_hhmm']}",
            "body": "Use shade around midday, a hat and sunscreen, and sunglasses. " + ("Keep children and babies out of direct sun in the middle of the day. " if carer else "")
                    + "WHO advises sun protection from UV 3.",
        })
    if oz and (oz["level"] >= 2 or (oz["level"] >= 1 and (asthma or outdoors or carer))):
        lv = pollen_mod.OZONE_LEVELS[oz["level"]]
        cards.append({
            "id": "ozone", "measure": None, "severity": "action" if oz["level"] >= 2 else "info",
            "title": f"Ozone {lv}: peak {oz['peak']:.0f} µg/m³ around {oz['peak_hhmm']}",
            "body": "Ozone irritates the airways and builds up on sunny afternoons. Plan hard exercise for the morning or evening, and keep a reliever inhaler to hand if you have asthma. "
                    "The PM2.5 air score does not include ozone.",
        })
    if du and (du["level"] >= 2 or (du["level"] >= 1 and (asthma or carer or "pollen_allergy" in toggles))):
        cards.append({
            "id": "dust", "measure": None, "severity": "action" if du["level"] >= 2 else "info",
            "title": f"Mineral dust {pollen_mod.DUST_LEVELS[du['level']]}: up to {du['peak']:.0f} µg/m³ around {du['peak_hhmm']}",
            "body": "Desert dust (often Saharan) is being carried over the region. It adds coarse particles that can aggravate asthma and hay fever. "
                    "Keep windows closed in the worst hours, and wet-clean surfaces rather than dry-sweeping.",
        })
    return cards


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
        tips = f"{'For your triggers: ' if pollen.get('triggers') else ''}mainly {names}, highest around {pollen['peak_hhmm']}."
        tips = tips[0].upper() + tips[1:]
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

    # --- pollen season outlook and food cross-reactions: only for people who said they are allergic
    triggers = set(pollen.get("triggers") or [])
    if allergic:
        cards.extend(_season_cards(cond.get("season") or [], triggers, pollen, pol))

    # --- UV, ozone, mineral dust: from the same CAMS feed as the pollen
    cards.extend(_environment_cards(cond.get("environment") or {}, toggles, asthma, carer, sensitive))

    # --- construction dust: context for sensitive users in the busiest districts. Not modelled in the air score.
    cons = cond.get("construction")
    if cons and (asthma or carer):
        cards.append(
            {
                "id": "construction_dust",
                "title": "Many building sites in your district",
                "body": (
                    f"About {cons['n_sites']} sites are mapped as under construction ({cons['share_pct']:.1f}% of the district area). "
                    "Building dust is mostly coarse particles that the PM2.5 map does not capture. On dry, windy days keep walks and play "
                    "away from open sites and unpaved ground, and keep windows facing a site closed. "
                    "Sites come from OpenStreetMap, are not dated and some may be finished."
                ),
                "measure": None,
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
