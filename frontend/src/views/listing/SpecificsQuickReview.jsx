import { useState } from "react";
import { Check, Eye, Loader2 } from "lucide-react";
import { postJson } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/fields";
import { reviewAspects } from "./specifics";

/* The "N to review" chip's contents, opened on the card that carries it.
 *
 * Those N are the item specifics the AI INFERRED rather than read off the
 * item (confidence "medium"). They block nothing — a draft publishes with
 * every one of them outstanding — so the whole reason the chip exists is that
 * a wrong specific is worse than a missing one, and the only way to answer it
 * was to open the listing, scroll to the Item specifics card and tick there.
 * A seller working a grid of twenty fresh drafts made twenty round trips to
 * say twenty times that the guess was fine, which is how the chip became
 * something to publish past rather than something to read.
 *
 * So the same two gestures the editor offers, on the card: ✓ to accept a
 * guess as it stands, or type over it and accept the correction in one move.
 * Same wording, same order, same rule that one ✓ clears a whole aspect —
 * this is the editor's specifics card reduced to the flagged rows, not a
 * second opinion about what "reviewed" means.
 *
 * What it deliberately is NOT is a one-tap "accept all" on the face of the
 * grid. The editor offers that last, only past one outstanding guess, and
 * worded as a claim the seller is making ("I've read them all") rather than
 * an instruction — because a button that clears twenty flags without showing
 * twenty values is how a wrong value reaches a live listing. It keeps those
 * terms exactly here, where the values are on screen above it.
 *
 * `onConfirm` is handed the aspects being accepted, by NAME — never the
 * specifics list — and owns persistence. Same split as CategoryQuickPick and
 * PriceQuickEdit, with one extra reason: see DraftSpecificsReview below.
 */

// One flagged aspect: what the AI called it, what it put there, and the ✓.
function AspectRow({ aspect, onConfirm, saving }) {
  // eBay's tick-box specifics hold several values under one name and show one
  // flag for the group (specifics.reviewAspects). A text box can only edit
  // one answer, so a group is read here and changed in the editor — offering
  // a single input for four ticked values would quietly drop three of them.
  const multi = aspect.values.length > 1;
  const stored = aspect.values[0];
  const [text, setText] = useState(stored);
  // The stored value can move underneath this box — the editor in another
  // tab, a background enrichment, the refresh after our own save. Adjusting
  // state during render (React's documented pattern, as in PriceQuickEdit's
  // MoneyField) keeps it showing what is stored without an effect.
  const [seen, setSeen] = useState(stored);
  if (stored !== seen) { setSeen(stored); setText(stored); }

  const typed = text.trim();
  // An emptied box is not a correction — there is no way to say "this aspect
  // has no answer" from here, and sending "" would delete the guess the
  // seller is only reading. The ✓ accepts what is stored in that case.
  const value = typed && typed !== stored ? typed : undefined;

  return (
    <li className="flex flex-col gap-1">
      <span className="text-[11px] font-semibold text-ink-faint">{aspect.name}</span>
      <div className="flex items-center gap-1.5">
        {multi ? (
          <span className="min-w-0 flex-1 text-[13px] leading-snug text-ink"
            title="Several values under one specific — change these in the full editor">
            {aspect.values.join(" · ")}
          </span>
        ) : (
          <Input
            className="h-9 text-[13px]"
            aria-label={aspect.name}
            disabled={saving}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              // Enter is the same answer as the ✓ beside it, down to what it
              // sends: a seller who has typed the right value should not
              // have to reach for a button to say they meant it, and the two
              // gestures must not reach the server as different requests.
              if (e.key !== "Enter") return;
              e.preventDefault();
              onConfirm([{ name: aspect.name, value }]);
            }}
          />
        )}
        <button
          type="button"
          disabled={saving}
          onClick={() => onConfirm([{ name: aspect.name, value }])}
          aria-label={value
            ? `Save "${value}" as the ${aspect.name}`
            : `${aspect.name} is right`}
          title={value
            ? `Save "${value}" as the ${aspect.name}`
            : "Looks right — clear the flag on this one"}
          className={cn(
            "grid place-items-center size-9 shrink-0 rounded-[12px] cursor-pointer",
            "border transition-colors duration-150 disabled:opacity-50",
            value
              ? "bg-blue border-blue text-on-accent"
              : "bg-card border-line text-ink-faint hover:text-blue hover:border-blue/40",
          )}
        >
          <Check size={16} aria-hidden />
        </button>
      </div>
    </li>
  );
}

export function SpecificsQuickReview({ listing, onConfirm, saving, className }) {
  const aspects = reviewAspects((listing || {}).item_specifics);
  // Nothing flagged: the chip that opens this is gone too, so there is no
  // panel to leave behind.
  if (!aspects.length) return null;

  return (
    <div className={cn(
      "flex flex-col gap-2 rounded-input border border-blue/35 bg-blue-soft px-3 py-2.5",
      className)}>
      {/* Blue, and saying so in words: the editor reserves amber for the
          blockers (an empty required specific eBay will refuse) and paints
          this nudge blue, because "eBay will reject this" and "give this a
          glance" being the same colour is what taught sellers to ignore
          both. The same distinction has to survive the trip to the card. */}
      <p className="flex items-start gap-1.5 text-[12px] leading-snug text-ink">
        {saving
          ? <Loader2 size={13} className="mt-px shrink-0 animate-spin text-blue" aria-hidden />
          : <Eye size={13} className="mt-px shrink-0 text-blue" aria-hidden />}
        <span className="min-w-0">
          <strong className="font-bold">
            {aspects.length} AI {aspects.length === 1 ? "guess" : "guesses"}
          </strong>
          {" — tick what's right, type over what isn't. None of it is holding "
            + "up the publish."}
        </span>
      </p>
      <ul className="flex flex-col gap-2">
        {aspects.map((a) => (
          <AspectRow key={a.name} aspect={a} onConfirm={onConfirm} saving={saving} />
        ))}
      </ul>
      {aspects.length > 1 && (
        // Last, past one outstanding guess, and worded as the seller's own
        // claim — the editor's terms exactly (views/listing/cards.jsx). The
        // values are all on screen directly above it, which is the only
        // condition under which this button is honest.
        <Button variant="primary" size="sm" className="self-start"
          disabled={saving}
          onClick={() => onConfirm(aspects.map((a) => ({ name: a.name })))}>
          <Check aria-hidden /> I&apos;ve read them all
        </Button>
      )}
    </div>
  );
}

/* The same panel, wired to a SAVED draft — read, tick, confirm, refresh.
 *
 * Drafts only, like the category, format and price controls it sits with:
 * `reviewAspectCount` is only ever read on a draft card (ListingCard), since
 * once a listing is live the seller has stood behind it and the AI's doubts
 * about the first draft are not a fact about the listing that is selling.
 *
 * It posts the aspect NAMES rather than the specifics list, and that is the
 * whole shape of the route behind it. A card holds whatever the last
 * /api/listings load handed it; sending `item_specifics` back wholesale would
 * erase every row that landed since — and this listing has a guaranteed
 * concurrent writer, because "Enrich all" fills specifics on drafts in a
 * background thread. The server applies the ✓ to the rows it is holding now.
 */
export function DraftSpecificsReview({ item, className }) {
  const { loadListings } = useApp();
  const { toast } = useToast();
  const [saving, setSaving] = useState(false);
  const confirm = async (aspects) => {
    setSaving(true);
    try {
      await postJson(`/api/listings/${item.id}/specifics/confirm`, { aspects });
      // The chip on the card counts off this cache, so it has to see the
      // change or the seller ticks a flag that stays on screen.
      await loadListings({ quiet: true });
    } catch (e) {
      toast(`Couldn't save that just now: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };
  return (
    <SpecificsQuickReview listing={item.listing} onConfirm={confirm}
      saving={saving} className={className} />
  );
}
