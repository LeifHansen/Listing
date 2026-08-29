# TestFlight launch checklist (iOS)

The short, do-in-order version. Full explanations live in `MOBILE.md`.
Everything here runs **on your Mac**. The shell **bundles the web build** and
talks to the deployed API — it does not load the website in a webview (that
architecture is an App Store guideline 4.2 rejection). `ios-prepare.sh` does
the whole build; there is nothing to hand-configure.

## A. One-time setup

- [ ] **Apple Developer Program** membership active ($99/yr) — check at
      https://developer.apple.com/account
- [ ] **Xcode** installed and opened once (accept the license)
- [ ] **App ID registered**: developer.apple.com → Certificates, IDs & Profiles
      → Identifiers → `+` → App IDs → bundle id **`com.thryftshop.app`**
- [ ] **App Store Connect app record**: appstoreconnect.apple.com → My Apps →
      `+` → New App → platform iOS, name **Thryft Shop**, bundle id
      `com.thryftshop.app`, SKU anything (e.g. `thryft-001`)

## B. Fresh build on your Mac (repeat per native build)

Run in Terminal, one block at a time:

```bash
# main IS what production deploys from, so it is what testers should be
# testing. Building from a stale feature branch is the same mistake as a hand
# `fly deploy` from a behind checkout (see README) — except no health check
# ever catches it, because the build that ships to TestFlight is whatever was
# on the Mac. If you're deliberately testing a feature branch, check that out
# instead, and say so in the TestFlight "What to Test" notes.
cd ~/Listing && git checkout main && git pull
cd frontend
npm install
./scripts/ios-prepare.sh
```

- [ ] Script finished clean and printed all five Info.plist keys

`ios-prepare.sh` builds the web bundle (with the production API base baked
in), creates `ios/` if it's missing, runs `cap sync`, writes every required
plist key — including `NSMicrophoneUsageDescription` (without it, tapping
**Scan a shelf** kills the app) and `ITSAppUsesNonExemptEncryption`, which
stops App Store Connect asking the export-compliance question on every
upload — and writes the **privacy manifest** (`PrivacyInfo.xcprivacy`) App
Store Connect requires. It's idempotent; run it before every build and never
hand-edit `Info.plist`. If it prints a note that the privacy manifest was
just created, do the one-time "add file to App target" step it describes.

The icon and splash screens are regenerated as part of that script, so there
is nothing separate to run when the art changes — edit
`frontend/scripts/brand-icon.mjs` and re-run the prepare script.

## C. Xcode: sign, archive, upload

```bash
npx cap open ios
```

- [ ] App target → **Signing & Capabilities** → ✓ Automatically manage signing
      → pick your **Team**; bundle id shows `com.thryftshop.app`
- [ ] App target → **General** → Version `1.0`, Build `1` (bump Build on every upload)
- [ ] Destination: **Any iOS Device (arm64)** — not a simulator
- [ ] **Product → Archive** → Organizer opens → **Distribute App →
      App Store Connect → Upload** (defaults are fine)

## D. TestFlight

- [ ] App Store Connect → Thryft Shop → **TestFlight** tab → wait for the build
      to finish "Processing" (5–30 min; you'll get an email)
- [ ] Export compliance shouldn't be asked at all any more (the prepare script
      sets `ITSAppUsesNonExemptEncryption=false`). If it is: standard HTTPS →
      "standard encryption / exempt"
- [ ] **Internal Testing** → create a group → add yourself
      (leifhansen1990@gmail.com) — no Apple review needed, available instantly
- [ ] On your iPhone: install **TestFlight** from the App Store → accept the
      email invite → install **Thryft Shop**

## E. Smoke test on the phone

- [ ] App opens to the dashboard, no white flash of the wrong site
- [ ] Nothing hidden under the notch/status bar; bottom nav clear of the home bar
- [ ] Log in works and **stays** logged in after killing + reopening the app
- [ ] Camera opens from New Listing (photo capture) and Shop Mode scan
- [ ] **Scan a shelf** records video without the app dying (this is the
      microphone permission — if the app vanishes, `Info.plist` is missing
      `NSMicrophoneUsageDescription`; re-run `./scripts/ios-prepare.sh`)
- [ ] **AI consent dialog appears once** before the first photo upload —
      Agree proceeds; "Not now" cancels cleanly and re-asks next time
- [ ] Photo upload → AI identify → draft appears
- [ ] **Connect eBay round-trips back into the app**: tap Connect → eBay's
      sign-in loads in the webview (allowNavigation already covers it) →
      after approving, the "Returning to Thryft Shop…" page lands you back in
      the app with the "eBay connected!" toast. If the auto-return stalls,
      the page's **Return to Thryft Shop** button must work — report if only
      the button path works.
- [ ] **Buy tokens opens the SYSTEM browser (Safari), not in-app** — this is
      an App Store compliance requirement, not a preference. Complete a test
      purchase; on returning to the app the balance updates by itself
      (requires `STRIPE_WEBHOOK_SECRET` set on the server).
- [ ] **View on eBay** (after publishing) actually opens — `window.open` is
      silently a no-op in some webview configs
- [ ] Settings → **Delete account** shows the confirm dialog, and the privacy
      policy / terms links open
- [ ] Airplane mode: launching shows *something* — not an endless spinner
- [ ] Publish a test listing / end it — full loop

## Iterating

The app now **bundles** the web build (see MOBILE.md), so web changes need a
new build to reach the app: re-run `./scripts/ios-prepare.sh` + archive for
any frontend change you want in the shell. Backend/API changes still land on
deploy with no new build.
