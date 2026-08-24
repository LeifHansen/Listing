import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import { api, postJson, downscaleAllForUpload, UPLOAD_TIMEOUT_MS } from "@/lib/api";
import { storeToken } from "@/lib/platform";
import { useToast } from "@/components/ui/Toaster";

/* Central app state, ported from the original app.js:
   auth session, eBay connection, server health, theme, navigation, the
   listing being worked on, and the saved-listings cache. */

const AppContext = createContext(null);

// "We know nothing about eBay traffic" — the state the metrics below fall back
// to whenever they are not ours to show (signed out, or eBay disconnected).
// Module-level so that fallback keeps a stable identity across renders.
const NO_METRICS = {};
const NO_METRICS_STATUS = { trafficOk: false, needsReconnect: false };

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
  // Grid (the default) or list, for the listing grids on the Sell screen.
  // It's a per-device viewing preference, not account data, so it rides
  // localStorage next to the theme rather than the server.
  const [listingsLayout, setLayout] = useState(() => {
    try {
      return localStorage.getItem("quickflip-listings-layout") === "list"
        ? "list" : "grid";
    } catch (e) { return "grid"; }
  });
  const setListingsLayout = useCallback((next) => {
    const mode = next === "list" ? "list" : "grid";
    setLayout(mode);
    try { localStorage.setItem("quickflip-listings-layout", mode); } catch (e) {}
  }, []);
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

  // ---------- eBay connection ----------
  // Declared ahead of auth: logout() lists loadEbayStatus as a dependency,
  // and a useCallback dependency array is evaluated during render — a
  // forward reference there is a temporal-dead-zone crash, not a lint nit.
  const [ebay, setEbay] = useState({
    connected: false, env: "", username: "", email: "", oauth_ready: false,
    oauth_missing: [], labels_enabled: false, foreign_listings: 0,
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
        // Listings still here from an eBay account other than the connected
        // one — see the banner in Settings.
        foreign_listings: s.foreign_listings || 0,
      });
    } catch (e) { /* keep previous */ }
  }, []);

  // Publishing is live if EITHER the user connected their eBay account or the
  // server has env-level credentials.
  const canPublishLive = ebay.connected || health.ebay_configured;

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
  }, [loadEbayStatus]);

  // ---------- AI tokens (monetization) ----------
  // Balance + catalog from /api/tokens. `enabled: false` (dev/self-hosted
  // installs) hides the whole surface. The dialog opens from the TopBar chip
  // or automatically when any AI call comes back 402 "out of tokens".
  const [tokens, setTokens] = useState({ enabled: false, total: 0, packs: [], costs: {} });
  const [tokensOpen, setTokensOpen] = useState(false);
  const loadTokens = useCallback(async () => {
    try { setTokens(await api("/api/tokens")); } catch (e) { /* keep previous */ }
  }, []);

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
    // This effect IS the subscription the rule asks for: a 60s poll of an
    // external system, plus one immediate read so the bell isn't blank until
    // the first tick. The state it writes lands after the fetch resolves; the
    // one synchronous write is the signed-out branch above, which clears the
    // bell exactly once per logout and cannot cascade (it depends only on
    // `user`, which it does not change).
    // eslint-disable-next-line react-hooks/set-state-in-effect
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
  const [metricsById, setMetricsById] = useState(NO_METRICS);
  // Whether eBay's traffic report (views/impressions) was actually readable —
  // so the UI can say "we couldn't ask" instead of showing everything as 0.
  const [metricsStatus, setMetricsStatus] = useState(NO_METRICS_STATUS);
  // Metrics only mean anything while someone is signed in AND eBay is
  // connected; the moment either drops, whatever we cached stops being ours to
  // show. That forget-it step used to sit at the top of the fetch effect near
  // the bottom of this file, which made it a setState inside an effect (a
  // cascading render, and one commit late). React's documented alternative is
  // to adjust state DURING render, so the stale numbers are already gone in
  // the render that loses the connection.
  // The fetch itself still lives in the effect — only the reset moved.
  //
  // The condition is "not ours to show", NOT "the moment it stopped being
  // ours": the effect below cleared on every run while signed out, and an
  // edge-triggered version would lose one case. An in-flight metrics response
  // can resolve in the gap between the render that drops the connection and
  // that render's effect cleanup (a concurrent render yields, microtasks run,
  // `alive` is still true) — so a write can land AFTER the edge has passed.
  // Level-triggering it costs an identity check and cannot leave stale numbers
  // on screen. It converges: the clear is skipped once both are the shared
  // empties, so this settles after at most one extra render pass.
  const metricsLive = !!user && ebay.connected;
  if (!metricsLive
      && (metricsById !== NO_METRICS || metricsStatus !== NO_METRICS_STATUS)) {
    setMetricsById(NO_METRICS);
    setMetricsStatus(NO_METRICS_STATUS);
  }

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

  // Apply a known-authoritative change to one card without waiting for the
  // next /api/listings round trip. Publishing is the case that matters: the
  // server has already told us the listing is live, so the card must not sit
  // under Drafts for the length of a refresh (or, if that refresh is slow or
  // fails, indefinitely). The next loadListings still wins — this only
  // closes the gap.
  const patchListing = useCallback((id, patch) => {
    if (!id || !patch) return;
    setListingsState((s) => {
      const items = s.items.map((it) => (it.id === id ? { ...it, ...patch } : it));
      return items === s.items ? s : { ...s, items };
    });
  }, []);

  // ---------- eBay store mirror ----------
  // The app mirrors the seller's WHOLE eBay store, not just what it created:
  // once eBay is connected, the first load imports every active listing and
  // reconciles live statuses — so the dashboard and Listings ARE the store.
  // Runs once per app session; `syncStore({ force: true })` re-runs it (the
  // "Sync with eBay" button).
  const [storeSync, setStoreSync] = useState({
    syncing: false, lastSynced: null, error: null, progress: null,
  });
  const syncedOnce = useRef(false);
  const lastReconcile = useRef(0); // ms — throttles the quiet status re-checks
  // The import is a background job now (one eBay GetItem per listing takes
  // minutes on a real store, which no browser will hold a request open for),
  // so the spinner follows the JOB, not one fetch. Polling ends on done, on a
  // job the server has no record of, or after a run of failed polls — the
  // "Syncing your eBay store…" line can no longer hang forever waiting on a
  // request that will never answer.
  const watchImport = useCallback(async (jobId) => {
    let fails = 0;
    let missing = 0;
    for (;;) {
      await new Promise((r) => setTimeout(r, 2000));
      let job;
      try {
        job = await api(`/api/ebay/import-status/${jobId}`);
      } catch (e) {
        const gone = (e.message || "").includes("(404)");
        // A 404 twice over means the server really has no such job (a restart
        // that predates the mirror, say). Anything else is a blip worth
        // retrying — the import itself is still running server-side.
        missing = gone ? missing + 1 : 0;
        fails += 1;
        if (missing >= 2) {
          throw new Error("The sync stopped — the server lost track of it. "
                          + "Try Sync with eBay again.", { cause: e });
        }
        if (fails >= 8) {
          throw new Error("Lost the connection while syncing your store. "
                          + "Anything imported already is in Listings.", { cause: e });
        }
        continue;
      }
      fails = 0;
      missing = 0;
      setStoreSync((s) => ({
        ...s,
        progress: job.total_items
          ? { phase: job.phase, done: job.current || 0, total: job.total_items }
          : null,
      }));
      if (job.done) {
        if (job.error) throw new Error(job.error);
        return {
          found: job.found || 0, imported: job.imported || 0,
          updated: job.updated || 0, deduped: job.deduped || 0,
          failed: job.failed || 0,
        };
      }
    }
  }, []);
  const syncStore = useCallback(async ({ force = false } = {}) => {
    if (!user || !ebay.connected) return null;
    if (syncedOnce.current && !force) return null;
    syncedOnce.current = true;
    setStoreSync((s) => ({ ...s, syncing: true, error: null, progress: null }));
    try {
      const started = await postJson("/api/ebay/import-listings", {});
      // Status reconciliation (sold/ended) can lag behind — fold it in quietly.
      // force: this is the deliberate "Sync with eBay" path (or first load),
      // so it may run the full per-item sweep; the background heartbeat below
      // deliberately does not.
      lastReconcile.current = Date.now();
      postJson("/api/ebay/sync-listings", { force: true })
        .then((r) => { if (r.changed) loadListings({ quiet: true }); })
        .catch(() => {});
      // job_id: the import runs in the background and we watch it. A body with
      // the counts already in it is a server that still imports inline.
      const res = started?.job_id ? await watchImport(started.job_id) : started;
      await loadListings({ quiet: true });
      setStoreSync({
        syncing: false, lastSynced: Date.now(), error: null, progress: null,
      });
      return res;
    } catch (e) {
      setStoreSync({
        syncing: false, lastSynced: null, error: e.message, progress: null,
      });
      // Failed for a reason worth retrying: let a later deliberate sync (or the
      // next app load) start a fresh one instead of latching "already synced".
      syncedOnce.current = false;
      return { error: e.message };
    }
  }, [user, ebay.connected, loadListings, watchImport]);
  // Kick the once-per-session mirror import off as soon as we have a user and
  // a connected eBay account. syncStore bails out immediately in every other
  // case, and latches `syncedOnce` so re-running this effect is a no-op — the
  // spinner state it sets synchronously happens once per session, on the run
  // that actually starts the job, and the deps it reads are not ones it writes.
  // eslint-disable-next-line react-hooks/set-state-in-effect
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
  // Marking it done is what stops the shell's banner from claiming the batch is
  // still processing for the rest of the session — it only knew "a batch
  // exists", so a finished one kept its spinner on every screen until the
  // seller reopened the queue and exited it.
  const bulkSettled = useCallback(() => {
    setActiveBulk((b) => (b && !b.done ? { ...b, done: true } : b));
    try { localStorage.removeItem("quickflip-bulk"); } catch (e) {}
  }, []);
  const clearBulk = useCallback(() => {
    setActiveBulk(null);
    try { localStorage.removeItem("quickflip-bulk"); } catch (e) {}
  }, []);
  // A running batch is watched from the SHELL, not only from the queue screen.
  // The queue polls the full status while it's open, but a seller who walks
  // away from it (Home, Listings, a reload that lands elsewhere) left nothing
  // watching — so a batch that finished server-side was never marked done and
  // the banner kept claiming it was processing for the rest of the session.
  // This is the cheap items-free poll: a heartbeat whose only job is to settle
  // the batch and refresh Drafts when it ends.
  useEffect(() => {
    const jobId = activeBulk?.jobId;
    if (!jobId || activeBulk?.done) return undefined;
    let timer;
    let stopped = false;
    let misses = 0;
    let fails = 0;
    const tick = async () => {
      try {
        const j = await api(`/api/bulk/status/${jobId}/brief`);
        misses = 0;
        fails = 0;
        if (stopped) return;
        if (j.done) {
          bulkSettled();
          loadListings({ quiet: true });
          return;  // settled — the effect tears itself down on the state change
        }
      } catch (e) {
        // Only a job the server truly has no record of ends the watch, and
        // only after a second look: a 404 on a blip (an auth hiccup mid-batch)
        // must not declare a running batch finished. Everything else is worth
        // retrying — the batch is still running, and this heartbeat is the only
        // thing that will notice it finish while the queue screen is closed.
        if ((e.message || "").includes("(404)")) misses += 1;
        fails += 1;
        if (misses >= 2) {
          if (!stopped) bulkSettled();
          return;
        }
      }
      // Back off while the server is unreachable so a dropped connection
      // doesn't mean a request every 5 seconds for as long as the tab is open.
      if (!stopped) timer = setTimeout(tick, fails ? 15000 : 5000);
    };
    timer = setTimeout(tick, 5000);
    return () => { stopped = true; clearTimeout(timer); };
  }, [activeBulk, bulkSettled, loadListings]);

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
      const { job_id } = await api("/api/bulk/upload",
        // A whole batch of photos over a phone connection: minutes, legitimately.
        { method: "POST", body: fd, timeoutMs: UPLOAD_TIMEOUT_MS });
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
      // Re-read the roster so the new connection shows in Settings. Nothing is
      // written synchronously: loadMarketplaces only calls setState after its
      // fetch resolves, and this effect reads the URL once on mount.
      // eslint-disable-next-line react-hooks/set-state-in-effect
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
  // loadTokens writes state only after its fetch resolves, so there is no
  // synchronous cascade here — just a fetch tied to who is signed in.
  // eslint-disable-next-line react-hooks/set-state-in-effect
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

  // Boot: everything the shell needs before the first screen can be honest.
  // All four write state only after their fetch resolves, so nothing here
  // renders twice in a row; the deps are stable callbacks, so it runs once.
  // (Three of them keep the previous value on failure; loadAuth is the
  // exception — a failed /api/auth/me sets the user to null, i.e. treats an
  // unreadable session as signed out, which is the safe direction.)
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadHealth();
    loadAuth();
    loadEbayStatus();
    loadMarketplaces();
  }, [loadHealth, loadAuth, loadEbayStatus, loadMarketplaces]);

  // Refresh the listings cache (and per-user marketplace connections) when
  // auth changes (login/logout).
  // loadListings does flip `loading` synchronously — that is the point of the
  // spinner, and it settles in the same fetch that clears it. It depends on
  // `user`, which it never writes, so there is no cascade: one render to show
  // the skeletons, one when the data lands.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadListings({ quiet: true });
    loadMarketplaces();
  }, [user, loadListings, loadMarketplaces]);

  // eBay views/watchers for live listings (best-effort; empty until eBay is
  // connected and the analytics scope granted). Refreshes as the set changes.
  // Signed out / disconnected is handled during render above (the state is
  // cleared there), so this effect only ever fetches.
  useEffect(() => {
    if (!user || !ebay.connected) return undefined;
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
    listingsLayout, setListingsLayout,
    health, loadHealth,
    user, setUser, authOpen, setAuthOpen, openAuth, afterLogin, loadAuth, logout,
    ebay, loadEbayStatus, canPublishLive,
    marketplaces, loadMarketplaces, connectedMarketplaces,
    tokens, tokensOpen, setTokensOpen, loadTokens,
    notifications, loadNotifications, markNotificationsRead,
    shipping, openShipping, closeShipping,
    policiesData, setPoliciesData,
    listingsState, loadListings, patchListing, metricsById, metricsStatus,
    storeSync, syncStore,
    session, setSession, startNew, openListing, deleteListing, bulkDeleteListings,
    skippedDraftIds, toggleSkipDraft,
    activeBulk, startBulk, bulkSettled, clearBulk, runBulkUpload,
    bulkRetry, clearBulkRetry,
  }), [
    dark, toggleDark, view, listingsTab, openListings, health, loadHealth, user, authOpen, openAuth,
    listingsLayout, setListingsLayout,
    loadAuth, logout, ebay, loadEbayStatus, canPublishLive, policiesData,
    marketplaces, loadMarketplaces, connectedMarketplaces,
    tokens, tokensOpen, loadTokens,
    notifications, loadNotifications, markNotificationsRead,
    shipping, openShipping, closeShipping,
    listingsState, loadListings, patchListing, metricsById, metricsStatus,
    storeSync, syncStore,
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
