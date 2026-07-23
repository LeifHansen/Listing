import { useEffect, useState } from "react";
import {
  Store, Truck, CreditCard, Undo2, MapPin, BadgeCheck, ExternalLink, RefreshCw,
} from "lucide-react";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { api, postJson } from "@/lib/api";
import { Card, SectionHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Field, Select } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";

// The three business-policy types → their label, icon, and the selected-default
// key used by POST /api/ebay/policies.
const POLICY_META = {
  fulfillment: { label: "Shipping", icon: Truck, key: "fulfillment_policy_id" },
  payment: { label: "Payment", icon: CreditCard, key: "payment_policy_id" },
  return: { label: "Returns", icon: Undo2, key: "return_policy_id" },
};

// EbayAccountView — a live mirror of the seller's most-updated eBay settings.
// Business-policy defaults are editable in place (they drive every publish);
// the rest is read-only with a Seller Hub link for deeper edits.
export function EbayAccountView() {
  const { setView } = useApp();
  const { toast } = useToast();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(null);

  const load = () => {
    setLoading(true);
    api("/api/ebay/account-overview")
      .then(setData)
      .catch(() => setData({ connected: false }))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const setDefault = async (key, value) => {
    setSaving(key);
    try {
      await postJson("/api/ebay/policies", { [key]: value });
      setData((d) => ({ ...d, selected: { ...d.selected, [key]: value } }));
      toast("Default updated.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't update: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(null);
    }
  };

  if (loading) {
    return <p className="text-sm text-ink-secondary">Loading your eBay settings…</p>;
  }

  if (!data?.connected) {
    return (
      <Card className="p-8 text-center flex flex-col items-center gap-3">
        <Store size={28} className="text-ink-faint" aria-hidden />
        <p className="font-semibold text-ink">eBay isn't connected</p>
        <p className="text-sm text-ink-secondary max-w-sm">
          Connect your eBay account to mirror and manage your seller settings here.
        </p>
        <Button variant="primary" onClick={() => setView("settings")}>Connect eBay</Button>
      </Card>
    );
  }

  const {
    account = {}, policies = {}, locations = [], programs = [], payments = {},
    selected = {},
  } = data;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-ink">eBay account</h1>
          <p className="text-sm text-ink-secondary mt-1">
            A live mirror of your most-updated eBay seller settings.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={load}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
          <Button variant="soft" size="sm"
            onClick={() => window.open("https://www.ebay.com/sh/ovw", "_blank", "noopener")}>
            Seller Hub <ExternalLink aria-hidden />
          </Button>
        </div>
      </div>

      <Card className="p-5 flex items-center gap-4">
        <span className="grid place-items-center size-11 rounded-[14px] bg-blue-soft text-blue shrink-0">
          <BadgeCheck size={22} aria-hidden />
        </span>
        <div className="min-w-0">
          <p className="font-semibold text-ink truncate">{account.username || "Connected"}</p>
          <p className="text-[13px] text-ink-secondary truncate">
            {account.email ? `${account.email} · ` : ""}{account.marketplace}
            {payments?.paymentsProgramStatus ? ` · Payments: ${payments.paymentsProgramStatus}` : ""}
          </p>
        </div>
      </Card>

      <div>
        <SectionHeader icon={Store} title="Business policies" />
        <div className="grid md:grid-cols-3 gap-4">
          {Object.entries(POLICY_META).map(([type, meta]) => {
            const list = policies[type] || [];
            const Icon = meta.icon;
            return (
              <Card key={type} className="p-5 flex flex-col gap-3">
                <div className="flex items-center gap-2 font-semibold text-ink">
                  <Icon size={17} className="text-blue" aria-hidden /> {meta.label}
                </div>
                {list.length ? (
                  <>
                    <Field label="Default for new listings">
                      <Select
                        value={selected[meta.key] || ""}
                        disabled={saving === meta.key}
                        onChange={(e) => setDefault(meta.key, e.target.value)}
                      >
                        {list.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name}{p.summary ? ` · ${p.summary}` : ""}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    <p className="text-xs text-ink-faint">
                      {list.length} polic{list.length === 1 ? "y" : "ies"} on your account
                    </p>
                  </>
                ) : (
                  <p className="text-[13px] text-ink-secondary">
                    No {meta.label.toLowerCase()} policies yet — create one in Seller Hub.
                  </p>
                )}
              </Card>
            );
          })}
        </div>
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        <Card className="p-5">
          <div className="flex items-center gap-2 font-semibold text-ink mb-3">
            <MapPin size={17} className="text-blue" aria-hidden /> Ship-from locations
          </div>
          {locations.length ? (
            <ul className="flex flex-col gap-2">
              {locations.map((loc, i) => {
                const a = (loc.location && loc.location.address) || {};
                const line = [a.city, a.stateOrProvince, a.postalCode, a.country]
                  .filter(Boolean).join(", ");
                const isDefault = loc.merchantLocationKey === selected.merchant_location_key;
                return (
                  <li key={loc.merchantLocationKey || i} className="text-[13px] flex flex-wrap items-center gap-2">
                    <span className="font-medium text-ink">{loc.name || loc.merchantLocationKey}</span>
                    {isDefault && <TagPill tone="green">Default</TagPill>}
                    {line && <span className="text-ink-secondary">{line}</span>}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-[13px] text-ink-secondary">No inventory locations found.</p>
          )}
        </Card>

        <Card className="p-5">
          <div className="flex items-center gap-2 font-semibold text-ink mb-3">
            <BadgeCheck size={17} className="text-blue" aria-hidden /> Opted-in programs
          </div>
          {programs.length ? (
            <div className="flex flex-wrap gap-2">
              {programs.map((p) => (
                <TagPill key={p} tone="neutral">{p.replace(/_/g, " ").toLowerCase()}</TagPill>
              ))}
            </div>
          ) : (
            <p className="text-[13px] text-ink-secondary">Not opted into any programs.</p>
          )}
        </Card>
      </div>

      <p className="text-xs text-ink-faint">
        Handling time, store, and vacation settings live in eBay Seller Hub — use the button above to manage them.
      </p>
    </div>
  );
}
