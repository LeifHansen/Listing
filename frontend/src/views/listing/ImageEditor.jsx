import { useCallback, useEffect, useRef, useState } from "react";
import {
  RotateCcw, Crop, Sparkles, Highlighter, CheckCircle2, Eraser, Wand2,
} from "lucide-react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { AIStatusInline } from "@/components/ui/AIStatus";
import { useToast } from "@/components/ui/Toaster";
import { api } from "@/lib/api";
import { cn, mediaUrl } from "@/lib/utils";

/* Photo studio: brush clean-up, a manual crop tool, and three AI assists —
   - analyze: re-checks the item's borders and highlights leftover background
   - auto clean: whitens everything the AI says is outside the item
   - remove background: full rembg cutout onto pure white
   - smart crop: crops to the item with a clean margin
   Every action only previews onto the canvas; nothing is stored until Save. */

const HIGHLIGHT_COLOR = "#e85c46"; // brand coral, tinted over leftovers

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
  // Manual crop tool: drag a rect, then Apply.
  const [tool, setTool] = useState("brush"); // "brush" | "crop"
  const toolRef = useRef("brush");
  const cropStart = useRef(null);
  const liveRect = useRef(null);
  const [cropRect, setCropRect] = useState(null); // {x,y,w,h} in canvas px
  const { toast } = useToast();

  useEffect(() => { toolRef.current = tool; }, [tool]);

  const clearOverlay = useCallback(() => {
    const o = overlayRef.current;
    if (o) o.getContext("2d").clearRect(0, 0, o.width, o.height);
  }, []);

  // Crop framing: dim everything outside the dragged rect.
  const drawCropOverlay = useCallback((rect) => {
    const overlay = overlayRef.current;
    const canvas = canvasRef.current;
    if (!overlay || !canvas) return;
    if (overlay.width !== canvas.width || overlay.height !== canvas.height) {
      overlay.width = canvas.width;
      overlay.height = canvas.height;
    }
    const ctx = overlay.getContext("2d");
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    if (!rect) return;
    ctx.save();
    ctx.fillStyle = "rgba(16, 17, 20, 0.45)";
    ctx.fillRect(0, 0, overlay.width, overlay.height);
    ctx.clearRect(rect.x, rect.y, rect.w, rect.h);
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = Math.max(2, overlay.width / 300);
    ctx.setLineDash([10, 7]);
    ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
    ctx.restore();
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

  const removeBg = useCallback(async () => {
    const canvas = canvasRef.current;
    if (!canvas || aiBusy) return;
    setAiBusy("Removing the background…");
    try {
      const blob = await canvasBlob(canvas);
      const res = await studioCall("/api/image/remove-bg", sessionId, name, blob);
      await applyPreview(res.image);
      toast("Background removed — review and Save to keep it.", { kind: "success" });
    } catch (e) {
      toast(`Remove background failed: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }, [aiBusy, sessionId, name, applyPreview, toast]);

  // Manual crop: toggle the tool, drag a rect, Apply.
  const toggleCropTool = useCallback(() => {
    if (aiBusy) return;
    setCropRect(null);
    liveRect.current = null;
    cropStart.current = null;
    clearOverlay();
    if (toolRef.current === "crop") {
      setTool("brush");
      return;
    }
    setHighlight(false);
    setTool("crop");
  }, [aiBusy, clearOverlay]);

  const cancelCrop = useCallback(() => {
    setCropRect(null);
    liveRect.current = null;
    clearOverlay();
  }, [clearOverlay]);

  const applyCrop = useCallback(async () => {
    const canvas = canvasRef.current;
    const rect = cropRect;
    if (!canvas || !rect) return;
    const tmp = document.createElement("canvas");
    tmp.width = Math.max(1, Math.round(rect.w));
    tmp.height = Math.max(1, Math.round(rect.h));
    tmp.getContext("2d").drawImage(
      canvas, rect.x, rect.y, rect.w, rect.h, 0, 0, tmp.width, tmp.height);
    canvas.width = tmp.width;
    canvas.height = tmp.height;
    canvas.getContext("2d").drawImage(tmp, 0, 0);
    setCropRect(null);
    liveRect.current = null;
    clearOverlay();
    setTool("brush");
    toast("Cropped — review and Save to keep it.", { kind: "success" });
    const blob = await canvasBlob(canvas);
    analyze(blob);
  }, [cropRect, clearOverlay, analyze, toast]);

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
    setTool("brush");
    setCropRect(null);
    (async () => {
      await load();
      if (initialAction === "crop") smartCrop();
      else if (initialAction === "removebg") removeBg();
      else if (initialAction === "manualcrop") {
        setHighlight(false);
        clearOverlay();
        setTool("crop");
      }
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
    // Crop tool: drag out a rect (dimmed framing preview via the overlay).
    const cropDragTo = (e) => {
      const a = cropStart.current;
      if (!a) return;
      const b = point(e);
      liveRect.current = {
        x: Math.min(a.x, b.x),
        y: Math.min(a.y, b.y),
        w: Math.abs(a.x - b.x),
        h: Math.abs(a.y - b.y),
      };
      drawCropOverlay(liveRect.current);
    };
    const start = (e) => {
      if (toolRef.current === "crop") {
        cropStart.current = point(e);
        liveRect.current = null;
        setCropRect(null);
        drawCropOverlay(null);
        e.preventDefault();
        return;
      }
      painting.current = true; paintAt(e); e.preventDefault();
    };
    const move = (e) => {
      if (toolRef.current === "crop") {
        if (cropStart.current) { cropDragTo(e); e.preventDefault(); }
        return;
      }
      if (painting.current) { paintAt(e); e.preventDefault(); }
    };
    const end = () => {
      if (toolRef.current === "crop") {
        const r = liveRect.current;
        cropStart.current = null;
        if (r && r.w >= 24 && r.h >= 24) setCropRect({ ...r });
        else { liveRect.current = null; drawCropOverlay(null); }
        return;
      }
      painting.current = false;
    };

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
        <Button variant="soft" size="sm" onClick={removeBg} disabled={!!aiBusy}>
          <Eraser aria-hidden /> Remove background
        </Button>
        <Button variant="soft" size="sm" onClick={autoClean} disabled={!!aiBusy}>
          <Sparkles aria-hidden /> Auto clean
        </Button>
        <Button variant="soft" size="sm" onClick={smartCrop} disabled={!!aiBusy}>
          <Wand2 aria-hidden /> Smart crop
        </Button>
        <Button
          variant={tool === "crop" ? "primary" : "soft"} size="sm"
          onClick={toggleCropTool} disabled={!!aiBusy}
          aria-pressed={tool === "crop"}
        >
          <Crop aria-hidden /> Crop
        </Button>
        {tool === "crop" && cropRect && (
          <>
            <Button variant="primary" size="sm" onClick={applyCrop}>
              <CheckCircle2 aria-hidden /> Apply crop
            </Button>
            <Button variant="ghost" size="sm" onClick={cancelCrop}>Cancel</Button>
          </>
        )}
        <Button
          variant={highlight ? "danger" : "ghost"} size="sm"
          onClick={toggleHighlight} disabled={!!aiBusy}
          aria-pressed={highlight}
        >
          <Highlighter aria-hidden /> {highlight ? "Hide highlight" : "Highlight leftovers"}
        </Button>
        <span className="ml-auto text-xs text-ink-secondary hidden sm:block">
          {tool === "crop"
            ? "Drag on the photo to frame the crop, then Apply."
            : "Paint over anything with the white brush."}
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
