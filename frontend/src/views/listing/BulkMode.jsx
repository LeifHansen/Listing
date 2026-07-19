import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Rocket, PenLine, ExternalLink, CheckCircle2, AlertTriangle } from "lucide-react";
import { cn, CONDITIONS, conditionLabel } from "@/lib/utils";
import { api, postJson } from "@/lib/api";
import { useApp } from "@/store";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";
import { AIStatusCard } from "@/components/ui/AIStatus";
import { useToast } from "@/components/ui/Toaster";

/* Bulk mode: one photo dump spanning many items. The server groups the photos,
   identifies each item, and (optionally) publishes them; this component polls
   the job and renders a live queue with inline edits + publish controls. */

const PHASE_MESSAGES = {
  uploading: ["Uploading your photo pile…"],
  optimizing: ["Optimizing photos…", "Straightening & brightening…"],
  grouping: ["Sorting photos into items…", "Matching angles of the same item…"],
  identifying: ["Identifying items…", "Writing titles & prices…", "Detecting brands…"],
};

function BulkItemCard({ item, checked, onCheck, onChange, onOpen, onPublish, publishing }) {
  const l = item.listing || {};
  const editable = item.status !== "error";
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
        <img
          src={`${item.thumb}?v=1`}
          alt=""
          className="size-12 rounded-[10px] object-cover border border-line"
          onError={(e) => { e.currentTarget.style.display = "none"; }}
        />
        <div className="ml-auto">
          {item.status === "published" && (
            <TagPill tone="green">
              <CheckCircle2 size={12} aria-hidden /> Live{item.listing_id ? ` · ${item.listing_id}` : ""}
            </TagPill>
          )}
          {item.status === "draft" && <TagPill tone="blue">Draft</TagPill>}
          {item.status === "error" && (
            <TagPill tone="yellow"><AlertTriangle size={12} aria-hidden /> Needs attention</TagPill>
          )}
        </div>
      </div>

      {editable ? (
        <>
          <Input
            value={l.title || item.title || ""}
            placeholder="Title"
            onChange={(e) => onChange({ ...l, title: e.target.value })}
          />
          <div className="grid grid-cols-2 gap-2.5">
            <Input
              type="number" step="0.01" min="0" placeholder="Price" inputMode="decimal"
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
        </>
      ) : (
        <p className="text-[13px] text-ink-secondary">
          {item.error || "Couldn't identify this item."}
        </p>
      )}
      {item.status === "draft" && item.error && (
        <p className="text-xs text-warning font-medium">{item.error}</p>
      )}

      <div className="flex items-center gap-2 mt-auto">
        {editable && (
          <Button variant="ghost" size="sm" onClick={onOpen}>
            <ExternalLink aria-hidden /> Full editor
          </Button>
        )}
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

export function BulkQueue({ jobId, mode, onExit, onSettled }) {
  const { setSession, loadListings } = useApp();
  const { toast, confirm } = useToast();
  const [job, setJob] = useState(null);
  const [items, setItems] = useState([]);
  const [checked, setChecked] = useState({});
  const [publishing, setPublishing] = useState({});
  const stopped = useRef(false);
  const fails = useRef(0);

  // Poll the job until done; items render as they arrive. Resilient to transient
  // poll failures — a busy server (heavy batch) can blip a request even though
  // the job is still running, so we retry instead of abandoning the batch.
  useEffect(() => {
    stopped.current = false;
    fails.current = 0;
    let timer;
    const poll = async () => {
      try {
        const j = await api(`/api/bulk/status/${jobId}`);
        if (stopped.current) return;
        fails.current = 0;
        setJob(j);
        if (j.items?.length) {
          // Merge WITHOUT clobbering the user's inline edits: the server never
          // re-edits an item once it's identified, so for items we already have
          // we keep the local listing (which may hold edits like a changed
          // condition/price) and only pick up new items from the poll.
          setItems((cur) => {
            const mine = new Map(cur.map((it) => [it.session_id, it]));
            return j.items.map((srv) => {
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
          loadListings({ quiet: true });
          onSettled?.();  // stop persisting; a reload shouldn't restore a done batch
        }
      } catch (e) {
        if (stopped.current) return;
        fails.current += 1;
        // A 404 = the job is genuinely gone (server restarted/evicted). Any
        // other error (network blip, server busy) is transient — keep retrying.
        const gone = (e.message || "").includes("(404)");
        if (gone) {
          onSettled?.();
          toast("This batch was interrupted (the server restarted). Any items it finished are saved in Drafts.",
            { kind: "warning" });
        } else if (fails.current < 6) {
          timer = setTimeout(poll, 3000);  // transient — the job is likely still running
        } else {
          // Give up watching but KEEP it persisted — the batch may still be
          // finishing server-side, so the banner lets the user reopen and resume.
          toast("Lost the connection while watching this batch — it may still be finishing. Reopen New Listing to check, and see Drafts for completed items.",
            { kind: "warning" });
        }
      }
    };
    poll();
    return () => { stopped.current = true; clearTimeout(timer); };
  }, [jobId, loadListings, toast, onSettled]);

  const updateItem = (sid, listing) => {
    setItems((cur) => cur.map((it) =>
      it.session_id === sid ? { ...it, listing } : it));
  };

  const openItem = (it) => {
    setSession({ sessionId: it.session_id, listing: it.listing, confidence: null });
    onExit();
  };

  const publishOne = useCallback(async (it) => {
    setPublishing((p) => ({ ...p, [it.session_id]: true }));
    try {
      // Persist inline edits first, then publish.
      await postJson(`/api/save/${it.session_id}`, it.listing);
      const res = await postJson("/api/publish", {
        session_id: it.session_id, listing: it.listing, mode: "live",
      });
      setItems((cur) => cur.map((x) => x.session_id === it.session_id
        ? {
            ...x,
            status: res.published ? "published" : "draft",
            listing_id: res.listing_id || null,
            error: res.published ? null
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
  }, []);

  const publishSelected = async () => {
    const targets = items.filter((it) => it.status === "draft" && checked[it.session_id]);
    if (!targets.length) { toast("Nothing selected to publish.", { kind: "warning" }); return; }
    if (!(await confirm({
      title: `Publish ${targets.length} listing${targets.length === 1 ? "" : "s"} live?`,
      message: "Each goes straight to your eBay store.",
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

  const busy = job && !job.done;
  const phase = job?.phase || "uploading";
  const drafts = items.filter((it) => it.status === "draft");
  const progressDetail = phase === "identifying" && job?.total_items
    ? ` (${job.current}/${job.total_items})`
    : phase === "optimizing" && job?.total_photos
      ? ` (${Math.min(job.current || 0, job.total_photos)}/${job.total_photos} photos)`
      : phase === "grouping" && job?.total_photos
        ? ` (${job.total_photos} photos)` : "";

  return (
    <div className="flex flex-col gap-4">
      {busy && (
        <AIStatusCard messages={(PHASE_MESSAGES[phase] || ["Working…"]).map((m) => m + progressDetail)} />
      )}
      {job?.done && (
        <Card className={cn("py-4", job.error ? "border-warning/40" : "border-success/30")}>
          <p className="text-sm font-semibold text-ink flex items-center gap-2">
            {job.error
              ? <><AlertTriangle size={17} className="text-warning" aria-hidden /> {job.error}</>
              : <>
                  <CheckCircle2 size={17} className="text-success" aria-hidden />
                  {items.length} item{items.length === 1 ? "" : "s"} {mode === "live" ? "processed" : "queued as drafts"}. Review below — they're also saved in Drafts.
                </>}
          </p>
        </Card>
      )}

      {drafts.length > 0 && (
        <div className="flex flex-wrap items-center gap-2.5">
          <Button variant="primary" onClick={publishSelected}>
            <Rocket aria-hidden /> Publish selected ({drafts.filter((d) => checked[d.session_id]).length})
          </Button>
          <Button variant="ghost" onClick={onExit}>
            <PenLine aria-hidden /> Start another batch
          </Button>
        </div>
      )}

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
              onPublish={() => publishOne(it).then((ok) => ok && loadListings({ quiet: true }))}
              publishing={!!publishing[it.session_id]}
            />
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
