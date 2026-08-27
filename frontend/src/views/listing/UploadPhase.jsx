import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Sparkles, FolderOpen, Trash2, Camera } from "lucide-react";
import { cn, once } from "@/lib/utils";
import { lastRemoveBg, rememberRemoveBg } from "@/lib/photoPrefs";
import {
  api, pollJob, downscaleAllForUpload, IMAGE_EXT_RE, UPLOAD_TIMEOUT_MS,
} from "@/lib/api";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { Toggle } from "@/components/ui/fields";
import { Card } from "@/components/ui/Card";
import { AIStatusCard } from "@/components/ui/AIStatus";
import { WorkflowSkeleton } from "@/components/ui/Skeleton";
import { PhotoUploadIllustration } from "@/components/ui/illustrations";
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
export function UploadPhase() {
  const { setSession, runBulkUpload, bulkRetry, clearBulkRetry } = useApp();
  const { toast } = useToast();
  const inputRef = useRef(null);
  const cameraRef = useRef(null);
  // A bulk upload that failed hands its pile back here — this component was
  // unmounted while it ran, so the photos would otherwise be gone.
  const [files, setFiles] = useState(() => bulkRetry?.files || []); // { file, url }
  // Seeded from the seller's last choice so it is not re-ticked every visit,
  // and remembered so "Add photos" later gives the same treatment.
  const [removeBg, setRemoveBg] = useState(
    () => (bulkRetry ? !!bulkRetry.removeBg : lastRemoveBg()));
  const chooseRemoveBg = useCallback((on) => {
    setRemoveBg(on);
    rememberRemoveBg(on);
  }, []);
  const [bulk, setBulk] = useState(() => !!bulkRetry);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  // Live status of the background pipeline job (phase/current/total_photos),
  // so the wait card reports what's actually happening instead of guessing.
  const [stage, setStage] = useState(null);
  // Taken once, on mount — a later retry must not re-seed the drop zone.
  useEffect(() => { clearBulkRetry(); }, [clearBulkRetry]);
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

  // The batch screen takes over on the click — no waiting on the upload with
  // the Sell tab still on screen — which unmounts this component, so the
  // upload itself runs from the store (see runBulkUpload).
  const startBulk = once("bulk", async () => {
    if (!files.length) return;
    await runBulkUpload(files, removeBg);
  });

  const process = once("process", async () => {
    if (!files.length) return;
    if (bulkOn) return startBulk();
    setBusy(true);
    setStage(null);
    try {
      const prepped = await downscaleAllForUpload(files.map((f) => f.file));
      const fd = new FormData();
      prepped.forEach((f) => fd.append("files", f));
      fd.append("remove_bg", removeBg ? "true" : "false");
      // The upload returns as soon as the originals are saved; optimization
      // and the whole identify chain run as ONE background job we poll, with
      // real per-stage progress instead of a request that blocks silently
      // through the photo pass.
      fd.append("pipeline", "true");
      const up = await api("/api/upload",
        { method: "POST", body: fd, timeoutMs: UPLOAD_TIMEOUT_MS });
      let last = null;
      const result = await pollJob(up.job_id, {
        onUpdate: (j) => { last = j; setStage(j); },
      });
      const optResults = last?.upload?.optimize_results || [];
      if (removeBg) {
        const warning = bgFailureMessage(optResults, prepped.length);
        if (warning) toast(warning, { kind: "warning", ttl: 10000 });
      }
      // Photos shot with the item lying sideways get straightened server-side
      // (EXIF can't catch that) — say so, since it's a visible change.
      const turned = optResults.filter((r) => r && r.rotated).length;
      if (turned) {
        toast(`Straightened ${turned} sideways photo${turned === 1 ? "" : "s"} — rotate any of them in the editor if we got one wrong.`,
          { kind: "success" });
      }
      files.forEach((f) => URL.revokeObjectURL(f.url));
      setSession({
        sessionId: up.session_id,
        listing: result.listing,
        confidence: result.confidence,
        // Server already ran the specifics/maker enrichment for this draft —
        // the editor's autofill effect skips its (re-charging) re-run.
        specificsAutofilled: !!result.specifics_autofilled,
      });
    } catch (e) {
      toast(`Error: ${e.message}`, { kind: "error" });
    } finally {
      setBusy(false);
      setStage(null);
    }
  });

  if (busy) {
    // Real pipeline stages from the job status; the pre-job moment (uploading
    // the files themselves) is the only guessed line.
    const total = stage?.total_photos || files.length;
    const doneCount = Math.min((stage?.current || 0) + 1, total);
    const stageLine = !stage?.phase ? "Uploading your photos…"
      : stage.phase === "optimizing"
        ? `${removeBg ? "Cleaning up" : "Optimizing"} photo ${doneCount} of ${total}…`
        : stage.phase === "identifying" ? "Identifying your item…"
          : stage.phase === "category" ? "Finding the right eBay category…"
            : stage.phase === "specifics" ? "Filling item specifics from your photos…"
              : stage.phase === "maker" ? "Double-checking the brand…"
                : "Finishing up…";
    return (
      <div className="flex flex-col gap-5">
        <AIStatusCard messages={[stageLine]} />
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
          <PhotoUploadIllustration />
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
                onChange={chooseRemoveBg}
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
