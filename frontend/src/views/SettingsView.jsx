import { useCallback, useEffect, useState } from "react";
import {
  Link2, Unlink, Wallet, ExternalLink, CheckCircle2, AlertTriangle,
  MapPin, Settings as SettingsIcon, LogIn, UserRound, RefreshCw,
  PackageOpen, Truck, Plus, TrendingUp, Megaphone, Store, BadgeCheck,
  Trash2, Clock,
} from "lucide-react";
import { api, postJson, startConnect } from "@/lib/api";
import { CONDITIONS, conditionLabel } from "@/lib/utils";
import { useApp } from "@/store";
import { Card, SectionHeader } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";
import { EmptyState } from "@/components/ui/EmptyState";
import { AccountIllustration } from "@/components/ui/illustrations";
import { useToast } from "@/components/ui/Toaster";

// eBay's three business policies. "Shipping policy" is the one that also
// names the carrier service, so it is the ONLY place the app says "shipping
// policy" — the per-listing control (ShippingPolicySelect) overrides this
// exact field and uses the same word. It used to be called a "shipping
// service" there, which read as a second, separate setting.
const POLICY_KINDS = [
  {
    key: "fulfillment", field: "fulfillment_policy_id", label: "Shipping policy",
    help: "Applied to every new listing. It's what sets the carrier service — a listing can override it on its own Shipping card.",
  },
  { key: "payment", field: "payment_policy_id", label: "Payment policy" },
  { key: "return", field: "return_policy_id", label: "Return policy" },
];

// Settings — eBay account + the listing defaults applied to every publish.
export function SettingsView() {
  const {
    user, openAuth, ebay, loadEbayStatus, policiesData, setPoliciesData,
  } = useApp();
  const { toast, confirm } = useToast();
  const [data, setData] = useState(policiesData);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [optingIn, setOptingIn] = useState(false);
  const [creatingPolicies, setCreatingPolicies] = useState(false);
  const [postal, setPostal] = useState("");
  const [selected, setSelected] = useState({});
  const [prefs, setPrefs] = useState(null); // new-listing defaults (null = loading)

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await api("/api/ebay/policies");
      setData(d);
      setPoliciesData(d);
      setPostal(d.ship_from_postal || "");
      setSelected(d.selected || {});
    } catch (e) {
      toast(`Couldn't load policies: ${e.message}`, { kind: "error" });
    } finally {
      setLoading(false);
    }
  }, [setPoliciesData, toast]);

  // Policies are fetched once both external systems are ready: an authenticated
  // session and a live eBay link. `load()` flips `loading` synchronously and
  // that flip is load-bearing — `data` is seeded from the store cache, so
  // without it the previous account's policies stay on screen (and stay
  // editable) for the whole round trip after a re-login or a reconnect. The
  // compiler rule flags exactly that flip; every other setState in `load`
  // happens after the await. Suppressed rather than "fixed", because the
  // alternatives (deriving the flag during render, or seeding `loading` from
  // the connection state) all move which frame the shimmer appears on.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- deliberate: see the note above
    if (user && ebay.connected) load();
  }, [user, ebay.connected, load]);

  // New-listing defaults load independently of the eBay connection — they
  // pre-fill every AI draft and apply even in dry-run mode.
  useEffect(() => {
    if (!user) return;
    api("/api/prefs").then((r) => setPrefs(r.prefs || {})).catch(() => setPrefs({}));
  }, [user]);
  const setPref = (k, v) => setPrefs((p) => ({ ...p, [k]: v }));

  // One Save for both default groups: the new-listing packing defaults always,
  // and the eBay publish defaults (policies + ship-from) when connected. Policy
  // selections are only sent once loaded, so a click mid-load can't overwrite
  // them with empty values.
  const save = async () => {
    setSaving(true);
    try {
      if (prefs) {
        const r = await postJson("/api/prefs", prefs);
        setPrefs(r.prefs || {});
      }
      if (ebay.connected && data) {
        // Always sent, including empty. Omitting a blank made clearing the
        // ZIP a silent no-op reported as "Defaults saved": the field showed
        // empty, the stored value never changed, and it reappeared on the
        // next load. `data` is loaded here (the enclosing check), so this
        // cannot fire mid-load with a value the seller never saw.
        const payload = { ...selected, ship_from_postal: postal.trim() };
        await postJson("/api/ebay/policies", payload);
        setPoliciesData(null); // refresh the publish-step summary next time
        load();
      }
      toast("Defaults saved — they apply to every new listing you draft and publish.",
        { kind: "success" });
    } catch (e) {
      toast(`Couldn't save: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };

  const disconnect = async () => {
    if (!(await confirm({
      title: "Disconnect this eBay account?",
      message: "To connect a DIFFERENT account, first sign out of eBay in your browser (or use a private window) so eBay lets you choose — otherwise it may silently reconnect the same account.",
      confirmLabel: "Disconnect",
      danger: true,
    }))) return;
    try {
      await postJson("/api/ebay/disconnect", {});
      await loadEbayStatus();
      setData(null);
      setPoliciesData(null);
      toast("Disconnected. Click 'Connect eBay' to link the account you want.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't disconnect: ${e.message}`, { kind: "error" });
    }
  };

  const checkPayout = async () => {
    setChecking(true);
    try {
      const s = await api("/api/ebay/payments-status");
      if (s.opted_in) {
        toast(`Payments are set up on eBay (${s.env}): status ${s.status}. Bank/payout onboarding is complete — you can publish live listings.`, { kind: "success" });
      } else if (s.error) {
        toast(`Couldn't verify payments setup (${s.env}): ${s.error}\n${s.detail || ""}`, { kind: "warning" });
      } else {
        toast(`eBay (${s.env}) reports payments status "${s.status || "unknown"}". Finish payout setup in eBay Seller Hub → Payments (bank verification can take 1–2 days).`, { kind: "warning" });
      }
    } catch (e) {
      toast(`Payments check failed: ${e.message}`, { kind: "error" });
    } finally {
      setChecking(false);
    }
  };

  if (!user) {
    return (
      <SettingsShell>
        <Card className="p-0">
          <EmptyState
            illustration={AccountIllustration}
            title="Log in first"
            message="Your eBay connection and listing defaults live on your account."
            action={
              <Button variant="primary" size="lg" onClick={() => openAuth()}>
                <LogIn aria-hidden /> Log in
              </Button>
            }
          />
        </Card>
        {/* Logged out too: in the native app there's no address bar, so this
            is the only route to the policies. */}
        <LegalLinks />
      </SettingsShell>
    );
  }

  return (
    <SettingsShell>
      <ProfileCard />

      {/* eBay account + all defaults, consolidated into one card with a single
          Save for both default groups. */}
      <Card>
        {/* ── eBay account ── */}
        <SectionHeader
          icon={Link2}
          title="eBay account"
          hint="Publishing goes to the account linked here"
        />
        {ebay.connected ? (
          <div className="flex flex-col gap-4">
            <p className="text-sm text-ink-secondary">
              {ebay.username ? (
                <>
                  Connected to eBay account <strong className="text-ink">{ebay.username}</strong>
                  {ebay.email && <span> ({ebay.email})</span>} on{" "}
                  <strong className="text-ink">{ebay.env || "production"}</strong>.
                </>
              ) : (
                <>Connected, but this link was made before we could read the account name.
                  <strong> Disconnect and reconnect</strong> to confirm which account it is.</>
              )}
            </p>
            <ForeignListingsNotice />
            <div className="flex flex-wrap gap-2.5">
              <Button variant="secondary" onClick={checkPayout} loading={checking}>
                <Wallet aria-hidden /> Check payout setup
              </Button>
              <Button variant="danger" onClick={disconnect}>
                <Unlink aria-hidden /> Disconnect / switch account
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <p className="text-sm text-ink-secondary">
              Not connected yet — publishing runs in dry-run mode (you get the exact eBay
              API payload without posting).
            </p>
            {ebay.oauth_ready ? (
              <div>
                <Button
                  variant="primary"
                  onClick={() => startConnect("/api/ebay/connect").catch((e) =>
                    toast(`Couldn't open the connect screen: ${e.message}`, { kind: "error" }))}
                >
                  <Link2 aria-hidden /> Connect eBay
                </Button>
              </div>
            ) : (
              <div className="rounded-tile bg-warning-soft border border-warning/30 p-4 flex gap-3">
                <AlertTriangle size={18} className="text-warning shrink-0 mt-0.5" aria-hidden />
                <div className="text-sm min-w-0">
                  <p className="font-bold text-ink">“Sign in with eBay” isn’t set up on the server</p>
                  {(ebay.oauth_missing || []).length > 0 ? (
                    <p className="text-ink-secondary mt-0.5">
                      The server reports {ebay.oauth_missing.length === 1 ? "this variable is" : "these variables are"} missing
                      or placeholder text:{" "}
                      {ebay.oauth_missing.map((name, i) => (
                        <span key={name}>
                          {i > 0 && ", "}
                          <code className="text-ink font-semibold">{name}</code>
                        </span>
                      ))}
                      . Set {ebay.oauth_missing.length === 1 ? "it" : "them"} on the deployment
                      (e.g. <code className="text-ink font-semibold">fly secrets set …</code>) —
                      values containing <code className="text-ink font-semibold">&lt;</code> are
                      treated as unset. Until then, publishing stays in dry-run mode.
                    </p>
                  ) : (
                    <p className="text-ink-secondary mt-0.5">
                      The Connect button can’t do anything until these are set on the
                      deployment: <code className="text-ink font-semibold">EBAY_CLIENT_ID</code>,{" "}
                      <code className="text-ink font-semibold">EBAY_CLIENT_SECRET</code>, and{" "}
                      <code className="text-ink font-semibold">EBAY_RUNAME</code>{" "}
                      (e.g. <code className="text-ink font-semibold">fly secrets set …</code>).
                      Until then, publishing stays in dry-run mode.
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── New-listing defaults ── */}
        {/* ── Pricing strategy (slider) ── */}
        <div className="mt-7 pt-7 border-t border-line">
          <SectionHeader
            icon={TrendingUp}
            title="Pricing strategy"
            hint="Where the AI prices every draft and comp suggestion — from priced-to-move to patient top dollar."
          />
          {prefs === null ? (
            <div className="ai-shimmer h-16 rounded-tile" aria-hidden />
          ) : (
            <PricingStrategySlider prefs={prefs} set={setPref} />
          )}
        </div>

        {/* ── Auto-promote on publish ── */}
        <div className="mt-7 pt-7 border-t border-line">
          <SectionHeader
            icon={Megaphone}
            title="Promoted Listings"
            hint="Automatically promote each listing the moment it publishes, at eBay's recommended ad rate (pay only when it sells through the ad)."
          />
          {prefs === null ? (
            <div className="ai-shimmer h-12 rounded-tile" aria-hidden />
          ) : (
            <div className="max-w-lg">
              <Field label="Auto-promote new listings">
                <Select
                  value={String(prefs.auto_promote ?? 1)}
                  onChange={(e) => setPref("auto_promote", Number(e.target.value))}
                >
                  <option value="1">On — promote at eBay’s recommended rate</option>
                  <option value="0">Off — only when I toggle Promote on a listing</option>
                </Select>
              </Field>
            </div>
          )}
        </div>

        <div className="mt-7 pt-7 border-t border-line">
          <SectionHeader
            icon={PackageOpen}
            title="New-listing defaults"
            hint="Pre-filled on every listing the AI drafts — tweak any of it per listing. Perfect when most of what you sell packs the same way."
          />
          {prefs === null ? (
            <div className="ai-shimmer h-28 rounded-tile" aria-hidden />
          ) : (
            <NewListingDefaultsFields prefs={prefs} set={setPref} />
          )}
        </div>

        {/* ── eBay publish defaults (business policies + ship-from) ── */}
        <div className="mt-7 pt-7 border-t border-line">
          <SectionHeader
            icon={SettingsIcon}
            title="eBay publish defaults"
            hint="Applied to every listing you publish — shipping, payment & returns come from your eBay business policies"
          />
          {!ebay.connected ? (
            <p className="text-sm text-ink-secondary">
              Connect your eBay account first — your shipping, payment, and return templates
              come from there.
            </p>
          ) : loading || !data ? (
            <div className="ai-shimmer h-32 rounded-tile" aria-hidden />
          ) : (
            <div className="flex flex-col gap-5 max-w-lg">
              <Field
                label={
                  <span className="inline-flex items-center gap-1.5">
                    <MapPin size={14} aria-hidden /> Ship-from ZIP code
                  </span>
                }
                help={data.location_set
                  ? undefined
                  : "Required to publish — eBay needs a location to ship from. Enter your ZIP and Save; we'll create it on eBay for you."}
              >
                <Input
                  inputMode="numeric" placeholder="e.g. 90210" className="max-w-44"
                  value={postal}
                  onChange={(e) => setPostal(e.target.value)}
                />
                {data.location_set && (
                  <TagPill tone="green" className="self-start mt-1">
                    <CheckCircle2 size={12} aria-hidden /> eBay ship-from location is set
                  </TagPill>
                )}
              </Field>

              {POLICY_KINDS.map(({ key, field, label, help }) => {
                const opts = data.policies[key] || [];
                return (
                  <Field
                    key={key} label={label}
                    help={opts.length ? help : `No ${label.toLowerCase()} on eBay yet.`}
                  >
                    <Select
                      value={selected[field] || ""}
                      onChange={(e) => setSelected((s) => ({ ...s, [field]: e.target.value }))}
                    >
                      <option value="">— none —</option>
                      {opts.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}{p.summary ? ` · ${p.summary}` : ""}
                        </option>
                      ))}
                    </Select>
                  </Field>
                );
              })}

              <AddShippingServiceRow
                onCreated={async (pol) => {
                  // Re-apply AFTER the reload, not before. load() ends with
                  // setSelected(d.selected), and the server only stores this
                  // policy as the default when the account had none — so for
                  // a seller who already had one, the optimistic selection
                  // was overwritten a moment later and the toast's "selected
                  // it above" was simply false.
                  setPoliciesData(null);
                  await load();
                  setSelected((s) => ({ ...s, fulfillment_policy_id: pol.id }));
                }}
              />

              {POLICY_KINDS.some(({ key }) => !(data.policies[key] || []).length) && (
                <div className="rounded-tile bg-warning-soft border border-warning/30 p-4 text-sm">
                  <p className="text-ink flex gap-2">
                    <AlertTriangle size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
                    <span>
                      eBay requires shipping, payment &amp; return policies to publish. If these
                      dropdowns are empty, business policies are usually switched off for the
                      account — they’re an eBay seller program, not a default.
                    </span>
                  </p>
                  {/* The button that used to be a sentence telling the seller to go
                      find a page on eBay. eBay takes up to 24h and returns nothing,
                      so this reports what was actually asked, never that policies
                      are ready. */}
                  <div className="flex flex-wrap items-center gap-3 mt-3">
                    <Button
                      size="sm" variant="soft" disabled={optingIn}
                      onClick={async () => {
                        setOptingIn(true);
                        try {
                          const r = await postJson("/api/ebay/opt-in-policies", {});
                          toast(r.message, { kind: r.already ? "success" : "info" });
                          if (r.already) load();
                        } catch (e) {
                          toast(e.message, { kind: "error" });
                        } finally {
                          setOptingIn(false);
                        }
                      }}
                    >
                      {optingIn ? "Asking eBay…" : "Turn on business policies"}
                    </Button>
                    {/* Opting in is necessary and not sufficient: the account
                        still needs one shipping, one payment and one return
                        policy. This makes whichever are missing. Separate
                        button because eBay's opt-in takes up to 24h, so right
                        after opting in this one will legitimately fail — and
                        saying which is missing beats one button that hides
                        which half went wrong. */}
                    <Button
                      size="sm" variant="soft" disabled={creatingPolicies}
                      onClick={async () => {
                        setCreatingPolicies(true);
                        try {
                          const r = await postJson("/api/ebay/ensure-all-policies", {});
                          const made = (r.created || []).length;
                          const failed = Object.keys(r.errors || {});
                          if (failed.length) {
                            toast(
                              `Couldn't create your ${failed.join(" and ")} policy. `
                              + `eBay said: ${r.errors[failed[0]]}`,
                              { kind: "error" });
                          } else {
                            toast(made
                              ? `Created your ${(r.created || []).join(", ")} policy — you can publish now.`
                              : "You already had all three policies.",
                              { kind: "success" });
                          }
                          load();
                        } catch (e) {
                          toast(e.message, { kind: "error" });
                        } finally {
                          setCreatingPolicies(false);
                        }
                      }}
                    >
                      {creatingPolicies ? "Creating…" : "Create my policies"}
                    </Button>
                    <a
                      href={data.manage_url} target="_blank" rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 font-semibold text-blue"
                    >
                      Manage them on eBay <ExternalLink size={13} aria-hidden />
                    </a>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── one Save for both default groups ── */}
        <div className="mt-7 pt-6 border-t border-line">
          <Button variant="primary" size="lg" onClick={save} loading={saving}>
            Save defaults
          </Button>
        </div>

        {/* ── live mirror of the eBay account (was a separate nav page) ── */}
        {ebay.connected && (
          <div className="mt-7 pt-7 border-t border-line">
            <SectionHeader
              icon={Store}
              title="Your eBay account"
              hint="A live mirror of your seller settings. Handling time, store, and vacation settings live in Seller Hub."
            />
            <EbayAccountMirror />
          </div>
        )}
      </Card>

      <MarketplaceConnections />
      <DeleteAccountCard />
      <LegalLinks />
    </SettingsShell>
  );
}

// Cross-posting marketplaces — every registered marketplace except eBay
// (which keeps its own card above). One section per marketplace: connected →
// account + Disconnect; configured but not connected → Connect button;
// coming soon (access pending on the marketplace's side) → a "Coming soon"
// pill and the wait explained; not configured on the server → the same
// missing-env explainer the eBay card uses. Renders nothing while eBay is
// the only marketplace registered.
function MarketplaceConnections() {
  const { marketplaces, loadMarketplaces } = useApp();
  const { toast, confirm } = useToast();
  // Coming-soon marketplaces sink below the ones you can actually connect.
  const others = marketplaces
    .filter((m) => m.key !== "ebay")
    .slice()
    .sort((a, b) => (a.coming_soon ? 1 : 0) - (b.coming_soon ? 1 : 0));
  if (!others.length) return null;

  const disconnect = async (m) => {
    if (!(await confirm({
      title: `Disconnect ${m.label}?`,
      message: "Cross-posting there stops until you reconnect. Your saved defaults are kept.",
      confirmLabel: "Disconnect",
      danger: true,
    }))) return;
    try {
      await postJson(`/api/${m.key}/disconnect`, {});
      await loadMarketplaces();
      toast(`${m.label} disconnected.`, { kind: "success" });
    } catch (e) {
      toast(`Couldn't disconnect: ${e.message}`, { kind: "error" });
    }
  };

  return (
    <Card>
      <SectionHeader
        icon={Link2}
        title="Cross-posting marketplaces"
        hint="Connect more marketplaces to post a listing to several of them at once"
      />
      <div className="flex flex-col">
        {others.map((m, i) => (
          <div key={m.key} className={i > 0 ? "mt-6 pt-6 border-t border-line" : ""}>
            <div className="flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1">
                <p className="font-semibold text-ink flex items-center gap-2">
                  {m.label}
                  {!m.connected && m.coming_soon && (
                    <TagPill tone="blue">
                      <Clock size={11} aria-hidden /> Coming soon
                    </TagPill>
                  )}
                </p>
                {m.connected ? (
                  <p className="text-sm text-ink-secondary">
                    Connected{m.username ? (
                      <> as <strong className="text-ink">{m.username}</strong></>
                    ) : null}.
                  </p>
                ) : m.oauth_ready ? (
                  <p className="text-sm text-ink-secondary">
                    Not connected yet — link your {m.label} account to cross-post listings.
                  </p>
                ) : m.coming_soon ? (
                  <p className="text-sm text-ink-secondary">
                    {m.coming_soon_note
                      || `${m.label} access is pending — cross-posting turns on as soon as it's approved.`}
                  </p>
                ) : (
                  <p className="text-sm text-ink-secondary">
                    Not set up on the server yet.
                  </p>
                )}
              </div>
              {m.connected ? (
                <Button variant="danger" onClick={() => disconnect(m)}>
                  <Unlink aria-hidden /> Disconnect
                </Button>
              ) : m.oauth_ready ? (
                <Button
                  variant="primary"
                  onClick={() => startConnect(`/api/${m.key}/connect`).catch((e) =>
                    toast(`Couldn't open the connect screen: ${e.message}`, { kind: "error" }))}
                >
                  <Link2 aria-hidden /> Connect {m.label}
                </Button>
              ) : m.coming_soon ? (
                <Button variant="secondary" disabled>
                  <Clock aria-hidden /> Connect {m.label}
                </Button>
              ) : null}
            </div>
            {!m.connected && !m.oauth_ready && !m.coming_soon && (
              <div className="rounded-tile bg-warning-soft border border-warning/30 p-4 flex gap-3 mt-3">
                <AlertTriangle size={18} className="text-warning shrink-0 mt-0.5" aria-hidden />
                <div className="text-sm min-w-0">
                  <p className="font-bold text-ink">
                    “Sign in with {m.label}” isn’t set up on the server
                  </p>
                  <p className="text-ink-secondary mt-0.5">
                    The Connect button can’t do anything until{" "}
                    {(m.oauth_missing || []).length === 1 ? "this variable is" : "these variables are"} set
                    on the deployment:{" "}
                    {(m.oauth_missing || []).map((name, j) => (
                      <span key={name}>
                        {j > 0 && ", "}
                        <code className="text-ink font-semibold">{name}</code>
                      </span>
                    ))}
                    {" "}(e.g. <code className="text-ink font-semibold">fly secrets set …</code>).
                  </p>
                </div>
              </div>
            )}
            {m.key === "etsy" && m.connected && <EtsyDefaults />}
          </div>
        ))}
      </div>
    </Card>
  );
}

// Etsy publish defaults: which shipping profile + return policy new Etsy
// listings use (Etsy requires both for physical items). Loaded from the
// seller's shop; saved into the account's marketplace settings.
function EtsyDefaults() {
  const { toast } = useToast();
  const [data, setData] = useState(null);   // {shipping_profiles, return_policies, selected}
  const [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState({});

  useEffect(() => {
    api("/api/etsy/settings-options")
      .then((d) => { setData(d); setSelected(d.selected || {}); })
      .catch(() => setData({ error: true }));
  }, []);

  if (!data) return <div className="ai-shimmer h-16 rounded-tile mt-4" aria-hidden />;
  if (data.error) {
    return (
      <p className="text-[13px] text-ink-secondary mt-3">
        Couldn’t load your Etsy shipping profiles — retry from Settings after
        reconnecting Etsy.
      </p>
    );
  }

  const save = async () => {
    setSaving(true);
    try {
      await postJson("/api/etsy/settings-options", selected);
      toast("Etsy defaults saved — new Etsy listings will use them.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't save: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 max-w-lg mt-4">
      <Field
        label="Shipping profile"
        help={(data.shipping_profiles || []).length
          ? "Etsy requires a shipping profile on every physical listing."
          : "No shipping profiles on your Etsy shop yet — create one on Etsy, then reopen Settings."}
      >
        <Select
          value={selected.shipping_profile_id || ""}
          onChange={(e) => setSelected((s) => ({ ...s, shipping_profile_id: e.target.value }))}
        >
          <option value="">— none —</option>
          {(data.shipping_profiles || []).map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </Select>
      </Field>
      <Field
        label="Return policy"
        help={(data.return_policies || []).length
          ? undefined
          : "No return policies on your Etsy shop yet — Etsy adds one the first time you set returns up in Shop Manager."}
      >
        <Select
          value={selected.return_policy_id || ""}
          onChange={(e) => setSelected((s) => ({ ...s, return_policy_id: e.target.value }))}
        >
          <option value="">— none —</option>
          {(data.return_policies || []).map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </Select>
      </Field>
      <div>
        <Button variant="secondary" onClick={save} loading={saving}>
          Save Etsy defaults
        </Button>
      </div>
    </div>
  );
}

// Leaving is as easy as joining: one button here, no email, no support ticket.
// The dialog states plainly what goes and what survives (anything already
// published stays live on the seller's own eBay account — we can delete our
// copy, not their listings).
function DeleteAccountCard() {
  const { user, setUser, loadEbayStatus, setPoliciesData } = useApp();
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [summary, setSummary] = useState(null);
  const [password, setPassword] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");

  const start = async () => {
    setPassword("");
    setError("");
    setSummary(null);
    setOpen(true);
    try {
      setSummary(await api("/api/account/summary"));
    } catch {
      setSummary({}); // the dialog still works; it just can't show the counts
    }
  };

  const remove = async () => {
    setDeleting(true);
    setError("");
    try {
      const res = await postJson("/api/account/delete", { password });
      setOpen(false);
      // Clear every trace of the account from the running app, not just the
      // user: the top bar reads the eBay connection and would keep showing
      // the deleted account's username until a reload.
      setUser(null);
      setPoliciesData(null);
      loadEbayStatus();
      toast(
        `Your account is deleted${res.deleted_listings
          ? ` — ${res.deleted_listings} listing${res.deleted_listings === 1 ? "" : "s"} and ${res.deleted_listings === 1 ? "its" : "their"} photos are gone`
          : ""}. Thanks for giving Thryft Shop a try.`,
        { kind: "success" },
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <>
      <Card>
        <SectionHeader
          icon={UserRound}
          title="Delete account"
          hint="Erases your account, listings, and photos — right here, no email required"
        />
        <div className="flex flex-col gap-4 max-w-lg">
          <p className="text-sm text-ink-secondary">
            You can close your account whenever you like. Anything you already
            published stays live on eBay under your own seller account — end
            those in eBay first if you want them gone too.
          </p>
          <div>
            <Button variant="danger" onClick={start}>
              <Trash2 aria-hidden /> Delete my account
            </Button>
          </div>
        </div>
      </Card>

      <Dialog open={open} onClose={() => !deleting && setOpen(false)} title="Delete your account?">
        <div className="flex flex-col gap-4">
          <p className="text-sm text-ink-secondary">
            This permanently erases <strong className="text-ink">{user?.email}</strong>
            {summary?.counted && summary.listings ? (
              <>, <strong className="text-ink">
                {summary.listings} listing{summary.listings === 1 ? "" : "s"}
              </strong> and every photo on them</>
            ) : (
              <> and everything saved to it</>
            )}
            {summary?.ebay_connected ? ", and disconnects your eBay account." : "."}
            {" "}It can&rsquo;t be undone.
          </p>

          {/* Never let a DB hiccup hide this: if the counts couldn't be read,
              warn generically rather than silently implying nothing is live. */}
          {summary && (summary.counted === false || !!summary.live_listings) && (
            <p className="text-sm rounded-tile border border-warning/30 bg-warning-soft p-3 text-ink">
              <AlertTriangle size={15} className="inline mr-1.5 -mt-0.5" aria-hidden />
              {summary.counted === false ? (
                <>Any listing you already published stays live on eBay under your
                  own seller account and keeps selling — deleting here only removes
                  Thryft Shop&rsquo;s copy. End them in eBay first if you want them
                  taken down.</>
              ) : (
                <>{summary.live_listings} of your listings
                  {" "}{summary.live_listings === 1 ? "is" : "are"} live on eBay.
                  {" "}{summary.live_listings === 1 ? "It stays" : "They stay"} up
                  and {summary.live_listings === 1 ? "keeps" : "keep"} selling — deleting
                  here only removes Thryft Shop&rsquo;s copy. End
                  {" "}{summary.live_listings === 1 ? "it" : "them"} in eBay first if
                  you want {summary.live_listings === 1 ? "it" : "them"} taken down.</>
              )}
            </p>
          )}

          <Field label="Confirm your password" help="So a stray tap or a borrowed phone can't do this.">
            <Input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => { setPassword(e.target.value); setError(""); }}
              onKeyDown={(e) => { if (e.key === "Enter" && password && !deleting) remove(); }}
            />
          </Field>

          {error && <p className="text-sm text-error">{error}</p>}

          <div className="flex flex-wrap gap-2.5 justify-end">
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={deleting}>
              Keep my account
            </Button>
            <Button variant="danger" onClick={remove} loading={deleting} disabled={!password}>
              Delete permanently
            </Button>
          </div>
        </div>
      </Dialog>
    </>
  );
}

// The app has no address bar, so these are the only way to reach the policies
// from inside it — which Apple requires and which is just good manners.
function LegalLinks() {
  const link = "text-ink-secondary hover:text-ink underline underline-offset-2";
  return (
    <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-secondary px-1">
      <a className={link} href="/privacy-policy" target="_blank" rel="noreferrer">Privacy policy</a>
      <a className={link} href="/terms" target="_blank" rel="noreferrer">Terms of service</a>
      <a className={link} href="/about" target="_blank" rel="noreferrer">About</a>
      <a className={link} href="mailto:leifhansen1990@gmail.com">Support</a>
    </p>
  );
}

// The read-only side of the seller's eBay account — identity, ship-from
// locations, and opted-in programs. Folded in from the old "eBay Account" nav
// page, which duplicated the editable policy defaults above.
function EbayAccountMirror() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  // The fetch itself touches state only in the promise callbacks; the shimmer
  // flag is flipped by whoever asks for a refetch. Split that way because the
  // first load doesn't need the flip at all — `loading` already starts true —
  // so the mount effect stays free of a synchronous setState.
  const fetchOverview = useCallback(() => {
    api("/api/ebay/account-overview")
      .then(setData)
      .catch(() => setData({ connected: false }))
      .finally(() => setLoading(false));
  }, []);
  // Refresh button: back to the shimmer, then re-fetch.
  const load = useCallback(() => {
    setLoading(true);
    fetchOverview();
  }, [fetchOverview]);
  useEffect(() => { fetchOverview(); }, [fetchOverview]);

  if (loading) return <div className="ai-shimmer h-24 rounded-tile" aria-hidden />;
  if (!data?.connected) {
    return <p className="text-[13px] text-ink-secondary">Couldn’t load your eBay account details.</p>;
  }

  const {
    account = {}, locations = [], programs = [], payments = {}, selected = {},
    // programs_known tells "eBay said none" apart from "eBay didn't answer".
    // Without it this panel reported an unreadable lookup as "not opted into
    // any programs", which is the one answer that makes a seller act.
    programs_known: programsKnown = false, privileges = null,
  } = data;
  const hasPolicyProgram = programs.includes("SELLING_POLICY_MANAGEMENT");
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="grid place-items-center size-10 rounded-[14px] bg-blue-soft text-blue shrink-0">
          <BadgeCheck size={20} aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-ink truncate">{account.username || "Connected"}</p>
          <p className="text-[13px] text-ink-secondary truncate">
            {account.email ? `${account.email} · ` : ""}{account.marketplace}
            {payments?.status ? ` · Payments: ${payments.status.replace(/_/g, " ").toLowerCase()}` : ""}
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={load}>
          <RefreshCw aria-hidden /> Refresh
        </Button>
        <Button variant="soft" size="sm"
          onClick={() => window.open("https://www.ebay.com/sh/ovw", "_blank", "noopener")}>
          Seller Hub <ExternalLink aria-hidden />
        </Button>
      </div>

      {/* Account health. Both of these stop a publish for a reason no listing
          field explains — an unfinished registration, and the monthly selling
          limit (eBay error 21919188). Seeing them here beats meeting them as a
          rejection. Rendered only when eBay actually answered: `privileges` is
          null when the lookup failed, and inventing "registration incomplete"
          from that would be a scary claim we cannot stand behind. */}
      {privileges && (!privileges.registration_complete || privileges.selling_limit) && (
        <div className="flex flex-col gap-1.5 rounded-tile bg-warning-soft border border-warning/30 p-3 text-[13px]">
          {!privileges.registration_complete && (
            <p className="text-ink flex gap-2">
              <AlertTriangle size={15} className="text-warning shrink-0 mt-0.5" aria-hidden />
              <span>
                eBay says this account’s seller registration isn’t finished.
                Publishing will fail until it is — finish it in Seller Hub.
              </span>
            </p>
          )}
          {privileges.selling_limit && (
            <p className="text-ink-secondary">
              Monthly selling limit:{" "}
              {privileges.selling_limit.quantity != null
                ? `${privileges.selling_limit.quantity} items`
                : "no item cap"}
              {privileges.selling_limit.amount
                ? ` · ${privileges.selling_limit.amount} ${privileges.selling_limit.currency || ""}`.trimEnd()
                : ""}
              . Publishing past it fails with “this listing would cause you to
              exceed the amount you can list”.
            </p>
          )}
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        <div>
          <p className="text-[13px] font-semibold text-ink flex items-center gap-1.5 mb-2">
            <MapPin size={14} className="text-blue" aria-hidden /> Ship-from locations
          </p>
          {locations.length ? (
            <ul className="flex flex-col gap-1.5">
              {locations.map((loc, i) => {
                const a = (loc.location && loc.location.address) || {};
                const line = [a.city, a.stateOrProvince, a.postalCode, a.country]
                  .filter(Boolean).join(", ");
                return (
                  <li key={loc.merchantLocationKey || i}
                      className="text-[13px] flex flex-wrap items-center gap-2">
                    <span className="font-medium text-ink">{loc.name || loc.merchantLocationKey}</span>
                    {loc.merchantLocationKey === selected.merchant_location_key && (
                      <TagPill tone="green">Default</TagPill>
                    )}
                    {line && <span className="text-ink-secondary">{line}</span>}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-[13px] text-ink-secondary">No inventory locations found.</p>
          )}
        </div>

        <div>
          <p className="text-[13px] font-semibold text-ink flex items-center gap-1.5 mb-2">
            <BadgeCheck size={14} className="text-blue" aria-hidden /> Opted-in programs
          </p>
          {programs.length ? (
            <div className="flex flex-wrap gap-2">
              {programs.map((p) => (
                <TagPill key={p} tone="neutral">{p.replace(/_/g, " ").toLowerCase()}</TagPill>
              ))}
            </div>
          ) : programsKnown ? (
            <p className="text-[13px] text-ink-secondary">Not opted into any programs.</p>
          ) : (
            <p className="text-[13px] text-ink-secondary">
              Couldn’t read your programs from eBay just now.
            </p>
          )}
          {programsKnown && !hasPolicyProgram && (
            <p className="text-[13px] text-ink-secondary mt-2">
              Business policies are off for this account, which is why the
              shipping, payment and return dropdowns above are empty.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

// Pricing strategy — a three-stop slider deciding where on the market range
// the AI prices every draft and comp suggestion.
const PRICING_STRATEGIES = [
  {
    value: "quick_flip", label: "Quick Flip",
    blurb: "Priced at the low end of comps to sell fast.",
  },
  {
    value: "median", label: "Median Pricing",
    blurb: "Typical middle-of-market price.",
  },
  {
    value: "long_sale", label: "Long Sale",
    blurb: "Priced at the high end — patient, maximizes the sale.",
  },
];

function PricingStrategySlider({ prefs, set }) {
  const idx = Math.max(
    0, PRICING_STRATEGIES.findIndex((s) => s.value === (prefs.pricing_strategy || "median")));
  const current = PRICING_STRATEGIES[idx];
  return (
    <div className="flex flex-col gap-2 max-w-lg">
      <input
        type="range" min="0" max="2" step="1" value={idx}
        aria-label="Pricing strategy"
        aria-valuetext={current.label}
        onChange={(e) => set("pricing_strategy", PRICING_STRATEGIES[Number(e.target.value)].value)}
        className="w-full accent-blue"
      />
      <div className="grid grid-cols-3 text-xs">
        {PRICING_STRATEGIES.map((s, i) => (
          <button
            key={s.value} type="button"
            onClick={() => set("pricing_strategy", s.value)}
            className={`bg-transparent border-0 p-0 cursor-pointer ${
              i === 0 ? "text-left" : i === 2 ? "text-right" : "text-center"} ${
              i === idx ? "font-bold text-ink" : "text-ink-faint"}`}
          >
            {s.label}
          </button>
        ))}
      </div>
      <p className="text-[13px] text-ink-secondary">{current.blurb}</p>
    </div>
  );
}

// New-listing defaults — pre-filled into every AI draft so repeat sellers stop
// re-typing package weight, dimensions, quantity, and condition. Fields only;
// the parent owns the prefs state, loading, and the shared Save button.
function NewListingDefaultsFields({ prefs, set }) {
  return (
    <div className="flex flex-col gap-5 max-w-lg">
      <div className="grid grid-cols-2 gap-4">
        <Field label="Package weight — lb">
          <Input
            type="number" min="0" step="1" inputMode="numeric"
            value={prefs.package_weight_lb ?? ""}
            onChange={(e) => set("package_weight_lb", e.target.value)}
          />
        </Field>
        <Field label="Package weight — oz">
          <Input
            type="number" min="0" max="15" step="0.1" inputMode="decimal"
            value={prefs.package_weight_oz ?? ""}
            onChange={(e) => set("package_weight_oz", e.target.value)}
          />
        </Field>
      </div>
      <div className="grid grid-cols-3 gap-4">
        {[
          ["package_length_in", "Length (in)"],
          ["package_width_in", "Width (in)"],
          ["package_height_in", "Height (in)"],
        ].map(([key, label]) => (
          <Field key={key} label={label}>
            <Input
              type="number" min="0" step="0.1" inputMode="decimal"
              value={prefs[key] ?? ""}
              onChange={(e) => set(key, e.target.value)}
            />
          </Field>
        ))}
      </div>
      <div className="grid sm:grid-cols-2 gap-4">
        <Field label="Condition" help="Set one to always use it; otherwise the AI judges from the photos.">
          <Select
            value={prefs.condition || ""}
            onChange={(e) => set("condition", e.target.value)}
          >
            <option value="">Let the AI decide (from photos)</option>
            {CONDITIONS.map((c) => (
              <option key={c} value={c}>{conditionLabel(c)}</option>
            ))}
          </Select>
        </Field>
        <Field label="Quantity" help="Only applied when listing more than one of an item.">
          <Input
            type="number" min="1" step="1" inputMode="numeric"
            value={prefs.quantity ?? ""}
            onChange={(e) => set("quantity", e.target.value)}
          />
        </Field>
      </div>
    </div>
  );
}

/* Listings left over from a previously-connected eBay account.
 *
 * Connecting a second eBay account doesn't move the first account's listings
 * anywhere — records belong to the APP user, not the eBay one — so they stay
 * in the app looking exactly like listings of the account now connected. They
 * are excluded from every eBay call (see services/listing_sync.belongs_to),
 * but "excluded" is invisible; without this notice they simply read as "the
 * new account somehow has my old items". */
function ForeignListingsNotice() {
  const { ebay, loadEbayStatus, loadListings } = useApp();
  const { toast, confirm } = useToast();
  const [working, setWorking] = useState(false);
  const foreign = ebay.foreign_listings || 0;
  // eBay-linked records from before the app tracked which account listed
  // them. After a switch these are the OLD account's items wearing no label —
  // but a seller who never switched has the same shape for their own older
  // imports, so nothing but the seller can say whose they are. That's why
  // their unlink rides the same button but only when they exist, and the
  // request says so explicitly (include_unowned).
  const unowned = ebay.unowned_listings || 0;
  const count = foreign + unowned;
  if (!count) return null;

  const release = async () => {
    const ok = await confirm({
      title: `Unlink ${count} listing${count === 1 ? "" : "s"} from your old eBay account?`,
      message: "They stay here with their photos and details, as drafts you can "
        + "publish to the account you're connected to now. Nothing is deleted, "
        + "and nothing changes on eBay."
        + (unowned
          ? ` ${unowned} of them ${unowned === 1 ? "was" : "were"} linked before `
            + "the app tracked accounts — only unlink if those items aren't "
            + "on the account you're connected to now."
          : ""),
      confirmLabel: "Unlink them",
    });
    if (!ok) return;
    setWorking(true);
    try {
      const res = await postJson("/api/ebay/release-foreign-listings",
        unowned ? { include_unowned: true } : {});
      toast(`Unlinked ${res.released} listing${res.released === 1 ? "" : "s"}.`,
        { kind: "success" });
      await Promise.all([loadEbayStatus(), loadListings()]);
    } catch (e) {
      toast(`Couldn't unlink them: ${e.message}`, { kind: "error" });
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="rounded-tile bg-warning-soft border border-warning/30 p-4 flex gap-3">
      <AlertTriangle size={18} className="text-warning shrink-0 mt-0.5" aria-hidden />
      <div className="text-sm min-w-0">
        <p className="font-bold text-ink">
          {count} listing{count === 1 ? "" : "s"} here {count === 1 ? "is" : "are"} linked
          to an eBay account that isn&apos;t the one connected
        </p>
        <p className="text-ink-secondary mt-0.5">
          They were listed on an account you connected earlier. Thryft Shop
          leaves them alone — it won’t sync, edit, or end them while
          {ebay.username ? <strong className="text-ink"> {ebay.username} </strong> : " this account "}
          is connected. Reconnect that account to manage them again, or unlink
          them here to keep the drafts and drop the old eBay link.
        </p>
        <Button variant="secondary" className="mt-2.5" onClick={release} loading={working}>
          <Unlink aria-hidden /> Unlink from the old account
        </Button>
      </div>
    </div>
  );
}

// The shortcut that CREATES a shipping policy, for sellers who don't have one
// covering the service they want. Deliberately worded as an action on the
// dropdown above ("Need another one?") rather than as a setting: it writes to
// the same eBay object the Shipping policy field selects, and presenting it as
// a peer made the two look like separate things that had to agree.
function AddShippingServiceRow({ onCreated }) {
  const { toast } = useToast();
  const [services, setServices] = useState([]);
  const [code, setCode] = useState("");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    api("/api/ebay/shipping-services")
      .then((r) => setServices(r.services || []))
      .catch(() => {});
  }, []);

  const create = async () => {
    if (!code) return;
    setCreating(true);
    try {
      const pol = await postJson("/api/ebay/ensure-policy", { service_code: code });
      toast(pol.created
        ? `Created "${pol.name}" on your eBay account and selected it above.`
        : `Your "${pol.name}" policy already ships this — selected it above.`,
        { kind: "success" });
      onCreated(pol);
      setCode("");
    } catch (e) {
      toast(`Couldn't add that service: ${e.message}`, { kind: "error" });
    } finally {
      setCreating(false);
    }
  };

  return (
    <Field
      label={
        <span className="inline-flex items-center gap-1.5">
          <Truck size={14} aria-hidden /> Need another shipping policy?
        </span>
      }
      help="Pick a carrier service and we'll create the matching shipping policy on your eBay account (or reuse one you already have) and select it above."
    >
      <div className="flex gap-2">
        <div className="flex-1 min-w-0">
          <Select value={code} onChange={(e) => setCode(e.target.value)}>
            <option value="">Choose a carrier service…</option>
            {services.map((s) => (
              <option key={s.code} value={s.code}>
                {s.label}{s.note ? ` — ${s.note}` : ""}
              </option>
            ))}
          </Select>
        </div>
        <Button variant="secondary" onClick={create} loading={creating} disabled={!code}>
          <Plus aria-hidden /> Add
        </Button>
      </div>
    </Field>
  );
}

// Profile: display name (shown in greetings) + one-tap sync from eBay.
function ProfileCard() {
  const { user, setUser, ebay } = useApp();
  const { toast } = useToast();
  const displayName = user?.display_name || "";
  const [name, setName] = useState(displayName);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);

  // The field is a draft the user types into, re-seeded whenever the stored
  // display name changes underneath it — a save, an eBay sync, or signing in as
  // someone else. This is React's documented "adjust state when a prop changes"
  // pattern: compare against the previous value during render, so the input
  // never paints a frame of the old name the way an effect would.
  const [prevDisplayName, setPrevDisplayName] = useState(displayName);
  if (displayName !== prevDisplayName) {
    setPrevDisplayName(displayName);
    setName(displayName);
  }

  const save = async () => {
    setSaving(true);
    try {
      const res = await postJson("/api/profile", { display_name: name.trim() });
      setUser((u) => ({ ...u, display_name: res.user.display_name }));
      toast("Profile saved.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't save profile: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };

  const sync = async () => {
    setSyncing(true);
    try {
      const p = await api("/api/profile/sync-ebay", { method: "POST" });
      setUser((u) => ({ ...u, display_name: p.user.display_name }));
      setName(p.user.display_name || "");
      toast("Synced from eBay — username, policies, and location pulled in.", { kind: "success" });
    } catch (e) {
      toast(`Sync failed: ${e.message}`, { kind: "error" });
    } finally {
      setSyncing(false);
    }
  };

  return (
    <Card>
      <SectionHeader
        icon={UserRound}
        title="Profile"
        hint="How Thryft Shop greets you — sync pulls your eBay username and settings"
      />
      <div className="flex flex-col gap-4 max-w-lg">
        <Field label="Display name" help={`Email: ${user?.email || ""}`}>
          <Input
            maxLength={80}
            placeholder="e.g. your shop name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <div className="flex flex-wrap gap-2.5">
          <Button variant="primary" onClick={save} loading={saving}>Save profile</Button>
          {ebay.connected && (
            <Button variant="secondary" onClick={sync} loading={syncing}>
              <RefreshCw aria-hidden /> Sync from eBay
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}

function SettingsShell({ children }) {
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-xl sm:text-2xl font-bold text-ink">Settings</h1>
        <p className="text-sm text-ink-secondary mt-1">
          Your eBay connection and the defaults applied to every publish.
        </p>
      </div>
      {children}
    </div>
  );
}
