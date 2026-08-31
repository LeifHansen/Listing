# Thryft Shop — marketing site

The public site: `thryft.shop`. Statically generated with Astro, deployed to
Cloudflare Pages, and **completely separate from the app**, which keeps serving
at its own origin. Nothing here can take production down.

```bash
cd marketing
npm install
npm run dev      # http://localhost:4321
npm run build    # → dist/
npm run preview  # serve the built output
npm run links    # internal links + per-page SEO tags (needs a build first)
```

## The brand is imported, not copied

`src/styles/global.css` starts with:

```css
@import "../../../frontend/src/styles/tokens.css";
```

That is the app's own token file. The palette, the Fredoka/Nunito Sans pairing,
the radii, the shadow tiers, the `.dark` block, the focus ring and the
reduced-motion guard are the exact declarations the product ships — not a
second copy that drifts a shade at a time.

Change `--brand-blue` in the app and this site restyles with it. CI asserts the
tokens actually reach the built CSS, because a broken import would still
produce a successful build and a silently unstyled page.

The one thing that is a re-expression rather than an import is the component
set: Astro components aren't JSX, so `src/components/Button.astro` restates the
variants from `frontend/src/components/ui/Button.jsx`. The variant and size
**names** match deliberately — a `primary` button here is a `primary` button
there.

## Content

| Where | What |
|---|---|
| `src/lib/site.js` | Every fact that appears on more than one page — nav, pricing, the pipeline, features. Pricing mirrors `backend/services/tokens.py` |
| `src/content/blog/` | Blog posts (markdown). `draft: true` keeps one out of the build |
| `src/content/changelog/` | Release notes |
| `src/content/legal/` | Privacy Policy and Terms — the maintained source of that text |
| `src/pages/` | One file per route |

**Pricing lives in one place.** No page hardcodes a number; they all read
`packs` and `tokenCosts` from `src/lib/site.js`. When `backend/services/
tokens.py` changes, update that file and every page follows.

### The legal pages

The app still serves its own copies at `/about`, `/terms` and
`/privacy-policy`, and those URLs are registered with eBay, Etsy and Apple.
**Don't delete them.** The versions here are canonical for humans and search
engines; repointing the registered URLs is a deliberate, separate step for when
the domain is live.

## Assets

Masters go in `marketing/assets/` (see the README there); optimized derivatives
are served from `public/`. The mascot art and logos in `public/brand/` are
copied from the app's brand set.

`public/og-default.png` is **generated and committed** by
`scripts/build-og.mjs`. It is not built in CI on purpose: rendering the
wordmark needs Fredoka installed as a system font, and a CI box without it
would silently ship a card set in DejaVu. Regenerate locally and commit:

```bash
node scripts/build-og.mjs   # fetches Fredoka if fontconfig can't see it
```

## Configuration

| Variable | Where | What it does |
|---|---|---|
| `SITE_URL` | build env | The public origin. Drives every canonical URL, sitemap entry and OG image URL. Defaults to `https://thryft.shop` |
| `PUBLIC_SIGNUP_ENDPOINT` | build env | Where the notify-me forms POST. **Unset, the forms render as a mailto link instead** — they never silently discard an address |

In CI these come from the repository variables `MARKETING_SITE_URL` and
`MARKETING_SIGNUP_ENDPOINT`.

## Deploying

Push to `main` with changes under `marketing/`. `.github/workflows/
marketing-deploy.yml` builds and publishes to Cloudflare Pages via
`npx wrangler`. It needs:

- secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`
- variables: `CLOUDFLARE_PAGES_PROJECT` (defaults to `thryft-marketing`),
  `MARKETING_SITE_URL`

`public/_headers` carries the CSP, HSTS and framing rules — Cloudflare Pages
reads that file at deploy time. Adding a third-party script means adding its
origin there, or it will be blocked with no visible error.

## Before launch

- [ ] Register the domain; set `MARKETING_SITE_URL` and update `robots.txt`
- [ ] Replace `site.supportEmail` with a real address on that domain
      (`src/lib/site.js`) — the founder's personal inbox is deliberately not
      published here
- [ ] Add the TestFlight public link as `site.testflightUrl`; the iOS CTA falls
      back to an invite-request form until it is set
- [ ] Drop real screenshots and the hero art into `marketing/assets/` and swap
      the placeholders (search the source for `ASSET SWAP`)
- [ ] Fill in `public/.well-known/` once the App Store and Play releases exist
      (see the README there)
- [ ] Point `app.<domain>` at the Fly app — additive, and it does not require
      touching the app
