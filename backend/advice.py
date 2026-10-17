"""Action cards. Every card names the municipal plan (PDUEK) measure it implements."""

PROFILES = ("driver", "wood_heater", "vulnerable")

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


def build_cards(profile: str, level: str, alert: dict | None, best: dict | None, zone: dict) -> list[dict]:
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
