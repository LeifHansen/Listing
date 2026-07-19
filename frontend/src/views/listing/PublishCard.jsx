import { useEffect } from "react";
import { motion } from "framer-motion";
import { Rocket, Save, CheckCircle2, AlertTriangle, ArrowRight, Eye, ListChecks } from "lucide-react";
import { api } from "@/lib/api";
import { useApp } from "@/store";
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
      hint={canPublishLive
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
export function PublishBar({ w }) {
  const { canPublishLive } = useApp();
  const attention = Object.values(w.completion).filter((s) => s === "attention").length;
  const ready = attention === 0;
  return (
    <div className="sticky bottom-20 md:bottom-4 z-30 pt-1">
      <div className="bg-card/95 backdrop-blur border border-line-strong rounded-card shadow-float p-2.5 sm:p-3 flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="flex items-center gap-2 min-w-0 flex-1 pl-1">
          {ready
            ? <CheckCircle2 size={18} className="text-success shrink-0" aria-hidden />
            : <AlertTriangle size={18} className="text-warning shrink-0" aria-hidden />}
          <span className="text-sm font-semibold text-ink truncate">
            {ready
              ? "Looks ready — publish when you are"
              : `${attention} field${attention === 1 ? "" : "s"} to finish`}
          </span>
        </span>
        <span className="flex items-center gap-2 shrink-0">
          <Button variant="ghost" size="sm" onClick={w.runPreflight} className="hidden sm:inline-flex">
            <ListChecks aria-hidden /> Check
          </Button>
          <Button variant="secondary" size="md" onClick={() => w.publish("draft")}>
            <Save aria-hidden /> Save Draft
          </Button>
          <Button variant="primary" size="md" onClick={() => w.publish("live")}>
            <Rocket aria-hidden /> {canPublishLive ? "Publish Live" : "Publish"}
          </Button>
        </span>
      </div>
    </div>
  );
}
