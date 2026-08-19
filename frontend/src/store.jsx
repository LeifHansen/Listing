import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import { api, postJson, downscaleAllForUpload } from "@/lib/api";
import { storeToken } from "@/lib/platform";
import { useToast } from "@/components/ui/Toaster";

/* Central app state, ported from the original app.js:
   auth session, eBay connection, server health, theme, navigation, the
   listing being worked on, and the saved-listings cache. */

const AppContext = createContext(null);

export function AppProvider({ children }) {
  const { toast } = useToast();

  // ---------- theme ----------
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"));
  const toggleDark = useCallback(() => {
    setDark((d) => {
      const next = !d;
      document.documentElement.classList.toggle("dark", next);
      try { localStorage.setItem("quickflip-theme", next ? "dark" : "light"); } catch (e) {}
      return next;
    });
  }, []);

  // ---------- navigation ----------
  const [view, setView] = useState("dashboard");
  // Which tab of the listings pipeline is showing. Deep links (a dashboard
  // tile, a task row) set it and jump: openListings("drafts"). The pipeline
  // lives on the merged Sell screen now, so opening it clears any open
  // editor session (same as the Sell nav's startNew always did) and records
  // the requested tab so the screen can scroll to the right section.
  const [listingsTab, setListingsTab] = useState("active");
  const listingsJumpRef = useRef(null);
  const openListings = useCallback((tab) => {
    if (tab) setListingsTab(tab);
    listingsJumpRef.current = tab || "active";
    setSession(null);
    setView("new");
  }, []);

  // ---------- server health ----------
  const [health, setHealth] = useState({
    anthropic_configured: false, ebay_configured: false, taxonomy_configured: false,
  });
  const loadHealth = useCallback(async () => {
    try {
      const h = await api("/api/health");
      setHealth({ ...h, _loaded: true });
    } catch (e) { /* banner stays hidden until we know */ }
  }, []);

  // ---------- auth ----------
  const [user, setUser] = useState(null);
  const [authOpen, setAuthOpen] = useState(false);
  // Action to resume after a login that interrupted it (e.g. Shop-mode "Buy").
  const afterLogin = useRef(null);

  const loadAuth = useCallback(async () => {
    try {
      const res = await api("/api/auth/me");
      setUser(res.user);
    } catch (e) { setUser(null); }
  }, []);

  const openAuth = useCallback((resume) => {
    afterLogin.current = resume || null;
    setAuthOpen(true);
  }, []);

  const logout = useCallback(async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {}
    storeToken(null); // native shell's bearer token — no-op on the web
    setUser(null);
    loadEbayStatus();
  }, []);

  // ---------- AI tokens (monetization) ----------
  // Balance + catalog from /api/tokens. `enabled: false` (dev/self-hosted
  // installs) hides the whole surface. The dialog opens from the TopBar chip
  // or automatically when any AI call comes back 402 "out of tokens".
  const [tokens, setTokens] = useState({ enabled: false, total: 0, packs: [], costs: {} });
  const [tokensOpen, setTokensOpen] = useState(false);
  const loadTokens = useCallback(async () => {
    try { setTokens(await api("/api/tokens")); } catch (e) { /* keep previous */ }
  }, []);

  // ---------- eBay connection ----------
  const [ebay, setEbay] = useState({
    connected: false, env: "", username: "", email: "", oauth_ready: false,
    oauth_missing: [], labels_enabled: false,
  });
  const [policiesData, setPoliciesData] = useState(null); // cached /api/ebay/policies

  const loadEbayStatus = useCallback(async () => {
    try {
      const s = await api("/api/ebay/status");
      setEbay({
        connected: !!s.connected,
        env: s.env || "",
        username: s.username || "",
        email: s.email || "",
        oauth_ready: !!s.oauth_ready,
        oauth_missing: s.oauth_missing || [],
        labels_enabled: !!s.labels_enabled,
      });
    } catch (e) { /* keep previous */ }
  }, []);

  // Publishing is live if EITHER the user connected their eBay account or the
  // server has env-level credentials.
  const canPublishLive = ebay.connected || health.ebay_configured;

  // ---------- marketplace roster (eBay + Etsy + Depop + ...) ----------
  // Every registered marketplace with this user's connection state, from
  // GET /api/marketplaces. The `ebay` object above stays the eBay fast-path
  // every existing consumer uses; this roster powers the generic Settings
  // cards and the publish-target chips.
  const [marketplaces, setMarketplaces] = useState([]);
  const loadMarketplaces = useCallback(async () => {
    try {
      const res = await api("/api/marketplaces");
      setMarketplaces(res.marketplaces || []);
    } catch (e) { /* keep previous */ }
  }, []);
  const connectedMarketplaces = useMemo(
    () => marketplaces.filter((m) => m.connected),
    [marketplaces]);

  // ---------- notifications (sold alerts) ----------
  // Polled while logged in so "your item sold" reaches the seller without a
  // refresh. The bell in the TopBar renders items + the unread badge; a sold
  // notification's primary action opens the shipping dialog for that listing.
  const [notifications, setNotifications] = useState({ items: [], unread: 0 });
  const loadNotifications = useCallback(async () => {
    if (!user) { setNotifications({ items: [], unread: 0 }); return; }
    try {
      const res = await api("/api/notifications");
      setNotifications({ items: res.notifications || [], unread: res.unread || 0 });
    } catch (e) { /* keep previous */ }
  }, [user]);
  useEffect(() => {
    loadNotifications();
    if (!user) return undefined;
    const t = setInterval(loadNotifications, 60000);
    return () => clearInterval(t);
  }, [user, loadNotifications]);
  const markNotificationsRead = useCallback(async (ids) => {
    // Optimistic: the badge clears instantly, the server catches up.
    setNotifications((n) => ({
      items: n.items.map((i) => (
        !ids || ids.includes(i.id) ? { ...i, read: true } : i)),
      unread: ids ? Math.max(0, n.unread - ids.length) : 0,
    }));
    try {
      await postJson("/api/notifications/read", ids ? { ids } : { all: true });
    } catch (e) { /* the next poll re-syncs */ }
  }, []);

  // ---------- shipping dialog (sold → label) ----------
  // null = closed; { listingId } = one listing's order; {} = all awaiting.
  const [shipping, setShipping] = useState(null);
  const openShipping = useCallback(
    (listingId) => setShipping({ listingId: listingId || null }), []);
  const closeShipping = useCallback(() => setShipping(null), []);

  // ---------- saved listings cache ----------
  const [listingsState, setListingsState] = useState({
    loaded: false, loading: false, authed: true, dbConfigured: true, items: [],
  });
  // eBay views/watchers per live listing, keyed by our listing record id.
  const [metricsById, setMetricsById] = useState({});
  // Whether eBay's traffic report (views/impressions) was actually readable —
  // so the UI can say "we couldn't ask" instead of showing everything as 0.
  const [metricsStatus, setMetricsStatus] = useState(
    { trafficOk: false, needsReconnect: false });

  const loadListings = useCallback(async ({ quiet = false } = {}) => {
    // `quiet` suppresses the spinner for background refreshes — but the FIRST
    // load is quiet too (the boot effect), and suppressing it there meant the
    // skeletons never rendered and every visit flashed "No listings yet"
    // before the store appeared. A load that has nothing to show yet always
    // counts as loading.
    setListingsState((s) => ({ ...s, loading: !quiet || !s.loaded }));
    try {
      const res = await api("/api/listings");
      setListingsState({
        loaded: true,
        loading: false,
        authed: !!res.authed,
        dbConfigured: !!(res.db && res.db.configured),
        dbConnected: !!(res.db && res.db.connected),
        items: res.listings || [],
      });
    } catch (e) {
      setListingsState((s) => ({ ...s, loading: false, loaded: true }));
      if (!quiet) toast(`Couldn't load listings: ${e.message}`, { kind: "error" });
    }
  }, [toast]);

  // ---------- eBay store mirror ----------
  // The app mirrors the seller's WHOLE eBay store, not just what it created:
  // once eBay is connected, the first load imports every active listing and
  // reconciles live statuses — so the dashboard and Listings ARE the store.
  // Runs once per app session; `syncStore({ force: true })` re-runs it (the
  // "Sync with eBay" button).
  const [storeSync, setStoreSync] = useState({
    syncing: false, lastSynced: null, error: null,
  });
  const syncedOnce = useRef(false);
  const lastReconcile = useRef(0); // ms — throttles the quiet status re-checks
  const syncStore = useCallback(async ({ force = false } = {}) => {
    if (!user || !ebay.connected) return null;
    if (syncedOnce.current && !force) return null;
    syncedOnce.current = true;
    setStoreSync((s) => ({ ...s, syncing: true, error: null }));
    try {
      const res = await postJson("/api/ebay/import-listings", {});
      // Status reconciliation (sold/ended) can lag behind — fold it in quietly.
      // force: this is the deliberate "Sync with eBay" path (or first load),
      // so it may run the full per-item sweep; the background heartbeat below
      // deliberately does not.
      lastReconcile.current = Date.now();
      postJson("/api/ebay/sync-listings", { force: true })
        .then((r) => { if (r.changed) loadListings({ quiet: true }); })
        .catch(() => {});
      await loadListings({ quiet: true });
      setStoreSync({ syncing: false, lastSynced: Date.now(), error: null });
      return res;
    } catch (e) {
      setStoreSync({ syncing: false, lastSynced: null, error: e.message });
      return { error: e.message };
    }
  }, [user, ebay.connected, loadListings]);
  useEffect(() => { syncStore(); }, [syncStore]);

  // The mirror import runs once per app session — but a tab (or the native
  // shell) can stay open for days, and a listing that ends or sells ON eBay
  // in that time would sit under Active until a manual sync. Quietly re-check
  // live statuses when the app comes back into focus and on a slow heartbeat
  // while it stays visible, so those records slide into Inactive/Sold on
  // their own.
  //
  // Cadence is a QUOTA decision, not a UI one: every check fans out real eBay
  // calls server-side, and eBay's Trading API is capped per DAY for the whole
  // app. At the original 10-minute beat one open tab spent the entire daily
  // allowance by itself, and once it ran out every call failed — including
  // the one that publishes a listing. Half-hourly (and never inside 20
  // minutes) keeps a day's background checks in the dozens; the server also
  // skips the expensive per-item sweeps for these unforced calls, so what
  // runs here is the cheap finished-list pass that actually moves records.
  const reconcileStatuses = useCallback(async () => {
    if (!user || !ebay.connected || document.hidden) return;
    if (Date.now() - lastReconcile.current < 20 * 60000) return;
    lastReconcile.current = Date.now();
    try {
      const r = await postJson("/api/ebay/sync-listings", {});
      if (r.changed) loadListings({ quiet: true });
    } catch (e) { /* best-effort — the next pass tries again */ }
  }, [user, ebay.connected, loadListings]);
  useEffect(() => {
    const onVisible = () => { if (!document.hidden) reconcileStatuses(); };
    document.addEventListener("visibilitychange", onVisible);
    const t = setInterval(reconcileStatuses, 30 * 60000);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      clearInterval(t);
    };
  }, [reconcileStatuses]);

  // ---------- the listing being worked on ----------
  // session: { sessionId, listing, confidence } — null until AI identify runs
  // or a saved listing is opened.
  const [session, setSession] = useState(null);

  // Drafts the user has set aside. A skipped draft still lives in Drafts (and
  // can be un-skipped from its card), but the post-publish queue never offers
  // it as the "next" one to work on. In-memory on purpose: a reload clears the
  // skips, so nothing is ever permanently hidden.
  const [skippedDraftIds, setSkippedDraftIds] = useState(() => new Set());
  const toggleSkipDraft = useCallback((id) => {
    setSkippedDraftIds((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const startNew = useCallback(() => {
    setSession(null);
    setView("new");
  }, []);

  const openListing = useCallback(async (id) => {
    try {
      const rec = await api(`/api/listings/${id}`);
      // status rides along so the workflow knows a live listing is being
      // REVISED (Update Live Listing / End listing) rather than published.
      setSession({ sessionId: rec.id, listing: rec.listing, confidence: null, status: rec.status });
      setView("new");
    } catch (e) {
      toast(`Couldn't open listing: ${e.message}`, { kind: "error" });
    }
  }, [toast]);

  const deleteListing = useCallback(async (id) => {
    try {
      await api(`/api/listings/${id}`, { method: "DELETE" });
      // Drop it from the cache immediately (snappy), and close it if open.
      setListingsState((s) => ({ ...s, items: s.items.filter((i) => i.id !== id) }));
      setSession((cur) => (cur && cur.sessionId === id ? null : cur));
      toast("Listing deleted.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't delete: ${e.message}`, { kind: "error" });
    }
  }, [toast]);

  // Mass delete (drafts): one request for the whole selection.
  const bulkDeleteListings = useCallback(async (ids) => {
    try {
      const res = await postJson("/api/listings/bulk-delete", { ids });
      const gone = new Set(res.deleted || []);
      setListingsState((s) => ({ ...s, items: s.items.filter((i) => !gone.has(i.id)) }));
      setSession((cur) => (cur && gone.has(cur.sessionId) ? null : cur));
      toast(`Deleted ${gone.size} listing${gone.size === 1 ? "" : "s"}.`
        + (res.skipped?.length ? ` ${res.skipped.length} couldn't be removed.` : ""),
        { kind: res.skipped?.length ? "warning" : "success" });
      return true;
    } catch (e) {
      toast(`Couldn't delete: ${e.message}`, { kind: "error" });
      return false;
    }
  }, [toast]);

  // ---------- active bulk job (survives navigation + reload) ----------
  // { jobId } — persisted so leaving the progress screen (or a reload)
  // never strands a running batch. Completed items also auto-save to Drafts.
  const [activeBulk, setActiveBulk] = useState(() => {
    try {
      const raw = localStorage.getItem("quickflip-bulk");
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  });
  const startBulk = useCallback((jobId) => {
    const b = { jobId };
    setActiveBulk(b);
    try { localStorage.setItem("quickflip-bulk", JSON.stringify(b)); } catch (e) {}
    setView("new");
  }, []);
  // A batch begins when the seller hits the button, not when the server hands
  // back a job id: uploading a big pile takes seconds, and until now those
  // seconds were spent still sitting on the Sell tab with the listings in
  // view. Parking a job-less entry flips straight to the batch screen, which
  // shows "Uploading your photo pile…" until the id arrives. Not persisted —
  // there is no job to resume yet.
  const beginBulk = useCallback(() => {
    setActiveBulk({ jobId: null });
    setView("new");
  }, []);
  // The pile of a batch whose upload never got off the ground. The uploader
  // unmounts the moment the batch screen takes over, so without handing the
  // photos back the seller would return to an empty drop zone and have to
  // pick every one of them again.
  const [bulkRetry, setBulkRetry] = useState(null); // { files, removeBg }
  const clearBulkRetry = useCallback(() => setBulkRetry(null), []);
  // Job finished: stop persisting (a reload shouldn't restore a done batch) but
  // keep it in memory so the results stay on screen until the user moves on.
  const bulkSettled = useCallback(() => {
    try { localStorage.removeItem("quickflip-bulk"); } catch (e) {}
  }, []);
  const clearBulk = useCallback(() => {
    setActiveBulk(null);
    try { localStorage.removeItem("quickflip-bulk"); } catch (e) {}
  }, []);
  // The whole bulk upload — screen flip first, then the slow part. It lives
  // here rather than in the uploader because that flip unmounts the uploader
  // while the downscale + POST are still running.
  const runBulkUpload = useCallback(async (files, removeBg) => {
    beginBulk();
    try {
      const prepped = await downscaleAllForUpload(files.map((f) => f.file));
      const fd = new FormData();
      prepped.forEach((f) => fd.append("files", f));
      fd.append("remove_bg", removeBg ? "true" : "false");
      const { job_id } = await api("/api/bulk/upload", { method: "POST", body: fd });
      files.forEach((f) => URL.revokeObjectURL(f.url));
      startBulk(job_id);
    } catch (e) {
      // Back to the uploader with the pile intact, so retrying is one click.
      setBulkRetry({ files, removeBg });
      clearBulk();
      toast(`Bulk upload failed: ${e.message}`, { kind: "error" });
    }
  }, [beginBulk, startBulk, clearBulk, toast]);

  // ---------- OAuth redirect landing (eBay + generic marketplaces) ----------
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const e = params.get("ebay");
    if (e === "connected") toast("eBay connected! You can now publish real listings.", { kind: "success" });
    else if (e === "error") toast("eBay connection failed. Please try again.", { kind: "error" });
    // Generic marketplaces land on ?connected=etsy / ?connect_error=etsy.
    const ok = params.get("connected");
    const bad = params.get("connect_error");
    const label = (k) => k ? k.charAt(0).toUpperCase() + k.slice(1) : "";
    if (ok) {
      toast(`${label(ok)} connected! You can now cross-post listings there.`, { kind: "success" });
      loadMarketplaces();
    } else if (bad) {
      toast(`${label(bad)} connection failed. Please try again.`, { kind: "error" });
    }
    if (e || ok || bad) history.replaceState({}, "", window.location.pathname);
  }, [toast, loadMarketplaces]);

  // ---------- token purchase redirect landing ----------
  // Stripe Checkout sends the buyer back with ?tokens=success&session_id=…;
  // confirm credits the pack even if the webhook hasn't landed (idempotent
  // server-side, so webhook + confirm can both run).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const t = params.get("tokens");
    if (!t) return;
    history.replaceState({}, "", window.location.pathname);
    if (t === "cancelled") { toast("Purchase cancelled — no charge was made."); return; }
    if (t !== "success") return;
    const sessionId = params.get("session_id") || "";
    (async () => {
      try {
        const res = await api(`/api/tokens/confirm?session_id=${encodeURIComponent(sessionId)}`);
        toast(`Payment received — ${res.tokens} tokens added to your account!`, { kind: "success" });
      } catch (e) {
        toast(`Purchase confirmation is still processing: ${e.message}`, { kind: "warning" });
      }
      loadTokens();
    })();
  }, [toast, loadTokens]);

  // Any AI call that 402s for tokens pops the buy dialog (see lib/api.js).
  useEffect(() => {
    const onNeeded = () => { loadTokens(); setTokensOpen(true); };
    window.addEventListener("tokens:needed", onNeeded);
    return () => window.removeEventListener("tokens:needed", onNeeded);
  }, [loadTokens]);

  // Balance changes with login state; it also refreshes when the dialog opens.
  useEffect(() => { loadTokens(); }, [user, loadTokens]);

  // Refresh the balance when the app regains focus. In the native shell a
  // token purchase happens in the system browser (App Store rules), so the
  // moment of return IS the moment the balance changed; on the web it just
  // keeps a long-lived tab honest.
  useEffect(() => {
    const onVisible = () => { if (!document.hidden) loadTokens(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [loadTokens]);

  useEffect(() => {
    loadHealth();
    loadAuth();
    loadEbayStatus();
    loadMarketplaces();
  }, [loadHealth, loadAuth, loadEbayStatus, loadMarketplaces]);

  // Refresh the listings cache (and per-user marketplace connections) when
  // auth changes (login/logout).
  useEffect(() => {
    loadListings({ quiet: true });
    loadMarketplaces();
  }, [user, loadListings, loadMarketplaces]);

  // eBay views/watchers for live listings (best-effort; empty until eBay is
  // connected and the analytics scope granted). Refreshes as the set changes.
  useEffect(() => {
    if (!user || !ebay.connected) {
      setMetricsById({});
      setMetricsStatus({ trafficOk: false, needsReconnect: false });
      return;
    }
    let alive = true;
    api("/api/ebay/listing-metrics")
      .then((r) => {
        if (!alive) return;
        setMetricsById(r.metrics || {});
        setMetricsStatus({
          trafficOk: !!r.traffic_ok, needsReconnect: !!r.needs_reconnect });
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [user, ebay.connected, listingsState.items.length]);

  const value = useMemo(() => ({
    dark, toggleDark,
    view, setView, listingsTab, setListingsTab, openListings, listingsJumpRef,
    health, loadHealth,
    user, setUser, authOpen, setAuthOpen, openAuth, afterLogin, loadAuth, logout,
    ebay, loadEbayStatus, canPublishLive,
    marketplaces, loadMarketplaces, connectedMarketplaces,
    tokens, tokensOpen, setTokensOpen, loadTokens,
    notifications, loadNotifications, markNotificationsRead,
    shipping, openShipping, closeShipping,
    policiesData, setPoliciesData,
    listingsState, loadListings, metricsById, metricsStatus,
    storeSync, syncStore,
    session, setSession, startNew, openListing, deleteListing, bulkDeleteListings,
    skippedDraftIds, toggleSkipDraft,
    activeBulk, startBulk, bulkSettled, clearBulk, runBulkUpload,
    bulkRetry, clearBulkRetry,
  }), [
    dark, toggleDark, view, listingsTab, openListings, health, loadHealth, user, authOpen, openAuth,
    loadAuth, logout, ebay, loadEbayStatus, canPublishLive, policiesData,
    marketplaces, loadMarketplaces, connectedMarketplaces,
    tokens, tokensOpen, loadTokens,
    notifications, loadNotifications, markNotificationsRead,
    shipping, openShipping, closeShipping,
    listingsState, loadListings, metricsById, metricsStatus, storeSync, syncStore,
    session, startNew, openListing,
    deleteListing, bulkDeleteListings, skippedDraftIds, toggleSkipDraft,
    activeBulk, startBulk, bulkSettled, clearBulk, runBulkUpload,
    bulkRetry, clearBulkRetry,
  ]);

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used inside <AppProvider>");
  return ctx;
}

export { postJson };
