# Thryft Shop — native app (TestFlight / Play) runbook

The app is a web app; we ship it as a native app with **Capacitor**, which wraps
it in a native shell. v1 loads the live production site
(`https://listing-lfwjrg.fly.dev`) directly, so there are **no code changes** —
auth, API calls, camera, and CORS all work exactly like the mobile website.

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
  own and change `appId` in `frontend/capacitor.config.ts` to match.

---

## 1. Add Capacitor (run in `frontend/`)

```bash
cd frontend

# Capacitor tooling (dev-only — do NOT commit these into the deploy build's
# package.json on a branch that Fly builds with `npm ci`; install locally).
npm install --save-dev @capacitor/cli
npm install @capacitor/core @capacitor/ios @capacitor/android

# capacitor.config.ts is already in this folder. Build the web bundle once so
# `dist/` exists (Capacitor needs a webDir even when loading a remote URL):
npm run build

# Generate the native projects:
npx cap add ios
npx cap add android    # optional, only if you also want Android/Play
```

## 2. App icon + splash from the logo

```bash
# Uses the existing brand logo as the source.
npm install --save-dev @capacitor/assets
npx capacitor-assets generate --iconBackgroundColor '#ffffff' \
  --splashBackgroundColor '#ffffff' \
  --assetPath public/thryft-shop-logo-final.png
npx cap sync
```

## 3. iOS permission strings (required — the app uses the camera)

Open `frontend/ios/App/App/Info.plist` and add:

```xml
<key>NSCameraUsageDescription</key>
<string>Thryft Shop uses your camera to photograph items for your listings.</string>
<key>NSPhotoLibraryUsageDescription</key>
<string>Thryft Shop uses your photos to create listings.</string>
<key>NSPhotoLibraryAddUsageDescription</key>
<string>Thryft Shop saves processed photos you choose to export.</string>
```

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

Because v1 loads the live site, **web changes deploy the moment you merge** —
testers see them on next app launch with no new TestFlight build. You only need
a new native build when you change the icon, permissions, native plugins, or the
`server.url`.

## Path to an App Store *release* (later)

Apple's guideline 4.2 can reject apps that are a thin wrapper around a website.
Before public release we should:
- **Bundle the web assets** (remove `server.url` so it loads local `dist`), which
  requires:
  - Making the API base configurable — prefix requests with the production URL
    when running natively (a small change in `frontend/src/lib/api.js` +
    `mediaUrl` in `frontend/src/lib/utils`).
  - Adding **CORS** on the backend (`fastapi.middleware.cors`) allowing
    `capacitor://localhost` / `https://localhost` with `allow_credentials`, and
    switching auth to a token header **or** `SameSite=None; Secure` cookies.
- Add a couple of native touches (native share, haptics, push) so it's clearly
  more than a website.

Ping me when you want that and I'll implement it.
