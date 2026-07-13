import { useState } from "react";
import { motion } from "framer-motion";
import { Sparkles, AlertTriangle, RotateCcw } from "lucide-react";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Button } from "@/components/ui/Button";
import { ConfidenceBadge } from "@/components/ui/badges";
import { AIStatusCard } from "@/components/ui/AIStatus";
import { useListingForm } from "./listing/useListingForm";
import { UploadPhase } from "./listing/UploadPhase";
import { ImageEditor } from "./listing/ImageEditor";
import { PublishCard } from "./listing/PublishCard";
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

function Workflow() {
  const { session, startNew } = useApp();
  const { confirm } = useToast();
  const w = useListingForm();
  // { name, action? } — the photo open in the studio; action "crop" runs
  // smart crop as soon as the photo loads.
  const [editing, setEditing] = useState(null);

  const restart = async () => {
    if (await confirm({
      title: "Start a new listing?",
      message: "Your current draft stays saved in Drafts — you can come back to it anytime.",
      confirmLabel: "Start new",
    })) startNew();
  };

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

      {w.aiBusy ? (
        <motion.div variants={rise}>
          <AIStatusCard messages={w.aiBusy} />
        </motion.div>
      ) : (
        <motion.div variants={rise}>
          <RefineBar w={w} />
        </motion.div>
      )}

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
        <PublishCard w={w} />
      </motion.div>

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
  const { session } = useApp();
  if (!session) {
    return (
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-ink">New listing</h1>
          <p className="text-sm text-ink-secondary mt-1">
            Start with photos — the AI handles the boring parts.
          </p>
        </div>
        <UploadPhase />
      </div>
    );
  }
  return <Workflow />;
}
