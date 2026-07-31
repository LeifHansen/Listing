# TestFlight launch checklist (iOS)

The short, do-in-order version. Full explanations live in `MOBILE.md`.
Everything here runs **on your Mac**; the web side is already deployed and the
app shell loads the live site, so no code changes are needed for v1.

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
cd ~/Listing && git checkout claude/ebay-listing-generator-se7lao && git pull
cd frontend
npm install
npm run build
npx cap sync ios
```

- [ ] Repo pulled on the deploy branch, `npm run build` + `npx cap sync ios` clean

If `ios/` doesn't exist yet on this Mac (fresh clone / after the crash):

```bash
npx cap add ios
npx capacitor-assets generate --assetPath assets --iconBackgroundColor '#ffffff' --splashBackgroundColor '#ffffff'
npx cap sync ios
```

- [ ] **Permission strings added** (required — App Store upload rejects without
      them). Open `frontend/ios/App/App/Info.plist` and confirm/add:
      `NSCameraUsageDescription`, `NSPhotoLibraryUsageDescription`,
      `NSPhotoLibraryAddUsageDescription` (exact strings are in `MOBILE.md` §3)

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
- [ ] If asked about **export compliance**: the app uses only standard HTTPS →
      answer "standard encryption / exempt"
- [ ] **Internal Testing** → create a group → add yourself
      (leifhansen1990@gmail.com) — no Apple review needed, available instantly
- [ ] On your iPhone: install **TestFlight** from the App Store → accept the
      email invite → install **Thryft Shop**

## E. Smoke test on the phone

- [ ] App opens to the dashboard, no white flash of the wrong site
- [ ] Nothing hidden under the notch/status bar; bottom nav clear of the home bar
- [ ] Log in works and **stays** logged in after killing + reopening the app
- [ ] Camera opens from New Listing (photo capture) and Shop Mode scan
- [ ] Photo upload → AI identify → draft appears
- [ ] eBay connect (Settings) completes and returns to the app
- [ ] Publish a test listing / end it — full loop

## Iterating

Web changes deploy on merge and appear in the app on next launch — **no new
TestFlight build needed**. Re-run sections B–C only when the icon, permissions,
native plugins, or `server.url` change.
