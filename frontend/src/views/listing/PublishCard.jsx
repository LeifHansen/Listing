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
import { Button } from "@/components/ui/Button";

function nameFor(data, key, field) {
  return ((data.policies[key] || []).find((p) => p.id === data.selected[field]) || {}).name || "not set";
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

  const publishedOk = r && !r.error
    && (r.published || r.draft || r.ebay_draft || r.dry_run || r.preflight);

  return (
    <WorkflowCard
      id="publish" icon={Rocket} title="Publish"
      hint={w.isLive
        ? "This listing is LIVE on eBay — Update Live Listing pushes your edits straight to it; End listing takes it off eBay"
        : canPublishLive
          ? "Save as Draft saves it here and stages it on your connected eBay account (as an unpublished offer); Publish Live makes it a live listing"
          : "Dry-run mode: no eBay connection yet, so publishing generates the exact API payload to inspect"}
      state={publishedOk ? "complete" : "todo"}
    >
      <div className="flex flex-col gap-5">
        {ebay.connected && policiesData && (
          <p className="text-[13px] text-ink-secondary">
            Applies to this listing — <strong className="text-ink">Shipping:</strong>{" "}
            {nameFor(policiesData, "fulfillment", "fulfillment_policy_id")} ·{" "}
            <strong className="text-ink">Payment:</strong>{" "}
            {nameFor(policiesData, "payment", "payment_policy_id")} ·{" "}
            <strong className="text-ink">Returns:</strong>{" "}
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

        {!publishedOk && !r?.error && (
          <p className="text-[13px] text-ink-secondary">
            Use the pinned <strong className="text-ink">Publish bar</strong> below to{" "}
            <strong className="text-ink">Save as Draft</strong> or{" "}
            <strong className="text-ink">Publish Live</strong> — it stays in reach as you scroll.
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
export function PublishBar({ w }) {
  const { canPublishLive, deleteListing, setSession, setView } = useApp();
  const { confirm } = useToast();
  const attention = Object.values(w.completion).filter((s) => s === "attention").length;
  const ready = attention === 0;

  const askDelete = async () => {
    if (await confirm({
      title: "Delete this listing?",
      message: "It's permanently removed, photos included. This can't be undone.",
      confirmLabel: "Delete",
      danger: true,
    })) {
      await deleteListing(w.sessionId);
      setSession(null);
      setView("drafts");
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

  return (
    <div className="sticky bottom-20 md:bottom-4 z-30 pt-1">
      <div className={cn(
        "rounded-card border-2 backdrop-blur shadow-float p-3.5 sm:p-4 bg-card/95",
        "flex flex-col gap-3 sm:flex-row sm:items-center",
        ready ? "border-success/45" : "border-warning/50",
      )}>
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
            <span className="block text-[13px] text-ink-secondary leading-snug mt-0.5">
              {status.sub}
            </span>
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
              <Button variant="ghost" size="md" onClick={askDelete}
                aria-label="Delete this listing">
                <Trash2 aria-hidden /> <span className="hidden sm:inline">Delete</span>
              </Button>
              <Button variant="ghost" size="md" onClick={w.runPreflight} className="hidden sm:inline-flex">
                <ListChecks aria-hidden /> Check
              </Button>
              <Button variant="secondary" size="lg" className="flex-1 sm:flex-none" onClick={() => w.publish("draft")}>
                <Save aria-hidden /> Save Draft
              </Button>
              <Button variant="primary" size="lg" className="flex-1 sm:flex-none" onClick={() => w.publish("live")}>
                <Rocket aria-hidden /> {canPublishLive ? "Publish Live" : "Publish"}
              </Button>
            </>
          )}
        </span>
      </div>
    </div>
  );
}
