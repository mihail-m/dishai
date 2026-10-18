# Alerts and notifications

One list of what needs attention in the next 48 hours (`GET /api/alerts`, `backend/alerts.py`). Each alert has a kind, level (info / watch / warning), when, peak, likely cause, source, confidence and 1-3 suggested actions. The panel banner, the suggestions list and the For me plan read the same objects; a push is sent once per (alert id, level).

| Kind | Source | What it can claim | Thresholds |
|---|---|---|---|
| `pm` | DishAI PM2.5 model (CAMS + citizen sensors), back-tested in [PM_MODEL.md](PM_MODEL.md) | a pollution episode is likely (probability shown) | existing banner: red from P 0.7, amber from 0.2 |
| `heat` | Open-Meteo feels-like forecast | a hot stretch of at least 2 h | peak 27 / 32 / 38 °C = info / watch / warning (the advice bands) |
| `dust` | Copernicus CAMS mineral dust, via Open-Meteo, keyless | desert dust forecast over the city | at least 2 h at 20 µg/m3 (watch) / 40 (warning) |
| `fire` | NASA FIRMS VIIRS active fires + Open-Meteo 850 hPa wind, needs a free `FIRMS_MAP_KEY` | fires lie upwind and smoke **may** arrive; says nothing about how much | 10 detections or 200 MW of radiative power within 500 km, within +-40 deg of where the 850 hPa wind comes from, reachable within 30 h |

CAMS already carries fire and dust emissions, so a smoke or dust episode raises the `pm` alert too; the other kinds name the cause (a `pm` alert overlapping a dust or fire alert lists it under "likely contributing").

## What was tested, and what is not validated
* **Dust thresholds** come from the history of the field itself (Jan 2025 - Oct 2026: the CAMS dust value reached 20 µg/m3 on 12 days, 40 on 3, and never 50). A comparison with the mean PM10 of Sofia's EEA stations had only 5 days between 15 and 30 µg/m3 in 2026 (PM10 about 50 % above typical), too few to validate.
* **A smoke rule from CAMS aerosol optical depth and carbon monoxide was tested and dropped**: it fired 69 times in 21 months, almost all ordinary haze, and the CO ratio did not separate events. Without a fire feed there is no honest keyless smoke alert.
* The **EFFIS / GWIS web services returned server errors** (`msLoadMap(): Unable to access file`) when checked on 18 Oct 2026, so they are not used.
* **Not implemented**: CAMS GFAS fire emissions (ADS account and key, daily 0.1 deg grid) and CAMS smoke-plume forecasts; both would replace the upwind rule with an actual transport forecast.
* The FIRMS client and the web-push sender were only exercised with recorded responses and a fake sender (no key was available).

## Notifications
* **Web Push** (works with the page closed): needs `pywebpush` and VAPID keys. `python -m backend.push --gen-keys`, paste the three lines into `.env`. iPhone: only after "Add to Home Screen" (iOS 16.4+).
* **Without keys** the opt-in still works: alerts appear as notifications while DishAI is open (service worker `public/sw.js`).
* A background task re-evaluates every 10 minutes and sends new (alert, level) pairs to subscriptions that asked for that kind and level.
* **Privacy**: a subscription stores the browser's push endpoint and keys (required to send) and the chosen kinds and minimum level. No location, district, name or e-mail. `data/push_subs.json` is git-ignored; unsubscribing deletes the row. Endpoints are accepted only from real push services (FCM, Mozilla, Apple, Windows), because the server makes a request to whatever endpoint it is given.
* There is no rate limit on subscribing yet beyond a 5,000-subscription cap and a once-a-minute limit on test messages; put the app behind a reverse proxy with rate limiting before a public launch.
