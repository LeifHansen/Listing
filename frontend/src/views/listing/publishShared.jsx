import { useCallback, useMemo, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { postJson } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useApp } from "@/store";

/* Publish-target selection + the one-click publish recipe, shared by the
   editor's publish bar (useListingForm), the bulk queue, and the Sell
   screen's drafts strip. One localStorage-remembered selection drives all
   three. */

const STORAGE_KEY = "quickflip-publish-marketplaces";

// Which marketplaces publishes go to. Remembered across listings; the
// selector only matters once a non-eBay marketplace is connected — until
// then effectiveTargets is null and callers stay on the legacy single-eBay
// publish path (byte-identical responses).
export function usePublishTargets() {
  const { connectedMarketplaces } = useApp();
  const [selected, setSelected] = useState(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      const arr = raw ? JSON.parse(raw) : null;
      return Array.isArray(arr) && arr.length ? arr : ["ebay"];
    } catch (e) { return ["ebay"]; }
  });
  const toggle = useCallback((key) => {
    setSelected((cur) => {
      const next = cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key];
      if (!next.length) return cur; // always at least one target
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch (e) {}
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

// The cheap client-side gate for one-click Publish buttons (a
// guaranteed-to-fail publish wastes the click). Title and price are required
// everywhere; package weight and an eBay category ONLY matter when eBay is one
// of the targets. Gating an Etsy-only publish on them disabled the button for
// listings the backend would have accepted — /api/publish and the preflight
// both only run the providers they were given.
//
// `targets` is the effectiveTargets array (null = the legacy single-eBay path).
export function missingRequired(l = {}, targets = null) {
  const miss = [];
  if (!(l.title || "").trim()) miss.push("title");
  if (!(Number(l.price) > 0)) miss.push("price");
  const wantsEbay = !targets || !targets.length || targets.includes("ebay");
  if (wantsEbay) {
    const oz = (parseFloat(l.package_weight_lb) || 0) * 16
      + (parseFloat(l.package_weight_oz) || 0);
    if (!(oz > 0)) miss.push("weight");
    if (!(l.category_id || "").toString().trim()) miss.push("category");
  }
  return miss;
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
