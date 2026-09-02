import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, pollJob, postJson, downscaleAllForUpload, batchModelTimeoutMs } from "@/lib/api";
import { lastRemoveBg } from "@/lib/photoPrefs";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { once } from "@/lib/utils";
import { nearestCondition } from "@/lib/conditions";
import {
  publishListing, usePublishTargets, blockedReason, fixTargetFor,
} from "./publishShared";
import { ebayBlockers, weightOz } from "./blockers";
import {
  confirmSpecificRows, specificRowIndex, specificValues,
  toggleSpecificValue as toggleValue,
} from "./specifics";

/* All state + actions for the listing workflow. The form object mirrors the
   backend Listing model; item_specifics stays the single source of truth for
   both the free-form rows and the category-required aspect fields. */

const EMPTY = {
  title: "", subtitle: "", brand: "", price: "", purchase_price: "", quantity: 1,
  // What it ACTUALLY sold for (see models.Listing.sold_price) — eBay fills
  // this in from the transaction; the seller can correct it in the editor
  // when eBay never reported one. "" = unknown, not free.
  sold_price: "",
  listing_format: "FIXED_PRICE", auction_start_price: "", auction_duration: "DAYS_7",
  package_weight_lb: "", package_weight_oz: "",
  package_length_in: "", package_width_in: "", package_height_in: "",
  fulfillment_policy_id: "",
  category_suggestion: "", category_id: "", condition: "USED_GOOD",
  condition_description: "", description: "", item_specifics: [],
  promote: false, ad_rate_percent: 0,
  images: [], image_urls: [], currency: "USD", missing_info: [],
  // "ebay" for a listing imported from the seller's store (edits go back via
  // the Trading API); "" for one created here.
  source: "", sku: "", ebay_listing_id: "", view_url: "",
  // Marketplace-specific fields (their own editor cards) + per-marketplace
  // publish state (server-owned; carried through saves untouched).
  etsy: {
    taxonomy_id: 0, who_made: "", when_made: "", is_supply: false,
    materials: [], tags: [], shipping_profile_id: "", return_policy_id: "",
  },
  depop: { category: "", size: "" },
  marketplaces: {},
};

function fromListing(l) {
  if (!l) return { ...EMPTY };
  return {
    ...EMPTY,
    ...l,
    etsy: { ...EMPTY.etsy, ...(l.etsy || {}) },
    depop: { ...EMPTY.depop, ...(l.depop || {}) },
    marketplaces: l.marketplaces || {},
    price: l.price != null ? l.price : "",
    purchase_price: l.purchase_price != null ? l.purchase_price : "",
    sold_price: l.sold_price != null ? l.sold_price : "",
    auction_start_price: l.auction_start_price != null ? l.auction_start_price : "",
    quantity: l.quantity || 1,
    package_weight_lb: l.package_weight_lb || "",
    package_weight_oz: l.package_weight_oz || "",
    package_length_in: l.package_length_in || "",
    package_width_in: l.package_width_in || "",
    package_height_in: l.package_height_in || "",
    item_specifics: (l.item_specifics || []).map((s) => ({ ...s })),
    images: l.images || [],
    image_urls: l.image_urls || [],
  };
}

export function useListingForm() {
  const {
    session, setSession, health, loadListings, invalidateListings,
    openListings, patchListing,
  } = useApp();
  const { toast } = useToast();

  const [form, setForm] = useState(() => fromListing(session?.listing));
  const [aiBusy, setAiBusy] = useState(null); // string[] of friendly messages, or null
  const [publishResult, setPublishResult] = useState(null);
  const [fixTarget, setFixTarget] = useState(null); // which field group eBay flagged
  const [catSuggestions, setCatSuggestions] = useState(null);
  const [priceData, setPriceData] = useState(null);
  const [categoryMeta, setCategoryMeta] = useState(
    { conditions: [], aspects: [], conditionsChecked: true });

  // ---------- marketplace targets ----------
  // Which marketplaces the Publish buttons hit — the shared remembered
  // selection (publishShared) also used by the drafts strip and bulk queue.
  // chipTargets: the effective list; null = selector hidden -> legacy eBay
  // publish path.
  const {
    selected: marketTargets, toggle: toggleMarketTarget,
    effectiveTargets: chipTargets,
  } = usePublishTargets();

  const sessionId = session?.sessionId;
  // Live = this session is (still) a live eBay listing being revised, so the
  // publish actions become Update / End instead of Publish / Save Draft.
  // source==="ebay" alone is NOT enough: every Trading publish sets it, so an
  // ENDED listing opened from the Inactive tab would wrongly show Update/End.
  // An ENDED record gets the Publish action instead — the server relists it
  // as a fresh listing (eBay can't revise an ended item). A SOLD record is
  // settled too, but it never reaches the publish path at all: the editor
  // renders SoldArchive for it and the server refuses to publish it.
  const settled = session?.status === "ended" || session?.status === "sold";
  // A sold listing is an archive record — SoldArchive replaces the whole
  // workflow rather than the workflow growing a sold-only branch.
  const isSold = session?.status === "sold";
  const isLive = !settled && (session?.status === "published" || session?.status === "live"
    || (session?.listing?.source || "") === "ebay");
  const ebayListingId = session?.listing?.ebay_listing_id
    || publishResult?.listing_id || "";

  // Re-seed the form whenever a different listing session is opened. The
  // guard is a ref that is read and written ONLY inside the effect below:
  // comparing it during render (to expose a "reseed is pending" flag) is
  // exactly what React's refs rule rejects, because a render that React
  // throws away would still have consumed the comparison. Nothing outside
  // this effect needs to know a re-seed is pending — see the category-meta
  // effect further down, which reads the incoming listing directly instead.
  const seededFor = useRef(sessionId);
  useEffect(() => {
    if (seededFor.current !== sessionId) {
      seededFor.current = sessionId;
      setForm(fromListing(session?.listing));
      setPublishResult(null);
      setFixTarget(null);
      setCatSuggestions(null);
      setPriceData(null);
      setCategoryMeta({ conditions: [], aspects: [], conditionsChecked: true });
    }
  }, [sessionId, session]);

  const set = useCallback((key, value) => {
    setForm((f) => ({ ...f, [key]: value }));
  }, []);

  // Read the form back into a backend Listing payload.
  const collect = useCallback(() => {
    const num = (v) => parseFloat(v) || 0;
    return {
      ...(session?.listing || {}),
      ...form,
      price: form.price === "" ? null : parseFloat(form.price),
      purchase_price: form.purchase_price === "" ? null : parseFloat(form.purchase_price),
      sold_price: form.sold_price === "" ? null : parseFloat(form.sold_price),
      auction_start_price: form.auction_start_price === "" ? null : parseFloat(form.auction_start_price),
      quantity: parseInt(form.quantity || "1", 10),
      package_weight_lb: num(form.package_weight_lb),
      package_weight_oz: num(form.package_weight_oz),
      package_length_in: num(form.package_length_in),
      package_width_in: num(form.package_width_in),
      package_height_in: num(form.package_height_in),
      // Blank rows are kept (a specific being typed still needs its row) —
      // except where the SAME aspect already has an answer elsewhere in the
      // list. Those are pure leftovers, and a blank row sitting in front of
      // the answer is what made a filled aspect read as empty.
      item_specifics: (() => {
        const rows = form.item_specifics
          .map((s) => ({ name: s.name.trim(), value: s.value.trim(),
                         confidence: s.confidence || "" }))
          .filter((s) => s.name);
        const answered = new Set(
          rows.filter((s) => s.value).map((s) => s.name.toLowerCase()));
        return rows.filter((s) => s.value || !answered.has(s.name.toLowerCase()));
      })(),
      images: form.images || [],
      currency: form.currency || "USD",
    };
  }, [form, session]);

  // ---------- item specifics (single source of truth) ----------
  // The aspect's ANSWER row — the first one holding a value, not just the
  // first one carrying the name (see specifics.js). An empty leftover row
  // used to shadow the real value: the field rendered blank and the blocker
  // list called a filled aspect missing.
  const getSpecificRow = useCallback((name) => {
    const i = specificRowIndex(form.item_specifics, name);
    return i >= 0 ? form.item_specifics[i] : null;
  }, [form.item_specifics]);

  const getSpecific = useCallback(
    (name) => getSpecificRow(name)?.value || "", [getSpecificRow]);

  // Every value held under one aspect name. A multi-select (checkbox) aspect
  // — Features, Style, Season — legitimately holds several rows, and the
  // checkbox group needs all of them, not just the first.
  const getSpecificValues = useCallback(
    (name) => specificValues(form.item_specifics, name), [form.item_specifics]);

  // Tick / untick one value of a multi-select aspect (see specifics.js).
  const toggleSpecificValue = useCallback((name, value, on) => {
    setForm((f) => {
      const specs = toggleValue(f.item_specifics, name, value, on);
      return specs === f.item_specifics ? f : { ...f, item_specifics: specs };
    });
  }, []);

  // A seller edit clears the AI confidence flag: the value is now theirs, so
  // neither the ✓ (AI high) nor the ⚠ (review) badge applies anymore.
  const upsertSpecific = useCallback((name, value) => {
    setForm((f) => {
      const specs = [...f.item_specifics];
      // The same row getSpecificRow shows, so an edit lands on the value the
      // seller is looking at rather than on an empty row hiding above it.
      const i = specificRowIndex(specs, name);
      if (i >= 0) specs[i] = { ...specs[i], value, confidence: "" };
      else if (value) specs.push({ name, value, confidence: "" });
      return { ...f, item_specifics: specs };
    });
  }, []);

  // Accept an AI-inferred value as-is. The point of the ⚠ flag is that a
  // wrong specific is worse than a missing one, so the seller has to actually
  // look at every inference — but "I looked, it's right" needed a gesture
  // that wasn't retyping the value it already holds. Clearing the flag (not
  // the value) is that gesture, and it makes the review count fall as they go.
  const confirmSpecific = useCallback((name) => {
    setForm((f) => {
      // Every row for the aspect, not just the first: a multi-select aspect
      // shows one flag for the whole group, so one ✓ has to clear the group.
      const specs = confirmSpecificRows(f.item_specifics, name);
      return specs === f.item_specifics ? f : { ...f, item_specifics: specs };
    });
  }, []);

  // Accept every outstanding inference at once. Deliberately NOT offered as a
  // headline action — see the comment at its call site; it exists for the
  // seller who has already read the list.
  const confirmAllSpecifics = useCallback(() => {
    setForm((f) => ({
      ...f,
      item_specifics: f.item_specifics.map(
        (s) => (s.confidence === "medium" ? { ...s, confidence: "" } : s)),
    }));
  }, []);

  // ---------- category-driven fields ----------
  // Fetch the category's valid conditions + required/recommended aspects so
  // the seller completes everything without leaving.
  const loadCategoryMeta = useCallback(async (categoryId) => {
    const cid = (categoryId ?? form.category_id ?? "").trim();
    if (!health.taxonomy_configured || !cid) {
      setCategoryMeta({ conditions: [], aspects: [], conditionsChecked: true });
      return;
    }
    const [cond, asp] = await Promise.all([
      // `checked: false` is the route saying it could not ask eBay. An empty
      // list otherwise means "eBay puts no condition requirement on this
      // category", and the editor falls back to the generic list — which is
      // how a seller picks a condition eBay refuses at publish (error 25021,
      // the reason this lookup exists). A failed request is the same news.
      postJson("/api/item-conditions", { category_id: cid })
        .catch(() => ({ conditions: [], checked: false })),
      postJson("/api/item-aspects", { category_id: cid }).catch(() => ({ aspects: [] })),
    ]);
    const conditions = cond.conditions || [];
    setCategoryMeta({ conditions, aspects: asp.aspects || [],
                      conditionsChecked: cond.checked !== false });
    // If the current condition isn't one this category offers, move it to the
    // CLOSEST one that is, so we never submit a condition eBay rejects
    // (25021). Closest, not first: eBay lists "New" first almost everywhere,
    // and snapping a worn item to it — which is what this did — swaps a
    // publish error for a listing that lies about the item. nearestCondition
    // never crosses the new/used line, and returns null when the category
    // offers nothing honest, in which case the listing keeps what it has and
    // the Condition card flags it.
    if (conditions.length) {
      setForm((f) => {
        const fitted = nearestCondition(f.condition, conditions.map((c) => c.enum));
        return !fitted || fitted === f.condition ? f : { ...f, condition: fitted };
      });
    }
  }, [form.category_id, health.taxonomy_configured]);

  // Load meta when a session opens with a category already set. This effect
  // runs only on mount and on a session switch, and in both cases the
  // category to load is the incoming listing's: on a switch the form state is
  // one render behind (the effect above re-seeds it in this same commit), and
  // on mount the form was itself seeded from this listing, so reading the
  // listing covers both without needing to know which case we are in.
  useEffect(() => {
    if (!sessionId) return;
    const cid = session?.listing?.category_id;
    // loadCategoryMeta is async: the conditions/aspects it sets land after the
    // /api/item-conditions + /api/item-aspects round trip, so this is a
    // fetch-on-open rather than a synchronous render cascade. Its one
    // synchronous setState path (taxonomy not configured on the server) only
    // re-asserts the empty meta a session switch has just written.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (cid) loadCategoryMeta(cid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const suggestCategories = useCallback(async () => {
    if (!health.taxonomy_configured) {
      setCatSuggestions({ error: "Automatic categories need eBay API credentials on the server. You can still enter a category ID manually." });
      return;
    }
    const l = collect();
    const query = [l.brand, l.title, l.category_suggestion].filter(Boolean).join(" ").trim();
    if (!query) { setCatSuggestions({ error: "Add a title or brand first." }); return; }
    setCatSuggestions({ loading: true });
    try {
      const res = await postJson("/api/category-suggestions", { query, limit: 5 });
      setCatSuggestions({ items: res.suggestions || [] });
    } catch (e) {
      setCatSuggestions({ error: `Couldn't fetch categories: ${e.message}` });
    }
  }, [collect, health.taxonomy_configured]);

  const chooseCategory = useCallback((s) => {
    setForm((f) => ({
      ...f,
      category_id: s.category_id,
      category_suggestion: s.path || s.category_name,
    }));
    loadCategoryMeta(s.category_id);
  }, [loadCategoryMeta]);

  const checkMarketPrice = useCallback(async () => {
    if (!health.taxonomy_configured) {
      setPriceData({ error: "Price check needs eBay API credentials on the server." });
      return;
    }
    const l = collect();
    const query = [l.brand, l.title].filter(Boolean).join(" ").trim();
    if (!query) { setPriceData({ error: "Add a title or brand first." }); return; }
    setPriceData({ loading: true });
    try {
      const data = await postJson("/api/price-suggestions", {
        query, category_id: l.category_id || null, condition: l.condition || null,
      });
      setPriceData(data);
    } catch (e) {
      setPriceData({ error: `Couldn't check prices: ${e.message}` });
    }
  }, [collect, health.taxonomy_configured]);

  // ---------- AI refine ----------
  const refine = useMemo(() => once("refine", async (prompt) => {
    if (!prompt.trim()) return;
    setAiBusy([
      "Rewriting your listing…",
      "Polishing the details…",
      "Optimizing for eBay search…",
    ]);
    try {
      const current = collect();
      const updated = await postJson("/api/refine", {
        session_id: sessionId, listing: current, prompt,
      });
      setSession((s) => ({ ...s, listing: updated }));
      setForm(fromListing(updated));
      // The re-seed guard needs no touching here: it is keyed on sessionId,
      // which a refine never changes, so it already matches. (It used to be
      // re-stamped with the captured sessionId at this point, which was a
      // no-op at best — and actively wrong if the seller opened a different
      // listing while the refine was in flight, since it re-armed a re-seed
      // against a session that had already moved on.)
      toast("Listing refined ✨", { kind: "success" });
      // A refine rewrites the title and price the cards are showing.
      invalidateListings();
      return true;
    } catch (e) {
      toast(`Refine error: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }), [collect, sessionId, setSession, invalidateListings, toast]);

  // ---------- images ----------
  // Cache-bust per PHOTO, not globally: a global counter made one rotate
  // re-download every tile's full-size image — the main reason rotating felt
  // slow on a listing with many photos.
  const [imageVersions, setImageVersions] = useState({});
  const bumpImageVersion = useCallback((name) => {
    if (name) setImageVersions((m) => ({ ...m, [name]: (m[name] || 0) + 1 }));
  }, []);

  // One-tap 90° clockwise rotate; only the rotated tile refreshes.
  //
  // RETHROWS after the toast. The tile turns its photo the moment the button
  // is pressed and undoes that turn if the rotate fails (see PhotoTile) — and
  // swallowing the error here meant the undo never ran. The photo stayed
  // turned on screen while the saved file was untouched, and since a failed
  // rotate also bumps no version, nothing else came along to correct it: the
  // seller was left looking at an orientation that did not exist, on a tile
  // that would keep it until the editor was reopened.
  const rotateImage = useCallback(async (name) => {
    try {
      await postJson("/api/rotate-image", { session_id: sessionId, name });
      bumpImageVersion(name);
      // The saved file changed, so every card showing this listing is now
      // showing a photo that no longer exists. See store.invalidateListings.
      invalidateListings();
    } catch (e) {
      toast(`Couldn't rotate: ${e.message}`, { kind: "error" });
      throw e;
    }
  }, [sessionId, bumpImageVersion, invalidateListings, toast]);

  // One-click by default; pass a confirmFn to gate it behind a dialog.
  // Optimistic: the tile disappears immediately and comes back only if the
  // server delete fails.
  //
  // The SESSION is updated alongside the form, and the server's remaining
  // list is applied on top. Both matter beyond tidiness: the delete is now
  // persisted server-side (see /api/delete-image), so its answer is the list
  // the next reorder is checked against — and a form and session that
  // disagree about the photos are what made a delete followed by a drag come
  // back as "this listing's photos changed somewhere else".
  const deleteImage = useCallback(async (name, confirmFn) => {
    const prev = form.images || [];
    if (prev.length <= 1) {
      toast("A listing needs at least one photo — add another before deleting this one.", { kind: "warning" });
      return;
    }
    if (confirmFn && !(await confirmFn({
      title: "Delete this photo?",
      message: "This can't be undone.",
      confirmLabel: "Delete",
      danger: true,
    }))) return;
    const setImages = (imgs) => {
      setForm((f) => ({ ...f, images: imgs }));
      setSession((s) => (s ? { ...s, listing: { ...(s.listing || {}), images: imgs } } : s));
    };
    const next = prev.filter((n) => n !== name);
    setImages(next);
    try {
      const res = await postJson("/api/delete-image", { session_id: sessionId, name });
      const saved = Array.isArray(res?.images) && res.images.length ? res.images : next;
      if (saved.join("|") !== next.join("|")) setImages(saved);
      // Deleting the FIRST photo changes the thumbnail every card renders.
      invalidateListings();
    } catch (e) {
      setImages(prev);
      toast(`Couldn't delete the photo: ${e.message}`, { kind: "error" });
    }
  }, [form.images, sessionId, setForm, setSession, invalidateListings, toast]);

  // Persist a new photo order. The FIRST image is the eBay gallery/hero photo,
  // so order matters — it must survive a reload and be what we publish. Update
  // the form + session immediately (optimistic) and save in the background.
  // Persist a new photo order. Two things this deliberately does NOT do any
  // more: send the whole listing (a drag could overwrite a title being edited
  // in another tab with a stale copy), and swallow the failure. It used to end
  // in `.catch(() => {})`, so a rejected save left the new order on screen,
  // saved nowhere, until a reload quietly put it back — which is the seller
  // dragging their main photo into place and finding it moved again later.
  const reorderImages = useCallback(async (nextImages) => {
    const imgs = [...nextImages];
    const previous = form.images || [];
    setForm((f) => ({ ...f, images: imgs }));
    setSession((s) => (s ? { ...s, listing: { ...(s.listing || {}), images: imgs } } : s));
    if (!sessionId) return;
    try {
      const res = await api(`/api/listings/${sessionId}/images/order`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ images: imgs }),
      });
      // The server's answer wins — it is what eBay will be handed.
      const saved = res.images || imgs;
      if (saved.join("|") !== imgs.join("|")) {
        setForm((f) => ({ ...f, images: saved }));
        setSession((s) => (s ? { ...s, listing: { ...(s.listing || {}), images: saved } } : s));
      }
      // The first image is the card's thumbnail, so a reorder can change what
      // every listing card is showing.
      invalidateListings();
    } catch (e) {
      setForm((f) => ({ ...f, images: previous }));
      setSession((s) => (s ? { ...s, listing: { ...(s.listing || {}), images: previous } } : s));
      toast(`Photo order not saved: ${e.message}`, { kind: "error" });
    }
  }, [form.images, sessionId, setForm, setSession, invalidateListings, toast]);

  // Upload more photos onto this listing: optimize server-side, append the new
  // files to the image order, and persist.
  const [addingPhotos, setAddingPhotos] = useState(false);
  const addImages = useCallback(async (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length || !sessionId) return;
    setAddingPhotos(true);
    try {
      const fd = new FormData();
      // Every other upload path re-encodes to 2000px first; this one did not,
      // so "Add photos" was the one action that shipped raw 5-12MB phone
      // photos -- over a mobile connection, into a 1GB volume, on the request
      // that already has the least time to spare.
      const prepped = await downscaleAllForUpload(files);
      prepped.forEach((f) => fd.append("files", f));
      // Photos joining a listing get the same treatment its existing photos
      // got. Sending nothing here left the server on its `false` default, so
      // "Add photos" quietly produced originals-with-backgrounds alongside
      // cut-outs, with no toggle on the card and no word about it afterwards.
      const removeBg = lastRemoveBg();
      fd.append("remove_bg", removeBg ? "true" : "false");
      // This endpoint still runs the cutouts INLINE (every other upload path
      // hands them to a job), and inference is single-flight, so the deadline
      // has to scale with the photo count or the client abandons work the
      // server is mid-way through -- losing the photos AND the tokens.
      const res = await api(`/api/upload-more/${sessionId}`,
        { method: "POST", body: fd,
          timeoutMs: batchModelTimeoutMs(prepped.length, removeBg) });
      const added = res.added || [];
      if (added.length) {
        const next = [...(form.images || []), ...added];
        setForm((f) => ({ ...f, images: next }));
        setSession((s) => (s ? { ...s, listing: { ...(s.listing || {}), images: next } } : s));
        // Awaited, inside the try: a rejected save left the new photos on
        // screen and saved nowhere, so a reload lost them with no error ever
        // shown -- the same trap reorderImages above documents having fixed.
        // The outer catch turns it into "Couldn't add photos: ...".
        await postJson(`/api/save/${sessionId}`, { ...collect(), images: next });
        toast(`Added ${added.length} photo${added.length === 1 ? "" : "s"}.`, { kind: "success" });
        // A cutout that failed keeps the original photo, background and all.
        // That is the right fallback -- a photo is better than no photo -- but
        // it has to be SAID, or the seller is left wondering why two of their
        // photos look nothing like the others.
        const kept = (res.optimize_results || []).filter((r) => r.bg_error);
        if (kept.length) {
          toast(`${kept.length} photo${kept.length === 1 ? " kept its" : "s kept their"} `
                + `background: ${kept[0].bg_error}`, { kind: "warning" });
        }
        // New photos were saved onto the listing — and if it had none, the
        // card was rendering the no-photo placeholder.
        invalidateListings();
      }
    } catch (e) {
      toast(`Couldn't add photos: ${e.message}`, { kind: "error" });
    } finally {
      setAddingPhotos(false);
    }
  }, [sessionId, form.images, collect, setForm, setSession,
      invalidateListings, toast]);

  // ---------- pre-publish checklist ----------
  const runPreflight = useCallback(async () => {
    setAiBusy(["Checking everything the marketplaces require…"]);
    try {
      // The mode the PUBLISH will actually run in: an edit to a live listing
      // is a revise, and the server checks a revise against what it resends,
      // not against the full create contract (see preflight.validate). Asking
      // for the create checklist here reported a package weight and category
      // aspects the live listing had already satisfied on eBay.
      const body = {
        session_id: sessionId, listing: collect(),
        mode: isLive ? "revise" : "live",
      };
      if (chipTargets) body.marketplaces = chipTargets;
      const res = await postJson("/api/publish-preflight", body);
      // Marketplace-specific checklists ride along under by_marketplace —
      // fold them into one list so the fix-it panel covers everything.
      const issues = [
        ...(res.issues || []),
        ...Object.values(res.by_marketplace || {}).flat(),
      ];
      const errors = issues.filter((i) => i.level !== "warn");
      setPublishResult({
        preflight: true,
        error: errors.length > 0,
        issues,
        message: errors.length
          ? `Not quite ready — ${errors.length} thing${errors.length === 1 ? "" : "s"} to fix before publishing:`
          : "All checks passed — this listing is ready to publish. 🎉",
      });
      const first = fixTargetFor(errors);
      if (first) setFixTarget(first);
    } catch (e) {
      toast(`Couldn't run the check: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }, [collect, sessionId, toast, chipTargets, isLive]);

  // ---------- publish ----------
  const publish = useMemo(() => once("publish", async (mode) => {
    setFixTarget(null);
    setPublishResult(null);
    const multi = chipTargets && chipTargets.length > 1;
    setAiBusy(mode === "live"
      ? [multi ? "Publishing to your marketplaces…" : "Publishing to eBay…",
         "Uploading photos…", "Crossing the t's…"]
      : ["Saving your draft…"]);
    try {
      const listing = collect();
      setSession((s) => ({ ...s, listing }));
      // Same recipe the drafts strip and bulk queue use — see publishListing.
      // The editor is not allowed its own publish path.
      const result = await publishListing(sessionId, listing, chipTargets, mode);
      setPublishResult(result);
      // A clean draft save is "done editing" — hand the seller back the Sell
      // overview (drafts + listings grid) instead of leaving them parked in
      // the editor. Saves with problems stay put so the fix-it highlight has
      // a form to point at.
      let savedClean = false;
      if (result.multi) {
        if (!result.published && mode === "draft") {
          toast(result.message || "Draft saved — find it anytime under Drafts.",
            { kind: "success" });
        }
        const issues = Object.values(result.results || {})
          .flatMap((res) => res.issues || []);
        savedClean = mode === "draft" && !result.published && !issues.length
          && !Object.values(result.results || {}).some((res) => !res.ok);
        if (!result.published && issues.length) {
          const first = fixTargetFor(issues);
          if (first) setFixTarget(first);
        }
      } else {
        // Live success swaps in the PublishedScreen; draft saves get a toast
        // so there's clear feedback even if the result banner is below the fold.
        if (!result.error && !result.published && mode === "draft") {
          toast(result.ebay_draft
            ? "Draft saved here and staged on your eBay account — publish it live when you're ready."
            : "Draft saved — find it anytime under Drafts.", { kind: "success" });
          savedClean = true;
        }
        if (result.error && result.issues && result.issues.length) {
          const first = fixTargetFor(result.issues);
          if (first) setFixTarget(first);
        }
      }
      // A live publish that did NOT go live has to say so out loud. Without
      // this the editor simply re-rendered itself: no screen change, no
      // toast, no error text — indistinguishable from the click not
      // registering. The result banner explains the details; this is the
      // part you can't miss.
      if (mode === "live" && !result.published) {
        // blockedReason, not result.message: eBay's catch-all for an
        // account-level hold blames the title, and this toast is the one piece
        // of the outcome a seller cannot miss.
        toast(blockedReason(result, isLive
          ? "The update didn't reach eBay — check the publish card for what to fix."
          : "That didn't go live — check the publish card for what to fix."),
        { kind: "error" });
      }
      // Reflect the outcome on the card immediately. loadListings is the
      // authority and lands a moment later, but a listing that just went
      // live must never still be sitting under Drafts while it does.
      //
      // The open SESSION carries a status too, and it was the one thing here
      // that never moved: it stayed "draft" for the rest of the visit, so the
      // dashboard went on offering "Continue <title>" for a listing that was
      // already live — the one screen that reads the session rather than the
      // listings cache. Both are updated, or neither should be.
      if (result.published) {
        patchListing(sessionId, { status: "published" });
        setSession((s) => (s && s.sessionId === sessionId
          ? { ...s, status: "published" } : s));
      }
      // The listing went live but our own record of it didn't move. Say it
      // plainly — the danger here is the seller publishing a second time.
      const recordWarning = result.record_warning
        || Object.values(result.results || {}).map((res) => res.record_warning)
          .find(Boolean);
      if (recordWarning) toast(recordWarning, { kind: "warning" });
      loadListings({ quiet: true });
      if (savedClean) openListings("drafts");
    } catch (e) {
      toast(`Publish error: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }), [collect, sessionId, setSession, loadListings, openListings, patchListing,
      toast, chipTargets, isLive]);

  // End (withdraw) the live listing everywhere it's live; it stays here as an
  // editable 'ended' record so it can be relisted later. eBay keeps its
  // original endpoint; other marketplaces go through the generic one.
  const endListing = useMemo(() => once("end-listing", async () => {
    setAiBusy(["Ending the listing…"]);
    try {
      const states = session?.listing?.marketplaces || {};
      const others = Object.entries(states)
        .filter(([key, st]) => key !== "ebay" && st?.status === "published")
        .map(([key]) => key);
      // A pre-multi live listing has an eBay id but no marketplaces entry.
      const ebayLive = states.ebay
        ? states.ebay.status === "published"
        : !!session?.listing?.ebay_listing_id;
      let message = "";
      // Where the record actually landed: "ended" unless eBay reveals the
      // listing had already SOLD. Both file under Inactive — a sale is
      // archived there rather than left relistable in place.
      let endedAs = "ended";
      for (const key of others) {
        try {
          const res = await postJson(`/api/${key}/end-listing`, { session_id: sessionId });
          message = res.message || message;
        } catch (e) {
          toast(`Couldn't end it on ${key}: ${e.message}`, { kind: "error" });
        }
      }
      if (ebayLive || !others.length) {
        const res = await postJson("/api/ebay/end-listing", { session_id: sessionId });
        message = res.message || message;
        if (res.status === "sold") endedAs = "sold";
      }
      toast(message || "Listing ended.", { kind: "success" });
      setSession((s) => (s ? { ...s, status: endedAs } : s));
      setPublishResult(null);
      loadListings({ quiet: true });
    } catch (e) {
      toast(`Couldn't end the listing: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }), [sessionId, session, setSession, loadListings, toast]);

  // ---------- the archive (a sold listing) ----------
  // A sold record is not a draft: it is what one finished sale was, so the
  // editor shows it read-only and the server refuses to publish it. Two
  // actions remain, and they are the two an archive needs.

  // Correct the sale's OWN numbers — what it went for and what it cost —
  // which are the inputs to the profit total and the only fields the
  // archive can still get wrong (eBay doesn't always report a sale amount).
  const saveSaleFigures = useMemo(() => once("save-sale-figures", async () => {
    const listing = collect();
    try {
      await postJson(`/api/save/${sessionId}`, listing);
      // The record keeps its sold status (the server never demotes one) —
      // patch the cached copy so the archive card's totals update at once.
      patchListing(sessionId, { listing });
      setSession((cur) => (cur ? { ...cur, listing } : cur));
      toast("Sale figures saved.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't save: ${e.message}`, { kind: "error" });
    }
  }), [collect, sessionId, patchListing, setSession, toast]);

  // Sell another one. The sold listing itself can never go back on eBay, so
  // this mints a NEW draft from its copy, specifics and surviving photos —
  // the archive record is left exactly as it is.
  const relist = useMemo(() => once("relist", async () => {
    setAiBusy(["Building a fresh listing from this one…"]);
    try {
      const res = await postJson(`/api/listings/${sessionId}/relist`, {});
      await loadListings({ quiet: true });
      setSession({
        sessionId: res.id, listing: res.listing, confidence: null, status: "draft",
      });
      setPublishResult(null);
      // Selling PURGES the photos to reclaim storage, so a relist usually
      // starts with none. Say so — a draft that silently lost its photos
      // reads as a bug, and the seller has to know to add them.
      toast(res.photos
        ? "New draft ready — edit and publish it whenever you like."
        : "New draft ready. Its photos went with the sale, so add fresh ones.",
        { kind: res.photos ? "success" : "warning" });
    } catch (e) {
      toast(`Couldn't start a relist: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }), [sessionId, loadListings, setSession, toast]);

  // Auto-fill eBay's category item specifics from the photos (fixed-value
  // aspects picked from eBay's allowed values), merged without clobbering
  // anything the seller already entered.
  const autofillSpecifics = useMemo(() => once("autofill-specifics", async () => {
    if (!form.category_id.trim()) {
      toast("Pick an eBay category first — specifics are per category.", { kind: "warning" });
      return;
    }
    setAiBusy(["Reading your photos for eBay item specifics…"]);
    try {
      const res = await postJson(`/api/autofill-specifics/${sessionId}`, {
        session_id: sessionId, listing: collect(), mode: "draft",
      });
      setForm((f) => ({ ...f, item_specifics: (res.item_specifics || []).map((s) => ({ ...s })) }));
      toast(res.added
        ? `Filled ${res.added} item specific${res.added === 1 ? "" : "s"} from your photos.`
        : "Nothing new to add — your item specifics already look complete.",
        { kind: "success" });
      // Saved server-side, so the record behind every card has changed — and
      // the drafts strip counts unreviewed specifics off it.
      invalidateListings();
    } catch (e) {
      toast(`Couldn't auto-fill specifics: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }), [form.category_id, sessionId, collect, setForm, invalidateListings, toast]);

  // ---------- fill in everything, one step before publishing ----------
  // The last thing worth doing to a listing before it goes live: one pass
  // that settles the eBay category if it is still missing, fills every item
  // specific the photos can answer, and double-checks the maker.
  //
  // This is the same enrichment the dashboard's "Enrich all" runs, moved to
  // where it can actually land. There it has to reach a listing eBay is
  // already showing — which means a resolvable category, photos still on the
  // server (an imported listing's live on eBay), a connected account, and a
  // ReviseItem that eBay accepts — and any one of those missing comes back
  // as "skipped" with the blanks still blank. Here the listing has not been
  // published yet: nothing to revise, the photos are right there, and the
  // answer is saved locally.
  //
  // It fills BLANKS. Anything already written is left exactly as it is, so
  // this is safe to press on a listing that is nearly finished.
  const fillInDetails = useMemo(() => once("enrich-listing", async () => {
    const before = collect();
    setAiBusy([
      "Reading your photos for everything eBay asks for…",
      "Filling in the details buyers filter by…",
      "Double-checking the maker…",
    ]);
    try {
      // A job, not a request: the fill is a vision call over every photo
      // plus a maker check, which routinely outlives the request deadline.
      // It used to finish and save on the server AFTER this had reported
      // "Couldn't fill in the details" -- a working feature reported broken.
      const start = await postJson(`/api/enrich/${sessionId}`, {
        session_id: sessionId, listing: before, mode: "draft",
      });
      const res = start.job_id ? await pollJob(start.job_id) : start;
      // The server merged onto the copy we just sent, so its answer is this
      // form plus the fills — adopting it whole cannot lose an edit.
      if (res.listing) {
        setSession((s) => (s ? { ...s, listing: res.listing } : s));
        setForm(fromListing(res.listing));
      }
      const gotCategory = !before.category_id.trim()
        && !!(res.listing?.category_id || "");
      const parts = [];
      if (gotCategory) parts.push("picked the eBay category");
      // Name what was filled. "Filled 3 details" is a count; the seller's
      // question is whether anything happened, and "Color: Blue, Size: M"
      // answers it where a number does not.
      const filled = (res.filled || []).map((f) => `${f.name}: ${f.value}`);
      if (filled.length) {
        const more = filled.length > 4 ? ` and ${filled.length - 4} more` : "";
        parts.push(`filled ${filled.slice(0, 4).join(", ")}${more}`);
      } else if (res.added) {
        parts.push(`filled ${res.added} detail${res.added === 1 ? "" : "s"}`);
      }
      toast(parts.length
        ? `${parts.join(" and ")} from your photos.`
            .replace(/^./, (c) => c.toUpperCase())
        // A pass that ran and found nothing is a real answer, not a failure —
        // and it is the seller's cue that the rest is theirs to write.
        : "Nothing more the photos could answer — anything still blank needs you.",
        { kind: res.added || gotCategory ? "success" : "info" });
      invalidateListings();
      return true;
    } catch (e) {
      // Warning, not error: the listing is untouched and still publishable
      // by hand. Nothing was lost and nothing is broken.
      toast(`Couldn't fill in the details: ${e.message}`, { kind: "warning" });
      return false;
    } finally {
      setAiBusy(null);
    }
  }), [collect, sessionId, setSession, setForm, invalidateListings, toast]);

  // Auto-populate item specifics right after a fresh AI identify (session has a
  // confidence score), so listings come SEO-ready with no manual step. Runs
  // once per session, and NOT when reopening a saved listing or a bulk item
  // (confidence is null there) so we never re-spend on an already-filled item.
  // Also NOT when the identify job already ran the server-side enrichment
  // (specificsAutofilled) — re-running the same vision passes seconds later
  // added nothing and charged the account a second time. The effect still
  // fires when the server pass was skipped (no category resolved, taxonomy
  // down), which is exactly when a client-side fill completes the listing.
  const autoFilledFor = useRef(null);
  useEffect(() => {
    const aspects = categoryMeta.aspects || [];
    const fresh = !!session?.confidence;
    if (!fresh || session?.specificsAutofilled || !aspects.length
      || autoFilledFor.current === sessionId) return;
    const missingRequired = aspects.some((a) => a.required && !getSpecific(a.name));
    if (!missingRequired) return;
    autoFilledFor.current = sessionId;
    autofillSpecifics();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryMeta.aspects, sessionId, session]);

  // ---------- what is stopping this listing from reaching eBay ----------
  // The editor's copy of the app-wide blocker list, with the one thing only
  // the editor knows: the category's required item specifics, loaded into
  // categoryMeta. Everything downstream — the card chips, the publish bar,
  // the jump buttons — reads this and nothing else, so a card can only be
  // flagged for a reason that genuinely blocks a publish.
  // Checked against the contract this listing will actually publish under:
  // "revise" for one that is already live on eBay (the Update button), "live"
  // for a draft or a relist. Editing a live listing was being held to the
  // create contract, so a seller could be locked out of fixing a typo by a
  // package weight eBay never asked them for.
  const blockers = useMemo(
    () => ebayBlockers(collect(), {
      targets: chipTargets,
      aspects: categoryMeta.aspects.length ? categoryMeta.aspects : null,
      conditions: categoryMeta.conditions.length ? categoryMeta.conditions : null,
      mode: isLive ? "revise" : "live",
    }),
    [collect, categoryMeta.aspects, categoryMeta.conditions, chipTargets, isLive]);

  // ---------- completion per workflow card ----------
  // Three states, and the distinction between the last two is the whole
  // point: "attention" means eBay refuses the listing until this card is
  // dealt with, "todo" means the card is simply empty and publishing is
  // unaffected. Only cards holding a blocker get "attention" — a description
  // the seller skipped, or specifics the category doesn't require, no longer
  // wear the same warning as a missing price.
  const completion = useMemo(() => {
    const blocked = new Set(blockers.map((b) => b.target));
    const state = (target, filled) =>
      blocked.has(target) ? "attention" : (filled ? "complete" : "todo");
    return {
      // An imported eBay listing has no local files — its photos live on eBay,
      // so image_urls counts as complete.
      photos: state("photos",
        (form.images || []).length > 0 || (form.image_urls || []).length > 0),
      title: state("title", form.title.trim()),
      category: state("category", form.category_id.trim()),
      specifics: state("specifics",
        form.item_specifics.some((s) => s.name.trim())),
      // Price and condition share the Pricing card; either one blocking
      // flags it.
      pricing: (blocked.has("price") || blocked.has("condition"))
        ? "attention"
        : (Number(form.price) > 0 || Number(form.auction_start_price) > 0
          ? "complete" : "todo"),
      shipping: state("weight", weightOz(form) > 0),
      // eBay doesn't require a description — we fall back to the title — so
      // an empty one is grey, never a warning, and never counts toward the
      // publish bar's blocker count.
      description: form.description.trim() ? "complete" : "todo",
    };
  }, [form, blockers]);

  return {
    sessionId, form, set, setForm, collect,
    isLive, isSold, ebayListingId, endListing,
    saveSaleFigures, relist,
    aiBusy, setAiBusy,
    marketTargets, toggleMarketTarget, chipTargets,
    publish, publishResult, setPublishResult, runPreflight,
    fixTarget, setFixTarget,
    refine,
    autofillSpecifics, fillInDetails,
    suggestCategories, catSuggestions, chooseCategory,
    checkMarketPrice, priceData, setPriceData,
    categoryMeta, loadCategoryMeta,
    getSpecific, getSpecificRow, getSpecificValues, upsertSpecific,
    toggleSpecificValue, confirmSpecific, confirmAllSpecifics,
    deleteImage, rotateImage, reorderImages, addImages, addingPhotos,
    imageVersions, bumpImageVersion,
    completion, blockers,
  };
}
