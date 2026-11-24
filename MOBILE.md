# Thryft Shop — the iOS and Android apps

The app is a web app shipped as native apps with **Capacitor 8**. The shell
**bundles the web build** and talks to the production API
(`https://listing-lfwjrg.fly.dev`) cross-origin; it does not load the website
in a webview. That is deliberate: Apple's guideline 4.2 rejects "repackaged
website" apps, and Capacitor documents `server.url` as development-only.

What makes the bundled shell work, all already in the code:

- **The API base is baked in.** `npm run native:*` builds with `VITE_API_BASE`
  set; the web deploy builds without it and keeps relative URLs.
  `frontend/src/lib/platform.js` prefixes every request when running natively.
- **Auth is a bearer token** kept in the Keychain (iOS) / Keystore (Android)
  through `capacitor-secure-storage-plugin`, because cookies never cross
  origins. The backend's CORS allows `capacitor://localhost` (iOS) and
  `https://localhost` (Android).
- **Marketplace connects** (OAuth) mint a 60-second ticket for the navigation
  and finish on an interstitial that steers the webview back into the app.
  Which marketplaces are offered is the server's `MARKETPLACES_ENABLED`
  (production: eBay alone); a marketplace switched on there needs no app work.
- **Links out** open in the in-app browser (Safari view / Custom Tab), not in
  the app's own webview. On Android a link to an allowed host such as
  `ebay.com` would otherwise replace the app.
- **Token purchases** open in the **system browser**, never inside the app, and
  only where the store allows it — see [Purchases](#purchases-and-store-rules).

Store listing copy, privacy answers and review notes live in
[`docs/APP_STORE.md`](docs/APP_STORE.md). This file is how to build and ship.

---

## The build script

One command per platform, run from `frontend/`:

```bash
npm run native:ios
```

```bash
npm run native:android
```

`frontend/ios` and `frontend/android` are **generated and gitignored**, so
nothing is ever configured by hand in Xcode or Android Studio — it would be
lost on the next regeneration. The script (`frontend/scripts/native-prepare.mjs`)
does everything, on every run, idempotently:

1. builds the web bundle with the API base and the commit sha baked in;
2. `npx cap add` (first run) and `npx cap sync`;
3. generates every app icon and splash size from `frontend/assets/`;
4. **iOS:** writes the permission strings and the export-compliance answer
   into `Info.plist`; writes the privacy manifest (`PrivacyInfo.xcprivacy`)
   and adds it to the App target; sets the version, build number, device
   family (iPhone only, by default) and, if given, the signing team;
5. **Android:** sets `versionName` / `versionCode`, and with `--bundle` builds
   the release App Bundle (signed when a key is configured).

| `Info.plist` key | Why |
|---|---|
| `NSCameraUsageDescription` | photographing items |
| `NSPhotoLibraryUsageDescription` | picking existing photos |
| `NSPhotoLibraryAddUsageDescription` | the export sheet's "Save Image" |
| `NSMicrophoneUsageDescription` | **Scan a shelf and listing videos record video, and iOS routes any video capture through the mic — without this the app is killed on tap** |
| `ITSAppUsesNonExemptEncryption = NO` | answers the export-compliance question asked on every upload |

Android needs **no** runtime permissions: Capacitor only asks for the camera
when the manifest declares it, and without that it hands photo and video
capture to the system camera app. Photo picking uses the system picker.

### Settings

All optional environment variables:

| Variable | Default | |
|---|---|---|
| `THRYFT_APPLE_TEAM_ID` | — | 10-character team id; with it Xcode needs no clicks before Archive |
| `THRYFT_IOS_PROFILE` | — | an App Store profile's name: the Release build is signed manually with it (set by the TestFlight workflow) |
| `THRYFT_IOS_DEVICES` | `iphone` | `universal` adds iPad (then App Store Connect also wants iPad screenshots) |
| `THRYFT_APP_VERSION` | `version` in `frontend/package.json` | the store version |
| `THRYFT_BUILD_NUMBER` | minutes since the epoch | must rise with every upload |
| `THRYFT_API_BASE` | `https://listing-lfwjrg.fly.dev` | a staging server |
| `THRYFT_ANDROID_KEYSTORE` (+ `_KEYSTORE_PASSWORD`, `_KEY_ALIAS`, `_KEY_PASSWORD`) | — | signs the `--bundle` build |

**Versions.** Both stores take one version string per release (`1.0.0`) and a
build number that must be higher than every earlier upload. The version is
`frontend/package.json`'s `version` — bump it for each store release
(`npm version minor --no-git-tag-version` in `frontend/`). The build number is
the time of the build in minutes, which only goes up whichever machine or
branch builds it (a commit count does not: a shallow clone counts fewer).

**Art.** `frontend/assets/icon.png`, `splash.png` and `splash-dark.png` are
drawn from vector art by `npm run brand:assets`
(`frontend/scripts/brand-icon.mjs`). Edit that script, run it, and commit the
PNGs; the build script only fans them out into sizes.

**CI.** `.github/workflows/native.yml` runs both builds whenever an input to
them changes (the Capacitor packages, `capacitor.config.json`, the art, the
build script) and on demand from the Actions tab: a compiled iOS simulator
build, and an Android App Bundle uploaded as a workflow artifact.
`.github/workflows/testflight.yml` makes the signed iOS build and uploads it
to TestFlight, on demand ([From GitHub Actions](#from-github-actions)).

---

## iOS

Two ways to make the App Store build, from the same build script:

- **[From GitHub Actions](#from-github-actions)**, with no Mac at all: the
  **iOS → TestFlight** workflow archives a signed build and uploads it.
- **[On a Mac](#on-a-mac)** with Xcode (the newest release — App Store
  Connect only takes uploads built with a current SDK). CocoaPods is no
  longer needed: Capacitor 8 uses Swift Package Manager.

### One-time setup

- [ ] **Apple Developer Program** membership active ($99/yr) —
      <https://developer.apple.com/account>
- [ ] **App ID**: Certificates, IDs & Profiles → Identifiers → `+` → App IDs →
      bundle id **`com.thryftshop.app`** (the `appId` in
      `frontend/capacitor.config.json`)
- [ ] **App Store Connect record**: My Apps → `+` → New App → iOS, name
      **Thryft Shop**, bundle id `com.thryftshop.app`, SKU e.g. `thryft-001`

Build from `main` either way: it is what production runs, so it is what
testers should test. (If you deliberately ship a feature branch, say so in
TestFlight's "What to Test".)

### From GitHub Actions

One-time, besides the setup above:

- [ ] **An App Store Connect API key.** App Store Connect → Users and Access →
      Integrations → App Store Connect API → **Team Keys** → `+`. Name it
      `GitHub Actions`, access **Admin**: the workflow creates the signing
      certificate and profile, which no lesser role may. Download the
      `AuthKey_<KEY ID>.p8` file (Apple offers it only once), and note the
      **Key ID** beside it and the **Issuer ID** above the list.
- [ ] **A distribution key**: the private key the App Store certificate is
      issued for. It is made once and kept; the workflow asks Apple for the
      certificate itself.

      ```bash
      openssl genrsa -out ios-distribution.pem 2048
      ```

      Keep it in the password manager beside the Android upload key.
- [ ] **Four repository secrets** (GitHub → Settings → Secrets and variables →
      Actions → New repository secret), each pasted as it is:

      | Secret | Value |
      |---|---|
      | `APP_STORE_CONNECT_KEY_ID` | the Key ID (10 characters) |
      | `APP_STORE_CONNECT_ISSUER_ID` | the Issuer ID (a UUID) |
      | `APP_STORE_CONNECT_KEY` | the whole `AuthKey_<KEY ID>.p8` file |
      | `IOS_DISTRIBUTION_KEY` | the whole `ios-distribution.pem` file |

**Each build:** GitHub → **Actions → iOS → TestFlight → Run workflow** on
`main`. The run's summary links to the build in TestFlight; carry on at
[TestFlight](#testflight).

The first run also makes the signing assets, through the API: an **Apple
Distribution certificate** for the distribution key (Apple emails the account
holder that one was made) and an App Store profile named **Thryft Shop App
Store CI**. Later runs find both again. When the certificate expires, a year
on, the next run asks Apple for a new one for the same key; nobody needs to do
anything. If either is revoked or deleted in the developer portal, the next
run replaces it.

When a run fails, its error says what to fix: a missing secret, a key Apple
refuses (an ID pasted into the wrong secret, an Individual key rather than a
Team key, or a role below Admin), or no App Store Connect record yet. The
`build-logs` artifact has Xcode's full logs. A run that built but could not
upload keeps the `.ipa` as the `ios-app-store-ipa` artifact, which Apple's
**Transporter** app (Mac App Store) can upload instead.

### On a Mac

- [ ] Node 22 (`node -v`) and Xcode opened once (licence accepted)
- [ ] **An `ios/` folder from before Capacitor 8** (it has a `Podfile`) can't be
      upgraded in place. It is generated — delete `frontend/ios` and the script
      makes a fresh one. The script stops and says so if it finds one.

```bash
cd ~/Listing && git checkout main && git pull
```

```bash
cd frontend && npm ci && THRYFT_APPLE_TEAM_ID=XXXXXXXXXX npm run native:ios
```

```bash
npx cap open ios
```

In Xcode: destination **Any iOS Device (arm64)** → **Product → Archive** →
Organizer → **Distribute App → App Store Connect → Upload**. (Without
`THRYFT_APPLE_TEAM_ID`, first pick your Team under the App target's
Signing & Capabilities.)

### TestFlight

1. App Store Connect → Thryft Shop → **TestFlight**: wait for "Processing" to
   finish (5–30 min; there's an email).
2. **Internal testing**: add your team (up to 100). No review; available at once.
3. **External testing** (up to 10,000 by email or public link) needs a
   one-time Beta App Review, usually under a day.
4. Testers install **TestFlight** and accept the invite.

Export compliance shouldn't be asked (the plist answers it). If it is:
standard HTTPS only → exempt.

---

## Android

Builds anywhere with **JDK 21** and the **Android SDK** — or nowhere at all:
the **Native builds** workflow produces the signed bundle.

### One-time setup

- [ ] **Google Play Console** account ($25 once). A **personal** account made
      after November 2023 must run a **closed test with at least 12 testers
      for 14 days** before it can publish to production; an organization
      account (needs a D-U-N-S number) is exempt. Start that clock early.
- [ ] **Create the app**: Play Console → Create app → name **Thryft Shop**,
      default language English (US), App, Free. The package name is fixed by
      the first upload: **`com.thryftshop.app`**.
- [ ] **Play App Signing** stays on (the default). Google holds the key that
      signs what users install; you sign uploads with an **upload key**:

      ```bash
      keytool -genkeypair -v -keystore thryft-upload.jks -alias upload \
        -keyalg RSA -keysize 2048 -validity 10000
      ```

      Keep the file and its password in the password manager. Losing it is
      recoverable (Play Console can reset an upload key), but slowly.
- [ ] **For CI signing**, add four repository secrets (Settings → Secrets and
      variables → Actions):
      `ANDROID_UPLOAD_KEYSTORE_BASE64` (`base64 -i thryft-upload.jks`),
      `ANDROID_UPLOAD_KEYSTORE_PASSWORD`, `ANDROID_UPLOAD_KEY_ALIAS` (`upload`)
      and `ANDROID_UPLOAD_KEY_PASSWORD`.

### Each build

**From CI (no local setup):** GitHub → Actions → **Native builds** → Run
workflow on `main` → download the `android-app-bundle` artifact
(`app-release.aab`).

**Or locally**, with JDK 21 and the Android SDK (`ANDROID_HOME`) installed:

```bash
cd frontend && npm ci && THRYFT_ANDROID_KEYSTORE=~/keys/thryft-upload.jks THRYFT_ANDROID_KEYSTORE_PASSWORD=... npm run native:android -- --bundle
```

The bundle is `frontend/android/app/build/outputs/bundle/release/app-release.aab`.
(`npx cap open android` opens the project in Android Studio if you prefer
Build → Generate Signed App Bundle.)

### Upload

Play Console → Thryft Shop → **Testing → Internal testing** → Create release
→ upload the `.aab` → Save → Review → roll out. Internal testers (up to 100)
get it within minutes through a Play Store opt-in link. Promote the same
release to **Closed testing** for the 12-tester requirement, then to
**Production** once Play Console allows it.

---

## The app's own plugin: background removal on the device

`frontend/native/thryft-cutout` is a Capacitor plugin that lives in this repo
and is installed as a `file:` dependency, so `npx cap sync` registers it like
any other plugin and nothing in the generated `ios/` or `android/` is edited
by hand.

- **iOS:** Apple's Vision framework, `VNGenerateForegroundInstanceMaskRequest`
  (iOS 17+; the app's floor stays iOS 15 and older phones report
  themselves unsupported). It does not run in the **Simulator** — test on a
  device.
- **Android:** Google ML Kit subject segmentation (`16.0.0-beta1`, still
  published as a beta). The model is delivered by **Google Play services**:
  the plugin's manifest asks for it at install, and the app downloads it on
  demand if it is missing. A phone without Play services falls back.

Both answer only a soft greyscale mask of the photo; the server composites it
exactly as it composites Photoroom's cutouts. Which platforms may use their
engine is the server's `BG_LOCAL_PROVIDERS`, read on every app start — so an
engine is switched off **without an app release** if it misbehaves. The web
build's own engine (BiRefNet in the browser) is compiled out of the apps
(`VITE_NATIVE=1`, set by the build script).

The privacy manifest needs nothing new: the plugin calls Vision, ImageIO and
CoreVideo only, none of them "required reason" APIs.

## Smoke test on a device

Run on both platforms before promoting a build.

- [ ] Opens to the dashboard; no flash of the website, no endless spinner
- [ ] Nothing under the notch / status bar; bottom nav clear of the home bar
      or gesture bar (Android draws edge to edge)
- [ ] Log in works and **stays** logged in after killing and reopening
- [ ] Camera opens from New Listing and Shop Mode; **Scan a shelf** records
      video without the app dying (iOS: that is the microphone string)
- [ ] The **AI consent dialog** appears once before the first upload; Agree
      proceeds, "Not now" cancels cleanly and asks again next time
- [ ] Photo upload → AI identify → draft appears
- [ ] **Connect eBay round-trips**: eBay's sign-in loads, and after approving,
      "Returning to Thryft Shop…" lands back in the app with "eBay connected!"
      (if the auto-return stalls, its **Return to Thryft Shop** button works)
- [ ] **View on eBay** and the Seller Hub links open in the in-app browser and
      closing it returns to the app — the app itself never turns into eBay
- [ ] Publish a test listing and end it — the full loop
- [ ] Settings → **Delete account** shows the confirm dialog; the privacy
      policy and terms links open
- [ ] **Android back button**: closes an open dialog first, then returns to
      the dashboard, then puts the app in the background — never quits from
      the middle of a screen
- [ ] Airplane mode: launching shows *something*, not an endless spinner
- [ ] **Background removal on the device** (with `BG_LOCAL_PROVIDERS`
      including the platform): Settings → Background removal says "Ready"
      (Android may first say it is downloading — Play services fetches ML
      Kit's model). Upload two photos with **Remove background** ticked and
      cloud removal **off**: the banner counts them, each tile turns to the
      cutout on white, and Settings' Photoroom count does not move. A pair
      of shoes keeps both shoes. Kill the app mid-pile and reopen: the rest
      carry on. In the studio, **Remove BG**, then **Colour**, **PNG** and
      **Save**; reopen and **Remove BG** again — it cuts from the shot, not
      the white composite.
- [ ] Background removal on a device that can't (an iPhone on iOS 16, an
      Android phone without Play services): Settings says why, and with cloud
      removal on, photos go to Photoroom instead
- [ ] If billing is on (`TOKENS_ENABLED`): on iOS, **Buy tokens** opens Safari
      (not in-app) and the balance updates on return (needs
      `STRIPE_WEBHOOK_SECRET`); on Android there is no buy button at all

## Iterating

The shell bundles the web build, so **a frontend change reaches the apps
only with a new native build** (and a new store review for production).
Backend changes deploy on merge and reach the apps at once, since the bundled
UI talks to the deployed API — which also means an API change must keep
working for the app builds already installed.

## Purchases and store rules

Tokens are digital goods consumed in the app, which both stores normally
require to be sold through their own billing. The app sells them through
Stripe on the web instead, which is allowed only in narrow cases:

- **App Store, United States storefront:** since May 2025 apps may link out
  to an external purchase with no entitlement. Not elsewhere — so set the
  App Store availability to **the United States only** while billing is on.
  The checkout opens in Safari (`openExternal` in `platform.js`), never in
  the app.
- **Google Play:** linking out (even in the US) requires enrolling in Play's
  external-content-links program, with its fees and reporting. Until that is
  a decision, the **Android build has no purchase button** and doesn't say
  where tokens are sold; buyers top up on the web. Enrolling later is a
  build-time switch (`VITE_ANDROID_EXTERNAL_PURCHASES=1`), not a code change.

Billing is a server flag (`TOKENS_ENABLED`), which is why these guards live
in the app binary: switching billing on must not change what an approved
Android build shows. With billing off, as today, no purchase UI exists on
any platform.
