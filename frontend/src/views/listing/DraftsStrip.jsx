import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  FilePen, Rocket, PenLine, CheckSquare, Trash2, X, Truck,
} from "lucide-react";
import { api, pollJob, postJson } from "@/lib/api";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { SectionHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/fields";
import { ListingCard } from "@/components/ListingCard";
import { CategoryQuickPick } from "./CategoryQuickPick";
import {
  MarketTargetChips, missingRequired, publishListing, usePublishTargets,
} from "./publishShared";

/* The drafts experience on the merged Sell screen: every draft one click
   from Publish or Preview & Edit, plus select-mode bulk publish/delete.
   Renders nothing when there are no (matching) drafts — the upload box
   directly above is the empty-state CTA. */

const isDraft = (item) => item.status === "draft" || item.status === "dry_run";

// Shipping service picker right on a draft's card — the same eBay fulfillment
// policies the editor and bulk queue offer, account default preselected.
// Saves the moment it's changed.
function DraftShipping({ item }) {
  const { ebay, policiesData, setPoliciesData } = useApp();
  const { toast } = useToast();
  const [value, setValue] = useState(item.listing?.fulfillment_policy_id || "");
  useEffect(() => {
    if (!ebay.connected || policiesData) return;
    api("/api/ebay/policies").then(setPoliciesData).catch(() => {});
  }, [ebay.connected, policiesData, setPoliciesData]);
  if (!ebay.connected) return null;
  const policies = policiesData?.policies?.fulfillment || [];
  if (!policies.length) return null;
  const accountDefault = policiesData?.selected?.fulfillment_policy_id || "";
  const save = async (id) => {
    setValue(id);
    try {
      await postJson(`/api/save/${item.id}`, {
        ...(item.listing || {}), fulfillment_policy_id: id,
      });
    } catch (e) {
      toast(`Couldn't save the shipping service: ${e.message}`, { kind: "error" });
    }
  };
  return (
    <div className="mt-1.5 flex items-center gap-1.5" title="Shipping service for this draft">
      <Truck size={14} className="shrink-0 text-ink-faint" aria-hidden />
      <Select
        aria-label="Shipping service"
        className="h-9 text-[13px]"
        value={value || accountDefault}
        onChange={(e) => save(e.target.value)}
      >
        {policies.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}{p.summary ? ` · ${p.summary}` : ""}
          </option>
        ))}
      </Select>
    </div>
  );
}

// Category display + quick fix right on the draft card — a miscategorized
// item is the AI misfire that costs most once it's published, so the pick
// has to be visible (and fixable) without opening the full editor.
function DraftCategory({ item }) {
  const { loadListings } = useApp();
  const { toast } = useToast();
  const [saving, setSaving] = useState(false);
  const save = async (patch) => {
    setSaving(true);
    try {
      await postJson(`/api/save/${item.id}`, { ...(item.listing || {}), ...patch });
      // Refresh the cache so the card (and its Publish gate) sees the change.
      await loadListings({ quiet: true });
    } catch (e) {
      toast(`Couldn't save the category: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };
  return <CategoryQuickPick listing={item.listing} onPick={save} saving={saving} />;
}

// A multi-marketplace publish response as one toast line: "eBay ✓ · Etsy ✗".
function resultSummary(res) {
  if (!res.multi) return null;
  return Object.entries(res.results || {})
    .map(([key, r]) => `${key === "ebay" ? "eBay" : key.charAt(0).toUpperCase() + key.slice(1)} ${r.published ? "✓" : r.ok ? "—" : "✗"}`)
    .join(" · ");
}

export function DraftsStrip({ search = "" }) {
  const {
    listingsState, openListing, loadListings, deleteListing, bulkDeleteListings,
    metricsById, skippedDraftIds, toggleSkipDraft,
  } = useApp();
  const { confirm, toast } = useToast();
  const { selected, toggle, otherConnected, effectiveTargets } = usePublishTargets();

  const [selecting, setSelecting] = useState(false);
  const [sel, setSel] = useState({});
  const [publishing, setPublishing] = useState({});   // id -> bool
  const [startingOver, setStartingOver] = useState(null);

  const q = search.trim().toLowerCase();
  const drafts = listingsState.items
    .filter(isDraft)
    .filter((i) => !q
      || (i.listing?.title || i.title || "").toLowerCase().includes(q)
      || (i.listing?.brand || "").toLowerCase().includes(q)
      || (i.listing?.description || "").toLowerCase().includes(q))
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));

  if (!drafts.length) return null;

  const selIds = Object.keys(sel).filter((id) => sel[id]);
  const exitSelect = () => { setSelecting(false); setSel({}); };

  const publishOne = async (item) => {
    setPublishing((p) => ({ ...p, [item.id]: true }));
    try {
      const res = await publishListing(item.id, item.listing || {}, effectiveTargets);
      const summary = resultSummary(res);
      if (res.published) {
        const partial = res.multi
          && Object.values(res.results || {}).some((r) => !r.ok);
        toast(partial
          ? `${summary} — open the listing to fix the rest.`
          : (res.message || "Published! It's live now."),
          { kind: partial ? "warning" : "success" });
      } else {
        toast(res.message || "Publish blocked — open the draft to see what to fix.",
          { kind: "warning" });
      }
      await loadListings({ quiet: true });
      return !!res.published;
    } catch (e) {
      toast(`Publish error: ${e.message}`, { kind: "error" });
      return false;
    } finally {
      setPublishing((p) => ({ ...p, [item.id]: false }));
    }
  };

  const publishSelected = async () => {
    const targets = drafts.filter((d) => sel[d.id]);
    if (!targets.length) { toast("Nothing selected to publish.", { kind: "warning" }); return; }
    const targetNames = effectiveTargets && effectiveTargets.length > 1
      ? effectiveTargets
          .map((k) => k === "ebay" ? "eBay" : k.charAt(0).toUpperCase() + k.slice(1))
          .join(" and ")
      : "your eBay store";
    if (!(await confirm({
      title: `Publish ${targets.length} draft${targets.length === 1 ? "" : "s"} live?`,
      message: `Each goes straight to ${targetNames}.`,
      confirmLabel: "Publish live",
    }))) return;
    let ok = 0, failed = 0;
    for (const item of targets) {
      (await publishOne(item)) ? ok++ : failed++;
    }
    exitSelect();
    toast(`Published ${ok} listing${ok === 1 ? "" : "s"}.`
      + (failed ? ` ${failed} need attention — open them to fix.` : ""),
      { kind: failed ? "warning" : "success" });
  };

  const deleteSelected = async () => {
    if (!selIds.length) return;
    if (!(await confirm({
      title: `Delete ${selIds.length} draft${selIds.length === 1 ? "" : "s"}?`,
      message: "They'll be permanently removed, photos included. This can't be undone.",
      confirmLabel: "Delete all selected",
      danger: true,
    }))) return;
    if (await bulkDeleteListings(selIds)) exitSelect();
  };

  const askDelete = async (item) => {
    const name = item.listing?.title || item.title || "this listing";
    if (await confirm({
      title: "Delete this listing?",
      message: `"${name}" will be permanently removed. This can't be undone.`,
      confirmLabel: "Delete",
      danger: true,
    })) deleteListing(item.id);
  };

  // Start over on a draft: throw away the drafted copy and re-run the AI over
  // the listing's own photos.
  const startOver = async (item) => {
    const name = item.listing?.title || item.title || "this draft";
    if (!(await confirm({
      title: "Start this listing over?",
      message: `The AI will look at "${name}"'s photos again and rewrite the `
        + "title, description, and item specifics. Your photos are kept; any "
        + "edits you made to the text are replaced.",
      confirmLabel: "Start over",
    }))) return;
    setStartingOver(item.id);
    try {
      const { job_id } = await postJson(`/api/identify-async/${item.id}`, {});
      await pollJob(job_id);
      await loadListings({ quiet: true });
      toast("Rewritten from the photos.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't start over: ${e.message}`, { kind: "error" });
    } finally {
      setStartingOver(null);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <SectionHeader
        icon={FilePen}
        title={`Drafts (${drafts.length})`}
        action={
          <div className="flex flex-wrap items-center justify-end gap-2">
            {!selecting && (
              <MarketTargetChips selected={selected} toggle={toggle}
                otherConnected={otherConnected} />
            )}
            {selecting ? (
              <>
                <Button variant="primary" size="sm" onClick={publishSelected}
                  disabled={!selIds.length}>
                  <Rocket aria-hidden /> Publish selected ({selIds.length})
                </Button>
                <Button variant="danger" size="sm" onClick={deleteSelected}
                  disabled={!selIds.length}>
                  <Trash2 aria-hidden /> Delete selected ({selIds.length})
                </Button>
                <Button variant="ghost" size="sm"
                  onClick={() => setSel(Object.fromEntries(drafts.map((i) => [i.id, true])))}>
                  All
                </Button>
                <Button variant="ghost" size="sm" onClick={exitSelect}>
                  <X aria-hidden /> Cancel
                </Button>
              </>
            ) : (
              <Button variant="ghost" size="sm" onClick={() => setSelecting(true)}>
                <CheckSquare aria-hidden /> Select
              </Button>
            )}
          </div>
        }
      />
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {drafts.map((item, i) => {
          const missing = missingRequired(item.listing || {});
          return (
            <motion.div
              key={item.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.22, delay: Math.min(i * 0.03, 0.3) }}
            >
              {/* Delete lives in the labeled row below, not as another tiny
                  icon in the card's corner cluster. */}
              <ListingCard item={item} onOpen={openListing}
                onStartOver={startOver}
                startingOver={startingOver === item.id}
                onSkip={() => toggleSkipDraft(item.id)}
                skipped={skippedDraftIds.has(item.id)}
                metrics={metricsById[item.id]}
                selectable={selecting}
                selected={!!sel[item.id]}
                onSelect={() => setSel((s) => ({ ...s, [item.id]: !s[item.id] }))} />
              {!selecting && (
                <>
                  <div className="mt-1.5 flex items-center gap-1.5">
                    <Button variant="secondary" size="sm" className="flex-1"
                      onClick={() => publishOne(item)}
                      loading={!!publishing[item.id]}
                      disabled={missing.length > 0}
                      title={missing.length
                        ? `Missing: ${missing.join(", ")} — open Preview & Edit to finish`
                        : undefined}>
                      <Rocket aria-hidden /> Publish
                    </Button>
                    <Button variant="ghost" size="sm" className="flex-1"
                      onClick={() => openListing(item.id)}>
                      <PenLine aria-hidden /> Preview &amp; Edit
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => askDelete(item)}
                      aria-label="Delete this draft" title="Delete this draft"
                      className="shrink-0 text-ink-faint hover:text-error">
                      <Trash2 aria-hidden />
                    </Button>
                  </div>
                  <DraftCategory item={item} />
                  <DraftShipping item={item} />
                </>
              )}
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}
