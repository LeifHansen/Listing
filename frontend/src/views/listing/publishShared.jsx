import { useCallback, useMemo, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { api, postJson, PUBLISH_TIMEOUT_MS } from "@/lib/api";
import { readLocal, writeLocal } from "@/lib/localPrefs";
import { cn } from "@/lib/utils";
import { useApp } from "@/store";

/* Publish-target selection + the one-click publish recipe, shared by the
   editor's publish bar (useListingForm), the bulk queue, and the Sell
   screen's drafts strip. One localStorage-remembered selection drives all
   three.

   What DISABLES those publish buttons isn't here: it's ebayBlockers in
   blockers.js, the app's single definition of "what stops this reaching
   eBay". Every screen that gates a publish asks it, so a draft can't be
   publishable from its card and blocked in the editor. */

const STORAGE_KEY = "publish-marketplaces";   // see lib/localPrefs

// Which marketplaces publishes go to. Remembered across listings; the
// selector only matters once a non-eBay marketplace is connected — until
// then effectiveTargets is null and callers stay on the legacy single-eBay
// publish path (byte-identical responses).
export function usePublishTargets() {
  const { connectedMarketplaces } = useApp();
  const [selected, setSelected] = useState(() => {
    try {
      const raw = readLocal(STORAGE_KEY);
      const arr = raw ? JSON.parse(raw) : null;
      return Array.isArray(arr) && arr.length ? arr : ["ebay"];
    } catch (e) { return ["ebay"]; }
  });
  const toggle = useCallback((key) => {
    setSelected((cur) => {
      const next = cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key];
      if (!next.length) return cur; // always at least one target
      writeLocal(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }, []);
  const otherConnected = useMemo(
    () => connectedMarketplaces.filter((m) => m.key !== "ebay"),
    [connectedMarketplaces]);
  // null = selector hidden -> legacy eBay path.
  const effectiveTargets = useMemo(() => {
    if (!otherConnected.length) return null;
    const allowed = new Set(["ebay", ...otherConnected.map((m) => m.key)]);
    const sel = selected.filter((k) => allowed.has(k));
    return sel.length ? sel : ["ebay"];
  }, [otherConnected, selected]);
  return { selected, toggle, otherConnected, effectiveTargets };
}

// THE publish recipe — every publish in the app goes through here: the
// drafts strip, the bulk queue, and the editor's Publish/Save Draft bar.
// Persist first, then publish, so the record eBay is built from is exactly
// what was just saved. The editor used to skip the save and publish straight
// out of its in-memory form, which meant the same listing could publish fine
// from its card and fail from the editor — two paths, two behaviours, one
// very confused seller. Returns the /api/publish response (res.published,
// res.listing_id, and res.multi + res.results on fan-outs).
export async function publishListing(id, listing, effectiveTargets, mode = "live") {
  await postJson(`/api/save/${id}`, listing);
  const body = { session_id: id, listing, mode };
  if (effectiveTargets
      && !(effectiveTargets.length === 1 && effectiveTargets[0] === "ebay")) {
    body.marketplaces = effectiveTargets;
  }
  // The save above keeps the default deadline: it is a local write, and a
  // slow one is a real problem. Only the publish itself waits on eBay.
  try {
    return await postJson("/api/publish", body,
                          { timeoutMs: PUBLISH_TIMEOUT_MS });
  } catch (e) {
    // Refused → the seller has something to fix, and this is their error.
    // Answer lost → the server is very probably still publishing, and
    // reporting a failure here is what sends someone back to publish a
    // listing that is already live. Ask instead.
    if (!e?.unknownOutcome || mode !== "live") throw e;
    const settled = await resolveLostPublish(id);
    if (!settled.published) throw e;   // api.js already says the right thing
    return {
      published: true,
      listing_id: settled.listing_id,
      message: "Your listing is live on eBay. The publish outran the wait, "
        + "so this was confirmed from your store rather than from the "
        + "publish itself.",
    };
  }
}

// A record whose listing is on eBay right now. Both spellings are in the
// data: the publish path writes "published", the store sync writes "live".
const LIVE_STATUSES = new Set(["published", "live"]);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// What actually became of a publish whose answer never arrived.
//
// A client giving up on a request does not stop the server: the publish runs
// to the end, and the moment eBay accepts the listing the record carries its
// item id — the create path writes that BEFORE it does anything else, exactly
// so an id can never be the thing that goes missing. So the question "did it
// go live?" has an answer on the server within a few seconds of the timeout,
// and nothing in the app was asking it.
//
// Reads only, and a read that fails proves nothing either way, so a failed
// poll just waits for the next one. Sound because both bulk queues publish
// DRAFTS: a record that now reads published is this publish's doing and
// nothing else's.
//
// It answers for the RECORD, not per marketplace — a fan-out whose eBay half
// landed reads as published here with no word on the rest. That is the right
// answer to the only question being asked ("is this on eBay, or am I about to
// list it twice?"), and the listings refresh that follows carries the
// per-marketplace state the queue renders from.
//
// Resolves { published: true, listing_id } once the record settles, or
// { published: false } when it has not within the window — which is "still
// unknown", not "refused", and is why the caller rethrows the original error
// instead of inventing a rejection.
export async function resolveLostPublish(id, { attempts = 6, waitMs = 5000,
                                               wait = sleep } = {}) {
  for (let i = 0; i < attempts; i += 1) {
    await wait(waitMs);
    let rec;
    try {
      rec = await api(`/api/listings/${id}`);
    } catch (e) {
      continue;
    }
    const listingId = String(rec?.listing?.ebay_listing_id || "");
    if (LIVE_STATUSES.has(rec?.status) && listingId) {
      return { published: true, listing_id: listingId };
    }
  }
  return { published: false };
}

// What to tell the seller about a publish that never came back with an
// answer. Not a refusal — there is nothing to open and fix — so it does not
// go through blockedReason, which would dress a timeout up as eBay's verdict
// on the listing.
export const UNCONFIRMED_PUBLISH =
  "eBay didn't answer in time. This may already be live — check your store "
  + "before publishing it again.";

// Did the SERVER say it could not establish what the marketplace did?
//
// The other half of the same question `err.unknownOutcome` answers for this
// client. When the request reaches eBay and the reply is lost on the server
// side, the app is not left guessing: services/ebay_trading raises its own
// UnknownOutcome, and the providers now stamp `outcome_unknown` on the
// publish body (top level on the legacy single-eBay response, per
// marketplace on a fan-out). Before that flag existed this arrived as an
// ordinary unsuccessful publish, and the bulk summaries called it "refused".
export function outcomeUnknown(res) {
  if (!res) return false;
  if (res.outcome_unknown) return true;
  return Object.values(res.results || {}).some((r) => r?.outcome_unknown);
}

// How one publish lands in a bulk run's tally, and what its card says.
//
// Three outcomes, not two, and the third is the one that matters: a listing
// eBay refused (open it, fix the field), a listing that went live, and a
// listing nobody can say either way about — which must be CHECKED, never
// republished on the strength of a summary line. Both queues ask this rather
// than each writing the ternary themselves; they are the two screens where a
// wrong answer is repeated across a whole batch.
export function publishTally(res, fallback) {
  if (res?.published) return { published: true, unconfirmed: false, reason: null };
  if (outcomeUnknown(res)) {
    return { published: false, unconfirmed: true, reason: UNCONFIRMED_PUBLISH };
  }
  return { published: false, unconfirmed: false,
           reason: blockedReason(res, fallback) };
}

// Every issue a publish attempt raised, in both shapes it can arrive in: a
// single-marketplace result carries them at the top, a multi-marketplace one
// keeps a set per marketplace under `results`.
export function allIssues(res) {
  return [
    ...(res?.issues || []),
    ...Object.values(res?.results || {}).flatMap((r) => r?.issues || []),
  ].filter(Boolean);
}

// The errors that point at ONE card's fields — what that card has to show,
// and show without being asked, when a publish came back refused.
export function issuesFor(res, target) {
  return allIssues(res).filter((i) => i.target === target && i.level !== "warn");
}

// What to TELL the seller when a publish did not go live.
//
// res.message is eBay's own sentence, and for its catch-all rejections that
// sentence is actively misleading. Error 240 -- "The item cannot be listed or
// modified. The title and/or description may contain improper words or the
// listing or seller may be in violation of eBay policy" -- is an ACCOUNT-level
// hold: it repeats on every listing, names four possible causes and none of
// them the real one, and the first thing it blames is the title.
//
// The backend already resolves that. ebay_errors.explain() files a 240 under
// target "account" (there is a test asserting it must not point at the title),
// and ebay_account.publish_block_issues() spends one call asking eBay whether
// payments onboarding is the hold, appending a plain-language answer when it
// is. All of that arrives in res.issues.
//
// Leading with res.message threw it away and sent the seller editing titles
// that were never the problem. Prefer what the app worked out; fall back to
// eBay's words only when there is nothing better to say.
//
// A 240 eBay declined to explain arrives flagged `placeholder` -- "the publish
// stopped and nobody said why". It is an account-target issue like any other,
// so it used to win this pick outright and hide the diagnosis behind it: seven
// drafts, seven identical "eBay is blocking new listings on this account", and
// the sentence naming the actual hold never on screen. It now goes last.
export function blockedReason(res, fallback) {
  const issues = allIssues(res);
  const errors = issues.filter((i) => i && i.level !== "warn" && i.title);
  const named = errors.filter((i) => !i.placeholder);
  const best = named.find((i) => i.target && i.target !== "generic")
    || named[0] || errors[0];
  return best?.title || res?.message || fallback;
}

// Which field the editor should jump to and flag after a failed publish.
// Same ordering rule as blockedReason: a placeholder names no field worth
// jumping to, so it never wins over an issue that does.
export function fixTargetFor(issues) {
  const errors = (issues || []).filter((i) => i && i.level !== "warn");
  const usable = (i) => i.target && i.target !== "generic" && i.target !== "account";
  return (errors.find((i) => !i.placeholder && usable(i)) || {}).target || null;
}

// "Post to" toggle chips — one per connected marketplace. Render only when
// otherConnected is non-empty (eBay-only sellers never see the selector).
export function MarketTargetChips({ selected, toggle, otherConnected }) {
  if (!otherConnected.length) return null;
  const options = [{ key: "ebay", label: "eBay" },
    ...otherConnected.map((m) => ({ key: m.key, label: m.label }))];
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[12px] font-semibold text-ink-faint mr-1">Post to</span>
      {options.map((m) => {
        const on = selected.includes(m.key);
        return (
          <button
            key={m.key} type="button" onClick={() => toggle(m.key)}
            aria-pressed={on}
            className={cn(
              "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[12px] font-bold cursor-pointer transition-colors",
              on ? "bg-blue-soft border-blue/45 text-blue"
                : "bg-transparent border-line text-ink-faint hover:border-ink-faint",
            )}
          >
            {on && <CheckCircle2 size={11} aria-hidden />}
            {m.label}
          </button>
        );
      })}
    </div>
  );
}
