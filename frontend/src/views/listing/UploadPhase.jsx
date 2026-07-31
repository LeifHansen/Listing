import { useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Sparkles, FolderOpen, Trash2, Camera } from "lucide-react";
import { cn, once } from "@/lib/utils";
import { api, pollJob, downscaleAllForUpload, IMAGE_EXT_RE } from "@/lib/api";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { Toggle } from "@/components/ui/fields";
import { Card } from "@/components/ui/Card";
import { AIStatusCard } from "@/components/ui/AIStatus";
import { WorkflowSkeleton } from "@/components/ui/Skeleton";
import { CameraIllustration } from "@/components/ui/illustrations";
import { useToast } from "@/components/ui/Toaster";

// When background removal can't run (out of credits, bad key, rate limit) the
// server KEEPS the original photo — the right call, but silent: the photos just
// come back with their backgrounds, looking like the feature does nothing. Say
// so, with the reason the server gave.
function bgFailureMessage(results, total) {
  const failed = (results || []).filter((r) => r && r.bg_error);
  if (!failed.length) return null;
  const scope = failed.length === total
    ? "Backgrounds weren't removed"
    : `${failed.length} of ${total} photos kept their background`;
  // The server's reason is free text and usually has no trailing period, so
  // it ran straight into the next sentence ("cutout failed Your photos…").
  const reason = String(failed[0].bg_error).trim().replace(/[.!?]*$/, "");
  return `${scope} — ${reason}. Your photos were saved unchanged.`;
}

// Server-side caps (backend/main.py): one listing takes up to 40 photos; a
// bulk batch (many items) takes up to 250. Past 40 the pile can only be a
// bulk batch, so the toggle locks on rather than letting the upload bounce
// off the server with an error.
const MAX_SINGLE_FILES = 40;
const MAX_BATCH_FILES = 250;

// The photo uploader — centerpiece of a new listing. Big friendly drop zone,
// rounded photo cards, then one tap to let the AI take over. With several
// photos it can also run in bulk mode: one pile, many listings.
export function UploadPhase({ onBulkStarted }) {
  const { setSession } = useApp();
  const { toast, confirm } = useToast();
  const inputRef = useRef(null);
  const cameraRef = useRef(null);
  const [files, setFiles] = useState([]); // { file, url }
  const [removeBg, setRemoveBg] = useState(false);
  const [bulk, setBulk] = useState(false);
  const [bulkLive, setBulkLive] = useState(false);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  // Past the single-listing cap the pile can only be a bulk batch.
  const forceBulk = files.length > MAX_SINGLE_FILES;
  const bulkOn = bulk || forceBulk;

  const addFiles = (fileList) => {
    let overflow = 0;
    setFiles((cur) => {
      const next = [...cur];
      for (const f of fileList) {
        // HEIC/HEIF often arrive with an empty MIME type; accept by extension too.
        if (!f.type.startsWith("image/") && !IMAGE_EXT_RE.test(f.name || "")) continue;
        // Skip duplicates (same file picked twice) so we don't upload it twice.
        if (next.some((e) => e.file.name === f.name && e.file.size === f.size)) continue;
        if (next.length >= MAX_BATCH_FILES) { overflow += 1; continue; }
        next.push({ file: f, url: URL.createObjectURL(f) });
      }
      return next;
    });
    if (overflow) {
      toast(`A batch takes up to ${MAX_BATCH_FILES} photos — ${overflow} weren't added. Run them as a second batch.`,
        { kind: "warning" });
    }
  };

  const removeFile = (i) => {
    setFiles((cur) => {
      URL.revokeObjectURL(cur[i].url);
      return cur.filter((_, j) => j !== i);
    });
  };

  const startBulk = once("bulk", async () => {
    if (!files.length) return;
    const mode = bulkLive ? "live" : "draft";
    if (mode === "live" && !(await confirm({
      title: "Auto-publish ALL detected items?",
      message: "Each item the AI finds goes straight to your eBay store, live.",
      confirmLabel: "Publish everything",
    }))) return;
    setBusy(true);
    try {
      const prepped = await downscaleAllForUpload(files.map((f) => f.file));
      const fd = new FormData();
      prepped.forEach((f) => fd.append("files", f));
      fd.append("mode", mode);
      fd.append("remove_bg", removeBg ? "true" : "false");
      const { job_id } = await api("/api/bulk/upload", { method: "POST", body: fd });
      files.forEach((f) => URL.revokeObjectURL(f.url));
      setFiles([]);
      onBulkStarted(job_id, mode);
    } catch (e) {
      toast(`Bulk upload failed: ${e.message}`, { kind: "error" });
    } finally {
      setBusy(false);
    }
  });

  const process = once("process", async () => {
    if (!files.length) return;
    if (bulkOn) return startBulk();
    setBusy(true);
    try {
      const prepped = await downscaleAllForUpload(files.map((f) => f.file));
      const fd = new FormData();
      prepped.forEach((f) => fd.append("files", f));
      fd.append("remove_bg", removeBg ? "true" : "false");
      const up = await api("/api/upload", { method: "POST", body: fd });
      if (removeBg) {
        const warning = bgFailureMessage(up.optimize_results, prepped.length);
        if (warning) toast(warning, { kind: "warning", ttl: 10000 });
      }
      // Photos shot with the item lying sideways get straightened server-side
      // (EXIF can't catch that) — say so, since it's a visible change.
      const turned = (up.optimize_results || []).filter((r) => r && r.rotated).length;
      if (turned) {
        toast(`Straightened ${turned} sideways photo${turned === 1 ? "" : "s"} — rotate any of them in the editor if we got one wrong.`,
          { kind: "success" });
      }

      // Identify runs as a background job we poll, so a slow multi-photo vision
      // call can't outlive the browser/proxy timeout.
      const { job_id } = await api(`/api/identify-async/${up.session_id}`, { method: "POST" });
      const result = await pollJob(job_id);
      files.forEach((f) => URL.revokeObjectURL(f.url));
      setSession({
        sessionId: up.session_id,
        listing: result.listing,
        confidence: result.confidence,
      });
    } catch (e) {
      toast(`Error: ${e.message}`, { kind: "error" });
    } finally {
      setBusy(false);
    }
  });

  if (busy && bulkOn) {
    return <AIStatusCard messages={["Uploading your photo pile…", "This may take a moment…"]} />;
  }
  if (busy) {
    return (
      <div className="flex flex-col gap-5">
        <AIStatusCard messages={[
          removeBg ? "Removing backgrounds…" : "Optimizing your photos…",
          "Looking for brand & model…",
          "Detecting item specifics…",
          "Writing a better title…",
          "Finding the right category…",
          "Optimizing for eBay search…",
        ]} />
        <WorkflowSkeleton />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <Card
        className={cn(
          "border-2 border-dashed transition-colors duration-200 cursor-pointer",
          drag ? "border-blue bg-blue-soft" : "border-line-strong hover:border-blue/60",
        )}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragEnter={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={(e) => { e.preventDefault(); setDrag(false); }}
        onDrop={(e) => { e.preventDefault(); setDrag(false); addFiles(e.dataTransfer.files); }}
      >
        <div className="flex flex-col items-center text-center gap-3 py-8">
          <CameraIllustration />
          <h2 className="text-xl font-bold text-ink">Drag photos here</h2>
          <p className="text-sm text-ink-secondary">
            or bring them in another way — the AI writes the listing from your shots.
          </p>
          <div className="flex flex-wrap justify-center gap-2.5 mt-1">
            <Button
              variant="primary" size="lg"
              onClick={(e) => { e.stopPropagation(); inputRef.current?.click(); }}
            >
              <FolderOpen aria-hidden /> Browse Files
            </Button>
            <Button
              variant="secondary" size="lg"
              onClick={(e) => { e.stopPropagation(); cameraRef.current?.click(); }}
            >
              <Camera aria-hidden /> Take Photos
            </Button>
          </div>
        </div>
        <input
          ref={inputRef} type="file" accept="image/*,.heic,.heif,.hif" multiple hidden
          onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }}
        />
        <input
          ref={cameraRef} type="file" accept="image/*,.heic,.heif,.hif" capture="environment" hidden
          onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }}
        />
      </Card>

      <AnimatePresence>
        {files.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            <Card className="flex flex-col gap-5">
              <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-3">
                <AnimatePresence>
                  {files.map((f, i) => (
                    <motion.div
                      key={f.url}
                      layout
                      initial={{ opacity: 0, scale: 0.9 }}
                      animate={{ opacity: 1, scale: 1 }}
                      exit={{ opacity: 0, scale: 0.85 }}
                      transition={{ duration: 0.18 }}
                      className="relative rounded-tile overflow-hidden border border-line aspect-square group"
                    >
                      <img src={f.url} alt="" className="size-full object-cover" />
                      <button
                        type="button"
                        aria-label="Remove photo"
                        title="Remove photo"
                        onClick={() => removeFile(i)}
                        className="absolute top-1.5 left-1.5 z-10 grid place-items-center size-7 rounded-full
                          bg-card/85 backdrop-blur border border-line text-ink-faint shadow-card cursor-pointer
                          hover:text-error hover:border-error/40 transition-colors duration-150"
                      >
                        <Trash2 size={13} aria-hidden />
                      </button>
                    </motion.div>
                  ))}
                </AnimatePresence>
              </div>

              <Toggle
                checked={removeBg}
                onChange={setRemoveBg}
                label="Remove background & replace with white"
                help="Cleaner, eBay-friendly product shots. Adds a few seconds per photo."
              />

              {forceBulk ? (
                <p className="text-sm text-ink-secondary">
                  Bulk mode is on automatically — {files.length} photos is more
                  than one listing holds (max {MAX_SINGLE_FILES}), so the AI
                  will sort this pile into separate items.
                </p>
              ) : files.length >= 2 && (
                <Toggle
                  checked={bulk}
                  onChange={setBulk}
                  label="Bulk mode — this pile has multiple items"
                  help="The AI sorts the photos into items and drafts a listing for each one."
                />
              )}

              {bulkOn && (
                <Toggle
                  checked={bulkLive}
                  onChange={setBulkLive}
                  label="Auto-publish everything live"
                  help="Off = every item queues as a draft for review (recommended)."
                />
              )}

              <Button variant="primary" size="lg" className="self-start" onClick={process}>
                <Sparkles aria-hidden />
                {bulkOn
                  ? `Split ${files.length} photos into listings`
                  : "Identify with AI"}
              </Button>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
