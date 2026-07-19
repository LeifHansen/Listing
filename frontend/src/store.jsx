import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import { api, postJson } from "@/lib/api";
import { useToast } from "@/components/ui/Toaster";

/* Central app state, ported from the original app.js:
   auth session, eBay connection, server health, theme, navigation, the
   listing being worked on, and the saved-listings cache. */

const AppContext = createContext(null);

export const VIEWS = [
  "dashboard", "new", "shop", "inventory", "drafts", "listings", "settings",
];

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

  // ---------- saved listings cache ----------
  const [listingsState, setListingsState] = useState({
    loaded: false, loading: false, authed: true, dbConfigured: true, items: [],
  });

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

  // ---------- the listing being worked on ----------
  // session: { sessionId, listing, confidence } — null until AI identify runs
  // or a saved listing is opened.
  const [session, setSession] = useState(null);

  const startNew = useCallback(() => {
    setSession(null);
    setView("new");
  }, []);

  const openListing = useCallback(async (id) => {
    try {
      const rec = await api(`/api/listings/${id}`);
      setSession({ sessionId: rec.id, listing: rec.listing, confidence: null });
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

  // ---------- eBay OAuth redirect landing ----------
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const e = params.get("ebay");
    if (e === "connected") toast("eBay connected! You can now publish real listings.", { kind: "success" });
    else if (e === "error") toast("eBay connection failed. Please try again.", { kind: "error" });
    if (e) history.replaceState({}, "", window.location.pathname);
  }, [toast]);

  useEffect(() => {
    loadHealth();
    loadAuth();
    loadEbayStatus();
  }, [loadHealth, loadAuth, loadEbayStatus]);

  // Refresh the listings cache when auth changes (login/logout).
  useEffect(() => {
    loadListings({ quiet: true });
  }, [user, loadListings]);

  const value = useMemo(() => ({
    dark, toggleDark,
    view, setView,
    health, loadHealth,
    user, setUser, authOpen, setAuthOpen, openAuth, afterLogin, loadAuth, logout,
    ebay, loadEbayStatus, canPublishLive,
    policiesData, setPoliciesData,
    listingsState, loadListings,
    session, setSession, startNew, openListing, deleteListing,
  }), [
    dark, toggleDark, view, health, loadHealth, user, authOpen, openAuth,
    loadAuth, logout, ebay, loadEbayStatus, canPublishLive, policiesData,
    listingsState, loadListings, session, startNew, openListing, deleteListing,
  ]);

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used inside <AppProvider>");
  return ctx;
}

export { postJson };
