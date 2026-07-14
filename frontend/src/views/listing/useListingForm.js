import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, postJson } from "@/lib/api";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { once } from "@/lib/utils";

/* All state + actions for the listing workflow. The form object mirrors the
   backend Listing model; item_specifics stays the single source of truth for
   both the free-form rows and the category-required aspect fields. */

const EMPTY = {
  title: "", subtitle: "", brand: "", price: "", quantity: 1,
  package_weight_lb: "", package_weight_oz: "",
  package_length_in: "", package_width_in: "", package_height_in: "",
  fulfillment_policy_id: "",
  best_offer_enabled: false, best_offer_min: "", best_offer_accept: "",
  promote_enabled: false, promote_recommended: true, promote_percent: "",
  category_suggestion: "", category_id: "", condition: "USED_GOOD",
  condition_description: "", description: "", item_specifics: [],
  images: [], currency: "USD", missing_info: [],
};

// System-wide package defaults, overridable per user in Settings.
const SYSTEM_PKG_DEFAULTS = {
  default_weight_lb: 2, default_weight_oz: 8,
  default_length_in: 8, default_width_in: 8, default_height_in: 8,
};

function pkgDefaults(prefs) {
  return { ...SYSTEM_PKG_DEFAULTS, ...(prefs || {}) };
}

function fromListing(l, prefs) {
  if (!l) return { ...EMPTY };
  const d = pkgDefaults(prefs);
  // The AI's measurements win; the user's defaults fill what it left blank.
  const noWeight = !(l.package_weight_lb || l.package_weight_oz);
  const noDims = !(l.package_length_in || l.package_width_in || l.package_height_in);
  return {
    ...EMPTY,
    ...l,
    price: l.price != null ? l.price : "",
    best_offer_min: l.best_offer_min != null ? l.best_offer_min : "",
    best_offer_accept: l.best_offer_accept != null ? l.best_offer_accept : "",
    promote_percent: l.promote_percent != null ? l.promote_percent : "",
    quantity: l.quantity || 1,
    package_weight_lb: noWeight ? (d.default_weight_lb || "") : (l.package_weight_lb || ""),
    package_weight_oz: noWeight ? (d.default_weight_oz || "") : (l.package_weight_oz || ""),
    package_length_in: noDims ? (d.default_length_in || "") : (l.package_length_in || ""),
    package_width_in: noDims ? (d.default_width_in || "") : (l.package_width_in || ""),
    package_height_in: noDims ? (d.default_height_in || "") : (l.package_height_in || ""),
    item_specifics: (l.item_specifics || []).map((s) => ({ ...s })),
    images: l.images || [],
  };
}

export function useListingForm() {
  const { session, setSession, health, loadListings, setView, user } = useApp();
  const { toast } = useToast();

  const [form, setForm] = useState(() => fromListing(session?.listing, user?.prefs));
  const [aiBusy, setAiBusy] = useState(null); // string[] of friendly messages, or null
  const [publishResult, setPublishResult] = useState(null);
  const [fixTarget, setFixTarget] = useState(null); // which field group eBay flagged
  const [cardFocus, setCardFocus] = useState({ id: null, n: 0 }); // banner → card
  const [catSuggestions, setCatSuggestions] = useState(null);
  const [priceData, setPriceData] = useState(null);
  const [categoryMeta, setCategoryMeta] = useState({ conditions: [], aspects: [] });

  const sessionId = session?.sessionId;

  // Re-seed the form whenever a different listing session is opened.
  const seededFor = useRef(sessionId);
  const reseeded = seededFor.current !== sessionId;
  useEffect(() => {
    if (seededFor.current !== sessionId) {
      seededFor.current = sessionId;
      setForm(fromListing(session?.listing, user?.prefs));
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
      quantity: parseInt(form.quantity || "1", 10),
      package_weight_lb: num(form.package_weight_lb),
      package_weight_oz: num(form.package_weight_oz),
      package_length_in: num(form.package_length_in),
      package_width_in: num(form.package_width_in),
      package_height_in: num(form.package_height_in),
      best_offer_enabled: !!form.best_offer_enabled,
      best_offer_min: form.best_offer_min === "" ? null : parseFloat(form.best_offer_min),
      best_offer_accept: form.best_offer_accept === "" ? null : parseFloat(form.best_offer_accept),
      promote_enabled: !!form.promote_enabled,
      promote_recommended: !!form.promote_recommended,
      promote_percent: form.promote_percent === "" ? null : parseFloat(form.promote_percent),
      item_specifics: form.item_specifics
        .map((s) => ({ name: s.name.trim(), value: s.value.trim() }))
        .filter((s) => s.name),
      images: form.images || [],
      currency: form.currency || "USD",
    };
  }, [form, session]);

  // ---------- item specifics (single source of truth) ----------
  const getSpecific = useCallback((name) => {
    const row = form.item_specifics.find(
      (s) => s.name.trim().toLowerCase() === name.toLowerCase());
    return row ? row.value : "";
  }, [form.item_specifics]);

  const upsertSpecific = useCallback((name, value) => {
    setForm((f) => {
      const specs = [...f.item_specifics];
      const i = specs.findIndex((s) => s.name.trim().toLowerCase() === name.toLowerCase());
      if (i >= 0) specs[i] = { ...specs[i], value };
      else if (value) specs.push({ name, value });
      return { ...f, item_specifics: specs };
    });
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
      const items = res.suggestions || [];
      setCatSuggestions({ items });
      return items;
    } catch (e) {
      setCatSuggestions({ error: `Couldn't fetch categories: ${e.message}` });
      return [];
    }
  }, [collect, health.taxonomy_configured]);

  // Suggestions load themselves when a listing opens (no button to press);
  // if the AI didn't already pick a category, the best guess is selected.
  const suggestedFor = useRef(null);
  useEffect(() => {
    if (!sessionId || !health.taxonomy_configured) return;
    if (suggestedFor.current === sessionId) return;
    if (!(form.title.trim() || form.brand.trim())) return;
    suggestedFor.current = sessionId;
    const hadCategory = !!form.category_id.trim();
    (async () => {
      const items = await suggestCategories();
      if (!hadCategory && items && items.length) {
        setForm((f) => f.category_id.trim() ? f : {
          ...f,
          category_id: items[0].category_id,
          category_suggestion: items[0].path || items[0].category_name,
        });
        loadCategoryMeta(items[0].category_id);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, health.taxonomy_configured, form.title, form.brand]);

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
  const [imageVersion, setImageVersion] = useState(0); // cache-bust after edits

  const deleteImage = useCallback(async (name, confirmFn) => {
    if ((form.images || []).length <= 1) {
      toast("A listing needs at least one photo — add another before deleting this one.", { kind: "warning" });
      return;
    }
    if (!(await confirmFn({
      title: "Delete this photo?",
      message: "This can't be undone.",
      confirmLabel: "Delete",
      danger: true,
    }))) return;
    try {
      await postJson("/api/delete-image", { session_id: sessionId, name });
      setForm((f) => ({ ...f, images: f.images.filter((n) => n !== name) }));
    } catch (e) {
      toast(`Couldn't delete the photo: ${e.message}`, { kind: "error" });
    }
  }, [form.images, sessionId, toast]);

  // ---------- pre-publish checklist ----------
  const runPreflight = useCallback(async () => {
    setAiBusy(["Checking everything eBay requires…"]);
    try {
      const res = await postJson("/api/publish-preflight", {
        session_id: sessionId, listing: collect(), mode: "live",
      });
      const errors = (res.issues || []).filter((i) => i.level !== "warn");
      setPublishResult({
        preflight: true,
        error: errors.length > 0,
        issues: res.issues || [],
        message: errors.length
          ? `Not quite ready — ${errors.length} thing${errors.length === 1 ? "" : "s"} to fix before eBay will accept it:`
          : "All checks passed — this listing is ready to publish. 🎉",
      });
      const first = errors.find((x) => x.target && x.target !== "generic");
      if (first) setFixTarget(first.target);
    } catch (e) {
      toast(`Couldn't run the check: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }, [collect, sessionId, toast]);

  // ---------- publish ----------
  // publishing drives the full-screen "pushing to eBay" overlay; celebration
  // drives the success screen once eBay confirms the listing is live.
  const [publishing, setPublishing] = useState(null); // "draft" | "live" | null
  const [celebration, setCelebration] = useState(null); // { listingId }

  const publish = useMemo(() => once("publish", async (mode) => {
    setFixTarget(null);
    setPublishResult(null);
    setPublishing(mode);
    try {
      const listing = collect();
      setSession((s) => ({ ...s, listing }));
      // Publishing legitimately takes a while (photos + several eBay calls) —
      // give it a generous ceiling instead of hanging forever.
      const result = await postJson("/api/publish", {
        session_id: sessionId, listing, mode,
      }, { timeoutMs: 150_000 });
      setPublishResult(result);
      if (result.error && result.issues && result.issues.length) {
        const first = result.issues.find((x) => x.target && x.target !== "generic");
        if (first) setFixTarget(first.target);
      }
      loadListings({ quiet: true });
      if (result.published) {
        setCelebration({ listingId: result.listing_id || "" });
      }
    } catch (e) {
      if (e.timedOut) {
        // The publish may have SUCCEEDED server-side even though the response
        // never arrived — steer away from a blind retry (duplicate risk).
        toast(
          "eBay is taking unusually long. The listing may still have gone through — check Listings (or your eBay account) in a minute before trying again.",
          { kind: "warning", ttl: 12000 },
        );
        loadListings({ quiet: true });
      } else {
        toast(`Publish error: ${e.message}`, { kind: "error" });
      }
    } finally {
      setPublishing(null);
    }
  }), [collect, sessionId, setSession, loadListings, toast]);

  // Success screen dismissed: the listing is done being edited — close the
  // workflow and land on Listings, where it wears its "Live on eBay" badge
  // (and is gone from Drafts).
  const finishCelebration = useCallback(() => {
    setCelebration(null);
    setSession(null);
    setView("listings");
  }, [setSession, setView]);

  // ---------- completion per workflow card ----------
  const completion = useMemo(() => {
    const requiredAspects = categoryMeta.aspects.filter((a) => a.required);
    const missingAspects = requiredAspects.filter((a) => !getSpecific(a.name));
    const weight = (parseFloat(form.package_weight_lb) || 0) * 16
      + (parseFloat(form.package_weight_oz) || 0);
    return {
      photos: (form.images || []).length > 0 ? "complete" : "attention",
      title: form.title.trim() ? "complete" : "attention",
      category: form.category_id.trim() ? "complete" : "attention",
      specifics: missingAspects.length > 0 ? "attention"
        : (form.item_specifics.some((s) => s.name.trim()) ? "complete" : "todo"),
      pricing: form.price !== "" && Number(form.price) > 0 ? "complete" : "attention",
      shipping: weight > 0 ? "complete" : "attention",
      description: form.description.trim() ? "complete" : "attention",
    };
  }, [form, categoryMeta.aspects, getSpecific]);

  const focusCard = useCallback((id) => {
    setCardFocus((c) => ({ id, n: c.n + 1 }));
  }, []);

  return {
    sessionId, form, set, setForm, collect,
    cardFocus, focusCard,
    aiBusy, setAiBusy,
    publish, publishResult, setPublishResult, runPreflight,
    publishing, celebration, finishCelebration,
    fixTarget, setFixTarget,
    refine,
    suggestCategories, catSuggestions, chooseCategory,
    checkMarketPrice, priceData, setPriceData,
    categoryMeta, loadCategoryMeta,
    getSpecific, upsertSpecific,
    deleteImage, imageVersion, setImageVersion,
    completion,
  };
}
