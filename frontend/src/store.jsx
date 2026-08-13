import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import { api, postJson } from "@/lib/api";
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
    oauth_missing: [],
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

  // ---------- saved listings cache ----------
  const [listingsState, setListingsState] = useState({
    loaded: false, loading: false, authed: true, dbConfigured: true, items: [],
  });
  // eBay views/watchers per live listing, keyed by our listing record id.
  const [metricsById, setMetricsById] = useState({});

  const loadListings = useCallback(async ({ quiet = false } = {}) => {
    setListingsState((s) => ({ ...s, loading: !quiet }));
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
  const syncStore = useCallback(async ({ force = false } = {}) => {
    if (!user || !ebay.connected) return null;
    if (syncedOnce.current && !force) return null;
    syncedOnce.current = true;
    setStoreSync((s) => ({ ...s, syncing: true, error: null }));
    try {
      const res = await postJson("/api/ebay/import-listings", {});
      // Status reconciliation (sold/ended) can lag behind — fold it in quietly.
      postJson("/api/ebay/sync-listings", {})
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
  // { jobId, mode } — persisted so leaving the progress screen (or a reload)
  // never strands a running batch. Completed items also auto-save to Drafts.
  const [activeBulk, setActiveBulk] = useState(() => {
    try {
      const raw = localStorage.getItem("quickflip-bulk");
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  });
  const startBulk = useCallback((jobId, mode) => {
    const b = { jobId, mode };
    setActiveBulk(b);
    try { localStorage.setItem("quickflip-bulk", JSON.stringify(b)); } catch (e) {}
    setView("new");
  }, []);
  // Job finished: stop persisting (a reload shouldn't restore a done batch) but
  // keep it in memory so the results stay on screen until the user moves on.
  const bulkSettled = useCallback(() => {
    try { localStorage.removeItem("quickflip-bulk"); } catch (e) {}
  }, []);
  const clearBulk = useCallback(() => {
    setActiveBulk(null);
    try { localStorage.removeItem("quickflip-bulk"); } catch (e) {}
  }, []);

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
    if (!user || !ebay.connected) { setMetricsById({}); return; }
    let alive = true;
    api("/api/ebay/listing-metrics")
      .then((r) => { if (alive) setMetricsById(r.metrics || {}); })
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
    policiesData, setPoliciesData,
    listingsState, loadListings, metricsById,
    storeSync, syncStore,
    session, setSession, startNew, openListing, deleteListing, bulkDeleteListings,
    skippedDraftIds, toggleSkipDraft,
    activeBulk, startBulk, bulkSettled, clearBulk,
  }), [
    dark, toggleDark, view, listingsTab, openListings, health, loadHealth, user, authOpen, openAuth,
    loadAuth, logout, ebay, loadEbayStatus, canPublishLive, policiesData,
    marketplaces, loadMarketplaces, connectedMarketplaces,
    tokens, tokensOpen, loadTokens,
    listingsState, loadListings, metricsById, storeSync, syncStore,
    session, startNew, openListing,
    deleteListing, bulkDeleteListings, skippedDraftIds, toggleSkipDraft,
    activeBulk, startBulk, bulkSettled, clearBulk,
  ]);

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used inside <AppProvider>");
  return ctx;
}

export { postJson };
