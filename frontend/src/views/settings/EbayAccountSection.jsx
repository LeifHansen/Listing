import { useState } from "react";
import {
  AlertTriangle, BadgeCheck, ExternalLink, Link2, RefreshCw, Unlink, Wallet,
} from "lucide-react";
import { api, postJson, startConnect } from "@/lib/api";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { TagPill } from "@/components/ui/badges";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toaster";
import { useSettings } from "./context";
import { ForeignListingsNotice } from "./ForeignListingsNotice";

const SELLER_HUB = "https://www.ebay.com/sh/ovw";

// eBay's payments-program status, as a pill a seller can read. OPTED_IN is
// what a finished payout setup looks like; anything else is worth a glance.
function paymentsTone(status) {
  const s = String(status || "").toUpperCase();
  if (!s) return null;
  if (s === "OPTED_IN") return { tone: "green", label: "Payouts set up" };
  return { tone: "yellow", label: `Payouts: ${s.replace(/_/g, " ").toLowerCase()}` };
}

/**
 * The account every listing publishes to, and what eBay says about it.
 *
 * One card now: the connection (connect, reconnect, disconnect) and the
 * read-only facts that used to live in a separate mirror at the bottom of
 * the page -- payouts, the monthly selling limit, an unfinished
 * registration, the programs the account is in. The deployment environment
 * is no longer printed beside the username: a seller cannot act on it, and
 * the payments check deliberately stopped exposing it.
 */
export function EbayAccountSection() {
  const { ebay, loadEbayStatus } = useApp();
  const { toast, confirm } = useToast();
  const { overview, overviewLoading, loadOverview, resetPolicies } = useSettings();
  const [checking, setChecking] = useState(false);

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
      resetPolicies();
      toast("Disconnected. Click 'Connect eBay' to link the account you want.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't disconnect: ${e.message}`, { kind: "error" });
    }
  };

  const checkPayout = async () => {
    setChecking(true);
    try {
      // The server answers with a product STATE and the sentence to show —
      // never the environment, a raw status or eBay's whole body.
      const s = await api("/api/ebay/payments-status");
      const KIND = {
        ready: "success",
        action_required: "warning",
        reconnect_required: "warning",
        unavailable: "warning",
        contact_support: "error",
      };
      toast(s.message, { kind: KIND[s.state] || "warning" });
    } catch (e) {
      toast(`Payments check failed: ${e.message}`, { kind: "error" });
    } finally {
      setChecking(false);
    }
  };

  const connect = () => startConnect("/api/ebay/connect").catch((e) =>
    toast(`Couldn't open the connect screen: ${e.message}`, { kind: "error" }));

  if (!ebay.connected) {
    return (
      <div className="flex flex-col gap-4">
        <p className="text-sm text-ink-secondary">
          Not connected yet — publishing runs in dry-run mode (you get the exact eBay
          API payload without posting).
        </p>
        {ebay.oauth_ready ? (
          <div>
            <Button variant="primary" onClick={connect}>
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
    );
  }

  const o = overview && overview.connected ? overview : null;
  const privileges = o?.privileges || null;
  const programs = o?.programs || [];
  const programsKnown = Boolean(o?.programs_known);
  const payments = paymentsTone(o?.payments?.status);
  const hasPolicyProgram = programs.includes("SELLING_POLICY_MANAGEMENT");

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="grid place-items-center size-10 rounded-[14px] bg-blue-soft text-blue shrink-0">
          <BadgeCheck size={20} aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          {ebay.username ? (
            <p className="font-semibold text-ink truncate">
              Connected as <strong>{ebay.username}</strong>
              {ebay.env === "sandbox" && (
                <TagPill tone="yellow" className="ml-2 align-middle">Sandbox</TagPill>
              )}
            </p>
          ) : (
            <p className="text-sm text-ink">
              Connected, but this link was made before we could read the account name.
              <strong> Disconnect and reconnect</strong> to confirm which account it is.
            </p>
          )}
          <p className="text-[13px] text-ink-secondary truncate">
            {ebay.email ? `${ebay.email} · ` : ""}every listing you publish goes to this account.
          </p>
        </div>
        {payments && <TagPill tone={payments.tone}>{payments.label}</TagPill>}
      </div>

      <ForeignListingsNotice />

      {overviewLoading && !o ? (
        <Skeleton className="h-10 rounded-tile" />
      ) : o ? (
        <>
          {/* Account health. Both of these stop a publish for a reason no
              listing field explains — an unfinished registration, and the
              monthly selling limit (eBay error 21919188). Rendered only when
              eBay actually answered: `privileges` is null when the lookup
              failed, and inventing "registration incomplete" from that would
              be a claim we cannot stand behind. */}
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
          {programs.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-[13px]">
              <span className="text-ink-secondary">eBay programs:</span>
              {programs.map((p) => (
                <TagPill key={p} tone="neutral">{p.replace(/_/g, " ").toLowerCase()}</TagPill>
              ))}
            </div>
          )}
          {programsKnown && !hasPolicyProgram && (
            <p className="text-[13px] text-ink-secondary">
              Business policies are off for this account — turn them on under
              Returns &amp; payment below before publishing.
            </p>
          )}
        </>
      ) : overview && !overview.connected ? (
        <p className="text-[13px] text-ink-secondary">
          Couldn’t read the account details from eBay just now.
        </p>
      ) : null}

      <div className="flex flex-wrap gap-2.5">
        <Button variant="secondary" onClick={checkPayout} loading={checking}>
          <Wallet aria-hidden /> Check payout setup
        </Button>
        <Button variant="secondary"
          onClick={() => window.open(SELLER_HUB, "_blank", "noopener")}>
          Seller Hub <ExternalLink aria-hidden />
        </Button>
        <Button variant="ghost" onClick={loadOverview} loading={overviewLoading}>
          <RefreshCw aria-hidden /> Refresh
        </Button>
        <Button variant="danger" onClick={disconnect}>
          <Unlink aria-hidden /> Disconnect / switch account
        </Button>
      </div>
    </div>
  );
}
