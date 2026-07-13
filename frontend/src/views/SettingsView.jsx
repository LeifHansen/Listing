import { useCallback, useEffect, useState } from "react";
import {
  Link2, Unlink, Wallet, ExternalLink, CheckCircle2, AlertTriangle,
  MapPin, Settings as SettingsIcon, LogIn,
} from "lucide-react";
import { api, postJson } from "@/lib/api";
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
    const payload = { ...selected };
    if (postal.trim()) payload.ship_from_postal = postal.trim();
    setSaving(true);
    try {
      await postJson("/api/ebay/policies", payload);
      setPoliciesData(null); // refresh the publish-step summary next time
      toast("Saved. These now apply to every listing you publish.", { kind: "success" });
      load();
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

      {/* Listing defaults */}
      <Card>
        <SectionHeader
          icon={SettingsIcon}
          title="Listing defaults"
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

            <div>
              <Button variant="primary" size="lg" onClick={save} loading={saving}>
                Save defaults
              </Button>
            </div>
          </div>
        )}
      </Card>
    </SettingsShell>
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
