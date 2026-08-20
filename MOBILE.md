# Thryft Shop — native app (TestFlight / Play) runbook

The app is a web app; we ship it as a native app with **Capacitor**. The shell
**bundles the web build locally** and talks to the production API
(`https://listing-lfwjrg.fly.dev`) cross-origin — it does NOT load the remote
site in a webview. That architecture is deliberate: Apple's guideline 4.2
rejects "repackaged website" apps, and Capacitor documents `server.url` as a
development-only feature. The pieces that make the bundled shell work are
already in the codebase:

- `scripts/ios-prepare.sh` builds the bundle with `VITE_API_BASE` baked in
  (the web deploy builds without it and keeps relative URLs).
- Auth uses a **Bearer token** in the shell (cookies never cross origins);
  the backend allows the `capacitor://localhost` origin via CORS.
- OAuth connects (eBay/Etsy) mint a 60-second ticket for the navigation and
  finish on an interstitial that steers the webview back into the app.
- Token purchases open **in the system browser** (App Store guideline 3.1.1
  forbids completing a non-Apple checkout inside the app; a link out is
  allowed on the US storefront). The webhook credits the purchase, so set
  `STRIPE_WEBHOOK_SECRET` in production — for native buyers it is the
  delivery path, not a safety net.

Everything up to `npx cap add ios` can run anywhere; **the iOS build, signing,
and TestFlight upload must run on a Mac with Xcode.**

---

## 0. Prerequisites (one-time)

- macOS with **Xcode** (from the App Store) + Command Line Tools:
  `xcode-select --install`
- **CocoaPods**: `sudo gem install cocoapods` (or `brew install cocoapods`)
- **Node 18+** (`node -v`)
- Your **Apple Developer account** ($99/yr, which you have)
- Register an **App ID** in the Apple Developer portal (Certificates, IDs &
  Profiles → Identifiers) with bundle id **`com.thryftshop.app`** — or pick your
  own and change `appId` in `frontend/capacitor.config.json` to match.

---

## 1. Add Capacitor (run in `frontend/`)

```bash
cd frontend

# Capacitor tooling (dev-only — do NOT commit these into the deploy build's
# package.json on a branch that Fly builds with `npm ci`; install locally).
npm install --save-dev @capacitor/cli
npm install @capacitor/core @capacitor/ios @capacitor/android
# Browser plugin: token purchases must open in the SYSTEM browser (App Store
# guideline 3.1.1) — the app detects and uses this plugin when present.
npm install @capacitor/browser

# capacitor.config.json is already in this folder (deliberately JSON, not TS:
# the CLI's TypeScript config loader breaks on TypeScript 6 with
# "Cannot read properties of undefined (reading 'CommonJS')").
# Build the web bundle once so `dist/` exists (Capacitor needs a webDir even
# when loading a remote URL):
npm run build

# Generate the native projects:
npx cap add ios
npx cap add android    # optional, only if you also want Android/Play
```

## 2. App icon + splash — handled by the prepare script

**`./scripts/ios-prepare.sh` does this for you** (step 3 below); this section
is only for running it by hand or changing the art.

`frontend/assets/` holds the three sources Capacitor consumes — `icon.png`
(1024²) and `splash.png` / `splash-dark.png` (2732²). They are **generated**,
not hand-drawn: `npm run brand:assets` redraws them from the vector art in
`scripts/brand-icon.mjs`, so edit that script rather than the PNGs.

The icon is the logo's price-tag character, not the wordmark — "Thryft Shop"
is unreadable at the ~60pt a home screen actually renders.

```bash
npm run brand:assets            # redraw the three sources
npx --yes @capacitor/assets generate --ios \
  --assetPath assets \
  --iconBackgroundColor '#101a2e' \
  --splashBackgroundColor '#f8f3e7' \
  --splashBackgroundColorDark '#101a2e'
npx cap sync
```

Point `--assetPath` at that FOLDER, never at a single image file (that's the
"No assets found in the asset path" error). The background colours are the
brand canvases from `tokens.css` — they back Android's adaptive icon and the
generated iOS splash storyboard, so white would flash against a navy app on
every cold start.

Note that `cap sync` alone never generates icons; it copies web assets and
plugins only. Skipping the generate step ships Capacitor's placeholder icon.

## 3. iOS permission strings — handled by the prepare script

**Don't hand-edit `Info.plist`.** `frontend/ios/` is generated and gitignored,
so anything typed into Xcode is lost on the next regeneration — and a missing
permission string doesn't warn, it **terminates the app** the moment the
feature is tapped. Run this instead (it does steps 1–3 in one go: build,
`cap add`/`cap sync`, and every plist key):

```bash
cd frontend && ./scripts/ios-prepare.sh
```

It writes:

| Key | Why |
|---|---|
| `NSCameraUsageDescription` | photographing items |
| `NSPhotoLibraryUsageDescription` | picking existing photos |
| `NSPhotoLibraryAddUsageDescription` | saving processed photos |
| `NSMicrophoneUsageDescription` | **"Scan a shelf" records video, and iOS routes any video capture through the mic — without this the app is killed on tap** |
| `ITSAppUsesNonExemptEncryption=false` | permanently answers the export-compliance question asked on every upload |

## 4. Open in Xcode, sign, and archive

```bash
npx cap open ios
```

In Xcode:
1. Select the **App** target → **Signing & Capabilities** → check *Automatically
   manage signing* → pick your **Team** (your developer account). Confirm the
   **Bundle Identifier** is `com.thryftshop.app`.
2. Set a **Version** (e.g. `1.0`) and **Build** (e.g. `1`).
3. Choose destination **Any iOS Device (arm64)** (not a simulator).
4. **Product → Archive**. When it finishes, the Organizer opens.
5. **Distribute App → App Store Connect → Upload**.

## 5. TestFlight

1. In **App Store Connect** → your app → **TestFlight**, wait for the build to
   finish processing (a few minutes).
2. **Internal testing**: add yourself/your team (up to 100) — **no review
   needed**, available immediately. This is the fast path to testing on device.
3. **External testing** (up to 10,000 by email/link) requires a one-time Beta
   App Review (usually < 24h).
4. Testers install the **TestFlight** app and accept the invite → the Thryft
   Shop app installs like any App Store app.

## Android / Google Play (optional)

```bash
npx cap open android      # opens Android Studio
```
Build a signed **AAB** (Build → Generate Signed Bundle), then upload it to the
**Play Console** → Internal testing track. (Google Play Console is a separate
$25 one-time account.)

---

## Iterating

The shell **bundles** the web build (there is no `server.url` — see
`frontend/capacitor.config.json`), so **a web change needs a new native build**
to reach testers: rebuild the frontend, re-run `./scripts/ios-prepare.sh`, and
push a new TestFlight build. Backend changes still deploy on merge and reach
the app immediately, since the bundled UI talks to the deployed API.

A new native build is likewise required for the icon, permissions, or native
plugins.

## App Store readiness

Guideline 4.2 (a thin wrapper around a website) is already addressed — all of
the following shipped:
- **The web assets are bundled** (no `server.url`; the shell loads local
  `dist`).
- **The API base is configurable** — `frontend/src/lib/platform.js` supplies it
  and `apiUrl()` / `mediaUrl()` prefix every request when running natively.
- **CORS** allows `capacitor://localhost` / `https://localhost`
  (`backend/main.py`), and auth rides a bearer token rather than a cookie
  (`frontend/src/lib/api.js`), since same-origin cookies never travel in the
  shell.

Still optional polish for a public release: native share, haptics, and push.
