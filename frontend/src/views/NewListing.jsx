import { useState } from "react";
import { motion } from "framer-motion";
import {
  Sparkles, AlertTriangle, RotateCcw, CheckCircle2, ArrowRight, PlusCircle,
  LayoutDashboard,
} from "lucide-react";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Button } from "@/components/ui/Button";
import { ConfidenceBadge } from "@/components/ui/badges";
import { LoadingOverlay } from "@/components/ui/AIStatus";
import { BrandMark } from "@/components/BrandMark";
import { useListingForm } from "./listing/useListingForm";
import { UploadPhase } from "./listing/UploadPhase";
import { BulkQueue } from "./listing/BulkMode";
import { ImageEditor } from "./listing/ImageEditor";
import { PublishCard, PublishBar } from "./listing/PublishCard";
import {
  PhotosCard, TitleCard, CategoryCard, SpecificsCard, PricingCard,
  ShippingCard, DescriptionCard,
} from "./listing/cards";

const stagger = { hidden: {}, show: { transition: { staggerChildren: 0.04 } } };
const rise = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: { duration: 0.22, ease: "easeOut" } },
};

// RefineBar — the "AI, make it better" prompt pinned under the page title.
function RefineBar({ w }) {
  const [prompt, setPrompt] = useState("");
  const apply = async () => {
    const ok = await w.refine(prompt);
    if (ok) setPrompt("");
  };
  return (
    <div className="bg-card border border-line rounded-card shadow-card p-2.5 pl-4 flex items-center gap-3">
      <Sparkles size={18} className="text-blue shrink-0" aria-hidden />
      <input
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") apply(); }}
        placeholder='Ask AI to change anything — "make the title punchier, price at $45"'
        aria-label="Refine listing with AI"
        className="flex-1 min-w-0 bg-transparent text-[15px] placeholder:text-ink-faint focus:outline-none"
      />
      <Button variant="primary" size="md" onClick={apply} disabled={!prompt.trim()}>
        Apply
      </Button>
    </div>
  );
}

// PublishedScreen — the moment of glory. Replaces the workflow after a live
// publish so the user isn't left staring at the listing they just posted;
// offers the next queued draft to keep the assembly line moving.
function PublishedScreen({ w }) {
  const { listingsState, openListing, startNew, setView } = useApp();
  const r = w.publishResult;
  const draftsLeft = (listingsState.items || [])
    .filter((it) => (it.status === "draft" || it.status === "dry_run") && it.id !== w.sessionId)
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
  const next = draftsLeft[0];

  return (
    <motion.div
      initial={{ opacity: 0, y: 14, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
      className="grid place-items-center py-8 sm:py-14"
    >
      <div className="bg-card border border-line rounded-card shadow-float p-8 sm:p-10 max-w-xl w-full flex flex-col items-center text-center gap-4">
        <span className="ai-sparkle" aria-hidden>
          <BrandMark className="size-16 rounded-[20px]" />
        </span>
        <p className="inline-flex items-center gap-2 text-success font-bold text-sm">
          <CheckCircle2 size={17} aria-hidden /> Live on eBay
        </p>
        <h1 className="text-2xl font-bold tracking-tight text-ink">
          Listing published! 🎉
        </h1>
        <p className="text-sm text-ink-secondary">
          <strong className="text-ink">"{w.form.title || "Your item"}"</strong> is live
          {r?.listing_id && <> — Listing ID <strong className="text-ink">{r.listing_id}</strong></>}.
          Find it under Selling → Active in your eBay account.
        </p>

        <div className="mt-2 flex flex-wrap justify-center items-center gap-2.5">
          {next ? (
            <>
              <Button variant="primary" size="lg" onClick={() => openListing(next.id)}>
                Next Draft <ArrowRight aria-hidden />
              </Button>
              <Button variant="ghost" size="lg" onClick={() => setView("dashboard")}>
                <LayoutDashboard aria-hidden /> Dashboard
              </Button>
            </>
          ) : (
            <>
              <Button variant="primary" size="lg" onClick={() => setView("dashboard")}>
                <LayoutDashboard aria-hidden /> Back to Home
              </Button>
              <Button variant="ghost" size="lg" onClick={startNew}>
                <PlusCircle aria-hidden /> Start a new listing
              </Button>
            </>
          )}
        </div>
        {draftsLeft.length > 0 && (
          <p className="text-xs text-ink-faint">
            {draftsLeft.length} draft{draftsLeft.length === 1 ? "" : "s"} still waiting in your queue.
          </p>
        )}
      </div>
    </motion.div>
  );
}

function Workflow() {
  const { session, startNew } = useApp();
  const { confirm } = useToast();
  const w = useListingForm();
  // { name } — the photo open in the studio (clean up, remove background, crop).
  const [editing, setEditing] = useState(null);

  const restart = async () => {
    if (await confirm({
      title: "Start a new listing?",
      message: "Your current draft stays saved in Drafts — you can come back to it anytime.",
      confirmLabel: "Start new",
    })) startNew();
  };

  // A live publish replaces the workflow with the success screen — staying on
  // the just-posted listing was confusing.
  if (w.publishResult?.published) {
    return <PublishedScreen w={w} />;
  }

  return (
    <motion.div variants={stagger} initial="hidden" animate="show" className="flex flex-col gap-4">
      <motion.div variants={rise} className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-ink truncate">
            {w.form.title || "New listing"}
          </h1>
          <div className="flex items-center gap-2 mt-1">
            {session.confidence && <ConfidenceBadge level={session.confidence} />}
          </div>
        </div>
        <Button variant="ghost" onClick={restart}>
          <RotateCcw aria-hidden /> Start over
        </Button>
      </motion.div>

      {(w.form.missing_info || []).length > 0 && (
        <motion.div
          variants={rise}
          className="rounded-card bg-warning-soft border border-warning/30 p-4 flex gap-3"
        >
          <AlertTriangle size={18} className="text-warning shrink-0 mt-0.5" aria-hidden />
          <div className="text-sm">
            <p className="font-bold text-ink">Please verify / fill in:</p>
            <ul className="mt-1 list-disc list-inside text-ink-secondary">
              {w.form.missing_info.map((m, i) => <li key={i}>{m}</li>)}
            </ul>
          </div>
        </motion.div>
      )}

      {/* Long waits (publish, save, refine) use a full-screen branded overlay
          so feedback is visible no matter where the page is scrolled. */}
      {w.aiBusy && <LoadingOverlay messages={w.aiBusy} />}

      <motion.div variants={rise}>
        <RefineBar w={w} />
      </motion.div>

      <motion.div variants={rise} className="flex flex-col gap-4">
        <PhotosCard
          w={w}
          onEdit={(name) => setEditing({ name })}
          onDelete={(name) => w.deleteImage(name)}
        />
        <TitleCard w={w} />
        <CategoryCard w={w} />
        <SpecificsCard w={w} />
        <PricingCard w={w} />
        <ShippingCard w={w} />
        <DescriptionCard w={w} />
        <PublishCard w={w} />
      </motion.div>

      {/* Pinned primary action — stays in reach as you scroll the long form
          (kept outside the animated card stack so sticky positioning holds). */}
      <PublishBar w={w} />

      <ImageEditor
        sessionId={w.sessionId}
        name={editing?.name}
        initialAction={editing?.action}
        onClose={() => setEditing(null)}
        onSaved={() => w.setImageVersion((v) => v + 1)}
      />
    </motion.div>
  );
}

export function NewListing() {
  // activeBulk lives in the store + localStorage, so a running batch survives
  // navigating away and page reloads (see store startBulk/clearBulk).
  const { session, activeBulk, startBulk, clearBulk, bulkSettled } = useApp();

  if (!session && activeBulk) {
    return (
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-ink">Bulk listing</h1>
          <p className="text-sm text-ink-secondary mt-1">
            One photo pile, many listings — review each item below.
          </p>
        </div>
        <BulkQueue
          jobId={activeBulk.jobId}
          mode={activeBulk.mode}
          onExit={clearBulk}
          onSettled={bulkSettled}
        />
      </div>
    );
  }

  if (!session) {
    return (
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-ink">New listing</h1>
          <p className="text-sm text-ink-secondary mt-1">
            Start with photos — the AI handles the boring parts.
          </p>
        </div>
        <UploadPhase onBulkStarted={startBulk} />
      </div>
    );
  }
  return <Workflow />;
}
