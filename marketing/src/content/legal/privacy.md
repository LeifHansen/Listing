---
title: Privacy Policy
description: What information Thryft Shop handles, how it is used, and how to delete it.
updated: 2026-08-12
---

Thryft Shop helps sellers create marketplace listings (eBay, Etsy, Depop) from
photos. This page explains what information the service handles and how it is
used.

## Information we collect

- **Account details:** your email address and a securely hashed password (we
  never store your password in plain text).
- **Photos you upload:** product images you submit are stored so they can be
  optimized, analyzed, and attached to your listings. In the mobile app, the
  camera and photo library are used only when you choose to add a photo, and
  only the photos you pick are uploaded.
- **Listing drafts:** titles, descriptions, prices, and other listing details
  generated or edited in the app.
- **Payments:** if you buy AI tokens, the payment is handled by Stripe. Your
  card details go to Stripe directly and are never sent to or stored by us; we
  keep only the record of the purchase.
- **Marketplace connections:** if you connect a marketplace account (eBay,
  Etsy, or Depop), we store the OAuth refresh token that marketplace issues
  and — for eBay — the identifiers of your business policies and merchant
  location, and — for Etsy — your shop id and chosen shipping/return defaults.
  We never see your marketplace passwords.

## How we use it

- Uploaded photos are sent to Anthropic's Claude API to identify the item and
  draft the listing text.
- Listing details and photos are sent to the marketplaces you choose (eBay,
  Etsy, Depop) when you ask us to suggest categories or publish a listing
  there.
- Account and listing data are stored in our database (hosted on Neon) so your
  listing history is available when you log in.
- Uploaded photos are stored on our server and in Cloudflare R2 object storage,
  which is also where eBay fetches them from when a listing is published.

## What we don't do

- We do not sell your data or share it with anyone beyond the service providers
  named above (Anthropic, the marketplaces you connect — eBay, Etsy, Depop —
  our payment processor Stripe, and our hosting/storage providers Fly.io, Neon,
  and Cloudflare).
- We do not use your photos or listings for advertising.

## eBay account deletion

We subscribe to eBay's marketplace account deletion notifications. When eBay
tells us one of its users has requested deletion, we verify that the notice
really came from eBay, record it, and then erase the data we hold for the
matching account — the connection and its stored token, the listings, and the
photos behind them.

We match the notice on the permanent account identifier eBay sends, **not** on
a seller's username, because a username can be changed or reused and would
match the wrong person. We keep a record that the deletion happened, without
the personal details it was about.

## Deleting your account

You can delete your account yourself, from inside the app, at any time:
**Settings → Delete account**. Your account, your listing drafts and your
stored marketplace tokens are removed straight away. Your uploaded photos are
then deleted from our image storage; that part runs just after, so it can take
a short while to finish on a large account. You don't need to email anyone and
there's no waiting period.

Deleting your account removes the marketplace tokens we hold, so this app can
no longer reach your marketplace accounts. It does not by itself cancel the
authorization on the marketplace's side — to do that, revoke this app's access
in your eBay, Etsy or Depop account settings.

One thing we can't delete: listings you already published stay live on eBay,
Etsy, or Depop under your own seller account. End those on the marketplace
itself if you want them taken down.

You can also disconnect any marketplace on its own — either in Settings, or by
revoking access in that marketplace's own account settings — without deleting
your Thryft Shop account.
