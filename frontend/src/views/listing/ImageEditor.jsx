import { useCallback, useEffect, useRef, useState } from "react";
import {
  RotateCcw, RotateCw, Crop, Eraser, Wand2, Paintbrush, Undo2, Redo2, Brush,
  Eye, History, Cloud, Download, Palette,
} from "lucide-react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { AIStatusInline } from "@/components/ui/AIStatus";
import { useToast } from "@/components/ui/Toaster";
import { api, MODEL_TIMEOUT_MS } from "@/lib/api";
import { useApp } from "@/store";
import { cn } from "@/lib/utils";
import { loadImage, loadPhoto } from "@/lib/photoFile";
import {
  LocalCutoutError, applyMask, composeMask, previewCut,
} from "@/lib/bgRemoval/service";
import { PhotoroomProvider } from "@/lib/bgRemoval/providers/photoroom";
import { useLocalCutout } from "@/lib/bgRemoval/useLocalCutout";
import {
  ERASE, RESTORE, applySnapshot, compose, createHistory, drawStroke,
  layersFrom, pushOperation, pushStroke, releaseLayers, replay, rotateQuarter,
  snapshot,
} from "./editorLayers";

/* Photo studio — a small toolbar of one-tap tools, all previewed on the canvas
   (nothing is stored until Save):
   - Remove background: on THIS device wherever it can (Apple Vision, ML Kit,
     the browser model -- free, nothing sent anywhere), onto the seller's
     colour; Photoroom's cutout for a seller who switched cloud removal on,
     as the fallback or on "Enhance with cloud". A picture is cut by its edge
     and a close-up of a detail is asked about first, as in the batch.
   - Auto levels: stretch brightness/contrast to make the shot pop (client-side)
   - Crop: drag a rectangle; it applies the moment you release
   - Rotate: a quarter turn clockwise per tap, on the canvas (client-side)
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

// Every studio request. Session and name go as form fields (not in the URL
// path) so nothing falls through to the static handler and 405s.
async function studioCall(path, sessionId, name, blob, timeoutMs) {
  const fd = new FormData();
  fd.append("session_id", sessionId);
  fd.append("name", name);
  if (blob) fd.append("file", new File([blob], name, { type: blob.type }));
  // timeoutMs goes in the opts bag, which is the only channel api() reads.
  // Rolling our own AbortController here looked equivalent and was not: api()
  // computed its own deadline from opts.timeoutMs (absent -> the 90s default)
  // and then replaced our signal with its own. So MODEL_TIMEOUT_MS was
  // imported, passed down, and dropped -- "Remove background" kept giving up
  // at 90 seconds on an inference that takes ~107 in production, told the
  // seller it had taken "longer than 90s", and left them to save the
  // untouched original while the server finished into a closed socket.
  return api(path, { method: "POST", body: fd,
                     ...(timeoutMs ? { timeoutMs } : {}) });
}


export function ImageEditor({ sessionId, name, onClose, onSaved }) {
  // Which photo is open RIGHT NOW. This dialog is one long-lived instance
  // whose `name` prop changes (NewListing renders it unkeyed), so an in-flight
  // request holds the name it started on while the canvas has already moved
  // to another photo. A background removal is a round trip to Photoroom, which
  // is time enough to close the studio and open a different photo.
  const nameRef = useRef(name);
  useEffect(() => { nameRef.current = name; }, [name]);
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
  const { toast, confirm } = useToast();
  const { bgRemoval, loadBgRemovalStatus, openSettings, cutoutQueue } = useApp();
  const local = useLocalCutout();
  // The cloud: a seller who switched it on, while the app's month of it is
  // not spent. The device needs neither.
  const canCloud = !!(bgRemoval?.available && bgRemoval?.active
                      && !bgRemoval?.budget_exhausted);
  const canCut = local.usable || !!(bgRemoval?.available && bgRemoval?.active);
  const offerCut = local.usable || !!bgRemoval?.available;
  // What the photo pass's screen said about the open photo ("art", "detail").
  const kindRef = useRef("");
  // The last cut made ON THIS DEVICE, while it is still what the canvas
  // shows: its matte (to re-colour it, hand it over transparent, or save it
  // without cutting again), the bytes it was made from, and the history
  // entry it landed as -- Save compares that to the top of the history to
  // know nothing was changed after it.
  const cutRef = useRef(null);
  const [lastCut, setLastCut] = useState(null);  // {provider, background, cloud}

  // The device queue leaves the open photo alone: cutting it in the
  // background would be saved over by this studio's own Save.
  useEffect(() => {
    if (!name) return undefined;
    cutoutQueue?.setEditing?.(sessionId, name);
    return () => cutoutQueue?.setEditing?.(null, null);
  }, [cutoutQueue, sessionId, name]);

  // Opening a different photo starts from a clean slate: the brush tool, no
  // half-dragged crop carried over from the last one. This is derived DURING
  // RENDER off the previous `name` (React's documented "adjust state when a
  // prop changes" pattern) rather than in an effect, so the reset lands in the
  // same commit as the new photo instead of costing a second render — the
  // toolbar never paints one frame still showing the previous photo's Crop
  // button lit up.
  const [prevName, setPrevName] = useState(name);
  if (name !== prevName) {
    setPrevName(name);
    // Closing the studio (name -> null) deliberately leaves the tools alone,
    // exactly as the old effect's `if (!name) return` did — only opening a
    // photo resets them.
    if (name) {
      setTool("brush");
      setCropRect(null);
    }
  }

  useEffect(() => { toolRef.current = tool; }, [tool]);
  useEffect(() => { brushModeRef.current = brushMode; }, [brushMode]);

  const clearOverlay = useCallback(() => {
    const o = overlayRef.current;
    if (o) o.getContext("2d").clearRect(0, 0, o.width, o.height);
  }, []);

  // Revert and Restore original put back a canvas with no cut of this
  // studio's on it.
  const forgetCut = useCallback(() => {
    cutRef.current = null;
    setLastCut(null);
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

  // Answers whether the photo landed on the canvas. It does not when the
  // seller has moved to another photo while this one downloaded: every open is
  // a fresh download (the ?v= below), so photo A's bytes can arrive after
  // photo B's -- and painted then, Save would write A's pixels into B's file.
  // Same guard, same reason, as removeBg's.
  const load = useCallback(async () => {
    const canvas = canvasRef.current;
    if (!canvas || !name) return false;
    const startedOn = name;
    try {
      // Through the bytes route, not /media: a redirect to the bucket (an
      // offloaded photo) or the native shell's cross-origin <img> taints the
      // canvas, and then every tool that reads pixels -- Save included --
      // throws. See lib/photoFile.
      const { img, kind } = await loadPhoto(sessionId, name);
      if (nameRef.current !== startedOn) return false;
      kindRef.current = kind || "";
      // The cut this studio made is gone with the canvas it was on. (Its
      // controls hide themselves for another photo -- lastCut carries the
      // name -- and Revert/Restore say forgetCut themselves.)
      cutRef.current = null;
      // Revert and Restore original load over a stack that is still here.
      releaseLayers(layersRef.current, historyRef.current);
      layersRef.current = layersFrom(img);
      strokesRef.current = [];
      historyRef.current = createHistory();
      syncHistory();
      redraw();
      clearOverlay();
      return true;
    } catch (e) {
      if (nameRef.current === startedOn) toast(e.message, { kind: "error" });
      return false;
    }
  }, [sessionId, name, clearOverlay, redraw, syncHistory, toast]);

  // Land an AI-processed image as the new cutout layer.
  const applyPreview = useCallback(async (dataUrl) => {
    const img = await loadImage(dataUrl);
    commitOperation(img);
    clearOverlay();
  }, [commitOperation, clearOverlay]);

  // Where the history's top entry is now: Save reads this against the cut.
  const topEntry = () => {
    const undo = historyRef.current.undo;
    return undo[undo.length - 1] || null;
  };

  const removeBg = useCallback(async () => {
    const canvas = canvasRef.current;
    if (!canvas || aiBusy) return;
    // Neither this device nor the cloud can do it for this seller: the
    // server would refuse it (409), so send the seller to the one place that
    // can switch it on instead of to an error.
    if (!canCut) {
      toast("Background removal is off — turn it on in Settings first.",
        { kind: "info" });
      onClose?.();
      openSettings("bg-removal");
      return;
    }
    // Not before the photo has landed: the blank canvas would be sent off to
    // be cut out, and charged for.
    if (!layersRef.current) return;
    // A close-up of a tag or a stitch has no background: every pixel is the
    // item, and a cutout keeps the label and deletes the garment behind it.
    if (kindRef.current === "detail" && !(await confirm({
      title: "This looks like a close-up",
      message: "There's no background to take off a close-up of a detail — "
        + "removing it usually cuts into the item. Remove it anyway?",
      confirmLabel: "Remove it anyway",
    }))) return;
    const startedOn = name;
    setAiBusy(local.usable ? "Removing the background on this device…"
                           : "Removing the background…");
    try {
      if (local.usable) {
        // Unedited: cut the STORED photo, whose source is the photo as shot
        // -- never a composite on white from an earlier cut. Edited (a crop,
        // strokes): cut what the seller is looking at.
        const unedited = historyRef.current.undo.length === 0;
        const blob = unedited ? null : await canvasBlob(canvas);
        const res = await previewCut({ sessionId, name, blob,
                                       allowCloud: canCloud });
        // Committing this now would paint one photo's cutout onto another's
        // canvas -- and Save would then write those pixels into the other
        // photo's file. Staying quiet is right: the seller moved on.
        if (nameRef.current !== startedOn) return;
        await applyPreview(res.image);
        cutRef.current = res.mask ? {
          mask: res.mask, sourceSha: res.sourceSha, provider: res.provider,
          platform: res.platform, fromSource: res.fromSource, blob,
          entry: topEntry(), background: "",
        } : null;
        setLastCut({ name, provider: res.provider, cloud: !!res.cloud, background: "" });
        toast(res.cloud
          ? "This device couldn't cut it, so the cloud did — review and Save to keep it."
          : res.art
            ? "Cut to the picture's edge — review and Save to keep it."
            : "Background removed — review and Save to keep it.",
          { kind: "success" });
        if (res.cloud) loadBgRemovalStatus();
        return;
      }
      const blob = await canvasBlob(canvas);
      const res = await studioCall("/api/image/remove-bg", sessionId, name, blob,
                                   MODEL_TIMEOUT_MS);
      if (nameRef.current !== startedOn) return;
      await applyPreview(res.image);
      cutRef.current = null;
      setLastCut({ name, provider: "photoroom", cloud: true, background: "" });
      toast("Background removed — review and Save to keep it.",
        { kind: "success" });
      loadBgRemovalStatus();
    } catch (e) {
      if (e instanceof LocalCutoutError) {
        const offer = bgRemoval?.available && !canCloud
          ? " You can turn on cloud background removal in Settings to use it instead."
          : "";
        toast(`${e.message}${offer}`, { kind: "error" });
      } else {
        toast(`Remove background failed: ${e.message}`, { kind: "error" });
      }
    } finally {
      setAiBusy(null);
    }
  }, [aiBusy, canCut, canCloud, local.usable, sessionId, name, applyPreview,
      toast, confirm, onClose, openSettings, loadBgRemovalStatus,
      bgRemoval?.available]);

  // "Enhance with cloud": Photoroom's cutout of the photo, for a seller who
  // switched cloud removal on -- an explicit, paid step, never automatic here.
  const enhanceWithCloud = useCallback(async () => {
    if (aiBusy || !layersRef.current) return;
    if (!canCloud) {
      onClose?.();
      openSettings("bg-removal");
      return;
    }
    const startedOn = name;
    setAiBusy("Removing the background in the cloud…");
    try {
      const fromStored = cutRef.current?.fromSource;
      const blob = fromStored ? null : cutRef.current?.blob || null;
      const res = await PhotoroomProvider.removeBackground(blob, {
        sessionId, name, intent: "preview" });
      if (nameRef.current !== startedOn) return;
      await applyPreview(res.image);
      cutRef.current = null;
      setLastCut({ name, provider: "photoroom", cloud: true, background: "" });
      toast("Enhanced in the cloud — review and Save to keep it.", { kind: "success" });
      loadBgRemovalStatus();
    } catch (e) {
      toast(`Cloud background removal failed: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }, [aiBusy, canCloud, sessionId, name, applyPreview, toast, onClose,
      openSettings, loadBgRemovalStatus]);

  // The same device cut on another colour: the matte is reused, nothing is
  // cut again.
  const recolour = useCallback(async (background) => {
    const c = cutRef.current;
    if (!c || aiBusy) return;
    const startedOn = name;
    setAiBusy("Changing the background…");
    try {
      const out = await composeMask({ sessionId, name, blob: c.blob, mask: c.mask,
                                      background, provider: c.provider,
                                      platform: c.platform });
      if (nameRef.current !== startedOn) return;
      await applyPreview(out.image);
      cutRef.current = { ...c, entry: topEntry(), background };
      setLastCut((l) => ({ ...(l || {}), background }));
    } catch (e) {
      toast(`Couldn't change the background: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }, [aiBusy, sessionId, name, applyPreview, toast]);

  // The item on nothing, as a PNG, for the seller to use anywhere.
  const downloadPng = useCallback(async () => {
    const c = cutRef.current;
    if (!c || aiBusy) return;
    setAiBusy("Preparing the PNG…");
    try {
      const out = await composeMask({ sessionId, name, blob: c.blob, mask: c.mask,
                                      background: "transparent",
                                      provider: c.provider, platform: c.platform });
      const a = document.createElement("a");
      a.href = out.image;
      a.download = `${String(name).replace(/\.[^.]+$/, "")}-cutout.png`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (e) {
      toast(`Couldn't make the PNG: ${e.message}`, { kind: "error" });
    } finally {
      setAiBusy(null);
    }
  }, [aiBusy, sessionId, name, toast]);

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

  // Rotate 90° clockwise. On the canvas, not through /api/rotate-image: the
  // studio's contract is that nothing is written until Save, and a server
  // rotate would rewrite the file under an edit still in progress. It is one
  // undoable step like crop, and like crop it changes the frame, so the
  // layer stack is rebuilt around the turned picture (see rotateQuarter).
  const rotate = useCallback(() => {
    const canvas = canvasRef.current;
    const layers = layersRef.current;
    if (!canvas || !layers || aiBusy) return;
    // Compose fresh: Compare may have painted the original onto the canvas.
    compose(layers, canvas);
    // A half-dragged crop belongs to the old frame.
    setCropRect(null);
    liveRect.current = null;
    cropStart.current = null;
    clearOverlay();
    commitOperation(rotateQuarter(canvas));
  }, [aiBusy, clearOverlay, commitOperation]);

  // Crop applies the moment you release the drag — no separate Apply button.
  useEffect(() => {
    if (cropRect) applyCrop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cropRect]);

  // Load the newly-opened photo onto the canvas. The tool/crop reset that
  // used to head this effect now happens during render (see prevName above);
  // what is left is async work against the canvas and the API, so it has to
  // stay in an effect. (A photo left before it loaded is not painted — see
  // load.)
  //
  // Closing the studio, or moving to another photo, lets go of this one's
  // layer stack and history (see releaseLayers): the dialog stays mounted
  // between photos, so otherwise they outlive the photo they were for.
  useEffect(() => {
    if (!name) return undefined;
    load();
    return () => {
      releaseLayers(layersRef.current, historyRef.current);
      layersRef.current = null;
      strokesRef.current = [];
      historyRef.current = createHistory();
      syncHistory();
    };
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

  // "Restore original" — the photo as uploaded, before the pass touched it.
  //
  // The upload has always been on the server, in the session's original/
  // directory, and nothing in the app could reach it. That only became
  // survivable-until-it-wasn't when the background remover started eating
  // photos: handed a photograph of a PICTURE it keeps what the picture
  // depicts and deletes the artwork around it, and a seller's only option for
  // the ruined tiles was to delete them and re-upload photos we already had.
  //
  // Confirmed first, because it throws away editing done since — and the
  // server snapshots the outgoing copy to history, so it is not a one-way
  // door either.
  const [restoring, setRestoring] = useState(false);
  const restoreOriginal = async () => {
    if (!name) return;
    if (!(await confirm({
      title: "Restore the original photo?",
      message: "This puts back the photo exactly as you uploaded it, before "
        + "the background removal and any edits. Anything you have changed "
        + "here is discarded.",
      confirmLabel: "Restore it",
    }))) return;
    setRestoring(true);
    try {
      await studioCall("/api/image/restore-original", sessionId, name);
      forgetCut();
      await load();      // repaint from the file the server just rewrote
      onSaved();
      toast("Put back the photo as you shot it.", { kind: "success" });
    } catch (e) {
      // The originals are pruned on a timer, so "there is nothing to restore"
      // is a real answer and the server's sentence says so.
      toast(e.message, { kind: "error" });
    } finally {
      setRestoring(false);
    }
  };

  const save = async () => {
    const canvas = canvasRef.current;
    // Nothing to save until the photo has landed: the canvas is still blank
    // (or the last photo's), and writing it would replace this one's file.
    if (!canvas || !name || !layersRef.current) return;
    // Compose fresh rather than trusting whatever is on screen — Compare
    // paints the original onto this canvas, and a save mid-hold would
    // otherwise write the un-edited photo.
    compose(layersRef.current, canvas);
    // A device cut of the stored photo that nothing has touched since, on
    // the seller's own colour: saved as a CUT (the matte goes up, the server
    // composites it and keeps the photo as shot and the matte beside it), so
    // it can later be re-coloured, exported transparent, or cut again from
    // the shot. Anything else -- a crop or a stroke after the cut, a colour
    // picked here -- is the seller's own picture, saved as one.
    const c = cutRef.current;
    if (c && c.fromSource && !c.background && topEntry() === c.entry) {
      setSaving(true);
      try {
        await applyMask({ sessionId, name, mask: c.mask, sourceSha: c.sourceSha,
                          provider: c.provider, platform: c.platform,
                          origin: "studio" });
        onSaved();
        onClose();
        return;
      } catch (e) {
        // The photo changed under the cut, or the server would not take the
        // matte: the canvas is still what the seller meant to save.
      } finally {
        setSaving(false);
      }
    }
    const blob = await canvasBlob(canvas);
    if (!blob) { toast("Couldn't export the edited image.", { kind: "error" }); return; }
    setSaving(true);
    try {
      await studioCall("/api/edit-image", sessionId, name, blob);
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
        {offerCut && (
          <Tool icon={Eraser} label="Remove BG" onClick={removeBg} disabled={!!aiBusy} />
        )}
        {/* After a cut made on this device: the same cut on another colour,
            handed over transparent, or done again by the cloud. */}
        {lastCut && lastCut.name === name && !lastCut.cloud && (
          <>
            <label className={cn(
              "inline-flex items-center gap-1.5 h-9 px-3 rounded-[10px] text-[13px] font-semibold",
              "text-ink-secondary hover:bg-card hover:text-ink cursor-pointer shrink-0",
              aiBusy && "opacity-40 pointer-events-none")}
              title="Put the cutout on another colour">
              <Palette size={16} aria-hidden /> Colour
              <input type="color" className="sr-only" aria-label="Background colour"
                value={lastCut.background || "#ffffff"}
                onChange={(e) => recolour(e.target.value)} disabled={!!aiBusy} />
            </label>
            <Tool icon={Download} label="PNG" onClick={downloadPng} disabled={!!aiBusy}
              title="Download the item on a transparent background" />
            {bgRemoval?.available && (
              <Tool icon={Cloud} label="Enhance" onClick={enhanceWithCloud}
                disabled={!!aiBusy}
                title={canCloud ? "Remove the background again in the cloud (Photoroom)"
                                : "Turn on cloud background removal in Settings"} />
            )}
          </>
        )}
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
        {/* The one-tap rotate that used to sit on the photo tile, before the
            tile's buttons gave way to drag-to-reorder. */}
        <Tool icon={RotateCw} label="Rotate" onClick={rotate} disabled={!!aiBusy}
          title="Rotate 90° clockwise" />
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
          <Button variant="ghost" onClick={restoreOriginal}
            loading={restoring} disabled={!!aiBusy || restoring}
            title="Put back the photo exactly as you uploaded it, before background removal and any edits">
            <History aria-hidden /> Restore original
          </Button>
          <Button variant="ghost" onClick={() => { forgetCut(); load(); }}
            disabled={!!aiBusy || restoring}
            title="Discard every edit and reload the saved photo">
            <RotateCcw aria-hidden /> Revert
          </Button>
          {/* Disabled with the rest of the toolbar while AI work is in flight.
              Leaving it live let the seller close the studio mid-inference and
              open another photo, which is how a result landed on the wrong
              one. */}
          <Button variant="secondary" onClick={onClose} disabled={!!aiBusy}>Cancel</Button>
          <Button variant="primary" onClick={save} loading={saving} disabled={!!aiBusy}>
            Save
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
