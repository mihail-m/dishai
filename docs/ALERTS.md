# Alerts and notifications

One list of what needs attention in the next 48 hours (`GET /api/alerts`, `backend/alerts.py`). Each alert has a kind, level (info / watch / warning), when, peak, likely cause, source, confidence and 1-3 suggested actions. The panel banner, the suggestions list and the For me plan read the same objects; a push is sent once per (alert id, level).

| Kind | Source | What it can claim | Thresholds |
|---|---|---|---|
| `pm` | DishAI PM2.5 model (CAMS + citizen sensors), back-tested in [PM_MODEL.md](PM_MODEL.md) | a pollution episode is likely (probability shown) | existing banner: red from P 0.7, amber from 0.2 |
| `heat` | Open-Meteo feels-like forecast | a hot stretch of at least 2 h | peak 27 / 32 / 38 °C = info / watch / warning (the advice bands) |
| `dust` | Copernicus CAMS mineral dust at the surface, keyless via Open-Meteo, **taken as published** | desert dust forecast over the city; the message adds CAMS's own daily PM10 and the dust share | at least 2 h at 20 µg/m3 (watch) / 40 (warning) |
| `fire` | NASA FIRMS VIIRS active fires (keyless regional files, 3 satellites, last 24 h) + Open-Meteo 850 hPa wind, then CAMS | fires lie near or upwind and smoke **may** arrive; **likely** when CAMS carbon monoxide or PM2.5 is also above its 90th percentile for the month; warning when CAMS PM2.5 (3 h mean) reaches 25 µg/m3 | 10 detections or 200 MW within 500 km, within ±40° of where the 850 hPa wind comes from, reachable within 30 h; any fires within 25 km count whatever the wind does |
| `transport` | CAMS daily means against CAMS's own history for the month; the municipality's announcement | a suggestion to leave the car at home on a worse-than-usual day (info); the green ticket when it has been announced (watch) | worse than usual = daily PM2.5 or PM10 above the month's 75th percentile **and** at the WHO daily guideline (15 / 45 µg/m3), today or tomorrow |

The scientists' model is used as it is: nothing sits between the CAMS forecast and a dust alert. A smoke or dust episode raises the `pm` alert too, which then lists the cause, and the advice changes with the cause (`backend/plumes.py`): smoke gets a purifier, an FFP2 mask and "do not add smoke"; dust gets "close windows facing the wind, wet-clean"; a winter episode of heating and traffic in trapped air (heating season, stagnation score ≥ 35, CAMS PM2.5 or CO above the month's 90th percentile, no dust and no fires) gets "do not burn, leave the car, ventilate in the afternoon".

`GET /api/plumes` returns the same reading for display: daily PM2.5 / PM10 / dust peak next to the month's usual, the event type with its evidence, the hourly CAMS series, and the announced green tickets.

## The green ticket
Sofia Municipality issues a reduced-price all-day public-transport ticket (зелен билет) on bad-air days. It is a case-by-case decision, usually announced a day ahead, based on a national meteorological forecast of conditions that hold fine particles near the ground and on measured exceedances (BTA / BNT / Radio Bulgaria coverage of January 2025 and winter 2025/26); an older rule needed a 24 h PM10 mean of 200 µg/m3. **No feed or API was found** (and sofia.bg could not be reached from the build machine), so announcements are entered by hand and the app never infers one:

    python -m backend.green_ticket add 2026-01-09 --source https://... --note "free buffer parking too"
    python -m backend.green_ticket list | remove 2026-01-09

The price and terms change between announcements and are not stored; the note and link carry them. A scheduled job that reads the municipality's announcements would replace this once a stable source is known.

## What was checked, and what is not
* **Dust threshold precision**: over 955 days with a coarse-particle measurement (citizen sensors 2017-19 and 2024-26, station BG0050A 2026), days with CAMS dust of at least 20 µg/m3 (13 days) had coarse-particle excess (PM10 − PM2.5 at least 1.6 × its own 61-day median) on 7; at least 40 µg/m3 (3 days), on all 3. The base rate of such days is 15 %. Most coarse excess in Sofia is not desert dust, so recall is low and nothing here claims to catch it. This is evidence about timing, not about concentration.
* **Concentration was not validated**: the reference stations (EEA) only report hourly from January 2026, and that year had one day with CAMS dust ≥ 20 µg/m3 and two days at or above the 50 µg/m3 PM10 limit. The verified EEA daily files (`aggregationType: day`, which reach back years) were found late and not downloaded after the decision to use CAMS as published.
* **A Sofia-specific correction model was built and dropped.** Trained on the 2026 stations it cut the daily PM10 error by a third on ordinary days (4.98 vs 7.46 µg/m3) but could say nothing about strong dust, which the 2026 data never contained, so it added no information exactly where an alert matters. It is not in the code.
* **Dust thresholds** (20 / 40) come from the history of the field itself (daily maximum of CAMS dust, July 2017 to October 2026: at least 20 µg/m3 on 106 days, at least 40 on 26, at least 80 on 3, all in spring 2024).
* **A smoke rule from CAMS aerosol optical depth and carbon monoxide alone was tested and dropped**: it fired 69 times in 21 months, almost all ordinary haze. Smoke is therefore only called when fires are also detected; CO and PM2.5 are compared with the month, because in July they are rare and in January they are normal.
* **The smoke and local-pollution rules are not validated** against smoke measured in Sofia: the FIRMS archive needed to replay past fires requires a key.
* The EFFIS / GWIS web services returned server errors on 18 Oct 2026, so they are not used.
* **Not implemented**: CAMS GFAS fire emissions and CAMS vertical profiles (Copernicus Atmosphere Data Store account and key). They would replace the upwind rule with an actual transport forecast and show whether a plume is aloft or at street level. Today the surface value (lowest model level) is used and the column optical depth is shown next to it. Volcanic SO2 and other far-away events are not covered either.
* The web-push sender was only exercised with a fake sender.

## Notifications
* **Web Push** (works with the page closed): needs `pywebpush` and VAPID keys. `python -m backend.push --gen-keys`, paste the three lines into `.env`. iPhone: only after "Add to Home Screen" (iOS 16.4+).
* **Without keys** the opt-in still works: alerts appear as notifications while DishAI is open (service worker `public/sw.js`).
* A background task re-evaluates every 10 minutes and sends new (alert, level) pairs to subscriptions that asked for that kind and level.
* **Privacy**: a subscription stores the browser's push endpoint and keys (required to send) and the chosen kinds and minimum level. No location, district, name or e-mail. `data/push_subs.json` is git-ignored; unsubscribing deletes the row. Endpoints are accepted only from real push services (FCM, Mozilla, Apple, Windows), because the server makes a request to whatever endpoint it is given.
* There is no rate limit on subscribing yet beyond a 5,000-subscription cap and a once-a-minute limit on test messages; put the app behind a reverse proxy with rate limiting before a public launch.
