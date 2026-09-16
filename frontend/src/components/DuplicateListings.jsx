import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CopyCheck, ExternalLink, Info, ChevronDown, BellOff } from "lucide-react";
import { api, postJson } from "@/lib/api";
import { useToast } from "@/components/ui/Toaster";
import { Card, SectionHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { TagPill } from "@/components/ui/badges";
import { formatMoney } from "@/lib/utils";

/* Possible duplicate listings — two live eBay listings that look like one
   item listed twice.

   These are the leftovers of the publish race the app now prevents. They can't
   be fixed automatically: each one is a real listing on eBay, and choosing
   which to end is the seller's call (the older one usually has the watchers).
   So this card presents evidence and gets out of the way — it ends nothing on
   its own, and every End is one listing at a time behind a confirm.

   And a seller who has looked and decided all of them are fine can say so
   once, with Dismiss all. That has to stick, or the card is just nagging: the
   scan re-runs on every Dashboard load and would otherwise put the same
   answered question back every time. It sticks per GROUP, pinned to the state
   the seller judged (see duplicates.fingerprint) — edit either listing and
   that group is a new question, so it comes back. Hence the confirm: the way
   back from a mis-tap is an edit, which is too obscure to leave to a stray
   thumb. */

const CONFIDENCE = {
  high: { label: "Likely duplicate", tone: "red" },
  medium: { label: "Possible duplicate", tone: "yellow" },
  low: { label: "Worth a look", tone: "neutral" },
};

function listedOn(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString(undefined,
    { month: "short", day: "numeric", year: "numeric" });
}

// One live listing inside a suspected-duplicate group.
function DuplicateRow({ item, ending, onEnd }) {
  const when = listedOn(item.listed_at);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[13px] border border-line bg-bg p-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-bold text-sm text-ink tabular-nums">
            {formatMoney(item.price, item.currency) || "no price"}
          </span>
          {item.created_here && (
            <TagPill tone="blue">Created here</TagPill>
          )}
          {item.watch_count > 0 && (
            <TagPill tone="green">
              {item.watch_count} watcher{item.watch_count === 1 ? "" : "s"}
            </TagPill>
          )}
        </div>
        <p className="mt-0.5 text-[12px] text-ink-secondary tabular-nums">
          Item {item.ebay_listing_id}{when ? ` · listed ${when}` : ""}
        </p>
      </div>
      {item.view_url && (
        <a href={item.view_url} target="_blank" rel="noreferrer"
          className="inline-flex items-center gap-1 text-[13px] font-semibold text-blue hover:underline shrink-0">
          View <ExternalLink size={13} aria-hidden />
        </a>
      )}
      <Button variant="danger" size="sm" className="shrink-0"
        loading={ending} disabled={ending} onClick={() => onEnd(item)}>
        End this one
      </Button>
    </div>
  );
}

function DuplicateGroup({ group, ending, onEnd }) {
  const [open, setOpen] = useState(group.confidence === "high");
  const meta = CONFIDENCE[group.confidence] || CONFIDENCE.low;
  return (
    <div className="p-4">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="w-full flex items-start gap-3 text-left cursor-pointer">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <TagPill tone={meta.tone}>{meta.label}</TagPill>
            <span className="text-[12px] font-semibold text-ink-secondary">
              {group.listings.length} live listings
            </span>
          </div>
          <p className="mt-1 font-semibold text-sm text-ink line-clamp-2">
            {group.title}
          </p>
        </div>
        <motion.span animate={{ rotate: open ? 180 : 0 }}
          transition={{ duration: 0.18 }} className="text-ink-faint shrink-0 mt-1">
          <ChevronDown size={17} aria-hidden />
        </motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="overflow-hidden"
          >
            <div className="pt-3 space-y-2">
              {group.reasons.map((r) => (
                <p key={r} className="text-[13px] text-ink-secondary">• {r}</p>
              ))}
              {/* The reasons this might be perfectly fine carry equal weight —
                  a seller really can own two of the same thing. */}
              {group.caveats.map((c) => (
                <p key={c} className="flex items-start gap-1.5 text-[13px] text-warning">
                  <Info size={13} className="mt-0.5 shrink-0" aria-hidden /> {c}
                </p>
              ))}
              <div className="pt-1 space-y-2">
                {group.listings.map((item) => (
                  <DuplicateRow key={item.listing_id} item={item}
                    ending={ending === item.listing_id} onEnd={onEnd} />
                ))}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function DuplicateListings({ onChanged }) {
  const { confirm, toast } = useToast();
  const [state, setState] = useState({ loading: true, groups: [], hidden: 0 });
  const [ending, setEnding] = useState(null);
  const [dismissing, setDismissing] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await api("/api/ebay/duplicates");
      setState({ loading: false, groups: res.groups || [],
                 hidden: res.dismissed || 0 });
    } catch (e) {
      // advisory only — stay quiet
      setState({ loading: false, groups: [], hidden: 0 });
    }
  }, []);
  // Fetch the duplicate report once on mount. Nothing is written
  // synchronously: `load` suspends on the `await` before either setState runs,
  // so there is no cascading render for the compiler rule to protect against.
  // `state.loading` starts true and the card renders null until the round trip
  // finishes, so there is no flag to flip on the way in either.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- deliberate: see the note above
    load();
  }, [load]);

  // Dismiss all — "I've looked at these and they're fine."
  //
  // Sends the fingerprints the card is actually SHOWING rather than asking
  // the server to dismiss whatever it finds a second later: a group that
  // appeared between the load and the press is one the seller never saw, and
  // waving away an unseen duplicate is the one thing this button must not do.
  const dismissAll = async () => {
    const n = state.groups.length;
    if (!n) return;
    if (!(await confirm({
      title: n === 1 ? "Dismiss this one?" : `Dismiss all ${n}?`,
      message: n === 1
        ? "Both listings stay live on eBay — this only stops the reminder. "
          + "It comes back if you edit either one's price or format, or "
          + "relist one."
        : "Every listing stays live on eBay — this only stops the reminder. "
          + "They come back if you edit any of these listings' price or "
          + "format, or relist one.",
      confirmLabel: n === 1 ? "Dismiss" : "Dismiss all",
    }))) return;
    setDismissing(true);
    try {
      await postJson("/api/ebay/duplicates/dismiss", {
        fingerprints: state.groups.map((g) => g.fingerprint),
      });
      setState({ loading: false, groups: [], hidden: state.hidden + n });
      toast(n === 1
        ? "Dismissed — we won't mention it again unless it changes."
        : `Dismissed ${n} — we won't mention them again unless they change.`,
        { kind: "success" });
    } catch (e) {
      toast(`Couldn't dismiss that: ${e.message}`, { kind: "error" });
    } finally {
      setDismissing(false);
    }
  };

  const endOne = async (item) => {
    if (!(await confirm({
      title: "End this listing on eBay?",
      message: `Item ${item.ebay_listing_id} comes off eBay immediately. `
        + "Check the other listing is the one you want to keep first.",
      confirmLabel: "End listing",
      danger: true,
    }))) return;
    setEnding(item.listing_id);
    try {
      const res = await postJson("/api/ebay/end-listing", { session_id: item.listing_id });
      toast(res.status === "sold"
        ? "Turns out this one sold on eBay — it's archived under Inactive. 🎉"
        : res.removed
          ? "Listing ended and removed."
          : res.status === "ended"
            ? "Listing ended — it's under Inactive if you want to relist it."
            : res.message || "Listing ended.", { kind: "success" });
      await load();
      onChanged?.();
    } catch (e) {
      toast(`Couldn't end the listing: ${e.message}`, { kind: "error" });
    } finally {
      setEnding(null);
    }
  };

  // Silent when there's nothing to report: a permanent empty "no duplicates"
  // panel is clutter on a healthy store.
  if (state.loading || !state.groups.length) return null;

  const n = state.groups.length;
  return (
    <div>
      <SectionHeader icon={CopyCheck} title="Possible duplicate listings"
        action={(
          <Button variant="ghost" size="sm" className="shrink-0"
            loading={dismissing} disabled={dismissing} onClick={dismissAll}
            title="Stop reminding me about these">
            <BellOff size={15} aria-hidden />
            {n === 1 ? "Dismiss" : "Dismiss all"}
          </Button>
        )} />
      <Card className="p-0 divide-y divide-line overflow-hidden">
        <p className="px-4 pt-4 pb-3 text-[13px] text-ink-secondary">
          {n === 1 ? "One item looks" : `${n} items look`} like they're live on
          eBay more than once. Ending the extra keeps your listings clean —
          but check each one first: two of the same thing can be genuine.
          {/* What the card is deliberately NOT showing. A scan that quietly
              drops what you dismissed is indistinguishable from a scan that
              stopped finding anything — and this is the only place a seller
              can learn that the rule is "until you edit them". */}
          {state.hidden > 0 && (
            <> {state.hidden === 1
              ? "One more is dismissed; it comes"
              : `${state.hidden} more are dismissed; they come`} back here if
              you edit {state.hidden === 1 ? "it" : "them"}.</>
          )}
        </p>
        {state.groups.map((g) => (
          <DuplicateGroup key={g.fingerprint} group={g}
            ending={ending} onEnd={endOne} />
        ))}
      </Card>
    </div>
  );
}
