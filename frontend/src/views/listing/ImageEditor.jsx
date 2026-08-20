import { useCallback, useEffect, useRef, useState } from "react";
import {
  RotateCcw, Crop, Eraser, Wand2, Paintbrush, Undo2, Redo2, Brush, Eye,
} from "lucide-react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { AIStatusInline } from "@/components/ui/AIStatus";
import { useToast } from "@/components/ui/Toaster";
import { api } from "@/lib/api";
import { cn, mediaUrl } from "@/lib/utils";
import {
  ERASE, RESTORE, applySnapshot, compose, createHistory, drawStroke,
  layersFrom, pushOperation, pushStroke, replay, snapshot,
} from "./editorLayers";

/* Photo studio — a small toolbar of one-tap tools, all previewed on the canvas
   (nothing is stored until Save):
   - Remove background: in-house cutout onto pure white
   - Auto levels: stretch brightness/contrast to make the shot pop (client-side)
   - Crop: drag a rectangle; it applies the moment you release
   - Restore / Erase brushes, undo/redo, and Compare

   Nothing here is destructive. The original photo stays underneath the cutout
   for as long as the studio is open (see editorLayers.js), which is what makes
   Restore possible at all: when the remover eats a strap or a heel, the seller
   paints it back instead of hitting Revert and losing the parts it got right.
   Every edit is undoable, and Save is the only thing that writes. */

// One toolbar button — icon + label, with a pressed/active state for the
// toggle tools (Crop, and the two brushes).
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
  const [brush, setBrush] = useState(40);
  const brushRef = useRef(40);
  // The non-destructive layer stack + the edit history over it.
  const layersRef = useRef(null);
  const strokesRef = useRef([]);
  const historyRef = useRef(createHistory());
  const strokeRef = useRef(null);      // the stroke currently under the finger
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [brushMode, setBrushMode] = useState(ERASE);
  const brushModeRef = useRef(ERASE);
  const [saving, setSaving] = useState(false);
  const [aiBusy, setAiBusy] = useState(null); // destructive op running (locks Save)
  // Manual crop tool: drag a rect, then Apply.
  const [tool, setTool] = useState("brush"); // "brush" | "crop"
  const toolRef = useRef("brush");
  const cropStart = useRef(null);
  const liveRect = useRef(null);
  const [cropRect, setCropRect] = useState(null); // {x,y,w,h} in canvas px
  const { toast } = useToast();

  useEffect(() => { toolRef.current = tool; }, [tool]);
  useEffect(() => { brushModeRef.current = brushMode; }, [brushMode]);

  const clearOverlay = useCallback(() => {
    const o = overlayRef.current;
    if (o) o.getContext("2d").clearRect(0, 0, o.width, o.height);
  }, []);

  const syncHistory = useCallback(() => {
    setCanUndo(historyRef.current.undo.length > 0);
    setCanRedo(historyRef.current.redo.length > 0);
  }, []);

  // Re-draw the visible canvas from the layers. Everything that changes an
  // edit ends here.
  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const layers = layersRef.current;
    if (canvas && layers) compose(layers, canvas);
  }, []);

  // Replace the cutout layer with the result of a big operation, recording
  // the before/after so it can be undone like anything else.
  const commitOperation = useCallback((nextSource, { keepStrokes = false } = {}) => {
    const layers = layersRef.current;
    if (!layers) return;
    const before = snapshot(layers, strokesRef.current);
    const next = layersFrom(nextSource);
    // The ORIGINAL is carried forward, not replaced: it is the thing Restore
    // paints back, and a cutout that replaced it would leave nothing to
    // restore FROM. Crop is the exception -- it changes the frame, so the
    // original has to be re-cut to match, which layersFrom already did.
    if (!keepStrokes) strokesRef.current = [];
    if (layers.width === next.width && layers.height === next.height) {
      next.original = layers.original;
    }
    layersRef.current = next;
    replay(next, strokesRef.current);
    redraw();
    pushOperation(historyRef.current, before,
                  snapshot(next, strokesRef.current));
    syncHistory();
  }, [redraw, syncHistory]);

  const undo = useCallback(() => {
    const entry = historyRef.current.undo.pop();
    if (!entry) return;
    historyRef.current.redo.push(entry);
    if (entry.kind === "stroke") {
      strokesRef.current = strokesRef.current.slice(0, -1);
      replay(layersRef.current, strokesRef.current);
    } else {
      strokesRef.current = applySnapshot(layersRef.current, entry.before);
    }
    clearOverlay();
    redraw();
    syncHistory();
  }, [clearOverlay, redraw, syncHistory]);

  // Press-and-hold before/after. Draws the untouched original straight to the
  // visible canvas and puts the composition back on release — no state to get
  // out of step, and nothing about the edit changes.
  const showOriginal = useCallback(() => {
    const layers = layersRef.current;
    const canvas = canvasRef.current;
    if (!layers || !canvas) return;
    setComparing(true);
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(layers.original, 0, 0);
  }, []);

  const showEdited = useCallback(() => {
    setComparing((was) => {
      if (was) redraw();
      return false;
    });
  }, [redraw]);

  const redo = useCallback(() => {
    const entry = historyRef.current.redo.pop();
    if (!entry) return;
    historyRef.current.undo.push(entry);
    if (entry.kind === "stroke") {
      strokesRef.current = [...strokesRef.current, entry.stroke];
      drawStroke(layersRef.current, entry.stroke);
    } else {
      strokesRef.current = applySnapshot(layersRef.current, entry.after);
    }
    clearOverlay();
    redraw();
    syncHistory();
  }, [clearOverlay, redraw, syncHistory]);


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

  const load = useCallback(async () => {
    const canvas = canvasRef.current;
    if (!canvas || !name) return;
    try {
      // Same-origin so the canvas isn't tainted and toBlob() works.
      const img = await loadImage(`${mediaUrl(sessionId, name)}?v=${Date.now()}`);
      layersRef.current = layersFrom(img);
      strokesRef.current = [];
      historyRef.current = createHistory();
      syncHistory();
      redraw();
      clearOverlay();
    } catch (e) {
      toast(e.message, { kind: "error" });
    }
  }, [sessionId, name, clearOverlay, redraw, syncHistory, toast]);

  // Land an AI-processed image as the new cutout layer.
  const applyPreview = useCallback(async (dataUrl) => {
    const img = await loadImage(dataUrl);
    commitOperation(img);
    clearOverlay();
  }, [commitOperation, clearOverlay]);

  const removeBg = useCallback(async () => {
    const canvas = canvasRef.current;
    if (!canvas || aiBusy) return;
    setAiBusy("Removing the background…");
    try {
      const blob = await canvasBlob(canvas);
      const res = await studioCall("/api/image/remove-bg", sessionId, name, blob);
      await applyPreview(res.image);
      toast(
        `Background removed${res.engine === "adobe" ? " with Adobe Photoshop" : res.engine === "photoroom" ? " with Photoroom" : res.engine === "pixian" ? " with Pixian" : " (on-server model)"} — review and Save to keep it.`,
        { kind: "success" },
      );
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
    const layers = layersRef.current;
    if (!layers || aiBusy) return;
    const canvas = layers.cutout;
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
    // Into a scratch canvas, then through commitOperation, so this is one
    // undoable step like everything else rather than a change with no way back
    // short of Revert.
    const next = document.createElement("canvas");
    next.width = canvas.width;
    next.height = canvas.height;
    next.getContext("2d").putImageData(px, 0, 0);
    commitOperation(next, { keepStrokes: true });
    toast("Auto levels applied — Undo to take it back, Save to keep.",
          { kind: "success" });
  }, [aiBusy, commitOperation, toast]);

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
    setTool("crop");
  }, [aiBusy, clearOverlay]);

  const applyCrop = useCallback(async () => {
    const canvas = canvasRef.current;
    const rect = cropRect;
    if (!canvas || !rect || !layersRef.current) return;
    // Crop the COMPOSED image: it is what the seller framed. The layer stack
    // is rebuilt around it (which resets the original to the cropped view —
    // Restore can only reach back to pixels that are still in frame), and the
    // whole thing is one undoable step.
    const tmp = document.createElement("canvas");
    tmp.width = Math.max(1, Math.round(rect.w));
    tmp.height = Math.max(1, Math.round(rect.h));
    tmp.getContext("2d").drawImage(
      canvas, rect.x, rect.y, rect.w, rect.h, 0, 0, tmp.width, tmp.height);
    setCropRect(null);
    liveRect.current = null;
    clearOverlay();
    setTool("brush");
    commitOperation(tmp);
    toast("Cropped — Undo to take it back, Save to keep it.", { kind: "success" });
  }, [cropRect, clearOverlay, commitOperation, toast]);

  // Crop applies the moment you release the drag — no separate Apply button.
  useEffect(() => {
    if (cropRect) applyCrop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cropRect]);

  useEffect(() => {
    if (!name) return;
    setTool("brush");
    setCropRect(null);
    (async () => {
      await load();
      if (initialAction === "removebg") removeBg();
      else if (initialAction === "manualcrop" || initialAction === "crop") {
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
    // A stroke accumulates points and is re-drawn from its start each time —
    // so the segments join into a continuous line instead of the dotted trail
    // of separate stamps a quick drag used to leave.
    const paintAt = (e) => {
      if (!painting.current || !layersRef.current) return;
      const { x, y } = point(e);
      const stroke = strokeRef.current;
      if (!stroke) return;
      stroke.points.push([x, y]);
      // Redrawing the whole (short) stroke is cheaper than it sounds and
      // keeps live painting identical to what replay() will produce.
      replay(layersRef.current, [...strokesRef.current, stroke]);
      compose(layersRef.current, canvas);
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
      painting.current = true;
      const scale = canvas.width / canvas.getBoundingClientRect().width;
      strokeRef.current = {
        tool: brushModeRef.current,
        radius: (brushRef.current || 40) * scale / 2,
        points: [],
      };
      paintAt(e);
      e.preventDefault();
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
      if (painting.current && strokeRef.current?.points.length) {
        strokesRef.current = [...strokesRef.current, strokeRef.current];
        pushStroke(historyRef.current, strokeRef.current);
        setCanUndo(true);
        setCanRedo(false);
      }
      strokeRef.current = null;
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
  }, [name, drawCropOverlay]);

  const save = async () => {
    const canvas = canvasRef.current;
    if (!canvas || !name) return;
    // Compose fresh rather than trusting whatever is on screen — Compare
    // paints the original onto this canvas, and a save mid-hold would
    // otherwise write the un-edited photo.
    if (layersRef.current) compose(layersRef.current, canvas);
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

  return (
    <Dialog open={!!name} onClose={onClose} title="Photo studio" wide>
      <div className="flex items-center gap-1 p-1.5 mb-2 rounded-[14px] bg-bg-sunken border border-line overflow-x-auto">
        <Tool icon={Eraser} label="Remove BG" onClick={removeBg} disabled={!!aiBusy} />
        <Tool icon={Wand2} label="Auto levels" onClick={autoLevels} disabled={!!aiBusy} />
        <span className="w-px self-stretch bg-line mx-1 shrink-0" aria-hidden />
        {/* The two brushes. Restore is the one that did not exist: with only a
            white brush, a cutout that ate part of the item was a dead end. */}
        <Tool icon={Brush} label="Restore item" active={tool === "brush" && brushMode === RESTORE}
          onClick={() => { setTool("brush"); setBrushMode(RESTORE); }}
          disabled={!!aiBusy} />
        <Tool icon={Paintbrush} label="Erase BG" active={tool === "brush" && brushMode === ERASE}
          onClick={() => { setTool("brush"); setBrushMode(ERASE); }}
          disabled={!!aiBusy} />
        <span className="w-px self-stretch bg-line mx-1 shrink-0" aria-hidden />
        <Tool icon={Crop} label="Crop" active={tool === "crop"}
          onClick={toggleCropTool} disabled={!!aiBusy} />
        <span className="w-px self-stretch bg-line mx-1 shrink-0" aria-hidden />
        <Tool icon={Undo2} label="Undo" onClick={undo} disabled={!canUndo || !!aiBusy} />
        <Tool icon={Redo2} label="Redo" onClick={redo} disabled={!canRedo || !!aiBusy} />
        {/* Press and hold: the only honest way to judge a cutout is against
            the photo it came from. */}
        <Tool
          icon={Eye} label="Compare" active={comparing} disabled={!!aiBusy}
          onPointerDown={showOriginal} onPointerUp={showEdited}
          onPointerLeave={showEdited} onPointerCancel={showEdited}
        />
      </div>
      <p className="text-xs text-ink-secondary mb-3 px-0.5">
        {tool === "crop"
          ? "Drag a box on the photo — it crops the moment you let go."
          : brushMode === RESTORE
            ? "Paint over anything the cutout removed by mistake — the original photo comes back underneath."
            : "Paint the background out, or switch to Restore item to bring back anything the cutout took."}
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
        {aiBusy && <AIStatusInline message={aiBusy} />}
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
          <Button variant="ghost" onClick={load} disabled={!!aiBusy}
            title="Discard every edit and reload the saved photo">
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
