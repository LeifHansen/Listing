import { useCallback, useEffect, useRef, useState } from "react";
import { RotateCcw } from "lucide-react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toaster";
import { api } from "@/lib/api";
import { mediaUrl } from "@/lib/utils";

// Background clean-up editor: paint over any leftover background with white.
// Ported 1:1 from the original canvas editor (pointer→canvas coordinate
// mapping, brush scaling, touch support).
export function ImageEditor({ sessionId, name, onClose, onSaved }) {
  const canvasRef = useRef(null);
  const painting = useRef(false);
  const [brush, setBrush] = useState(40);
  const brushRef = useRef(40);
  const [saving, setSaving] = useState(false);
  const { toast } = useToast();

  const load = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !name) return;
    const ctx = canvas.getContext("2d");
    const img = new Image();
    img.onload = () => {
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      ctx.drawImage(img, 0, 0);
    };
    img.onerror = () => toast("Couldn't load the image to edit.", { kind: "error" });
    // Same-origin so the canvas isn't tainted and toBlob() works.
    img.src = `${mediaUrl(sessionId, name)}?v=${Date.now()}`;
  }, [sessionId, name, toast]);

  useEffect(() => { if (name) load(); }, [name, load]);
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
    const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.92));
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
    <Dialog open={!!name} onClose={onClose} title="Clean up background" wide>
      <p className="text-sm text-ink-secondary -mt-2 mb-4">
        Paint over any leftover background with white.
      </p>
      <div className="rounded-tile overflow-hidden border border-line bg-bg-sunken grid place-items-center max-h-[55vh]">
        <canvas ref={canvasRef} className="max-w-full max-h-[55vh] touch-none cursor-crosshair" />
      </div>
      <div className="flex flex-wrap items-center gap-3 mt-5">
        <label className="flex items-center gap-2.5 text-sm font-semibold text-ink">
          Brush
          <input
            type="range" min="8" max="120" value={brush}
            onChange={(e) => setBrush(parseInt(e.target.value, 10))}
            className="accent-(--brand-blue) w-36"
          />
        </label>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="ghost" onClick={load}>
            <RotateCcw aria-hidden /> Revert
          </Button>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={save} loading={saving}>Save</Button>
        </div>
      </div>
    </Dialog>
  );
}
