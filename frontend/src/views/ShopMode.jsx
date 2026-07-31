import { useRef, useState } from "react";
import { motion } from "framer-motion";
import { Camera, Video, Plus, RotateCcw, MapPin, Eye } from "lucide-react";
import { once, mediaUrl } from "@/lib/utils";
import { api, postJson, downscaleForUpload, extractFrames } from "@/lib/api";
import { useApp } from "@/store";
import { Card } from "@/components/ui/Card";
import { InfoTip } from "@/components/ui/fields";
import { Button } from "@/components/ui/Button";
import { AIStatusCard } from "@/components/ui/AIStatus";
import { ConfidenceBadge, TagPill, PriceBadge } from "@/components/ui/badges";
import { EmptyState } from "@/components/ui/EmptyState";
import { SearchIllustration } from "@/components/ui/illustrations";
import { useToast } from "@/components/ui/Toaster";

// Shop Mode — scan items while thrifting: instant ID + typical resale price,
// then one tap drops it into inventory to finish later.
export function ShopMode() {
  const { user, openAuth, health, setView, openListings, loadListings } = useApp();
  const { toast } = useToast();
  const itemRef = useRef(null);
  const shelfRef = useRef(null);
  const [busy, setBusy] = useState(null);
  const [result, setResult] = useState(null); // { session, listing, confidence, price }
  const [shelf, setShelf] = useState(null); // { items }

  const scanItem = once("shop", async (file) => {
    if (!file) return;
    setShelf(null);
    setResult(null);
    setBusy(["Scanning your find…", "Identifying brand & model…", "Checking what it sells for…"]);
    try {
      const prepped = await downscaleForUpload(file);
      const fd = new FormData();
      fd.append("files", prepped);
      fd.append("remove_bg", "false");
      const up = await api("/api/upload", { method: "POST", body: fd });
      const res = await api(`/api/identify/${up.session_id}`, { method: "POST" });

      // Best-effort market price (never block the scan on it).
      let price = null;
      if (health.taxonomy_configured) {
        try {
          const l = res.listing;
          const query = [l.brand, l.title].filter(Boolean).join(" ").trim();
          const pd = await postJson("/api/price-suggestions", {
            query, category_id: l.category_id || null, condition: l.condition || null,
          });
          price = pd.suggestion;
        } catch (e) { /* price is optional */ }
      }
      setResult({
        session: up.session_id, listing: res.listing,
        confidence: res.confidence, price,
      });
    } catch (e) {
      toast(`Scan error: ${e.message}`, { kind: "error" });
    } finally {
      setBusy(null);
    }
  });

  const scanShelf = once("shelf", async (file) => {
    if (!file) return;
    setResult(null);
    setShelf(null);
    setBusy(["Reading the video…", "Scanning the shelf for gems…", "Judging resale potential…"]);
    try {
      const frames = await extractFrames(file);
      if (!frames.length) {
        toast("Couldn't get any frames from that video.", { kind: "warning" });
        return;
      }
      const fd = new FormData();
      frames.forEach((b, i) => fd.append("files", new File([b], `frame_${i}.jpg`, { type: "image/jpeg" })));
      const res = await api("/api/shelf-scan", { method: "POST", body: fd });
      setShelf({ items: res.items || [] });
    } catch (e) {
      toast(`Shelf scan error: ${e.message}`, { kind: "error" });
    } finally {
      setBusy(null);
    }
  });

  const buy = once("buy", async () => {
    if (!result) return;
    if (!user) {
      // Resume the buy after the login that interrupted it.
      openAuth(() => buy());
      return;
    }
    try {
      await postJson("/api/inventory/add", {
        session_id: result.session, listing: result.listing,
      });
      setResult(null);
      loadListings({ quiet: true });
      toast("Added to your inventory! Open Inventory to finish and publish it.", { kind: "success" });
      openListings("finds");
    } catch (e) {
      toast(`Couldn't add to inventory: ${e.message}`, { kind: "error" });
    }
  });

  const l = result?.listing || {};
  const img = result && l.images?.[0] ? mediaUrl(result.session, l.images[0]) : null;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-2">
        <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-ink">Shop Mode</h1>
        <InfoTip text="Snap an item to identify it and see what it typically sells for on eBay. Tap Buy to drop it into your inventory and finish the listing later." />
      </div>

      <div className="grid sm:grid-cols-2 gap-4">
        <motion.button
          type="button"
          onClick={() => itemRef.current?.click()}
          whileHover={{ y: -2, boxShadow: "var(--shadow-card-hover)" }}
          whileTap={{ scale: 0.98 }}
          className="bg-card border border-line rounded-card shadow-card p-6 flex flex-col items-center gap-3 cursor-pointer text-center"
        >
          <span className="grid place-items-center size-14 rounded-[18px] bg-blue-soft text-blue">
            <Camera size={26} aria-hidden />
          </span>
          <span className="font-bold text-ink">Scan an item</span>
          <span className="text-[13px] text-ink-secondary">Instant ID + typical resale price</span>
        </motion.button>
        <motion.button
          type="button"
          onClick={() => shelfRef.current?.click()}
          whileHover={{ y: -2, boxShadow: "var(--shadow-card-hover)" }}
          whileTap={{ scale: 0.98 }}
          className="bg-card border border-line rounded-card shadow-card p-6 flex flex-col items-center gap-3 cursor-pointer text-center"
        >
          <span className="grid place-items-center size-14 rounded-[18px] bg-red-soft text-error">
            <Video size={26} aria-hidden />
          </span>
          <span className="font-bold text-ink">Scan a shelf</span>
          <span className="text-[13px] text-ink-secondary">
            Record a shelf or rack — we'll flag items worth a closer look
          </span>
        </motion.button>
      </div>

      <input
        ref={itemRef} type="file" accept="image/*,.heic,.heif,.hif" capture="environment" hidden
        onChange={(e) => { scanItem(e.target.files[0]); e.target.value = ""; }}
      />
      <input
        ref={shelfRef} type="file" accept="video/*" capture="environment" hidden
        onChange={(e) => { scanShelf(e.target.files[0]); e.target.value = ""; }}
      />

      {busy && <AIStatusCard messages={busy} />}

      {result && (
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
          <Card className="flex flex-col sm:flex-row gap-5">
            {img && (
              <img
                src={img} alt="Scanned item"
                className="rounded-tile object-cover w-full sm:w-44 aspect-square border border-line"
              />
            )}
            <div className="flex-1 min-w-0 flex flex-col gap-2">
              <h2 className="font-bold text-lg text-ink">
                {l.title || "(couldn't identify — try another angle)"}
              </h2>
              <div className="flex flex-wrap items-center gap-2">
                {l.condition && <TagPill>{l.condition.replaceAll("_", " ")}</TagPill>}
                <ConfidenceBadge level={result.confidence} />
              </div>
              {result.price ? (
                <div className="mt-1">
                  <p className="text-2xl font-bold text-ink">
                    <PriceBadge value={result.price.price} approx className="text-lg px-3 py-1" />
                  </p>
                  <p className="text-xs text-ink-secondary mt-1.5">
                    typical ${result.price.low}–${result.price.high} · {result.price.count} comps
                    · {result.price.basis}{result.price.sold_data ? "" : " (asking, not sold)"}
                  </p>
                </div>
              ) : (
                <p className="text-sm text-ink-secondary">No price estimate yet — you can set one later.</p>
              )}
              {/* What you'd pay — auto-read from a visible price sticker when
                  possible, editable, optional. Saved on the Buy so profit can
                  be worked out when the item sells. */}
              <label className="flex items-center gap-2 text-sm text-ink-secondary">
                <span className="font-medium text-ink">You paid</span>
                <span aria-hidden>$</span>
                <input
                  type="number" step="0.01" min="0" inputMode="decimal"
                  aria-label="Purchase price (what you paid)"
                  placeholder={l.purchase_price != null ? undefined : "0.00 (optional)"}
                  value={l.purchase_price != null ? l.purchase_price : ""}
                  onChange={(e) => setResult((r) => r && ({
                    ...r,
                    listing: {
                      ...r.listing,
                      purchase_price: e.target.value === "" ? null : parseFloat(e.target.value),
                    },
                  }))}
                  className="w-24 rounded-lg border border-line bg-card px-2.5 py-1.5 text-sm text-ink"
                />
                {l.purchase_price != null && result.price?.price > l.purchase_price && (
                  <span className="text-xs font-semibold text-success">
                    ≈ ${(result.price.price - l.purchase_price).toFixed(0)} potential profit
                  </span>
                )}
              </label>
              <div className="mt-auto pt-3 flex flex-wrap gap-2.5">
                <Button variant="primary" size="lg" onClick={buy}>
                  <Plus aria-hidden /> Buy — add to inventory
                </Button>
                <Button variant="ghost" size="lg" onClick={() => setResult(null)}>
                  <RotateCcw aria-hidden /> Scan another
                </Button>
              </div>
            </div>
          </Card>
        </motion.div>
      )}

      {shelf && (
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
          {shelf.items.length === 0 ? (
            <Card>
              <p className="text-sm text-ink-secondary">
                Nothing jumped out as a clear resale win. Try a slower pan or better
                light — or scan individual items.
              </p>
            </Card>
          ) : (
            <Card>
              <p className="font-bold text-ink flex items-center gap-2 mb-4">
                <Eye size={18} className="text-blue" aria-hidden />
                {shelf.items.length} item{shelf.items.length === 1 ? "" : "s"} worth a closer look
              </p>
              <ul className="flex flex-col gap-3">
                {shelf.items.map((it, i) => (
                  <li key={i} className="border border-line rounded-tile p-4">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-semibold text-sm text-ink">{it.name}</span>
                      <ConfidenceBadge level={it.confidence} />
                    </div>
                    {it.reason && <p className="text-[13px] text-ink-secondary mt-1">{it.reason}</p>}
                    {it.location && (
                      <p className="text-[13px] text-ink-secondary mt-1 inline-flex items-center gap-1">
                        <MapPin size={12} aria-hidden /> {it.location}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
              <p className="text-[13px] text-ink-secondary mt-4">
                Point your camera at one and tap <strong>Scan an item</strong> for a full ID + price.
              </p>
            </Card>
          )}
        </motion.div>
      )}

      {!busy && !result && !shelf && (
        <Card className="p-0">
          <EmptyState
            illustration={SearchIllustration}
            title="Nothing scanned yet"
            message="Point your camera at a thrift-store find and see what it's worth in seconds."
            action={
              <Button variant="primary" size="lg" onClick={() => itemRef.current?.click()}>
                <Camera aria-hidden /> Scan an item
              </Button>
            }
          />
        </Card>
      )}
    </div>
  );
}
