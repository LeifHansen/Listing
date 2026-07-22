import { useCallback, useEffect, useRef, useState } from "react";
import {
  RotateCcw, Crop, Highlighter, CheckCircle2, Eraser, Wand2, Paintbrush,
} from "lucide-react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { AIStatusInline } from "@/components/ui/AIStatus";
import { useToast } from "@/components/ui/Toaster";
import { api } from "@/lib/api";
import { cn, mediaUrl } from "@/lib/utils";

/* Photo studio — a small toolbar of one-tap tools, all previewed on the canvas
   (nothing is stored until Save):
   - Remove background: in-house cutout onto pure white
   - Auto levels: stretch brightness/contrast to make the shot pop (client-side)
   - Crop: drag a rectangle; it applies the moment you release
   - Highlight leftovers: tint any background the cutout missed red, so you can
     paint it out with the white brush (off until you turn it on). */

const HIGHLIGHT_COLOR = "#e85c46"; // brand coral, tinted over leftovers

// One toolbar button — icon + label, with a pressed/active state for the
// toggle tools (Crop, Highlight).
function Tool({ icon: Icon, label, active, className, ...props }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={cn(
        "inline-flex items-center gap-1.5 h-9 px-3 rounded-[10px] text-[13px] font-semibold",
        "cursor-pointer transition-colors duration-150 shrink-0",
        "disabled:opacity-40 disabled:pointer-events-none",
        active
          ? "bg-blue text-on-accent shadow-card"
          : "text-ink-secondary hover:bg-card hover:text-ink",
        className,
      )}
      {...props}
    >
      <Icon size={16} aria-hidden /> {label}
    </button>
  );
}

function canvasBlob(canvas, type = "image/jpeg", q = 0.92) {
  return new Promise((r) => canvas.toBlob(r, type, q));
}

async function studioCall(path, sessionId, name, blob, timeoutMs) {
  const fd = new FormData();
  fd.append("session_id", sessionId);
  fd.append("name", name);
  if (blob) fd.append("file", new File([blob], name, { type: blob.type }));
  const opts = { method: "POST", body: fd };
  if (timeoutMs) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    opts.signal = ctrl.signal;
    try { return await api(path, opts); }
    finally { clearTimeout(timer); }
  }
  return api(path, opts);
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
  const [aiBusy, setAiBusy] = useState(null); // destructive op running (locks Save)
  const [checking, setChecking] = useState(null); // passive border re-check (never locks Save)
  const [highlight, setHighlight] = useState(false);
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
      const res = await studioCall("/api/image/analyze", sessionId, name, blob, 30000);
      paintedSinceAnalyze.current = false;
      setResiduePct(res.residue_pct);
      // Highlight is opt-in now: only tint leftovers when the user explicitly
      // turns it on (show === true). On load / after an edit we just record the
      // residue for the status line, without the red overlay.
      const shouldShow = show === true;
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
    } catch (e) {
      toast(e.message, { kind: "error" });
      return;
    }
    // Border re-check is a passive assist: it never blocks Save (the image is
    // already on the canvas) and times out so a slow model can't lock the editor.
    setChecking("Re-checking the item's borders…");
    try { await analyze(null); }
    finally { setChecking(null); }
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

  // Auto levels — a percentile contrast/brightness stretch, done right on the
  // canvas so it's instant (no round-trip). Ignores the darkest/lightest 0.5%
  // so a stray highlight or shadow doesn't skew the whole image, then maps the
  // remaining luminance range across the full 0-255 span on every channel (so
  // hues are preserved — it brightens and adds contrast without a color cast).
  const autoLevels = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || aiBusy) return;
    const ctx = canvas.getContext("2d");
    const px = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const d = px.data;
    const hist = new Array(256).fill(0);
    for (let i = 0; i < d.length; i += 4) {
      hist[(d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114) | 0]++;
    }
    const clip = (d.length / 4) * 0.005;
    let lo = 0, hi = 255, acc = 0;
    for (let v = 0; v < 256; v++) { acc += hist[v]; if (acc > clip) { lo = v; break; } }
    acc = 0;
    for (let v = 255; v >= 0; v--) { acc += hist[v]; if (acc > clip) { hi = v; break; } }
    if (hi - lo < 8) { toast("This photo already uses the full range.", { kind: "info" }); return; }
    const scale = 255 / (hi - lo);
    const lut = new Uint8ClampedArray(256);
    for (let v = 0; v < 256; v++) lut[v] = (v - lo) * scale;
    for (let i = 0; i < d.length; i += 4) {
      d[i] = lut[d[i]]; d[i + 1] = lut[d[i + 1]]; d[i + 2] = lut[d[i + 2]];
    }
    ctx.putImageData(px, 0, 0);
    toast("Auto levels applied — Revert to undo, Save to keep.", { kind: "success" });
  }, [aiBusy, toast]);

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
    toast("Cropped — Revert to undo, Save to keep it.", { kind: "success" });
    const blob = await canvasBlob(canvas);
    analyze(blob);
  }, [cropRect, clearOverlay, analyze, toast]);

  // Crop applies the moment you release the drag — no separate Apply button.
  useEffect(() => {
    if (cropRect) applyCrop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cropRect]);

  const toggleHighlight = useCallback(async () => {
    if (aiBusy || checking) return;
    if (highlight) {
      setHighlight(false);
      clearOverlay();
      return;
    }
    // Re-analyze against the current canvas so fresh brush strokes count.
    setChecking("Re-checking the item's borders…");
    try {
      const blob = paintedSinceAnalyze.current
        ? await canvasBlob(canvasRef.current) : null;
      await analyze(blob, { show: true });
    } finally {
      setChecking(null);
    }
  }, [aiBusy, checking, highlight, analyze, clearOverlay]);

  useEffect(() => {
    if (!name) return;
    setTool("brush");
    setCropRect(null);
    (async () => {
      await load();
      if (initialAction === "removebg") removeBg();
      else if (initialAction === "manualcrop" || initialAction === "crop") {
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
      ? `Found leftover background (~${residuePct}% of the frame)${highlight ? " — tinted red" : ""}. Turn on Highlight to see it, or paint it out with the brush.`
      : "Item borders look clean.";

  return (
    <Dialog open={!!name} onClose={onClose} title="Photo studio" wide>
      <div className="flex items-center gap-1 p-1.5 mb-2 rounded-[14px] bg-bg-sunken border border-line overflow-x-auto">
        <Tool icon={Eraser} label="Remove BG" onClick={removeBg} disabled={!!aiBusy} />
        <Tool icon={Wand2} label="Auto levels" onClick={autoLevels} disabled={!!aiBusy} />
        <span className="w-px self-stretch bg-line mx-1 shrink-0" aria-hidden />
        <Tool icon={Crop} label="Crop" active={tool === "crop"}
          onClick={toggleCropTool} disabled={!!aiBusy} />
        <Tool icon={Highlighter} label="Highlight" active={highlight}
          onClick={toggleHighlight} disabled={!!aiBusy} />
      </div>
      <p className="text-xs text-ink-secondary mb-3 px-0.5">
        {tool === "crop"
          ? "Drag a box on the photo — it crops the moment you let go."
          : highlight
            ? "Leftover background is tinted red — paint over it with the white brush."
            : "Paint the background out with the white brush, or turn on Highlight to spot leftovers."}
      </p>

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
        {(aiBusy || checking) ? (
          <AIStatusInline message={aiBusy || checking} />
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
          <Paintbrush size={16} className="text-ink-secondary" aria-hidden /> Brush
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
