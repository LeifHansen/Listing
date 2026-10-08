import { useCallback, useEffect, useMemo, useState } from "react";
import { api, patchJson, postJson } from "@/lib/api";
import { policyView } from "@/lib/settingsSections";
import { useApp } from "@/store";

/**
 * The three things Settings reads, each in three states (loading, couldn't
 * ask, answered), and the writes that change them.
 *
 *   prefs     — the account's new-listing defaults (`/api/prefs`). `null`
 *               while loading; `prefsError` set when the read failed. The
 *               two are kept apart on purpose: a failed read must never
 *               render the app's fallbacks as the seller's saved choices.
 *   policies  — the eBay business policies and which are the defaults
 *               (`/api/ebay/policies`), only once eBay is connected.
 *   overview  — what eBay says about the account (`/api/ebay/account-
 *               overview`): identity, payouts, selling limit, programs,
 *               ship-from locations. One fetch, read by two sections.
 *
 * Every write goes to exactly one system and reports on its own, which is
 * what lets a control save itself the moment it changes.
 */
export function useSettingsData() {
  const { user, ebay, policiesData, setPoliciesData } = useApp();

  // ---- prefs -------------------------------------------------------------
  const [prefs, setPrefs] = useState(null);
  const [prefsError, setPrefsError] = useState("");
  // The error is cleared on the ANSWER, not on the attempt: a retry still in
  // flight has not learned anything yet.
  const loadPrefs = useCallback(() => (
    api("/api/prefs")
      .then((r) => { setPrefs(r.prefs || {}); setPrefsError(""); })
      .catch((e) => setPrefsError(e.message || "we couldn’t load your defaults"))
  ), []);
  useEffect(() => {
    if (user) loadPrefs();
  }, [user, loadPrefs]);

  // One key at a time, by PATCH. The server merges, so nothing else moves,
  // and the answer is the whole row as saved — which is what is shown.
  const savePref = useCallback(async (key, value) => {
    const r = await patchJson("/api/prefs", { [key]: value });
    setPrefs(r.prefs || {});
    return r;
  }, []);
  // A few keys at once, for the cards with a Save button.
  const savePrefs = useCallback(async (values) => {
    const r = await postJson("/api/prefs", values);
    setPrefs(r.prefs || {});
    return r;
  }, []);

  // ---- policies ----------------------------------------------------------
  const [data, setData] = useState(policiesData);
  const [loading, setLoading] = useState(false);
  // Did THIS screen's load succeed? `data` cannot answer that — it is seeded
  // from the store cache, which the listing editor fills.
  const [loadedHere, setLoadedHere] = useState(false);
  const [policiesError, setPoliciesError] = useState("");
  const loadPolicies = useCallback(async () => {
    setLoading(true);
    try {
      const d = await api("/api/ebay/policies");
      setData(d);
      setPoliciesData(d);
      setLoadedHere(true);
      setPoliciesError("");
    } catch (e) {
      setPoliciesError(e.message || "we couldn’t reach eBay");
      setLoadedHere(false);
    } finally {
      setLoading(false);
    }
  }, [setPoliciesData]);
  // `loading` flips synchronously and that flip is load-bearing: without it
  // the previous account's policies stay on screen for the whole round trip
  // after a reconnect. See the same note in the editor's policy select.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- deliberate: see the note above
    if (user && ebay.connected) loadPolicies();
  }, [user, ebay.connected, loadPolicies]);

  const policies = useMemo(() => policyView({
    status: loading ? "loading" : (policiesError ? "unavailable" : "ready"),
    error: policiesError,
    policies: loadedHere ? data?.policies : undefined,
  }), [loading, policiesError, loadedHere, data]);

  // A default policy. The server merges, so one field at a time is fine.
  const savePolicy = useCallback(async (field, id) => {
    const r = await postJson("/api/ebay/policies", { [field]: id });
    setData((d) => (d ? { ...d, selected: { ...(d.selected || {}), [field]: id } } : d));
    setPoliciesData(null); // the publish step re-reads its summary next time
    return r;
  }, [setPoliciesData]);

  // The ship-from ZIP. Sent even when empty — that is how it is cleared —
  // and then everything that describes the location is re-read.
  const saveZip = useCallback(async (postal) => {
    const r = await postJson("/api/ebay/policies", { ship_from_postal: postal.trim() });
    setPoliciesData(null);
    await loadPolicies();
    return r;
  }, [loadPolicies, setPoliciesData]);

  // Handling time lives on the eBay policy; the local copy is patched with
  // the answer so the select shows it without another round trip.
  const saveHandlingTime = useCallback(async (days) => {
    const r = await patchJson("/api/ebay/handling-time", { days });
    setData((d) => {
      if (!d) return d;
      const fulfillment = (d.policies?.fulfillment || []).map((p) => (
        p.id === r.policy_id ? { ...p, handling_days: r.handling_days } : p));
      return { ...d, policies: { ...d.policies, fulfillment } };
    });
    return r;
  }, []);

  // ---- overview ----------------------------------------------------------
  const [overview, setOverview] = useState(null);
  const [overviewLoading, setOverviewLoading] = useState(false);
  const fetchOverview = useCallback(() => (
    api("/api/ebay/account-overview")
      .then(setOverview)
      .catch(() => setOverview({ connected: false }))
      .finally(() => setOverviewLoading(false))
  ), []);
  const loadOverview = useCallback(() => {
    setOverviewLoading(true);
    return fetchOverview();
  }, [fetchOverview]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the shimmer must show before the first answer, same as the policies load above
    if (user && ebay.connected) loadOverview();
    else setOverview(null);
  }, [user, ebay.connected, loadOverview]);

  const resetPolicies = useCallback(() => {
    setData(null);
    setLoadedHere(false);
    setPoliciesData(null);
    setOverview(null);
  }, [setPoliciesData]);

  return useMemo(() => ({
    prefs, prefsError, loadPrefs, savePref, savePrefs,
    data, loading, loadedHere, policies, policiesError,
    loadPolicies, savePolicy, saveZip, saveHandlingTime, resetPolicies,
    overview, overviewLoading, loadOverview,
  }), [
    prefs, prefsError, loadPrefs, savePref, savePrefs,
    data, loading, loadedHere, policies, policiesError,
    loadPolicies, savePolicy, saveZip, saveHandlingTime, resetPolicies,
    overview, overviewLoading, loadOverview,
  ]);
}
