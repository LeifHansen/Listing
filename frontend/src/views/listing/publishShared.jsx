import { useCallback, useMemo, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { postJson } from "@/lib/api";
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
  return postJson("/api/publish", body);
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
  const issues = [
    ...(res?.issues || []),
    ...Object.values(res?.results || {}).flatMap((r) => r?.issues || []),
  ];
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
