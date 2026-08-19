import { useEffect } from "react";
import { motion } from "framer-motion";
import {
  Rocket, Save, CheckCircle2, AlertTriangle, ArrowRight, Eye, ListChecks,
  RefreshCw, ExternalLink, Ban, Trash2,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { WorkflowCard } from "./WorkflowCard";
import { MarketTargetChips, usePublishTargets } from "./publishShared";
import { Button } from "@/components/ui/Button";

function nameFor(data, key, field) {
  return ((data.policies[key] || []).find((p) => p.id === data.selected[field]) || {}).name || "not set";
}

// One row per marketplace after a multi-marketplace publish: ok/fail icon,
// the marketplace's own message, a "View on X" link when it's live, and each
// marketplace's fix-it issues with the same jump buttons the eBay panel has.
function MultiResultPanel({ r, onFix }) {
  const { marketplaces } = useApp();
  const labelFor = (key) =>
    (marketplaces.find((m) => m.key === key) || {}).label || key;
  const entries = Object.entries(r.results || {});
  const anyFail = entries.some(([, res]) => !res.ok);
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn(
        "rounded-tile border p-4",
        anyFail ? "bg-warning-soft border-warning/30"
          : "bg-success-soft border-success/25",
      )}
    >
      <p className="font-bold text-sm text-ink flex items-center gap-2">
        {anyFail
          ? <AlertTriangle size={17} className="text-warning" aria-hidden />
          : <CheckCircle2 size={17} className="text-success" aria-hidden />}
        {r.message || "Done!"}
      </p>
      <ul className="mt-3 flex flex-col gap-3.5">
        {entries.map(([key, res]) => (
          <li key={key} className="text-sm">
            <p className="font-semibold text-ink flex items-center gap-1.5">
              {res.ok
                ? <CheckCircle2 size={15} className="text-success" aria-hidden />
                : <AlertTriangle size={15} className="text-warning" aria-hidden />}
              {labelFor(key)}
              {res.dry_run && (
                <span className="text-xs font-medium text-ink-faint">dry run</span>
              )}
            </p>
            {res.message && (
              <p className="text-ink-secondary mt-0.5">{res.message}</p>
            )}
            {res.ok && res.url && (
              <a
                href={res.url} target="_blank" rel="noopener noreferrer"
                className="inline-flex items-center gap-1 mt-1 text-[13px] font-semibold text-blue hover:underline"
              >
                View on {labelFor(key)} <ExternalLink size={12} aria-hidden />
              </a>
            )}
            {(res.issues || []).length > 0 && (
              <ul className="mt-2 flex flex-col gap-2.5">
                {res.issues.map((it, i) => (
                  <li key={i} className="text-sm">
                    <p className="font-semibold text-ink">
                      {it.title}
                      {it.level === "warn" && (
                        <span className="ml-2 text-xs font-medium text-ink-faint">nice to have</span>
                      )}
                    </p>
                    {it.fix && <p className="text-ink-secondary mt-0.5">{it.fix}</p>}
                    {it.target && it.target !== "generic" && (
                      <Button variant="soft" size="sm" className="mt-2" onClick={() => onFix(it.target)}>
                        Fix this <ArrowRight aria-hidden />
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </motion.div>
  );
}

// Publish — the last card: what will apply, the two big buttons, and a
// friendly "what to fix" panel when eBay pushes back.
export function PublishCard({ w }) {
  const { canPublishLive, ebay, setView, policiesData, setPoliciesData } = useApp();
  const r = w.publishResult;

  // Show which shipping/payment/return policies will apply.
  useEffect(() => {
    if (!ebay.connected || policiesData) return;
    api("/api/ebay/policies").then(setPoliciesData).catch(() => {});
  }, [ebay.connected, policiesData, setPoliciesData]);

  const onFix = (target) => {
    if (target === "location" || target === "policies") { setView("settings"); return; }
    w.setFixTarget(null);
    // Re-set on the next frame so the flagged card re-triggers its scroll.
    requestAnimationFrame(() => w.setFixTarget(target));
  };

  const multiOk = r?.multi && Object.values(r.results || {}).every((res) => res.ok);
  const publishedOk = r && !r.error && !r.multi
    && (r.published || r.draft || r.ebay_draft || r.dry_run || r.preflight);

  return (
    <WorkflowCard
      id="publish" icon={Rocket} title="Publish"
      hint={w.isLive
        ? "This listing is LIVE on eBay — Update Live Listing pushes your edits straight to it; End listing takes it off eBay"
        : canPublishLive
          ? "Save as Draft saves it here and stages it on your connected eBay account (as an unpublished offer); Publish Live makes it a live listing"
          : "Dry-run mode: no eBay connection yet, so publishing generates the exact API payload to inspect"}
      state={publishedOk || multiOk ? "complete" : "todo"}
    >
      <div className="flex flex-col gap-5">
        {r?.multi && <MultiResultPanel r={r} onFix={onFix} />}
        {ebay.connected && policiesData && (
          <p className="text-[13px] text-ink-secondary"
            title={`Business policies applied to this listing — Shipping: ${nameFor(policiesData, "fulfillment", "fulfillment_policy_id")} · Payment: ${nameFor(policiesData, "payment", "payment_policy_id")} · Returns: ${nameFor(policiesData, "return", "return_policy_id")}`}>
            <strong className="text-ink">Policies:</strong>{" "}
            {nameFor(policiesData, "fulfillment", "fulfillment_policy_id")} ·{" "}
            {nameFor(policiesData, "payment", "payment_policy_id")} ·{" "}
            {nameFor(policiesData, "return", "return_policy_id")}{" "}
            <button
              type="button"
              onClick={() => setView("settings")}
              className="text-blue font-semibold cursor-pointer hover:underline"
            >
              change
            </button>
          </p>
        )}

        {/* Success states */}
        {publishedOk && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="rounded-tile bg-success-soft border border-success/25 p-4 flex gap-3"
          >
            <CheckCircle2 size={20} className="text-success shrink-0 mt-0.5" aria-hidden />
            <div className="text-sm text-ink min-w-0">
              {/* Live publishes swap to the PublishedScreen; this banner covers
                  draft saves, dry runs, and preflight results. */}
              <p className="font-semibold">{r.message || "Done!"}</p>
              {r.dry_run && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs font-semibold text-ink-secondary inline-flex items-center gap-1">
                    <Eye size={12} aria-hidden /> View the exact eBay API payload
                  </summary>
                  <pre className="mt-2 text-xs bg-bg-sunken rounded-[10px] p-3 overflow-x-auto max-h-72">
                    {JSON.stringify(r.payload, null, 2)}
                  </pre>
                  {r.export_path && (
                    <p className="text-xs text-ink-faint mt-1.5">Saved to {r.export_path}</p>
                  )}
                </details>
              )}
            </div>
          </motion.div>
        )}

        {/* Error → what to fix */}
        {r?.error && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="rounded-tile bg-warning-soft border border-warning/30 p-4"
          >
            <p className="font-bold text-sm text-ink flex items-center gap-2">
              <AlertTriangle size={17} className="text-warning" aria-hidden />
              {r.message || "eBay couldn't publish this yet"}
            </p>
            <ul className="mt-3 flex flex-col gap-3">
              {(r.issues?.length
                ? r.issues
                : [{ target: "generic", title: r.message || "eBay rejected the listing", fix: typeof r.detail === "string" ? r.detail : "" }]
              ).map((it, i) => (
                <li key={i} className="text-sm">
                  <p className="font-semibold text-ink">
                    {it.title}
                    {it.level === "warn" && (
                      <span className="ml-2 text-xs font-medium text-ink-faint">nice to have</span>
                    )}
                  </p>
                  {it.fix && <p className="text-ink-secondary mt-0.5">{it.fix}</p>}
                  {it.target && it.target !== "generic" && (
                    <Button variant="soft" size="sm" className="mt-2" onClick={() => onFix(it.target)}>
                      Fix this <ArrowRight aria-hidden />
                    </Button>
                  )}
                </li>
              ))}
            </ul>
            {r.detail && (
              <details className="mt-3">
                <summary className="cursor-pointer text-xs font-semibold text-ink-secondary">
                  eBay's exact message
                </summary>
                <pre className="mt-2 text-xs bg-bg-sunken rounded-[10px] p-3 overflow-x-auto max-h-60 whitespace-pre-wrap">
                  {typeof r.detail === "string" ? r.detail : JSON.stringify(r.detail, null, 2)}
                </pre>
              </details>
            )}
          </motion.div>
        )}
      </div>
    </WorkflowCard>
  );
}

// PublishBar — the primary action, pinned to the bottom of the workflow so
// Save/Publish is always one tap away instead of stranded at the end of a long
// form. Readiness count comes from the same per-card completion the cards show.
// For a LIVE listing the actions flip to revise mode: Update Live Listing +
// End listing (Save Draft disappears — on a published offer any eBay save
// goes straight to the live listing, so a "draft" would mislead).
// What each unfinished card is called in the publish bar, and which fix-it
// target jumps to it (flagging a card scrolls to it and pops the More Details
// fold open if it lives in there).
const GAP_META = {
  photos: { label: "Photos", target: "photos" },
  title: { label: "Title", target: "title" },
  category: { label: "eBay category", target: "category" },
  specifics: { label: "Required item specifics", target: "specifics" },
  pricing: { label: "Price", target: "price" },
  shipping: { label: "Package weight", target: "weight" },
  description: { label: "Description", target: "description" },
};

// Where this publish goes: one toggle chip per connected marketplace. The
// chips themselves are MarketTargetChips in publishShared — the drafts strip
// and bulk queue already render that exact component, and this file used to
// carry a byte-identical second copy. Both read the same remembered
// selection, so a divergence between them could only ever be a bug.
function MarketplaceChips({ w }) {
  const { otherConnected } = usePublishTargets();
  if (!w.chipTargets) return null;
  return (
    <MarketTargetChips
      selected={w.marketTargets}
      toggle={w.toggleMarketTarget}
      otherConnected={otherConnected}
    />
  );
}

export function PublishBar({ w }) {
  const { canPublishLive, deleteListing, setSession, setView, openListings } = useApp();
  const { confirm } = useToast();
  const gaps = Object.entries(w.completion)
    .filter(([, s]) => s === "attention")
    .map(([k]) => GAP_META[k])
    .filter(Boolean);
  const attention = gaps.length;
  const ready = attention === 0;
  // Never say "N fields left" without SAYING WHICH — these chips name each
  // gap and clicking one scrolls straight to the field that needs finishing.
  const jumpTo = (target) => {
    w.setFixTarget(null);
    requestAnimationFrame(() => w.setFixTarget(target));
  };

  const askCancel = async () => {
    if (await confirm({
      title: "Close without saving?",
      message: w.isLive
        ? "Changes you made here since the last update are discarded — the live listing stays as it is on eBay."
        : "Changes since the last save are discarded — the listing stays in Drafts exactly as last saved.",
      confirmLabel: "Close",
    })) {
      setSession(null);
      openListings(w.isLive ? "active" : "drafts");
    }
  };

  const askDelete = async () => {
    if (await confirm({
      title: "Delete this listing?",
      message: "It's permanently removed, photos included. This can't be undone.",
      confirmLabel: "Delete",
      danger: true,
    })) {
      await deleteListing(w.sessionId);
      setSession(null);
      openListings("drafts");
    }
  };

  const askEnd = async () => {
    if (await confirm({
      title: "End this listing on eBay?",
      message: "It comes off eBay immediately. It stays here as an ended listing you can edit and relist anytime.",
      confirmLabel: "End listing",
      danger: true,
    })) w.endListing();
  };

  const status = w.isLive
    ? {
        head: ready ? "Live on eBay" : `${attention} field${attention === 1 ? "" : "s"} left to finish`,
        sub: "Your edits publish straight to the live listing.",
      }
    : {
        head: ready ? "Ready to publish" : `${attention} field${attention === 1 ? "" : "s"} left to finish`,
        sub: ready
          ? "List it live on eBay, or keep it as a draft for now."
          : "Finish the flagged fields — or save a draft for now.",
      };

  const targetCount = (w.chipTargets || []).length;
  const liveLabel = targetCount > 1
    ? `Publish to ${targetCount} marketplaces`
    : canPublishLive ? "Publish Live" : "Publish";

  return (
    <div className="sticky bottom-20 md:bottom-4 z-30 pt-1">
      <div className={cn(
        "rounded-card border-2 backdrop-blur shadow-float p-3.5 sm:p-4 bg-card/95",
        "flex flex-col gap-3",
        ready ? "border-success/45" : "border-warning/50",
      )}>
        <MarketplaceChips w={w} />
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <span className="flex items-center gap-3 min-w-0 flex-1">
          <span className={cn(
            "grid place-items-center size-10 rounded-full shrink-0",
            ready ? "bg-success-soft text-success" : "bg-warning-soft text-warning",
          )}>
            {ready
              ? <CheckCircle2 size={20} aria-hidden />
              : <AlertTriangle size={20} aria-hidden />}
          </span>
          <span className="min-w-0">
            <span className="block text-[15px] sm:text-base font-bold text-ink leading-tight truncate">
              {status.head}
            </span>
            {ready ? (
              <span className="block text-[13px] text-ink-secondary leading-snug mt-0.5">
                {status.sub}
              </span>
            ) : (
              <span className="flex flex-wrap items-center gap-1.5 mt-1">
                {gaps.map((g) => (
                  <button
                    key={g.target}
                    type="button"
                    onClick={() => jumpTo(g.target)}
                    className="inline-flex items-center gap-1 rounded-full bg-warning-soft border border-warning/40 px-2.5 py-0.5 text-[12px] font-bold text-warning cursor-pointer hover:border-warning transition-colors"
                  >
                    {g.label} <ArrowRight size={11} aria-hidden />
                  </button>
                ))}
                <span className="text-[12px] text-ink-faint">tap to jump — or save a draft</span>
              </span>
            )}
          </span>
        </span>
        <span className="flex items-center gap-2.5 shrink-0 w-full sm:w-auto">
          {w.isLive ? (
            <>
              {w.ebayListingId && (
                <Button variant="ghost" size="md" className="hidden sm:inline-flex"
                  onClick={() => window.open(`https://www.ebay.com/itm/${w.ebayListingId}`, "_blank", "noopener")}>
                  <ExternalLink aria-hidden /> View
                </Button>
              )}
              <Button variant="ghost" size="md" onClick={askCancel} aria-label="Cancel editing">
                Cancel
              </Button>
              <Button variant="secondary" size="lg" onClick={askEnd}>
                <Ban aria-hidden /> End
              </Button>
              <Button variant="primary" size="lg" className="flex-1 sm:flex-none" onClick={() => w.publish("live")}>
                <RefreshCw aria-hidden /> Update Live Listing
              </Button>
            </>
          ) : (
            <>
              {/* Right where a listing that won't publish leaves you — no
                  hunting back through the list for its card. */}
              <Button variant="ghost" size="md" onClick={askCancel} aria-label="Cancel editing">
                Cancel
              </Button>
              <Button variant="ghost" size="md" onClick={askDelete}
                aria-label="Delete this listing">
                <Trash2 aria-hidden /> <span className="hidden sm:inline">Delete</span>
              </Button>
              {/* Reachable on a phone too: this is the safety net for the
                  riskiest action in the app, and hiding it below the sm
                  breakpoint left mobile sellers — most of them — with no way
                  to check a listing before it goes live. */}
              <Button variant="ghost" size="md" onClick={w.runPreflight}>
                <ListChecks aria-hidden /> <span className="hidden sm:inline">Check</span>
              </Button>
              <Button variant="secondary" size="lg" className="flex-1 sm:flex-none" onClick={() => w.publish("draft")}>
                <Save aria-hidden /> Save Draft
              </Button>
              <Button variant="primary" size="lg" className="flex-1 sm:flex-none" onClick={() => w.publish("live")}>
                <Rocket aria-hidden /> {liveLabel}
              </Button>
            </>
          )}
        </span>
        </div>
      </div>
    </div>
  );
}
