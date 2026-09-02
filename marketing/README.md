# Thryft Shop — marketing site

The public site: `thryftshop.com`. Statically generated with Astro, served by
nginx on its own small Fly app, and **completely separate from the product**,
which keeps serving at its own origin. Nothing here can take production down.

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
| `SITE_URL` | build env | The public origin. Drives every canonical URL, sitemap entry and OG image URL. Defaults to `https://thryftshop.com` |
| `PUBLIC_SIGNUP_ENDPOINT` | build env | Where the notify-me forms POST. **Unset, the forms render as a mailto link instead** — they never silently discard an address |

In CI these come from the repository variables `MARKETING_SITE_URL` and
`MARKETING_SIGNUP_ENDPOINT`.

## Hosting

A second Fly app, `thryft-marketing`, running nginx over the built `dist/`.
It shares nothing with the product: no volume, no database, no secrets, no
models — a `shared-cpu-1x` / 256mb machine that serves files.

### Why the Dockerfile has no build stage

The site imports its brand from `frontend/src/styles/tokens.css`, which lives
*outside* `marketing/`. A Docker build context of `marketing/` cannot see it,
and a context of the repo root would be filtered by the root `.dockerignore`,
which excludes `marketing/` to keep it out of the *app's* context.

So `npm run build` runs before the image does, on a checkout that has the whole
repo — which is what CI already does, and what the link and SEO checks already
ran against. The image ships exactly the bytes that were verified rather than a
second build that could differ. Building it by hand means building the site
first:

```bash
cd marketing
npm ci && npm run build
fly deploy
```

The image refuses to build without a `dist/`, and refuses again if the brand
tokens did not reach the stylesheet — both produce a working nginx serving
something useless, so they fail at build time instead of in a browser.

### What nginx is responsible for

Two things a managed static host would have done for us:

- **Clean URLs.** Astro's `build.format: "file"` emits `/pricing.html`, while
  every canonical URL, sitemap entry and internal link says `/pricing`. The
  `try_files $uri $uri.html` fallback is what stops every page 404ing.
- **Security headers.** CSP, HSTS, framing and referrer policy, in
  `security-headers.conf`. Note that nginx's `add_header` does **not** inherit
  into a location that sets one of its own, which is why the snippet is
  `include`d in each location rather than set once at the server level.

A missing page returns a real `404` with the branded page — not a `200` that
tells crawlers a dead URL is live.

## Deploying

Push to `main` with changes under `marketing/`.
`.github/workflows/marketing-deploy.yml` builds the site, runs the link and SEO
checks, deploys to Fly, and then **polls the live site until it reports the
commit it just shipped** (the image stamps it at `/.build`). A deploy that
reports success while the old image keeps serving is the failure the app's
pipeline was built to catch; the same reasoning applies here.

### Tokens

It authenticates with **`FLY_MARKETING_TOKEN`**, a deploy token scoped to
`thryft-marketing` alone (`fly tokens create deploy -a thryft-marketing`), and
falls back to `FLY_API_TOKEN` if that secret is not set.

Two narrow tokens rather than one broad one. `FLY_API_TOKEN` is scoped to the
product's app and *cannot* touch this one — that is what made the first two
marketing deploys fail with `unauthorized` — and the fix is not to widen it,
which would hand this workflow the ability to deploy the product.

It also reads the repository variable `MARKETING_SITE_URL`.

### First-time setup

Once, from `marketing/`:

Already done, and recorded here because the `-a` flag is easy to miss — `fly`
otherwise looks for a `fly.toml` in the working directory:

```bash
fly apps create thryft-marketing
fly ips allocate-v4 --shared -a thryft-marketing   # free; dedicated v4 is only for non-HTTP
fly ips allocate-v6 -a thryft-marketing
fly ips list -a thryft-marketing
```

## The domain

`thryftshop.com` is registered at **GoDaddy**, and stays there. Nothing about
this setup needs the nameservers moved: Fly is reached by plain `A`/`AAAA` and
`CNAME` records, which GoDaddy handles like any other host.

### The records

In GoDaddy → your domain → DNS → Records:

| Type | Name | Value | Notes |
|---|---|---|---|
| `A` | `@` | `66.241.124.158` | The apex. An `A` record, so no apex-CNAME problem. Shared IPv4 — Fly routes by SNI, which is all an HTTPS site needs |
| `AAAA` | `@` | `2a09:8280:1::180:7b83:0` | Dedicated |
| `CNAME` | `www` | `thryft-marketing.fly.dev` | Redirected to the apex; this site canonicalizes to the apex |
| `CNAME` | `app` | `listing-lfwjrg.fly.dev` | The product, and where the site's CTAs point. Additive — the `.fly.dev` host keeps working |

Then tell Fly which hostnames it should answer for, and it issues the
certificates itself:

```bash
cd marketing && fly certs add thryftshop.com && fly certs add www.thryftshop.com
```

DNS alone is not enough — a Fly app rejects a hostname it has not been told
about, and the certificate is issued against the DNS record, so add the record
first and the cert second.

**`app.thryftshop.com` is not on that list, because it is no longer a manual
step.** The product's own deploy workflow registers it (`.github/workflows/
deploy.yml`, "The app answers for app.thryftshop.com") — idempotently, and
without ever failing a release if the token cannot mint certificates. That line
used to live here, annotated "only when cutting the app over", and the
predictable happened: the `app` CNAME resolved for weeks while the hostname
served a certificate error, because a command in a README is not a command
anyone runs. The bootstrap the marketing deploy already does for its own app
and IPs is the precedent; this is the same idea one file over.

### The app is on both hostnames

`https://app.thryftshop.com` is where the marketing site sends everyone
(`src/lib/site.js`, `APP_URL`). `listing-lfwjrg.fly.dev` keeps working
alongside it — the SPA mount does not move, and every URL the backend builds
comes from the incoming request.

The one thing that cannot follow the request is OAuth. A marketplace returns a
seller to the single callback URL registered against the credential (eBay
resolves `EBAY_RUNAME` to one accepted URL; Etsy and Depop match `redirect_uri`
exactly), while the CSRF nonce cookie is host-only — so a connect begun on the
hostname the callback does *not* return to would be refused as "expired", on a
host where the seller's session cookie does not exist either.

So the app is told which origin those callbacks point at. `fly.toml` sets:

| | |
|---|---|
| `OAUTH_ORIGIN` | `https://listing-lfwjrg.fly.dev` — what is registered in the marketplace consoles today |
| `APP_ORIGINS` | both hostnames; the only origins a flow may return a seller to |

A connect started on `app.thryftshop.com` is sent to `OAUTH_ORIGIN` for the
handshake and the seller is returned to `app.thryftshop.com` at the end.
**Nothing has to be re-registered at eBay, Etsy or Depop for this to work.** If
you ever do move the accepted URL onto `app.thryftshop.com`, change
`OAUTH_ORIGIN` in the same commit and it keeps working in the other direction.

### Reserved, deliberately unset

`admin.thryftshop.com` **has no DNS record, and does not need one.**

The superadmin console shipped in #213, but it is a role-gated tab inside the
existing app — `view === "admin"` in `frontend/src/App.jsx`, guarded by
`isSuperadmin` — not a separate deployment. It is already reachable wherever
the app is, and pointing a subdomain at it would add a hostname to maintain
and a certificate to renew without changing who can see it.

If it is ever split into its own service, it gets a `CNAME` to that service's
`.fly.dev` host and a `fly certs add`, the same as everything else here. Until
then a record would aim at a host nobody has claimed, which is a
subdomain-takeover waiting to happen.

### URL form

One form, everywhere: **no `.html`, no trailing slash except the root.** The
build emits `.html` files that the host serves at clean paths, so the canonical,
`og:url`, the sitemap and the RSS feed could each easily disagree — and a
canonical that contradicts the sitemap tells a crawler the two are separate
pages. `npm run links` fails the build if they ever diverge.

## Before launch

Done:

- [x] ~~Register the domain~~ — `thryftshop.com`, at GoDaddy, staying there
- [x] ~~`fly apps create thryft-marketing` and allocate its IPs~~ — the app
      exists, with `66.241.124.158` (shared v4) and `2a09:8280:1::180:7b83:0`
- [x] ~~A deploy credential for CI~~ — `FLY_MARKETING_TOKEN`, scoped to this
      app alone. `FLY_API_TOKEN` is scoped to the product and cannot reach
      this one, which is what made the first three deploys fail
- [x] ~~The four DNS records at GoDaddy~~ (see "The records" above) — all
      resolve: the apex and `www` reach `thryft-marketing`, and `app` reaches
      `listing-lfwjrg`
- [x] ~~A certificate for `app.thryftshop.com`~~ — the product's deploy
      workflow registers it on every run, so it cannot be forgotten again. This
      is the half that was missing: the `app` record resolved while the
      hostname still served a certificate error

Left:

- [ ] Confirm `fly certs add thryftshop.com -a thryft-marketing` and the same
      for `www.` have been run. DNS is in place, so nothing blocks them; unlike
      the app's certificate these are still a manual step, because the
      marketing deploy only prints the commands into its job summary
- [ ] Set the `MARKETING_SITE_URL` repository variable to
      `https://thryftshop.com` so CI builds and the deploy check match. Until
      then the site builds against the default and deploys to
      `thryft-marketing.fly.dev`, which works
- [ ] **Make `support@thryftshop.com` actually receive mail.** It is published
      on twelve pages. An address that bounces is worse than no address, and
      GoDaddy can forward it. The founder's personal Gmail is deliberately not
      published here
- [ ] Add the TestFlight public link as `site.testflightUrl`; the iOS CTA falls
      back to an invite-request form until it is set
- [ ] Drop real screenshots and the hero art into `marketing/assets/` and swap
      the placeholders (search the source for `ASSET SWAP`)
- [ ] Fill in `public/.well-known/` once the App Store and Play releases exist
      (see the README there)
- [ ] Pick an analytics tool, or decide to go without. Nothing is wired up

### Triggering a deploy by hand

A push to `main` touching `marketing/**` deploys. To run it without a code
change: **Actions → Deploy marketing site → Run workflow**. Adding or changing
a repository secret does *not* start a run on its own — the secret is only read
when a run begins.
