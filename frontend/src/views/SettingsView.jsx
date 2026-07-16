import { useCallback, useEffect, useState } from "react";
import {
  Link2, Unlink, Wallet, ExternalLink, CheckCircle2, AlertTriangle,
  MapPin, Settings as SettingsIcon, LogIn, UserRound, RefreshCw,
} from "lucide-react";
import { api, postJson } from "@/lib/api";
import { useApp } from "@/store";
import { Card, SectionHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select, Toggle } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";
import { EmptyState } from "@/components/ui/EmptyState";
import { RobotIllustration } from "@/components/ui/illustrations";
import { useToast } from "@/components/ui/Toaster";
import { SHIPPING_SERVICES, DEFAULT_SHIPPING_SERVICE } from "@/lib/shipping";
import { TemplateManager } from "@/views/listing/Templates";
import { LayoutTemplate } from "lucide-react";

const POLICY_KINDS = [
  { key: "fulfillment", field: "fulfillment_policy_id", label: "Shipping policy" },
  { key: "payment", field: "payment_policy_id", label: "Payment policy" },
  { key: "return", field: "return_policy_id", label: "Return policy" },
];

// Settings — eBay account + the listing defaults applied to every publish.
// Package defaults (weight/dims) pre-fill the Shipping card on new listings
// when the AI didn't measure anything. System fallback: 2 lb 8 oz, 8×8×8 in.
const PKG_FIELDS = [
  ["default_weight_lb", "Weight — lb", 2],
  ["default_weight_oz", "Weight — oz", 8],
  ["default_length_in", "Length (in)", 8],
  ["default_width_in", "Width (in)", 8],
  ["default_height_in", "Height (in)", 8],
];

export function SettingsView() {
  const {
    user, setUser, openAuth, ebay, loadEbayStatus, policiesData, setPoliciesData,
  } = useApp();
  const { toast, confirm } = useToast();
  const [data, setData] = useState(policiesData);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [postal, setPostal] = useState("");
  const [selected, setSelected] = useState({});
  const [pkg, setPkg] = useState(() => Object.fromEntries(
    PKG_FIELDS.map(([key, , dflt]) => [key, user?.prefs?.[key] ?? dflt])));
  const [shipService, setShipService] = useState(
    user?.prefs?.default_shipping_service || DEFAULT_SHIPPING_SERVICE);

  // Reseed when the user (or their prefs) arrive AFTER mount — e.g. Settings
  // opened before login. Without this the fields showed system defaults and
  // "Save defaults" silently overwrote the user's saved values.
  useEffect(() => {
    if (!user) return;
    setPkg(Object.fromEntries(
      PKG_FIELDS.map(([key, , dflt]) => [key, user.prefs?.[key] ?? dflt])));
    setShipService(user.prefs?.default_shipping_service || DEFAULT_SHIPPING_SERVICE);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id, user?.prefs]);

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

  const save = async () => {
    setSaving(true);
    try {
      // Package defaults live on the user profile; policies live on eBay.
      const prefs = Object.fromEntries(
        Object.entries(pkg).map(([k, v]) => [k, parseFloat(v) || 0]));
      prefs.default_shipping_service = shipService;
      const res = await postJson("/api/profile", prefs);
      setUser((u) => ({ ...u, prefs: res.user.prefs }));
      if (ebay.connected) {
        const payload = { ...selected };
        if (postal.trim()) payload.ship_from_postal = postal.trim();
        await postJson("/api/ebay/policies", payload);
        setPoliciesData(null); // refresh the publish-step summary next time
        load();
      }
      toast("Saved. These now apply to every listing you publish.", { kind: "success" });
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

  const [creating, setCreating] = useState(false);

  // One tap pushes sensible starter policy templates to the user's eBay
  // account (only creating whatever kinds are missing) and re-pulls the lists.
  const createStarters = async () => {
    setCreating(true);
    try {
      const res = await postJson("/api/ebay/ensure-defaults", {});
      const made = ["fulfillment", "payment", "return"]
        .filter((k) => res[k]?.created).map((k) => res[k].name);
      toast(made.length
        ? `Created on eBay: ${made.join(", ")} — and set as your defaults.`
        : "Your eBay account already had the policies it needs — defaults updated.",
        { kind: "success" });
      setPoliciesData(null);
      load();
    } catch (e) {
      toast(`Couldn't create the starter policies: ${e.message}`, { kind: "error" });
    } finally {
      setCreating(false);
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

      {/* eBay account */}
      <Card>
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
            <div>
              <Button
                variant="primary"
                onClick={() => {
                  if (!ebay.oauth_ready) {
                    toast("eBay isn't configured on the server yet (needs EBAY_CLIENT_ID / SECRET / RUNAME).", { kind: "warning" });
                    return;
                  }
                  window.location.href = "/api/ebay/connect";
                }}
              >
                <Link2 aria-hidden /> Connect eBay
              </Button>
            </div>
          </div>
        )}
      </Card>

      {/* Listing templates */}
      <Card>
        <SectionHeader
          icon={LayoutTemplate}
          title="Listing templates"
          hint="Reusable logistics (category, condition, package, shipping) — pick one when starting a new listing"
        />
        <TemplateManager />
      </Card>

      {/* Listing defaults */}
      <Card>
        <SectionHeader
          icon={SettingsIcon}
          title="Listing defaults"
          hint="Applied to every listing you publish — shipping, payment & returns come from your eBay business policies"
        />
        <div className="flex flex-col gap-5 max-w-lg mb-5">
          <div>
            <p className="text-[13px] font-semibold text-ink mb-1.5">Default package</p>
            <p className="text-xs text-ink-secondary mb-3">
              Auto-applied to every new listing's Shipping card (you can still
              adjust any single listing before publishing). Leave a field at 0
              to fall back to the built-in default.
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
              {PKG_FIELDS.map(([key, label]) => (
                <Field key={key} label={label}>
                  <Input
                    type="number" min="0" step="0.1" inputMode="decimal"
                    value={pkg[key]}
                    onChange={(e) => setPkg((c) => ({ ...c, [key]: e.target.value }))}
                  />
                </Field>
              ))}
            </div>
          </div>

          <div>
            <p className="text-[13px] font-semibold text-ink mb-1.5">Default shipping service</p>
            <p className="text-xs text-ink-secondary mb-3">
              Pre-selected on every new listing. When your eBay account is
              connected, this picks the matching shipping business policy
              automatically (and creates a USPS Ground Advantage one if you have
              none yet).
            </p>
            <Select
              className="max-w-xs"
              value={shipService}
              onChange={(e) => setShipService(e.target.value)}
            >
              {SHIPPING_SERVICES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </Select>
          </div>
        </div>

        {!ebay.connected ? (
          <p className="text-sm text-ink-secondary">
            Connect your eBay account to also pick shipping, payment, and return
            policies — they come from there.
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
              const err = (data.policies.errors || {})[key];
              const help = opts.length
                ? undefined
                : err === "not_opted_in"
                  ? "Your eBay account isn't opted into Business Policies yet — tap “Create starter policies” below to opt in and add one."
                  : err
                    ? `Couldn't load from eBay (${err}). Try “Create starter policies” below.`
                    : `No ${label.toLowerCase()} on eBay yet.`;
              return (
                <Field key={key} label={label} help={help}>
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

            {POLICY_KINDS.some(({ key }) => !(data.policies[key] || []).length) && (
              <div className="rounded-tile bg-warning-soft border border-warning/30 p-4 text-sm">
                <p className="text-ink flex gap-2">
                  <AlertTriangle size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
                  <span>
                    eBay requires shipping, payment &amp; return policies to publish, and
                    your account is missing some. One tap creates sensible starter
                    templates on your eBay account: USPS Ground Advantage shipping,
                    Managed Payments, and 30-day returns.
                  </span>
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <Button variant="primary" size="sm" onClick={createStarters} loading={creating}>
                    Create starter policies on eBay
                  </Button>
                  <a
                    href={data.manage_url} target="_blank" rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-[13px] font-semibold text-blue"
                  >
                    or manage them on eBay <ExternalLink size={13} aria-hidden />
                  </a>
                </div>
              </div>
            )}

          </div>
        )}

        <div className="mt-5">
          <Button variant="primary" size="lg" onClick={save} loading={saving}>
            Save defaults
          </Button>
        </div>
      </Card>
    </SettingsShell>
  );
}

// Profile: display name (shown in greetings) + one-tap sync from eBay.
function ProfileCard() {
  const { user, setUser, ebay, loadEbaySync } = useApp();
  const { toast } = useToast();
  const [name, setName] = useState(user?.display_name || "");
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);

  useEffect(() => { setName(user?.display_name || ""); }, [user?.display_name]);

  const toggleSync = async (on) => {
    setUser((u) => ({ ...u, prefs: { ...(u.prefs || {}), sync_ebay_listings: on } }));
    try {
      await postJson("/api/profile", { sync_ebay_listings: on });
      loadEbaySync();
      toast(on
        ? "eBay sync on — your live listings and items-sold count will show up shortly."
        : "eBay sync off.", { kind: "success" });
    } catch (e) {
      setUser((u) => ({ ...u, prefs: { ...(u.prefs || {}), sync_ebay_listings: !on } }));
      toast(`Couldn't update: ${e.message}`, { kind: "error" });
    }
  };

  const toggleAutoPromote = async (on) => {
    setUser((u) => ({ ...u, prefs: { ...(u.prefs || {}), auto_promote: on } }));
    try {
      await postJson("/api/profile", { auto_promote: on });
      toast(on
        ? "New listings will default to eBay's recommended promotion rate."
        : "Auto-promote off.", { kind: "success" });
    } catch (e) {
      setUser((u) => ({ ...u, prefs: { ...(u.prefs || {}), auto_promote: !on } }));
      toast(`Couldn't update: ${e.message}`, { kind: "error" });
    }
  };

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
        hint="How Thryft greets you — sync pulls your eBay username and settings"
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

        {ebay.connected && (
          <div className="border-t border-line pt-4">
            <Toggle
              checked={!!user?.prefs?.sync_ebay_listings}
              onChange={toggleSync}
              label="Sync all my eBay listings & sales"
              help="Off (default): the Listings tab shows only listings you created in Thryft. On: pulls your entire active eBay inventory into the Listings tab (marked with an eBay badge) so you can edit price, quantity, or end them here — plus an items-sold tile on the dashboard."
            />
            <div className="border-t border-line pt-4">
              <Toggle
                checked={!!user?.prefs?.auto_promote}
                onChange={toggleAutoPromote}
                label="Auto-promote new listings"
                help="Turns on Promoted Listings at eBay's recommended ad rate for every new listing by default (you can still change it per listing). Requires Promoted Listings to be enabled on your eBay account."
              />
            </div>
          </div>
        )}
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
