import { useCallback, useEffect, useState } from "react";
import {
  Link2, Unlink, Wallet, ExternalLink, CheckCircle2, AlertTriangle,
  MapPin, Settings as SettingsIcon, LogIn, UserRound, RefreshCw,
  PackageOpen, Truck, Plus, TrendingUp, Megaphone, Store, BadgeCheck,
} from "lucide-react";
import { api, postJson } from "@/lib/api";
import { CONDITIONS, conditionLabel } from "@/lib/utils";
import { useApp } from "@/store";
import { Card, SectionHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";
import { EmptyState } from "@/components/ui/EmptyState";
import { RobotIllustration } from "@/components/ui/illustrations";
import { useToast } from "@/components/ui/Toaster";

const POLICY_KINDS = [
  { key: "fulfillment", field: "fulfillment_policy_id", label: "Shipping policy" },
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

  useEffect(() => {
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
        const payload = { ...selected };
        if (postal.trim()) payload.ship_from_postal = postal.trim();
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
            illustration={RobotIllustration}
            title="Log in first"
            message="Your eBay connection and listing defaults live on your account."
            action={
              <Button variant="primary" size="lg" onClick={() => openAuth()}>
                <LogIn aria-hidden /> Log in
              </Button>
            }
          />
        </Card>
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
                  onClick={() => { window.location.href = "/api/ebay/connect"; }}
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

              {POLICY_KINDS.map(({ key, field, label }) => {
                const opts = data.policies[key] || [];
                return (
                  <Field
                    key={key} label={label}
                    help={opts.length ? undefined : `No ${label.toLowerCase()} on eBay yet.`}
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
                onCreated={(pol) => {
                  setSelected((s) => ({ ...s, fulfillment_policy_id: pol.id }));
                  setPoliciesData(null);
                  load();
                }}
              />

              {POLICY_KINDS.some(({ key }) => !(data.policies[key] || []).length) && (
                <div className="rounded-tile bg-warning-soft border border-warning/30 p-4 text-sm">
                  <p className="text-ink flex gap-2">
                    <AlertTriangle size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
                    <span>
                      Missing a policy? eBay requires shipping, payment &amp; return policies to
                      publish. Create them on eBay, then reopen this page.
                    </span>
                  </p>
                  <a
                    href={data.manage_url} target="_blank" rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 mt-2 font-semibold text-blue"
                  >
                    Manage eBay business policies <ExternalLink size={13} aria-hidden />
                  </a>
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
    </SettingsShell>
  );
}

// The read-only side of the seller's eBay account — identity, ship-from
// locations, and opted-in programs. Folded in from the old "eBay Account" nav
// page, which duplicated the editable policy defaults above.
function EbayAccountMirror() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    api("/api/ebay/account-overview")
      .then(setData)
      .catch(() => setData({ connected: false }))
      .finally(() => setLoading(false));
  }, []);
  useEffect(load, [load]);

  if (loading) return <div className="ai-shimmer h-24 rounded-tile" aria-hidden />;
  if (!data?.connected) {
    return <p className="text-[13px] text-ink-secondary">Couldn’t load your eBay account details.</p>;
  }

  const { account = {}, locations = [], programs = [], payments = {}, selected = {} } = data;
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
            {payments?.paymentsProgramStatus ? ` · Payments: ${payments.paymentsProgramStatus}` : ""}
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
          ) : (
            <p className="text-[13px] text-ink-secondary">Not opted into any programs.</p>
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

// One-tap "create an eBay shipping policy for any service" — the dropdown of
// all eBay options; picking one creates (or reuses) a policy on the account.
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
          <Truck size={14} aria-hidden /> Add a shipping service
        </span>
      }
      help="All the eBay options in one dropdown — picking one creates (or reuses) a shipping policy on your eBay account, ready to select as your default above."
    >
      <div className="flex gap-2">
        <div className="flex-1 min-w-0">
          <Select value={code} onChange={(e) => setCode(e.target.value)}>
            <option value="">Choose an eBay shipping service…</option>
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
  const [name, setName] = useState(user?.display_name || "");
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);

  useEffect(() => { setName(user?.display_name || ""); }, [user?.display_name]);

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
        <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-ink">Settings</h1>
        <p className="text-sm text-ink-secondary mt-1">
          Your eBay connection and the defaults applied to every publish.
        </p>
      </div>
      {children}
    </div>
  );
}
