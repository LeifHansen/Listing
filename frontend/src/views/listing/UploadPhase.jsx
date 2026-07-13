import { useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Sparkles, FolderOpen, X, Camera } from "lucide-react";
import { cn, once } from "@/lib/utils";
import { api, downscaleForUpload, IMAGE_EXT_RE } from "@/lib/api";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { Toggle } from "@/components/ui/fields";
import { Card } from "@/components/ui/Card";
import { AIStatusCard } from "@/components/ui/AIStatus";
import { WorkflowSkeleton } from "@/components/ui/Skeleton";
import { CameraIllustration } from "@/components/ui/illustrations";
import { useToast } from "@/components/ui/Toaster";

// The photo uploader — centerpiece of a new listing. Big friendly drop zone,
// rounded photo cards, then one tap to let the AI take over.
export function UploadPhase() {
  const { setSession } = useApp();
  const { toast } = useToast();
  const inputRef = useRef(null);
  const cameraRef = useRef(null);
  const [files, setFiles] = useState([]); // { file, url }
  const [removeBg, setRemoveBg] = useState(false);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);

  const addFiles = (fileList) => {
    setFiles((cur) => {
      const next = [...cur];
      for (const f of fileList) {
        // HEIC/HEIF often arrive with an empty MIME type; accept by extension too.
        if (!f.type.startsWith("image/") && !IMAGE_EXT_RE.test(f.name || "")) continue;
        // Skip duplicates (same file picked twice) so we don't upload it twice.
        if (next.some((e) => e.file.name === f.name && e.file.size === f.size)) continue;
        next.push({ file: f, url: URL.createObjectURL(f) });
      }
      return next;
    });
  };

  const removeFile = (i) => {
    setFiles((cur) => {
      URL.revokeObjectURL(cur[i].url);
      return cur.filter((_, j) => j !== i);
    });
  };

  const process = once("process", async () => {
    if (!files.length) return;
    setBusy(true);
    try {
      const prepped = await Promise.all(files.map((f) => downscaleForUpload(f.file)));
      const fd = new FormData();
      prepped.forEach((f) => fd.append("files", f));
      fd.append("remove_bg", removeBg ? "true" : "false");
      const up = await api("/api/upload", { method: "POST", body: fd });

      const result = await api(`/api/identify/${up.session_id}`, { method: "POST" });
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
                        onClick={() => removeFile(i)}
                        className="absolute top-1.5 right-1.5 grid place-items-center size-7 rounded-full
                          bg-card/90 text-ink shadow-card cursor-pointer opacity-0 group-hover:opacity-100
                          focus-visible:opacity-100 transition-opacity duration-150"
                      >
                        <X size={13} aria-hidden />
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

              <Button variant="primary" size="lg" className="self-start" onClick={process}>
                <Sparkles aria-hidden /> Identify with AI
              </Button>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
