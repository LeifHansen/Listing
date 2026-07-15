import { useState } from "react";
import { motion } from "framer-motion";
import { Sparkles, RotateCcw, AlertTriangle, CheckCircle2, ArrowRight } from "lucide-react";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Button } from "@/components/ui/Button";
import { ConfidenceBadge } from "@/components/ui/badges";
import { AIStatusCard } from "@/components/ui/AIStatus";
import { useListingForm } from "./listing/useListingForm";
import { UploadPhase } from "./listing/UploadPhase";
import { BulkQueue } from "./listing/BulkMode";
import { MissingInfo } from "./listing/MissingInfo";
import { ImageEditor } from "./listing/ImageEditor";
import { PublishOverlay } from "./listing/PublishOverlay";
import { PublishCard } from "./listing/PublishCard";
import { PreviewPage } from "./listing/PreviewPage";
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

const SECTION_LABELS = {
  photos: "Photos", title: "Title", category: "Category",
  specifics: "Item specifics", pricing: "Pricing", shipping: "Shipping",
  description: "Description",
};

// Cards are collapsed by default, so this banner is the workflow's status
// line: which sections still need attention (tap one to jump in), or a green
// all-clear pointing at Publish.
function AttentionBanner({ w }) {
  const incomplete = Object.entries(w.completion)
    .filter(([, v]) => v === "attention")
    .map(([k]) => k);

  if (!incomplete.length) {
    return (
      <div className="rounded-card bg-success-soft border border-success/25 px-5 py-3.5 flex flex-wrap items-center gap-3">
        <p className="text-sm font-semibold text-ink flex items-center gap-2">
          <CheckCircle2 size={17} className="text-success" aria-hidden />
          All sections complete — this listing is ready to go.
        </p>
        <Button variant="ghost" size="sm" className="ml-auto" onClick={() => w.focusCard("publish")}>
          Go to Publish <ArrowRight aria-hidden />
        </Button>
      </div>
    );
  }
  return (
    <div className="rounded-card bg-warning-soft border border-warning/30 px-5 py-3.5">
      <div className="flex flex-wrap items-center gap-2.5">
        <p className="text-sm font-semibold text-ink flex items-center gap-2">
          <AlertTriangle size={16} className="text-warning" aria-hidden />
          {incomplete.length} section{incomplete.length === 1 ? "" : "s"} need{incomplete.length === 1 ? "s" : ""} attention:
        </p>
        {incomplete.map((id) => (
          <button
            key={id}
            type="button"
            onClick={() => w.focusCard(id)}
            className="rounded-full bg-card border border-warning/40 px-3 py-1 text-[13px] font-semibold text-ink cursor-pointer hover:border-warning transition-colors duration-150"
          >
            {SECTION_LABELS[id]}
          </button>
        ))}
      </div>
    </div>
  );
}

function Workflow() {
  const { session, startNew } = useApp();
  const { confirm } = useToast();
  const w = useListingForm();
  // { name, action? } — the photo open in the studio; action "crop" runs
  // smart crop as soon as the photo loads.
  const [editing, setEditing] = useState(null);
  // Full-page eBay-style preview — the gate before Publish.
  const [previewing, setPreviewing] = useState(false);

  const restart = async () => {
    if (await confirm({
      title: "Start a new listing?",
      message: "Your current draft stays saved in Drafts — you can come back to it anytime.",
      confirmLabel: "Start new",
    })) startNew();
  };

  if (previewing) {
    return (
      <>
        <PreviewPage
          w={w}
          onBack={() => setPreviewing(false)}
          onSaveDraft={() => { setPreviewing(false); w.publish("draft"); }}
          onPublish={() => { setPreviewing(false); w.publish("live"); }}
        />
        <PublishOverlay w={w} />
      </>
    );
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

      <motion.div variants={rise}>
        <MissingInfo w={w} />
      </motion.div>

      {w.aiBusy ? (
        <motion.div variants={rise}>
          <AIStatusCard messages={w.aiBusy} />
        </motion.div>
      ) : (
        <motion.div variants={rise}>
          <RefineBar w={w} />
        </motion.div>
      )}

      <motion.div variants={rise}>
        <AttentionBanner w={w} />
      </motion.div>

      <motion.div variants={rise} className="flex flex-col gap-4">
        <PhotosCard
          w={w}
          onEdit={(name) => setEditing({ name })}
          onSmartCrop={(name) => setEditing({ name, action: "crop" })}
          onDelete={(name) => w.deleteImage(name, confirm)}
        />
        <TitleCard w={w} />
        <CategoryCard w={w} />
        <SpecificsCard w={w} />
        <PricingCard w={w} />
        <ShippingCard w={w} />
        <DescriptionCard w={w} />
        <PublishCard w={w} onPreview={() => setPreviewing(true)} />
      </motion.div>

      <ImageEditor
        sessionId={w.sessionId}
        name={editing?.name}
        initialAction={editing?.action}
        onClose={() => setEditing(null)}
        onSaved={() => w.setImageVersion((v) => v + 1)}
      />
      <PublishOverlay w={w} />
    </motion.div>
  );
}

export function NewListing() {
  const { session } = useApp();
  // { jobId, mode } while a bulk batch is processing / under review.
  const [bulkJob, setBulkJob] = useState(null);

  if (!session && bulkJob) {
    return (
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-ink">Bulk listing</h1>
          <p className="text-sm text-ink-secondary mt-1">
            One photo pile, many listings — review each item below.
          </p>
        </div>
        <BulkQueue
          jobId={bulkJob.jobId}
          mode={bulkJob.mode}
          onExit={() => setBulkJob(null)}
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
        <UploadPhase onBulkStarted={(jobId, mode) => setBulkJob({ jobId, mode })} />
      </div>
    );
  }
  return <Workflow />;
}
