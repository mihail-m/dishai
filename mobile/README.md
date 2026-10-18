# AirPath Mobile

A separate React + TypeScript + Capacitor demo app in the DishAI repository. The existing `frontend/` and backend remain independent. The translucent surfaces are a custom web design inspired by glass materials, not Apple's native Liquid Glass API.

## Open the demo

From the repository root, double-click **Start Mobile.cmd** on Windows or **Start Mobile.command** on macOS. The launcher opens `http://127.0.0.1:5180` after Vite is ready. Keep its terminal window open while presenting; press Ctrl+C to stop it.

The first launch needs internet to prepare dependencies. The launcher reuses a compatible Node.js installation or downloads a checksummed Node.js 22 runtime inside the existing `.local/` folder. It does not install global packages. Following a Windows-to-Mac copy, if Finder reports that the command is not executable, run `chmod +x "Start Mobile.command"` once from the repository root.

If macOS opens the file in VS Code, right-click **Start Mobile.command** and choose **Open With → Terminal**. To run it regardless of the file association or executable permission, open Terminal, type `bash ` (including the space), drag **Start Mobile.command** into the Terminal window, and press Return. Use the `.command` file on macOS; `.cmd` is the Windows launcher.

The browser presentation is an interactive phone preview, not an Android Emulator. Sample forecast and route data are available without the backend. The source selector explicitly distinguishes **Demo** from **Connected** mode. Failed live requests show an error with retry and an option to return to the demo; they never silently relabel sample data as live.

If a double-click does not open the browser, use `http://127.0.0.1:5180` directly. Windows startup writes diagnostics to `.local/mobile-launch.log`. The launcher waits for the browser-opening command and tries an installed Chrome or Edge if the default HTTP association fails. Startup errors keep the launcher window open.

### Present the concept

Start with **Today**, move the 24-hour slider, and open the air-quality explanation. Compare **Fastest**, **Balanced**, and **Cleanest**, then open a suggested ventilation window or outdoor break and choose **Save to my day**. Saved plans persist locally in this browser or emulator. The app settings also contain **Take the demo tour**, which walks through a repeatable presentation sequence. The forecast refresh button fetches a complete new 24-hour outlook; the slider uses the data already loaded.

### Change the route

Tap either address in **Routes** to search local streets and landmarks in English or Bulgarian, or choose **Choose on map** and tap a street. Reverse swaps both endpoints; **Reset trip** restores the longer Prince’s Garden → Pirotska Street demo (about 2.1–2.4 km). The three alternatives follow actual bundled OpenStreetMap streets. Tap a route card or path to select it, and **Expand map** to see more of the journey.

The **Leave at** wheel stays above the map, including in the expanded view. Swipe through all 24 departure hours or use its arrows; **Lowest** selects the hour with the lowest estimated route PM2.5. The refresh button loads a new complete forecast. Hour changes use the preloaded forecast and routes without requesting new data.

Demo routing works offline within central Sofia (23.313–23.334 longitude, 42.688–42.702 latitude). Selected points snap to nearby connected paths; unsupported points show a clear message. The comparison uses illustrative street-class exposure factors, not measured pollution. Short or constrained trips may share the same roads. Each new trip preloads its 24 forecast hours before enabling the departure wheel.

**Streets** and **Sensors** are independent map toggles, both off by default. Street colors follow the selected departure hour. Sensor markers show fixed observations with their time and PM2.5 reading; future hours do not turn observations into predictions. Demo colors and sensors are labelled illustrative. In Connected mode, street scores come from the same forecast snapshot as the routes; the street geometry loads once when first enabled. The AirPath street score combines air quality and heat, with greener meaning a higher score; it is not an official air-quality index. **About map layers** explains the legend and data source.

Connected mode sends the selected endpoints to the existing `/api/route` service and displays its fastest, balanced, and cleanest results with their own confidence information. It uses OpenStreetMap tiles for wider map coverage. The existing web app and backend API are unchanged.

For optional street-number address lookup, configure a Nominatim-compatible **search endpoint** in `VITE_GEOCODER_URL`. No public geocoder is enabled by default. Pick a service whose terms cover the demo; public [Nominatim usage rules](https://operations.osmfoundation.org/policies/nominatim/) apply if you deliberately choose that service. Search is explicit-submit only, scoped to Sofia, cached, and requests are spaced by at least 1.1 seconds within the app. Multiple deployed clients require a shared rate limit at your service. A runtime `localStorage['airpath-geocoder-url']` override allows changing providers without rebuilding; credentials must stay on a server, not in the mobile build.

## Develop

With Node.js 22.12+ and npm installed:

```sh
cd mobile
npm ci
npm run dev
npm run build
npm test
npm run test:e2e
```

The development server binds only to `127.0.0.1:5180`. It proxies `/api` to the existing backend on `127.0.0.1:8000`. Start that backend using the existing **Start DishAI** launcher if you want live data. No backend is required for the bundled demo. Mobile browser tests use port 5181 so they do not collide with the existing web app's tests.

Playwright needs a Chromium browser installed for the current user (`npx playwright install chromium` if it is not already available).

## Run the Android app in an emulator

The generated `android/` project is included. Install [Android Studio 2025.2.1 or newer](https://capacitorjs.com/docs/getting-started/environment-setup), its Android SDK Platform 36, build tools, and Android Emulator. Android Studio supplies its own Java runtime. In Device Manager, create a Pixel virtual device using an Android 16 / API 36 system image.

From `mobile/`:

```sh
npm ci
npm run android:sync
npm run android:open
```

In Android Studio, let Gradle finish syncing, choose the virtual device, then press **Run**. The app opens as **AirPath** and loads the web assets bundled with the APK. A physical phone and app-store account are not required. After changing the app, run `npm run android:sync` again before running it in Android Studio. With Android SDK tools available on your PATH, `npm run android:run` can build and launch a selected emulator from the terminal.

Use the Android Emulator's Extended Controls > Record and Playback for demo recordings. The normal browser preview is useful for rapid design iteration; use the emulator to verify the final Android presentation.

Android Studio, an Android SDK, and Java were not found on the initial Windows development machine. Android project generation and web-to-native sync can be checked without them, but building an APK and running the emulator require that setup. No Android APK build or device run is implied by a successful web build.

### Optional live backend in the emulator

The Android Emulator reaches the laptop through `10.0.2.2`, not the emulator's `localhost`. Copy `.env.example` to `.env.local` in `mobile/`, uncomment this line, and build/sync again:

```dotenv
VITE_API_BASE_URL=http://10.0.2.2:8000
```

Keep the existing backend running on port 8000. This explicit setting also enables the WebView's mixed-content support. The Android **debug** manifest permits HTTP only to `10.0.2.2`; the normal release manifest does not opt in to cleartext traffic. Remove the setting before producing a release or use an HTTPS backend. Environment variables prefixed `VITE_` are public build-time configuration; never put secret API keys in them.

## Project structure

- `src/`: independent mobile UI, map, demo data, and API adapter.
- `scripts/`: cross-platform preview startup support.
- `android/`: Capacitor Android project, app icon, and launch theme.
- `capacitor.config.ts`: app identity and native web bundle configuration.

This first target is Android plus a desktop browser preview. An iOS native target is not generated; it can be added later on a Mac with Xcode if needed.
