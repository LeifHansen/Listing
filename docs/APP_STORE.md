# Store submission kit — App Store and Google Play

Everything the two store consoles ask for, ready to paste. How to build and
upload the apps is [`MOBILE.md`](../MOBILE.md); this is what goes around them.
Keep the privacy answers here in step with the privacy policy
(`frontend/public/privacy-policy.html`, and its copy on the marketing site in
`marketing/src/content/legal/privacy.md`) and with the privacy manifest that
`frontend/scripts/native-prepare.mjs` writes.

## Decide first

These block a submission and only the owner can settle them.

- [ ] **Support contact.** Both stores publish it. The app's legal pages and
      Settings show a personal Gmail; the marketing site shows
      `support@thryftshop.com`. Pick the company address, confirm it receives
      mail, and make every surface say it (LAUNCH_CHECKLIST, "Support address
      and legal identity").
- [ ] **A review account.** Both reviews sign in. Create a dedicated account
      (not a personal one) with a few finished drafts and an imported listing
      or two, so the reviewer sees the app working without publishing anything.
      Put its credentials only in the consoles' review fields, never in the repo.
- [ ] **Availability: United States only** on both stores, at least while
      billing could be switched on — see "Purchases" in `MOBILE.md`. The
      product is built around eBay US anyway (USD prices, US shipping).
- [ ] **Google Play account type.** A personal account must run a 14-day
      closed test with 12+ testers before production; an organization account
      doesn't. Recruit testers now if it is personal.
- [ ] **The API host in the binary.** Every installed build talks to
      `https://listing-lfwjrg.fly.dev` for as long as it is installed. If that
      hostname should ever be retired, switch the default (`THRYFT_API_BASE`
      in `native-prepare.mjs`) to `https://app.thryftshop.com` *before* the
      first public release — and test an eBay connect on a device against it.

## At a glance

| | App Store | Google Play |
|---|---|---|
| Name | Thryft Shop | Thryft Shop |
| Bundle / package id | `com.thryftshop.app` | `com.thryftshop.app` |
| Category | Business (secondary: Productivity) | Business |
| Price | Free | Free |
| Availability | United States | United States |
| Devices | iPhone (iPad runs it in compatibility mode) | Phones and tablets |
| Support URL | `https://thryftshop.com` (or a support page, once there is one) | contact email: the support address |
| Marketing URL | `https://thryftshop.com/mobile` | website: `https://thryftshop.com` |
| Privacy policy URL | `https://app.thryftshop.com/privacy-policy.html` | same |
| Account deletion URL | — (in-app: Settings → Delete account) | `https://app.thryftshop.com/privacy-policy.html#delete-account` |
| Copyright | `2026 Thryft Shop` | — |

## Listing copy

**Subtitle** (App Store, 30): `Photo to listing in seconds`

**Promotional text** (App Store, 170; editable without a review):

> Photograph an item and get a finished listing back — title, description,
> item specifics, category and price — ready to review and publish to your
> eBay account.

**Short description** (Google Play, 80):

> Snap a photo, get a finished listing: title, specifics, category and price.

**Keywords** (App Store, 100, comma-separated):

```
reseller,thrift,resale,listing,flip,flipping,secondhand,vintage,seller,inventory,ai,photo,price
```

No marketplace or competitor names in the name, subtitle or keywords: both
stores reject other companies' trademarks there. The description may say
what the app works with, which is why "eBay" appears only in the copy below.

**Description** (both, 4000):

```
Thryft Shop turns a few photos into a finished eBay listing.

Photograph the item and Thryft Shop's AI drafts the rest: a title buyers
search for, a clean description, the item specifics eBay asks for, a
category, a condition and a suggested price. Review the draft, change
anything you like, and publish it to your eBay account. What used to be
twenty minutes of typing is a photo and a glance.

BUILT FOR THE AISLE
• Shop Mode: scan a shelf with your camera and see resale estimates before
  anything goes in your basket.
• Photograph a find and have its draft before you leave the shop.
• Start on your phone and finish on your laptop — one account everywhere.

BUILT FOR PEOPLE WHO LIST A LOT
• Bulk drafting: add photos of many items at once and get a draft for each.
• Item specifics filled in for you, so listings show up in eBay's filters.
• Price suggestions based on what similar items sell for.
• Manage live listings: edit them, lower prices, send offers to watchers,
  end and relist.
• Read and answer buyer messages.
• Optional background removal for clean product photos — done on your
  phone where it can be, free, without sending the photo anywhere.

YOUR ACCOUNT, YOUR DATA
• Your sign-in is kept in your phone's secure storage.
• Photos go to an AI service only after you agree, and the app tells you
  which one.
• Delete your account at any time from Settings.

Thryft Shop connects to your own eBay seller account through eBay's own
sign-in; it never sees your eBay password. eBay is a trademark of eBay Inc.
Thryft Shop is not affiliated with or endorsed by eBay.
```

**What's New** (first release): `The first release of Thryft Shop for
iPhone.` / `…for Android.`

## Screenshots and graphics

| | Required | Notes |
|---|---|---|
| App Store, iPhone 6.9" | 1–10 screenshots, 1320×2868 portrait | The 6.9" set is scaled for the smaller iPhones. iPad screenshots are needed only for a `universal` build |
| App Store icon | — | Comes from the build (1024², no transparency: `frontend/assets/icon.png`) |
| Play phone screenshots | 2–8, 9:16, e.g. 1080×1920 | 4+ at 1080p or more makes the app eligible for more Play placements |
| Play app icon | 512×512 PNG | `frontend/assets/store/play-icon-512.png` |
| Play feature graphic | 1024×500 PNG/JPG | `frontend/assets/store/play-feature-graphic.png` |

Both Play graphics are drawn from the brand's vector art by
`npm run brand:assets` (`frontend/scripts/brand-icon.mjs`), so they change
with it. Five screens tell the story, in this order: the dashboard; the camera on an
item; the AI draft with its specifics filled in; Shop Mode's shelf results;
a published listing. Take them from a release build on a device (or the
simulator) signed in to the review account, so no real seller's data is in
them.

## App Review notes (App Store) / App access (Google Play)

```
Thryft Shop drafts marketplace listings from photos. To review it:

Sign in with:  <review account email>  /  <password>
(Tap "Log in" in the navigation, or "Log in to save your work" on the
dashboard.)

1. New Listing → add a photo (camera or library). The first time, the app
   asks permission to send photos to its AI provider; tap Agree. A draft
   with title, description, specifics and price appears shortly after.
2. Shop Mode → "Scan a shelf" records a short video and returns resale
   estimates. The microphone prompt appears because iOS treats video
   capture as audio capture; the sound is discarded on the device.
3. Publishing needs the user's own eBay seller account, connected through
   eBay's sign-in (OAuth). The review account has sample drafts and
   imported listings so every screen can be seen without publishing.
4. Settings → Delete account removes the account and its data.

There are no in-app purchases. All features are free in this version.
```

(If billing is on when you submit, replace the last line with how purchases
work: on iOS, "Buy tokens" opens Safari to a Stripe checkout, as the US
storefront allows; the Android build has no purchase button.)

## App Privacy (App Store Connect)

Data collection: **Yes**. Tracking: **No** (no ad SDKs, no data brokers, no
cross-app identifiers). For every type below: **Linked to the user**, **not
used for tracking**, purpose **App Functionality** only.

| Category | Type | What it is |
|---|---|---|
| Contact Info | Email Address | the account |
| Contact Info | Name | optional display name |
| User Content | Photos or Videos | item photos and listing videos |
| User Content | Emails or Text Messages | eBay buyer messages, relayed (not stored) |
| User Content | Other User Content | listing text and notes for the AI |
| Identifiers | User ID | the account id |
| Purchases | Purchase History | token purchases (only when billing is on) |
| Diagnostics | Crash Data | error reports, tagged with the account id |

Not collected: location, contacts, browsing or search history, health,
financial info (Stripe handles cards; the app never sees them), device IDs,
usage analytics.

## Data safety (Google Play)

- Does the app collect or share any of the required user data types? **Yes**
- Is all collected data encrypted in transit? **Yes** (HTTPS only)
- Do you provide a way for users to request that their data is deleted?
  **Yes** — in the app, and at the account deletion URL above.

**Shared: none.** Play doesn't count sending data to service providers that
process it on your behalf (the AI providers, Photoroom, hosting), or sending
it somewhere at the user's explicit request (publishing to eBay), as sharing.

Background removal runs **on the device** where it can (Apple Vision on
iPhone, ML Kit on Android — Google Play services downloads ML Kit's model;
it is Google's own component, not a data recipient). Only a black-and-white
mask of the item goes to our server, which already holds the photo. Photoroom
receives a photo only for a seller who switched cloud background removal on,
and only for a photo the device could not cut or the seller sent to the cloud
on purpose.

**Collected** (all **required** except Name and Purchase history, which are
optional; none processed ephemerally except messages):

| Play category → type | Purposes |
|---|---|
| Personal info → Email address | Account management, App functionality |
| Personal info → Name | Account management |
| Personal info → User IDs | Account management, App functionality |
| Photos and videos → Photos, Videos | App functionality |
| Messages → Other in-app messages (processed ephemerally) | App functionality |
| App activity → Other user-generated content | App functionality |
| Financial info → Purchase history | App functionality |
| App info and performance → Crash logs | Analytics (fixing bugs) |

## Ratings and declarations

**App Store age rating.** Answer **None / No** to every content question.
Capabilities: the app relays messages between a seller and eBay buyers
(**Messaging and Chat: Yes**); no unrestricted web access (links open
specific pages), no user-generated content published *in* the app, no ads.
Record the rating the questionnaire produces.

**Google Play content rating (IARC).** Category: *Utility, Productivity,
Communication or Other*. No violence, sexual content, profanity, drugs or
gambling. *Users can interact or exchange content:* **Yes** (buyer
messages). *Shares location:* No. *Digital purchases:* No (Yes only if billing
is on).

**Google Play declarations.**

- Target audience: **18 and over** (selling on eBay requires an adult
  account). Not designed for children.
- Ads: **No ads.**
- Advertising ID: **No** — nothing in the app reads it, and the manifest
  doesn't request `AD_ID`.
- App access: **Some functionality is restricted** — give the review account
  and the steps above.
- Financial features: **None.** Government app: No. Health: No. News: No.

## Before you press Submit

**App Store**

- [ ] A build of `main` uploaded (the iOS → TestFlight workflow, or
      `npm run native:ios` and Xcode), smoke-tested on a device from
      TestFlight (MOBILE.md checklist)
- [ ] Listing copy, keywords, URLs, category, age rating and App Privacy
      filled in from this file; screenshots uploaded
- [ ] Review notes and the review account's credentials in App Review
      Information, plus a phone number Apple can reach
- [ ] Pricing and Availability → United States only
- [ ] Export compliance: answered by the build (`ITSAppUsesNonExemptEncryption`)

**Google Play**

- [ ] A signed bundle from the Native builds workflow (or `--bundle`
      locally), smoke-tested from the Internal testing track
- [ ] Main store listing: name, short and full description, icon, feature
      graphic, phone screenshots
- [ ] App content: privacy policy, app access, ads, content rating, target
      audience, data safety, data deletion URL, advertising ID, financial
      features
- [ ] Countries: United States only
- [ ] Closed test with 12+ testers for 14 days (personal accounts), then
      apply for production access
