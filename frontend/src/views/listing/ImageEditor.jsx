import { useCallback, useEffect, useRef, useState } from "react";
import { RotateCcw, Crop, Sparkles, Highlighter, CheckCircle2 } from "lucide-react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { AIStatusInline } from "@/components/ui/AIStatus";
import { useToast } from "@/components/ui/Toaster";
import { api } from "@/lib/api";
import { cn, mediaUrl } from "@/lib/utils";

/* Photo studio: brush clean-up plus three AI assists —
   - analyze: re-checks the item's borders and highlights leftover background
   - auto clean: whitens everything the AI says is outside the item
   - smart crop: crops to the item with a clean margin
   Every AI action only previews onto the canvas; nothing is stored until Save. */

const HIGHLIGHT_COLOR = "#e53238"; // brand red, tinted over leftovers

function canvasBlob(canvas, type = "image/jpeg", q = 0.92) {
  return new Promise((r) => canvas.toBlob(r, type, q));
}

async function studioCall(path, sessionId, name, blob) {
  const fd = new FormData();
  fd.append("session_id", sessionId);
  fd.append("name", name);
  if (blob) fd.append("file", new File([blob], name, { type: blob.type }));
  return api(path, { method: "POST", body: fd });
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("Couldn't load the image."));
    img.src = src;
  });
}

export function ImageEditor({ sessionId, name, initialAction, onClose, onSaved }) {
  const canvasRef = useRef(null);
  const overlayRef = useRef(null);
  const painting = useRef(false);
  const paintedSinceAnalyze = useRef(false);
  const [brush, setBrush] = useState(40);
  const brushRef = useRef(40);
  const [saving, setSaving] = useState(false);
  const [aiBusy, setAiBusy] = useState(null); // friendly message while AI works
  const [highlight, setHighlight] = useState(true);
  const [residuePct, setResiduePct] = useState(null);
  const { toast } = useToast();

  const clearOverlay = useCallback(() => {
    const o = overlayRef.current;
    if (o) o.getContext("2d").clearRect(0, 0, o.width, o.height);
  }, []);

  // Tint the residue mask red and lay it over the photo.
  const drawOverlay = useCallback(async (maskUrl) => {
    const canvas = canvasRef.current;
    const overlay = overlayRef.current;
    if (!canvas || !overlay) return;
    overlay.width = canvas.width;
    overlay.height = canvas.height;
    const ctx = overlay.getContext("2d");
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    if (!maskUrl) return;
    const mask = await loadImage(maskUrl);
    const tint = document.createElement("canvas");
    tint.width = overlay.width;
    tint.height = overlay.height;
    const tctx = tint.getContext("2d");
    tctx.drawImage(mask, 0, 0, tint.width, tint.height);
    tctx.globalCompositeOperation = "source-in";
    tctx.fillStyle = HIGHLIGHT_COLOR;
    tctx.fillRect(0, 0, tint.width, tint.height);
    ctx.globalAlpha = 0.45;
    ctx.drawImage(tint, 0, 0);
    ctx.globalAlpha = 1;
  }, []);

  // Ask the AI to re-check the item borders; highlight whatever needs cleaning.
  const analyze = useCallback(async (blob, { show } = {}) => {
    try {
      const res = await studioCall("/api/image/analyze", sessionId, name, blob);
      paintedSinceAnalyze.current = false;
      setResiduePct(res.residue_pct);
      const shouldShow = show ?? (res.residue_pct >= 0.2);
      setHighlight(shouldShow);
      await drawOverlay(shouldShow ? res.mask : null);
    } catch (e) {
      setResiduePct(null); // analysis is an assist — never block the editor
    }
  }, [sessionId, name, drawOverlay]);

  const load = useCallback(async () => {
    const canvas = canvasRef.current;
    if (!canvas || !name) return;
    try {
      // Same-origin so the canvas isn't tainted and toBlob() works.
      const img = await loadImage(`${mediaUrl(sessionId, name)}?v=${Date.now()}`);
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      canvas.getContext("2d").drawImage(img, 0, 0);
      clearOverlay();
      setResiduePct(null);
      setAiBusy("Re-checking the item's borders…");
      await analyze(null);
    } catch (e) {
      toast(e.message, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }, [sessionId, name, analyze, clearOverlay, toast]);

  // Preview an AI-processed image (data URL) onto the canvas, then re-check.
  const applyPreview = useCallback(async (dataUrl) => {
    const canvas = canvasRef.current;
    const img = await loadImage(dataUrl);
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    canvas.getContext("2d").drawImage(img, 0, 0);
    clearOverlay();
    const blob = await canvasBlob(canvas);
    await analyze(blob);
  }, [analyze, clearOverlay]);

  const autoClean = useCallback(async () => {
    const canvas = canvasRef.current;
    if (!canvas || aiBusy) return;
    setAiBusy("Re-checking borders & cleaning the background…");
    try {
      const blob = await canvasBlob(canvas);
      const res = await studioCall("/api/image/auto-clean", sessionId, name, blob);
      await applyPreview(res.image);
      toast("Background cleaned — review and Save to keep it.", { kind: "success" });
    } catch (e) {
      toast(`Auto clean failed: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }, [aiBusy, sessionId, name, applyPreview, toast]);

  const smartCrop = useCallback(async () => {
    const canvas = canvasRef.current;
    if (!canvas || aiBusy) return;
    setAiBusy("Finding the item & framing the shot…");
    try {
      const blob = await canvasBlob(canvas);
      const res = await studioCall("/api/image/smart-crop", sessionId, name, blob);
      if (!res.applied) {
        toast(res.message || "Already nicely framed — no crop needed.", { kind: "info" });
        return;
      }
      await applyPreview(res.image);
      toast("Cropped to the item — review and Save to keep it.", { kind: "success" });
    } catch (e) {
      toast(`Smart crop failed: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }, [aiBusy, sessionId, name, applyPreview, toast]);

  const toggleHighlight = useCallback(async () => {
    if (aiBusy) return;
    if (highlight) {
      setHighlight(false);
      clearOverlay();
      return;
    }
    // Re-analyze against the current canvas so fresh brush strokes count.
    setAiBusy("Re-checking the item's borders…");
    try {
      const blob = paintedSinceAnalyze.current
        ? await canvasBlob(canvasRef.current) : null;
      await analyze(blob, { show: true });
    } finally {
      setAiBusy(null);
    }
  }, [aiBusy, highlight, analyze, clearOverlay]);

  useEffect(() => {
    if (!name) return;
    (async () => {
      await load();
      if (initialAction === "crop") smartCrop();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  useEffect(() => { brushRef.current = brush; }, [brush]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !name) return;

    const point = (e) => {
      const rect = canvas.getBoundingClientRect();
      const p = e.touches ? e.touches[0] : e;
      return {
        x: (p.clientX - rect.left) * (canvas.width / rect.width),
        y: (p.clientY - rect.top) * (canvas.height / rect.height),
      };
    };
    const paintAt = (e) => {
      if (!painting.current) return;
      const ctx = canvas.getContext("2d");
      const { x, y } = point(e);
      // Brush radius is in displayed pixels; scale to canvas pixels.
      const scale = canvas.width / canvas.getBoundingClientRect().width;
      const r = (brushRef.current || 40) * scale / 2;
      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
      paintedSinceAnalyze.current = true;
      // Clear the highlight where the user painted, so it doesn't nag about
      // an area they just fixed.
      const overlay = overlayRef.current;
      if (overlay && overlay.width) {
        const octx = overlay.getContext("2d");
        octx.save();
        octx.globalCompositeOperation = "destination-out";
        octx.beginPath();
        octx.arc(x, y, r, 0, Math.PI * 2);
        octx.fill();
        octx.restore();
      }
    };
    const start = (e) => { painting.current = true; paintAt(e); e.preventDefault(); };
    const move = (e) => { if (painting.current) { paintAt(e); e.preventDefault(); } };
    const end = () => { painting.current = false; };

    canvas.addEventListener("mousedown", start);
    canvas.addEventListener("mousemove", move);
    window.addEventListener("mouseup", end);
    canvas.addEventListener("touchstart", start, { passive: false });
    canvas.addEventListener("touchmove", move, { passive: false });
    canvas.addEventListener("touchend", end);
    return () => {
      canvas.removeEventListener("mousedown", start);
      canvas.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", end);
      canvas.removeEventListener("touchstart", start);
      canvas.removeEventListener("touchmove", move);
      canvas.removeEventListener("touchend", end);
    };
  }, [name]);

  const save = async () => {
    const canvas = canvasRef.current;
    if (!canvas || !name) return;
    const blob = await canvasBlob(canvas);
    if (!blob) { toast("Couldn't export the edited image.", { kind: "error" }); return; }
    setSaving(true);
    try {
      const fd = new FormData();
      // session/name as form fields (not URL path) so nothing falls through
      // to the static handler and 405s.
      fd.append("session_id", sessionId);
      fd.append("name", name);
      fd.append("file", new File([blob], name, { type: "image/jpeg" }));
      await api("/api/edit-image", { method: "POST", body: fd });
      onSaved();
      onClose();
    } catch (e) {
      toast(`Save failed: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };

  const borderState = residuePct == null
    ? null
    : residuePct >= 0.2
      ? `AI found leftover background (~${residuePct}% of the frame)${highlight ? " — highlighted in red" : ""}. Try Auto clean, or paint over it.`
      : "Item borders look clean.";

  return (
    <Dialog open={!!name} onClose={onClose} title="Photo studio" wide>
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <Button variant="soft" size="sm" onClick={autoClean} disabled={!!aiBusy}>
          <Sparkles aria-hidden /> Auto clean
        </Button>
        <Button variant="soft" size="sm" onClick={smartCrop} disabled={!!aiBusy}>
          <Crop aria-hidden /> Smart crop
        </Button>
        <Button
          variant={highlight ? "danger" : "ghost"} size="sm"
          onClick={toggleHighlight} disabled={!!aiBusy}
          aria-pressed={highlight}
        >
          <Highlighter aria-hidden /> {highlight ? "Hide highlight" : "Highlight leftovers"}
        </Button>
        <span className="ml-auto text-xs text-ink-secondary hidden sm:block">
          Paint over anything with the white brush.
        </span>
      </div>

      <div className="rounded-tile overflow-hidden border border-line bg-bg-sunken grid place-items-center max-h-[52vh] p-2">
        <div className="relative inline-block max-w-full">
          <canvas
            ref={canvasRef}
            className="block max-w-full max-h-[48vh] touch-none cursor-crosshair"
          />
          <canvas
            ref={overlayRef}
            aria-hidden
            className="absolute inset-0 w-full h-full pointer-events-none"
          />
        </div>
      </div>

      <div className="mt-3 min-h-5 text-[13px]" aria-live="polite">
        {aiBusy ? (
          <AIStatusInline message={aiBusy} />
        ) : borderState && (
          <span className={cn(
            "inline-flex items-center gap-1.5",
            residuePct >= 0.2 ? "text-warning font-medium" : "text-success font-medium",
          )}>
            {residuePct < 0.2 && <CheckCircle2 size={14} aria-hidden />}
            {borderState}
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3 mt-4">
        <label className="flex items-center gap-2.5 text-sm font-semibold text-ink">
          Brush
          <input
            type="range" min="8" max="120" value={brush}
            onChange={(e) => setBrush(parseInt(e.target.value, 10))}
            className="accent-(--brand-blue) w-36"
          />
        </label>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="ghost" onClick={load} disabled={!!aiBusy}>
            <RotateCcw aria-hidden /> Revert
          </Button>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={save} loading={saving} disabled={!!aiBusy}>
            Save
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
