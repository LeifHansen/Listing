import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Rocket, PenLine, ExternalLink, CheckCircle2, AlertTriangle, Combine, Trash2,
  ArrowRight, X,
} from "lucide-react";
import { cn, CONDITIONS, conditionLabel, mediaUrl } from "@/lib/utils";
import { api, postJson } from "@/lib/api";
import { apiUrl } from "@/lib/platform";
import { useApp } from "@/store";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input, Select, Toggle } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";
import { AIStatusCard } from "@/components/ui/AIStatus";
import { BrandProgress } from "@/components/ui/Progress";
import { useToast } from "@/components/ui/Toaster";
import { MergeListingsDialog } from "@/components/MergeListingsDialog";
import { CategoryQuickPick } from "./CategoryQuickPick";
import { MarketTargetChips, publishListing, usePublishTargets } from "./publishShared";
import { blockerLabels, ebayBlockers, TITLE_MAX } from "./blockers";

/* Bulk mode: one photo dump spanning many items. The server groups the photos,
   identifies each item, and (optionally) publishes them; this component polls
   the job and renders a live queue with inline edits + publish controls. */

const PHASE_MESSAGES = {
  uploading: ["Uploading your photo pile…"],
  optimizing: ["Optimizing photos…", "Straightening & removing backgrounds…"],
  grouping: ["Sorting photos into items…", "Matching angles of the same item…"],
  identifying: ["Identifying items…", "Writing titles & prices…", "Detecting brands…"],
};

// Duplicate-suspect detection: two drafts whose titles share most of their
// meaningful words are probably the same item split in two — surface a hint
// pointing at "Merge into one" instead of silently letting both publish.
const STOP_WORDS = new Set(["the", "and", "with", "for", "size", "mens", "womens", "new", "vintage"]);
function titleTokens(t) {
  return new Set((t || "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/)
    .map((w) => w.replace(/s$/, ""))  // light stemming: phases ≈ phase
    .filter((w) => w.length > 2 && !STOP_WORDS.has(w)));
}
function sharesEnough(A, B) {
  if (A.size < 3 || B.size < 3) return false;
  let shared = 0;
  A.forEach((w) => { if (B.has(w)) shared += 1; });
  return shared >= 3 && shared / Math.min(A.size, B.size) >= 0.4;
}
function duplicateSuspects(drafts) {
  // Tokenize ONCE per draft, not once per pair: the comparison is already
  // quadratic, and re-splitting both titles inside it made a big batch chew
  // through thousands of regex passes on every render.
  const tokens = drafts.map((d) => titleTokens(d.listing?.title || d.title || ""));
  const pairs = [];
  for (let i = 0; i < drafts.length; i++) {
    for (let j = i + 1; j < drafts.length; j++) {
      if (sharesEnough(tokens[i], tokens[j])) pairs.push([drafts[i], drafts[j]]);
    }
  }
  return pairs;
}

// Selling formats, mirroring the full editor's Pricing card.
const LISTING_FORMATS = [
  ["FIXED_PRICE", "Buy It Now"],
  ["AUCTION", "Auction"],
  ["AUCTION_BIN", "Auction + BIN"],
];
// eBay's own recommendation replaces this at publish time; it's just the
// starting number in the box.
const DEFAULT_AD_RATE = 10;

// Per-card shipping service = an eBay fulfillment (shipping) policy, same as
// the full editor's picker. Defaults to the account's policy; a change here
// rides on the draft and is honored at publish. Hidden until eBay is
// connected / policies load.
function ShippingServiceSelect({ value, onChange }) {
  const { ebay, policiesData, setPoliciesData } = useApp();
  useEffect(() => {
    if (!ebay.connected || policiesData) return;
    api("/api/ebay/policies").then(setPoliciesData).catch(() => {});
  }, [ebay.connected, policiesData, setPoliciesData]);
  if (!ebay.connected) return null;
  const policies = policiesData?.policies?.fulfillment || [];
  if (!policies.length) return null;
  const accountDefault = policiesData?.selected?.fulfillment_policy_id || "";
  return (
    <Select
      aria-label="Shipping service"
      value={value || accountDefault}
      onChange={(e) => onChange(e.target.value)}
    >
      {policies.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}{p.summary ? ` · ${p.summary}` : ""}
        </option>
      ))}
    </Select>
  );
}

function BulkItemCard({
  item, checked, onCheck, onChange, onOpen, onPublish, publishing,
  onDelete, deleting, onDeletePhoto, targets,
}) {
  const l = item.listing || {};
  const editable = item.status !== "error";
  const fmt = (l.listing_format || "FIXED_PRICE").toUpperCase();
  const isAuction = fmt.startsWith("AUCTION");
  // What is stopping THIS item from reaching eBay — the same rules the
  // editor and the drafts strip use (blockers.js). Target-aware: an
  // Etsy-only publish must not be gated on eBay-only fields (package weight,
  // eBay category).
  const blockers = item.status === "draft" ? ebayBlockers(l, { targets }) : [];
  // All of the item's photos, not just the first. An item that failed before
  // a listing existed still has the server-picked `thumb`.
  const photos = l.images?.length
    ? l.images.map((n) => ({ name: n, src: mediaUrl(item.session_id, n, 1) }))
    : (item.thumb ? [{ name: null, src: apiUrl(`${item.thumb}?v=1`) }] : []);
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn(
        "bg-card rounded-card border shadow-card p-4 flex flex-col gap-3",
        item.status === "error" ? "border-warning/50" : "border-line",
      )}
    >
      <div className="flex items-center gap-3">
        {item.status === "draft" && (
          <input
            type="checkbox"
            checked={checked}
            onChange={(e) => onCheck(e.target.checked)}
            aria-label={`Select ${l.title || item.title || "item"}`}
            className="size-4 accent-(--brand-blue) shrink-0"
          />
        )}
        {/* One row of thumbnails, as many as the card is wide — extras wrap
            below the max-height and are clipped away. */}
        <div className="flex-1 min-w-0 flex flex-wrap gap-1.5 max-h-12 overflow-hidden">
          {photos.map((ph, i) => (
            <div key={ph.name || ph.src} className="relative size-12 shrink-0">
              <img
                src={ph.src}
                alt=""
                loading="lazy"
                className="size-full rounded-[10px] object-cover border border-line"
                onError={(e) => {
                  // Hide the whole tile, not just the <img> — otherwise a photo
                  // that 404s leaves its remove button floating over nothing.
                  const tile = e.currentTarget.parentElement;
                  if (tile) tile.style.display = "none";
                }}
              />
              {/* Quick-delete this photo without leaving the queue. Always
                  visible (a hover-only control is unreachable on touch), and
                  only where there's a real file behind the tile — the fallback
                  `thumb` isn't a photo of this listing's own to delete. */}
              {ph.name && item.status === "draft" && onDeletePhoto && (
                <button
                  type="button"
                  onClick={() => onDeletePhoto(ph.name)}
                  aria-label={`Remove photo ${i + 1}`}
                  title="Remove this photo"
                  className={cn(
                    "absolute top-0.5 right-0.5 z-10 grid place-items-center size-[18px]",
                    "rounded-full bg-card/90 backdrop-blur border border-line shadow-card",
                    "text-ink-faint cursor-pointer transition-colors",
                    "hover:text-error hover:border-error/40",
                  )}
                >
                  <X size={11} strokeWidth={3} aria-hidden />
                </button>
              )}
            </div>
          ))}
        </div>
        <div className="shrink-0">
          {item.status === "published" && (
            <TagPill tone="green">
              <CheckCircle2 size={12} aria-hidden /> Live{item.listing_id ? ` · ${item.listing_id}` : ""}
            </TagPill>
          )}
          {item.status === "draft" && (
            blockers.length
              ? (
                <TagPill tone="yellow"
                  title={`eBay won't take this yet: ${blockerLabels(blockers)}`}>
                  <AlertTriangle size={12} aria-hidden /> Blocked
                </TagPill>
              )
              : <TagPill tone="blue">Draft</TagPill>
          )}
          {item.status === "error" && (
            <TagPill tone="yellow"><AlertTriangle size={12} aria-hidden /> Needs attention</TagPill>
          )}
        </div>
      </div>

      {editable ? (
        <>
          <div className="flex flex-col gap-1">
            <Input
              maxLength={TITLE_MAX}
              value={l.title || item.title || ""}
              placeholder="Title"
              aria-label="Title"
              onChange={(e) => onChange({ ...l, title: e.target.value })}
            />
            {/* Only once it starts to matter — these cards are dense, and a
                counter on every one of forty drafts is noise. */}
            {(l.title || item.title || "").length >= TITLE_MAX - 8 && (
              <span className="self-end text-[11px] font-semibold tabular-nums text-warning">
                {(l.title || item.title || "").length}/{TITLE_MAX}
              </span>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2.5">
            <Input
              type="number" step="0.01" min="0"
              placeholder={isAuction ? "Buy It Now" : "Price"} inputMode="decimal"
              aria-label={isAuction ? "Buy It Now price" : "Price"}
              value={l.price != null ? l.price : ""}
              onChange={(e) => onChange({ ...l, price: e.target.value === "" ? null : parseFloat(e.target.value) })}
            />
            <Select
              value={l.condition || "USED_GOOD"}
              onChange={(e) => onChange({ ...l, condition: e.target.value })}
            >
              {CONDITIONS.map((c) => <option key={c} value={c}>{conditionLabel(c)}</option>)}
            </Select>
          </div>

          {/* Selling format, and the fields each one needs. Defaults come from
              the account's listing settings, so a whole batch is priced the way
              the seller set up once — per-item overrides stay one tap away. */}
          <div className="grid grid-cols-2 gap-2.5">
            <Select
              aria-label="Listing format"
              value={fmt}
              onChange={(e) => onChange({ ...l, listing_format: e.target.value })}
            >
              {LISTING_FORMATS.map(([v, label]) => (
                <option key={v} value={v}>{label}</option>
              ))}
            </Select>
            {isAuction ? (
              <Input
                type="number" step="0.01" min="0" inputMode="decimal"
                placeholder="Start price" aria-label="Auction start price"
                value={l.auction_start_price != null ? l.auction_start_price : ""}
                onChange={(e) => onChange({
                  ...l,
                  auction_start_price: e.target.value === "" ? null : parseFloat(e.target.value),
                })}
              />
            ) : (
              <Select
                aria-label="Quantity"
                value={String(l.quantity || 1)}
                onChange={(e) => onChange({ ...l, quantity: parseInt(e.target.value, 10) })}
              >
                {[1, 2, 3, 4, 5, 10].map((q) => (
                  <option key={q} value={q}>{q === 1 ? "Qty 1" : `Qty ${q}`}</option>
                ))}
              </Select>
            )}
          </div>

          {/* Category on the card face: bulk batches are exactly where a
              wrong AI category slips through unnoticed. Edits ride the same
              local-then-save-on-publish path as the fields above. */}
          <CategoryQuickPick
            listing={l}
            onPick={(patch) => onChange({ ...l, ...patch })}
          />

          <ShippingServiceSelect
            value={l.fulfillment_policy_id}
            onChange={(id) => onChange({ ...l, fulfillment_policy_id: id })}
          />

          <div className="flex flex-wrap items-center gap-2.5">
            <Toggle
              checked={!!l.promote}
              onChange={(on) => onChange({
                ...l, promote: on,
                ad_rate_percent: on ? (l.ad_rate_percent || DEFAULT_AD_RATE) : 0,
              })}
              label="Promote"
            />
            {l.promote && (
              <Input
                type="number" step="0.5" min="0.5" max="100" inputMode="decimal"
                aria-label="Ad rate percent"
                className="w-24"
                value={l.ad_rate_percent || DEFAULT_AD_RATE}
                onChange={(e) => onChange({
                  ...l, ad_rate_percent: parseFloat(e.target.value) || DEFAULT_AD_RATE,
                })}
              />
            )}
            {l.promote && <span className="text-xs text-ink-faint">% ad rate</span>}
          </div>
        </>
      ) : (
        <p className="text-[13px] text-ink-secondary">
          {item.error || "Couldn't identify this item."}
        </p>
      )}
      {item.status === "draft" && blockers.length > 0 && (
        <p className="text-xs text-warning font-medium"
          title={blockers.map((b) => `${b.label}: ${b.why}`).join("\n")}>
          Keeping this off eBay: {blockerLabels(blockers)}
        </p>
      )}
      {item.status === "draft" && item.error && (
        <p className="text-xs text-warning font-medium">{item.error}</p>
      )}

      {/* flex-wrap: the buttons can't shrink (nowrap labels), so on narrow
          cards Publish must drop to its own right-aligned line instead of
          poking out past the card edge. */}
      <div className="flex flex-wrap items-center gap-2 mt-auto">
        {editable && (
          <Button variant="ghost" size="sm" onClick={onOpen}>
            <ExternalLink aria-hidden /> Review &amp; List
          </Button>
        )}
        {/* Not every auto-created draft is worth keeping — a duplicate you
            don't want to merge, or something the AI shouldn't have drafted.
            Deleting it here beats hunting for its card later. */}
        <Button variant="ghost" size="sm" onClick={onDelete} loading={deleting}
          aria-label="Delete this draft"
          className="text-ink-faint hover:text-error">
          <Trash2 aria-hidden /> Delete
        </Button>
        {item.status === "draft" && (
          <Button variant="secondary" size="sm" className="ml-auto"
            onClick={onPublish} loading={publishing}>
            <Rocket aria-hidden /> Publish
          </Button>
        )}
      </div>
    </motion.div>
  );
}

export function BulkQueue({ jobId, onExit, onSettled }) {
  const { setSession, loadListings, connectedMarketplaces } = useApp();
  const { toast, confirm } = useToast();

  // Bulk publish targets — the same remembered selection as the single-item
  // publish bar and the drafts strip (see publishShared).
  const {
    selected: bulkTargets, toggle: toggleBulkTarget, otherConnected,
    effectiveTargets,
  } = usePublishTargets();
  const [job, setJob] = useState(null);
  const [items, setItems] = useState([]);
  const [checked, setChecked] = useState({});
  const [publishing, setPublishing] = useState({});
  const [deleting, setDeleting] = useState({});
  // The merge review dialog. `key` bumps on every open so the dialog remounts
  // with fresh state (which draft is master, which entries win) instead of
  // reopening on the last merge's answers; `drafts` is the snapshot it was
  // opened on, so the queue polling underneath can't reshuffle it mid-review.
  const [merge, setMerge] = useState({ open: false, drafts: [], key: 0 });
  // Watching was given up on (job gone, or too many failed polls). Without it
  // the pre-first-poll "Uploading…" state below would spin forever.
  const [unwatched, setUnwatched] = useState(false);
  const stopped = useRef(false);
  const fails = useRef(0);
  const notFound = useRef(0);
  // Items merged away client-side — the still-running job's status would
  // otherwise resurrect them on the next poll.
  const removed = useRef(new Set());

  // Poll the job until done; items render as they arrive. Resilient to transient
  // poll failures — a busy server (heavy batch) can blip a request even though
  // the job is still running, so we retry instead of abandoning the batch.
  useEffect(() => {
    // No job id yet — the batch is still uploading from the store, and the
    // screen shows the upload phase until the id lands.
    if (!jobId) return;
    stopped.current = false;
    fails.current = 0;
    notFound.current = 0;
    setUnwatched(false);
    let timer;
    const poll = async () => {
      try {
        const j = await api(`/api/bulk/status/${jobId}`);
        if (stopped.current) return;
        fails.current = 0;
        notFound.current = 0;
        setJob(j);
        if (j.items?.length) {
          // Merge WITHOUT clobbering the user's inline edits: the server never
          // re-edits an item once it's identified, so for items we already have
          // we keep the local listing (which may hold edits like a changed
          // condition/price) and only pick up new items from the poll.
          setItems((cur) => {
            const mine = new Map(cur.map((it) => [it.session_id, it]));
            return j.items
              .filter((srv) => !removed.current.has(srv.session_id))
              .map((srv) => {
                const local = mine.get(srv.session_id);
                return local ? { ...srv, listing: local.listing ?? srv.listing } : srv;
              });
          });
          setChecked((c) => {
            const next = { ...c };
            j.items.forEach((it) => {
              if (next[it.session_id] === undefined && it.status === "draft") {
                next[it.session_id] = true;
              }
            });
            return next;
          });
        }
        if (!j.done) {
          timer = setTimeout(poll, 1500);
        } else {
          stopped.current = true;  // nothing left to watch — don't re-poll on focus
          loadListings({ quiet: true });
          onSettled?.();  // stop persisting; a reload shouldn't restore a done batch
        }
      } catch (e) {
        if (stopped.current) return;
        fails.current += 1;
        // A 404 means the server has no record of this job at all. Confirm it
        // with a second poll before believing it: a status check can 404 on a
        // blip (an auth hiccup mid-batch does it) while the batch itself is
        // still running, and declaring it dead is not something we can undo.
        const gone = (e.message || "").includes("(404)");
        notFound.current = gone ? notFound.current + 1 : 0;
        if (gone && notFound.current < 2) {
          timer = setTimeout(poll, 3000);
        } else if (gone) {
          // Terminal. Stop polling for good — including the visibility/focus
          // handler below, which otherwise re-ran this on every tab switch —
          // and mark the batch finished so the queue shows what happened.
          // Without this the progress bar sat spinning on the photo the batch
          // died at, with no result, no error, and no way out of the screen.
          stopped.current = true;
          onSettled?.();
          setUnwatched(true);
          loadListings({ quiet: true });  // whatever it did finish is in Drafts
          // A finished job, so the queue renders the outcome card with this
          // reason and a way off the screen. Clearing `busy` alone (unwatched)
          // stops the spinner but leaves nothing behind it: no explanation,
          // and no exit at all for a batch that died before drafting anything.
          setJob((j) => ({
            ...(j || {}),
            done: true,
            error: "This batch stopped early — the server restarted while it "
              + "was working, and no record of it survived. Anything it "
              + "finished is saved in Drafts; the rest need another run.",
          }));
          toast("This batch was interrupted (the server restarted). Any items it finished are saved in Drafts.",
            { kind: "warning" });
        } else if (fails.current < 6) {
          timer = setTimeout(poll, 3000);  // transient — the job is likely still running
        } else {
          setUnwatched(true);
          // Give up watching but KEEP it persisted — the batch may still be
          // finishing server-side, so the banner lets the user reopen and resume.
          toast("Lost the connection while watching this batch — it may still be finishing. Reopen the Sell tab to check, and see Drafts for completed items.",
            { kind: "warning" });
        }
      }
    };
    poll();
    // Browsers throttle timers hard in a hidden tab (down to about once a
    // minute, and frozen entirely after a while), so a batch watched from a
    // background tab looks stuck until a manual refresh. Poll immediately
    // whenever the tab comes back to the foreground.
    const onVisible = () => {
      if (document.visibilityState !== "visible" || stopped.current) return;
      clearTimeout(timer);
      fails.current = 0;
      poll();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      stopped.current = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [jobId, loadListings, toast, onSettled]);

  const updateItem = (sid, listing) => {
    setItems((cur) => cur.map((it) =>
      it.session_id === sid ? { ...it, listing } : it));
  };

  // Remove one photo from a draft straight from its thumbnail strip — the
  // fastest way to drop a blurry shot or a stray photo the grouper attached to
  // the wrong item. Optimistic: the tile goes immediately and comes back only
  // if the server delete fails. The shortened list is saved right away because
  // the file is really gone — an unsaved draft would keep pointing at it.
  const deletePhoto = async (it, name) => {
    const l = it.listing || {};
    const images = l.images || [];
    if (images.length <= 1) {
      toast("A listing needs at least one photo — add another before deleting this one.",
        { kind: "warning" });
      return;
    }
    const next = { ...l, images: images.filter((n) => n !== name) };
    updateItem(it.session_id, next);
    try {
      await postJson("/api/delete-image", { session_id: it.session_id, name });
      postJson(`/api/save/${it.session_id}`, next).catch(() => {});
    } catch (e) {
      updateItem(it.session_id, l);
      toast(`Couldn't delete the photo: ${e.message}`, { kind: "error" });
    }
  };

  // Open one item in the full editor. The batch stays in memory (no onExit)
  // so the editor's "Back to batch" button can bring this queue straight back.
  const openItem = (it) => {
    setSession({ sessionId: it.session_id, listing: it.listing, confidence: null });
  };

  const publishOne = useCallback(async (it) => {
    setPublishing((p) => ({ ...p, [it.session_id]: true }));
    try {
      // Persist inline edits first, then publish (one request per item — the
      // backend fans out to every selected marketplace); see publishShared.
      const res = await publishListing(it.session_id, it.listing, effectiveTargets);
      // Multi responses summarize per marketplace: "eBay ✓ · Etsy ✗".
      const summary = res.multi
        ? Object.entries(res.results || {})
            .map(([key, r]) => `${key === "ebay" ? "eBay" : key.charAt(0).toUpperCase() + key.slice(1)} ${r.published ? "✓" : r.ok ? "—" : "✗"}`)
            .join(" · ")
        : null;
      setItems((cur) => cur.map((x) => x.session_id === it.session_id
        ? {
            ...x,
            status: res.published ? "published" : "draft",
            listing_id: (res.multi
              ? res.results?.ebay?.listing_id : res.listing_id) || null,
            error: res.published
              ? (res.multi && Object.values(res.results || {}).some((r) => !r.ok)
                  ? `${summary} — open the full editor to fix the rest.` : null)
              : (res.message || "Publish blocked — open the full editor to fix."),
          }
        : x));
      return !!res.published;
    } catch (e) {
      setItems((cur) => cur.map((x) => x.session_id === it.session_id
        ? { ...x, error: e.message } : x));
      return false;
    } finally {
      setPublishing((p) => ({ ...p, [it.session_id]: false }));
    }
  }, [effectiveTargets]);

  // One card's Publish button. Asks first — it posts a real, fee-incurring
  // listing; "Publish selected" asks once for its whole set instead, which is
  // why the confirm lives here rather than inside publishOne.
  const confirmPublishOne = useCallback(async (it) => {
    const name = it.listing?.title || it.title || "this draft";
    if (!(await confirm({
      title: "Publish this draft live?",
      message: `"${name}" goes straight to your store.`,
      confirmLabel: "Publish live",
    }))) return;
    if (await publishOne(it)) loadListings({ quiet: true });
  }, [confirm, publishOne, loadListings]);

  // Delete a draft straight from the queue — the counterpart to Merge for
  // duplicates you don't want to keep at all, and the way out for anything
  // the AI shouldn't have drafted.
  const deleteOne = async (it) => {
    const name = it.listing?.title || it.title || "this draft";
    if (!(await confirm({
      title: "Delete this draft?",
      message: `"${name}" will be permanently removed, photos included. This can't be undone.`,
      confirmLabel: "Delete",
      danger: true,
    }))) return;
    setDeleting((d) => ({ ...d, [it.session_id]: true }));
    try {
      await api(`/api/listings/${it.session_id}`, { method: "DELETE" });
    } catch (e) {
      // An item that never produced a record (a failed identify) has nothing
      // to delete server-side — dropping it from the queue is the whole job.
      if (!(e.message || "").includes("(404)")) {
        toast(`Couldn't delete: ${e.message}`, { kind: "error" });
        setDeleting((d) => ({ ...d, [it.session_id]: false }));
        return;
      }
    }
    removed.current.add(it.session_id);
    setItems((cur) => cur.filter((x) => x.session_id !== it.session_id));
    setChecked((c) => { const n = { ...c }; delete n[it.session_id]; return n; });
    setDeleting((d) => ({ ...d, [it.session_id]: false }));
    loadListings({ quiet: true });
  };

  const deleteSelected = async () => {
    const targets = items.filter((it) => checked[it.session_id]);
    if (!targets.length) { toast("Nothing selected to delete.", { kind: "warning" }); return; }
    if (!(await confirm({
      title: `Delete ${targets.length} draft${targets.length === 1 ? "" : "s"}?`,
      message: "They'll be permanently removed, photos included. This can't be undone.",
      confirmLabel: "Delete all selected",
      danger: true,
    }))) return;
    const ids = targets.map((t) => t.session_id);
    try {
      const res = await postJson("/api/listings/bulk-delete", { ids });
      const gone = new Set(res.deleted || []);
      gone.forEach((id) => removed.current.add(id));
      setItems((cur) => cur.filter((x) => !gone.has(x.session_id)));
      setChecked({});
      loadListings({ quiet: true });
      toast(`Deleted ${gone.size} draft${gone.size === 1 ? "" : "s"}.`
        + (res.skipped?.length ? ` ${res.skipped.length} couldn't be removed.` : ""),
        { kind: res.skipped?.length ? "warning" : "success" });
    } catch (e) {
      toast(`Couldn't delete: ${e.message}`, { kind: "error" });
    }
  };

  // Merge duplicate drafts of the SAME item into one listing. Which draft is
  // the master, and whose entry wins where two drafts disagree, are both the
  // seller's calls — MergeListingsDialog asks them before anything is written.
  const mergeSelected = () => {
    const targets = items.filter((it) => it.status === "draft" && checked[it.session_id]);
    if (targets.length < 2) {
      toast("Select the duplicate drafts (2 or more) to merge.", { kind: "warning" });
      return;
    }
    setMerge((m) => ({ open: true, drafts: targets, key: m.key + 1 }));
  };

  // The merge went through: drop the consolidated drafts, take the master's
  // merged listing back, and say what moved over.
  const onMerged = (res, { masterId, title }) => {
    const gone = new Set(res.removed || []);
    gone.forEach((id) => removed.current.add(id));
    setItems((cur) => cur
      .filter((it) => !removed.current.has(it.session_id))
      .map((it) => (it.session_id === masterId ? { ...it, listing: res.listing } : it)));
    setChecked((c) => {
      const next = { ...c };
      gone.forEach((id) => delete next[id]);
      return next;
    });
    setMerge((m) => ({ ...m, open: false }));
    loadListings({ quiet: true });
    const fields = res.applied?.length
      ? `, ${res.applied.length} field${res.applied.length === 1 ? "" : "s"} carried over`
      : "";
    toast(`Merged into "${title}" — ${res.added} photo${res.added === 1 ? "" : "s"} moved over${fields}.`,
      { kind: "success" });
  };

  const publishSelected = async () => {
    const targets = items.filter((it) => it.status === "draft" && checked[it.session_id]);
    if (!targets.length) { toast("Nothing selected to publish.", { kind: "warning" }); return; }
    const targetNames = effectiveTargets && effectiveTargets.length > 1
      ? effectiveTargets
          .map((k) => (connectedMarketplaces.find((m) => m.key === k) || {}).label || k)
          .join(" and ")
      : "your eBay store";
    if (!(await confirm({
      title: `Publish ${targets.length} listing${targets.length === 1 ? "" : "s"} live?`,
      message: `Each goes straight to ${targetNames}.`,
      confirmLabel: "Publish live",
    }))) return;
    let ok = 0, failed = 0;
    for (const it of targets) {
      (await publishOne(it)) ? ok++ : failed++;
    }
    loadListings({ quiet: true });
    toast(`Published ${ok} listing${ok === 1 ? "" : "s"}.`
      + (failed ? ` ${failed} need attention — see the queue.` : ""),
      { kind: failed ? "warning" : "success" });
  };

  // Busy from the very first frame: the batch screen goes up on the click, so
  // it opens on "Uploading your photo pile…" while the photos are still going
  // out — before there is a job id, let alone a status to poll.
  const busy = !unwatched && (!job || !job.done);
  const phase = job?.phase || "uploading";
  // Phase-weighted % for the progress bar shown while the job runs; the
  // cards stream in below it as each item is drafted.
  const pct = Math.round((() => {
    const frac = (cur, tot) => (tot ? Math.min(1, (cur || 0) / tot) : 0);
    if (!job) return 3;
    if (job.done) return 100;
    if (phase === "uploading") return 5;
    if (phase === "optimizing") return 10 + 35 * frac(job.current, job.total_photos);
    if (phase === "grouping") return 50;
    if (phase === "identifying") return 55 + 44 * frac(job.current, job.total_items);
    return 95;
  })());
  const drafts = items.filter((it) => it.status === "draft");
  const blocked = drafts.filter(
    (it) => ebayBlockers(it.listing, { targets: effectiveTargets }).length > 0);
  // Memoized: the queue re-renders on every status poll and on every keystroke
  // in a card, and the pairwise scan is quadratic in the size of the batch.
  const dupes = useMemo(() => duplicateSuspects(drafts),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [drafts.map((d) => `${d.session_id}:${d.listing?.title || d.title || ""}`).join("|")]);
  const progressDetail = phase === "identifying" && job?.total_items
    ? ` (${job.current}/${job.total_items})`
    : phase === "optimizing" && job?.total_photos
      ? ` (${Math.min(job.current || 0, job.total_photos)}/${job.total_photos} photos)`
      : phase === "grouping" && job?.total_photos
        ? ` (${job.total_photos} photos)` : "";

  return (
    <div className="flex flex-col gap-4">
      {busy && (
        <div className="flex flex-col gap-3">
          <AIStatusCard messages={[
            // A batch the server picked back up after a restart keeps the same
            // job id, so this view just carries on polling. Say so, or the
            // count appearing to jump reads as a glitch.
            ...(job?.resumed ? ["Picking your batch back up where it stopped…"] : []),
            ...(PHASE_MESSAGES[phase] || ["Working…"]).map((m) => m + progressDetail),
          ]} />
          <BrandProgress
            className="px-1"
            value={pct}
            caption={items.length
              ? `${items.length} item${items.length === 1 ? "" : "s"} drafted so far`
              : null}
          />
        </div>
      )}
      {job?.done && (
        <Card className={cn("py-4", job.error ? "border-warning/40" : "border-success/30")}>
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm font-semibold text-ink flex items-center gap-2 flex-1 min-w-0">
              {job.error
                ? <><AlertTriangle size={17} className="text-warning" aria-hidden /> {job.error}</>
                : <>
                    <CheckCircle2 size={17} className="text-success" aria-hidden />
                    <span title="Also saved in Drafts — review below or come back anytime.">
                      {items.length} item{items.length === 1 ? "" : "s"} queued as drafts
                    </span>
                  </>}
            </p>
            {/* The guided path: step through each draft in the full editor —
                preview, tweak, publish — and the post-publish screen's "Next
                Draft" keeps the assembly line moving through the batch.
                With no drafts to step through (a batch that failed before it
                identified anything) this is the only way off the screen — the
                queue's own toolbar renders only alongside drafts. */}
            {drafts.length > 0 ? (
              <Button variant="primary" onClick={() => openItem(drafts[0])}
                title="Review each draft in the full editor and publish as you go.">
                Preview &amp; list <ArrowRight aria-hidden />
              </Button>
            ) : (
              <Button variant="secondary" onClick={onExit}>
                <PenLine aria-hidden /> Start another batch
              </Button>
            )}
          </div>
        </Card>
      )}

      {/* Background removal was asked for but couldn't run: the photos were
          deliberately kept unchanged, so say why instead of leaving the user
          to conclude the feature is broken. */}
      {job?.done && job.bg_error && (
        <Card className="py-3.5 border-warning/40 bg-warning-soft">
          <p className="text-sm text-ink flex items-start gap-2">
            <AlertTriangle size={17} className="text-warning shrink-0 mt-0.5" aria-hidden />
            <span title="The photos were saved unchanged.">
              <strong>Backgrounds weren't removed</strong> on {job.bg_failed || "some"}{" "}
              photo{job.bg_failed === 1 ? "" : "s"} —{" "}
              {String(job.bg_error).trim().replace(/[.!?]*$/, "")}. Your photos
              were saved unchanged.
            </span>
          </p>
        </Card>
      )}

      {job?.done && (() => {
        if (!dupes.length) return null;
        const [a, b] = dupes[0];
        return (
          <Card className="py-3.5 border-warning/40 bg-warning-soft">
            <p className="text-sm text-ink flex items-start gap-2">
              <Combine size={17} className="text-warning shrink-0 mt-0.5" aria-hidden />
              <span title="If they're the same item, tick just those drafts and hit Merge into one before publishing.">
                <strong>Possible duplicate{dupes.length > 1 ? "s" : ""}:</strong>{" "}
                "{(a.listing?.title || a.title || "").slice(0, 40)}…" &amp;{" "}
                "{(b.listing?.title || b.title || "").slice(0, 40)}…"
                {dupes.length > 1 ? ` (+${dupes.length - 1} more)` : ""} — select them
                and <strong>Merge into one</strong>.
              </span>
            </p>
          </Card>
        );
      })()}

      {job?.done && blocked.length > 0 && (
        <Card className="py-3.5 border-warning/40 bg-warning-soft">
          <p className="text-sm text-ink flex items-start gap-2">
            <AlertTriangle size={17} className="text-warning shrink-0 mt-0.5" aria-hidden />
            {/* Each card names its OWN blocking fields — this banner only
                says how many are affected, so it can't contradict them. */}
            <span title="Each blocked card lists the fields eBay is refusing it over. Fill them in on the card or in the full editor.">
              <strong>{blocked.length}</strong> of {drafts.length} draft{drafts.length === 1 ? "" : "s"}{" "}
              can&apos;t reach eBay yet — each one is marked{" "}
              <strong className="text-warning">Blocked</strong> below, with the fields that are holding it.
            </span>
          </p>
        </Card>
      )}

      {drafts.length > 0 && (
        <MarketTargetChips selected={bulkTargets} toggle={toggleBulkTarget}
          otherConnected={otherConnected} />
      )}

      {drafts.length > 0 && (
        <div className="flex flex-wrap items-center gap-2.5">
          <Button variant="primary" onClick={publishSelected}>
            <Rocket aria-hidden /> Publish selected ({drafts.filter((d) => checked[d.session_id]).length})
          </Button>
          {drafts.filter((d) => checked[d.session_id]).length >= 2 && (
            <Button variant="secondary" onClick={mergeSelected}
              title="Same item split into duplicates? Pick the master, choose whose entries win, and combine them into one listing.">
              <Combine aria-hidden /> Merge into one
            </Button>
          )}
          {/* Duplicates you'd rather drop than merge, and anything the batch
              shouldn't have drafted. */}
          {items.some((it) => checked[it.session_id]) && (
            <Button variant="danger" onClick={deleteSelected}
              title="Permanently delete the selected drafts.">
              <Trash2 aria-hidden /> Delete selected ({items.filter((it) => checked[it.session_id]).length})
            </Button>
          )}
          <Button variant="ghost" onClick={onExit}>
            <PenLine aria-hidden /> Start another batch
          </Button>
        </div>
      )}

      {merge.drafts.length >= 2 && (
        <MergeListingsDialog
          key={merge.key}
          open={merge.open}
          drafts={merge.drafts}
          onClose={() => setMerge((m) => ({ ...m, open: false }))}
          onMerged={onMerged}
        />
      )}

      {/* Preview cards stream in one by one, as each item is drafted. */}
      <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">
        <AnimatePresence>
          {items.map((it) => (
            <BulkItemCard
              key={it.session_id}
              item={it}
              checked={!!checked[it.session_id]}
              onCheck={(v) => setChecked((c) => ({ ...c, [it.session_id]: v }))}
              onChange={(l) => updateItem(it.session_id, l)}
              onOpen={() => openItem(it)}
              onPublish={() => confirmPublishOne(it)}
              publishing={!!publishing[it.session_id]}
              onDelete={() => deleteOne(it)}
              deleting={!!deleting[it.session_id]}
              onDeletePhoto={(name) => deletePhoto(it, name)}
              targets={effectiveTargets}
            />
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
