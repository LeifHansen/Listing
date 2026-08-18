import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, postJson } from "@/lib/api";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { once } from "@/lib/utils";
import { usePublishTargets } from "./publishShared";

/* All state + actions for the listing workflow. The form object mirrors the
   backend Listing model; item_specifics stays the single source of truth for
   both the free-form rows and the category-required aspect fields. */

const EMPTY = {
  title: "", subtitle: "", brand: "", price: "", purchase_price: "", quantity: 1,
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
  const { session, setSession, health, loadListings } = useApp();
  const { toast } = useToast();

  const [form, setForm] = useState(() => fromListing(session?.listing));
  const [aiBusy, setAiBusy] = useState(null); // string[] of friendly messages, or null
  const [publishResult, setPublishResult] = useState(null);
  const [fixTarget, setFixTarget] = useState(null); // which field group eBay flagged
  const [catSuggestions, setCatSuggestions] = useState(null);
  const [priceData, setPriceData] = useState(null);
  const [categoryMeta, setCategoryMeta] = useState({ conditions: [], aspects: [] });

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
  // Ended/sold records get the Publish action instead — the server relists
  // them as a fresh listing (eBay can't revise an ended item).
  const settled = session?.status === "ended" || session?.status === "sold";
  const isLive = !settled && (session?.status === "published" || session?.status === "live"
    || (session?.listing?.source || "") === "ebay");
  const ebayListingId = session?.listing?.ebay_listing_id
    || publishResult?.listing_id || "";

  // Re-seed the form whenever a different listing session is opened.
  const seededFor = useRef(sessionId);
  const reseeded = seededFor.current !== sessionId;
  useEffect(() => {
    if (seededFor.current !== sessionId) {
      seededFor.current = sessionId;
      setForm(fromListing(session?.listing));
      setPublishResult(null);
      setFixTarget(null);
      setCatSuggestions(null);
      setPriceData(null);
      setCategoryMeta({ conditions: [], aspects: [] });
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
      auction_start_price: form.auction_start_price === "" ? null : parseFloat(form.auction_start_price),
      quantity: parseInt(form.quantity || "1", 10),
      package_weight_lb: num(form.package_weight_lb),
      package_weight_oz: num(form.package_weight_oz),
      package_length_in: num(form.package_length_in),
      package_width_in: num(form.package_width_in),
      package_height_in: num(form.package_height_in),
      item_specifics: form.item_specifics
        .map((s) => ({ name: s.name.trim(), value: s.value.trim(), confidence: s.confidence || "" }))
        .filter((s) => s.name),
      images: form.images || [],
      currency: form.currency || "USD",
    };
  }, [form, session]);

  // ---------- item specifics (single source of truth) ----------
  const getSpecificRow = useCallback((name) => form.item_specifics.find(
    (s) => s.name.trim().toLowerCase() === name.toLowerCase()) || null,
  [form.item_specifics]);

  const getSpecific = useCallback(
    (name) => getSpecificRow(name)?.value || "", [getSpecificRow]);

  // A seller edit clears the AI confidence flag: the value is now theirs, so
  // neither the ✓ (AI high) nor the ⚠ (review) badge applies anymore.
  const upsertSpecific = useCallback((name, value) => {
    setForm((f) => {
      const specs = [...f.item_specifics];
      const i = specs.findIndex((s) => s.name.trim().toLowerCase() === name.toLowerCase());
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
      const specs = [...f.item_specifics];
      const i = specs.findIndex(
        (s) => s.name.trim().toLowerCase() === name.trim().toLowerCase());
      if (i < 0) return f;
      specs[i] = { ...specs[i], confidence: "" };
      return { ...f, item_specifics: specs };
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
      setCategoryMeta({ conditions: [], aspects: [] });
      return;
    }
    const [cond, asp] = await Promise.all([
      postJson("/api/item-conditions", { category_id: cid }).catch(() => ({ conditions: [] })),
      postJson("/api/item-aspects", { category_id: cid }).catch(() => ({ aspects: [] })),
    ]);
    const conditions = cond.conditions || [];
    setCategoryMeta({ conditions, aspects: asp.aspects || [] });
    // If the current condition isn't valid for this category, snap to the
    // first allowed one so we never submit a condition eBay rejects (25021).
    if (conditions.length) {
      setForm((f) => conditions.some((c) => c.enum === f.condition)
        ? f : { ...f, condition: conditions[0].enum });
    }
  }, [form.category_id, health.taxonomy_configured]);

  // Load meta when a session opens with a category already set. On a session
  // switch the form state is one render behind, so read the category straight
  // from the incoming listing rather than the (stale) form.
  useEffect(() => {
    if (!sessionId) return;
    const cid = reseeded ? session?.listing?.category_id : form.category_id;
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
      seededFor.current = sessionId; // keep the reseed guard in sync
      toast("Listing refined ✨", { kind: "success" });
      return true;
    } catch (e) {
      toast(`Refine error: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }), [collect, sessionId, setSession, toast]);

  // ---------- images ----------
  // Cache-bust per PHOTO, not globally: a global counter made one rotate
  // re-download every tile's full-size image — the main reason rotating felt
  // slow on a listing with many photos.
  const [imageVersions, setImageVersions] = useState({});
  const bumpImageVersion = useCallback((name) => {
    if (name) setImageVersions((m) => ({ ...m, [name]: (m[name] || 0) + 1 }));
  }, []);

  // One-tap 90° clockwise rotate; only the rotated tile refreshes.
  const rotateImage = useCallback(async (name) => {
    try {
      await postJson("/api/rotate-image", { session_id: sessionId, name });
      bumpImageVersion(name);
    } catch (e) {
      toast(`Couldn't rotate: ${e.message}`, { kind: "error" });
    }
  }, [sessionId, bumpImageVersion, toast]);

  // One-click by default; pass a confirmFn to gate it behind a dialog.
  // Optimistic: the tile disappears immediately and comes back only if the
  // server delete fails.
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
    setForm((f) => ({ ...f, images: f.images.filter((n) => n !== name) }));
    try {
      await postJson("/api/delete-image", { session_id: sessionId, name });
    } catch (e) {
      setForm((f) => ({ ...f, images: prev }));
      toast(`Couldn't delete the photo: ${e.message}`, { kind: "error" });
    }
  }, [form.images, sessionId, toast]);

  // Persist a new photo order. The FIRST image is the eBay gallery/hero photo,
  // so order matters — it must survive a reload and be what we publish. Update
  // the form + session immediately (optimistic) and save in the background.
  const reorderImages = useCallback((nextImages) => {
    const imgs = [...nextImages];
    setForm((f) => ({ ...f, images: imgs }));
    setSession((s) => (s ? { ...s, listing: { ...(s.listing || {}), images: imgs } } : s));
    if (!sessionId) return;
    postJson(`/api/save/${sessionId}`, { ...collect(), images: imgs }).catch(() => {});
  }, [collect, sessionId, setForm, setSession]);

  // Upload more photos onto this listing: optimize server-side, append the new
  // files to the image order, and persist.
  const [addingPhotos, setAddingPhotos] = useState(false);
  const addImages = useCallback(async (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length || !sessionId) return;
    setAddingPhotos(true);
    try {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      const res = await api(`/api/upload-more/${sessionId}`, { method: "POST", body: fd });
      const added = res.added || [];
      if (added.length) {
        const next = [...(form.images || []), ...added];
        setForm((f) => ({ ...f, images: next }));
        setSession((s) => (s ? { ...s, listing: { ...(s.listing || {}), images: next } } : s));
        postJson(`/api/save/${sessionId}`, { ...collect(), images: next }).catch(() => {});
        toast(`Added ${added.length} photo${added.length === 1 ? "" : "s"}.`, { kind: "success" });
      }
    } catch (e) {
      toast(`Couldn't add photos: ${e.message}`, { kind: "error" });
    } finally {
      setAddingPhotos(false);
    }
  }, [sessionId, form.images, collect, setForm, setSession, toast]);

  // ---------- pre-publish checklist ----------
  const runPreflight = useCallback(async () => {
    setAiBusy(["Checking everything the marketplaces require…"]);
    try {
      const body = { session_id: sessionId, listing: collect(), mode: "live" };
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
      const first = errors.find((x) => x.target && x.target !== "generic");
      if (first) setFixTarget(first.target);
    } catch (e) {
      toast(`Couldn't run the check: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }, [collect, sessionId, toast, chipTargets]);

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
      const body = { session_id: sessionId, listing, mode };
      // Only send `marketplaces` when the selector is visible and picked
      // something beyond bare eBay — everyone else stays on the legacy
      // single-eBay path with its byte-identical responses.
      if (chipTargets && !(chipTargets.length === 1 && chipTargets[0] === "ebay")) {
        body.marketplaces = chipTargets;
      }
      const result = await postJson("/api/publish", body);
      setPublishResult(result);
      if (result.multi) {
        if (!result.published && mode === "draft") {
          toast(result.message || "Draft saved — find it anytime under Drafts.",
            { kind: "success" });
        }
        const issues = Object.values(result.results || {})
          .flatMap((res) => res.issues || []);
        if (!result.published && issues.length) {
          const first = issues.find((x) => x.target && x.target !== "generic");
          if (first) setFixTarget(first.target);
        }
      } else {
        // Live success swaps in the PublishedScreen; draft saves get a toast
        // so there's clear feedback even if the result banner is below the fold.
        if (!result.error && !result.published && mode === "draft") {
          toast(result.ebay_draft
            ? "Draft saved here and staged on your eBay account — publish it live when you're ready."
            : "Draft saved — find it anytime under Drafts.", { kind: "success" });
        }
        if (result.error && result.issues && result.issues.length) {
          const first = result.issues.find((x) => x.target && x.target !== "generic");
          if (first) setFixTarget(first.target);
        }
      }
      loadListings({ quiet: true });
    } catch (e) {
      toast(`Publish error: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }), [collect, sessionId, setSession, loadListings, toast, chipTargets]);

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
      // Where the record actually landed: "ended" (Inactive) unless eBay
      // reveals the listing had already SOLD — then it files under Sold.
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
    } catch (e) {
      toast(`Couldn't auto-fill specifics: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }), [form.category_id, sessionId, collect, setForm, toast]);

  // Auto-populate item specifics right after a fresh AI identify (session has a
  // confidence score), so listings come SEO-ready with no manual step. Runs
  // once per session, and NOT when reopening a saved listing or a bulk item
  // (confidence is null there) so we never re-spend on an already-filled item.
  const autoFilledFor = useRef(null);
  useEffect(() => {
    const aspects = categoryMeta.aspects || [];
    const fresh = !!session?.confidence;
    if (!fresh || !aspects.length || autoFilledFor.current === sessionId) return;
    const missingRequired = aspects.some((a) => a.required && !getSpecific(a.name));
    if (!missingRequired) return;
    autoFilledFor.current = sessionId;
    autofillSpecifics();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryMeta.aspects, sessionId, session]);

  // ---------- completion per workflow card ----------
  const completion = useMemo(() => {
    const requiredAspects = categoryMeta.aspects.filter((a) => a.required);
    const missingAspects = requiredAspects.filter((a) => !getSpecific(a.name));
    const weight = (parseFloat(form.package_weight_lb) || 0) * 16
      + (parseFloat(form.package_weight_oz) || 0);
    return {
      // An imported eBay listing has no local files — its photos live on eBay,
      // so image_urls counts as complete.
      photos: ((form.images || []).length > 0 || (form.image_urls || []).length > 0)
        ? "complete" : "attention",
      title: form.title.trim() ? "complete" : "attention",
      category: form.category_id.trim() ? "complete" : "attention",
      specifics: missingAspects.length > 0 ? "attention"
        : (form.item_specifics.some((s) => s.name.trim()) ? "complete" : "todo"),
      pricing: (
        form.listing_format === "AUCTION"
          ? Number(form.auction_start_price) > 0
          : form.listing_format === "AUCTION_BIN"
            ? Number(form.auction_start_price) > 0 && Number(form.price) > 0
            : Number(form.price) > 0
      ) ? "complete" : "attention",
      shipping: weight > 0 ? "complete" : "attention",
      // eBay doesn't require a description (we fall back to the title), so an
      // empty one is "todo" (grey), never "attention" — it must not count
      // toward the publish bar's "N fields left to finish".
      description: form.description.trim() ? "complete" : "todo",
    };
  }, [form, categoryMeta.aspects, getSpecific]);

  return {
    sessionId, form, set, setForm, collect,
    isLive, ebayListingId, endListing,
    aiBusy, setAiBusy,
    marketTargets, toggleMarketTarget, chipTargets,
    publish, publishResult, setPublishResult, runPreflight,
    fixTarget, setFixTarget,
    refine,
    autofillSpecifics,
    suggestCategories, catSuggestions, chooseCategory,
    checkMarketPrice, priceData, setPriceData,
    categoryMeta, loadCategoryMeta,
    getSpecific, getSpecificRow, upsertSpecific,
    confirmSpecific, confirmAllSpecifics,
    deleteImage, rotateImage, reorderImages, addImages, addingPhotos,
    imageVersions, bumpImageVersion,
    completion,
  };
}
