import { useEffect, useRef, useState } from "react";
import { Plus, X, AlertTriangle, Check, ChevronRight, CornerDownLeft } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/fields";
import { specificRowIndex } from "./specifics";
import { Section } from "./Section";
import { issuesFor } from "./publishShared";

/* Details -- the item specifics, as chips.
 *
 * eBay's form draws every aspect of a category as a labelled box: forty
 * boxes for a pair of jeans, most of them answered by the AI before the
 * seller arrives, the two that matter buried among them. The draft arrives
 * filled (backend _fill_what_is_left), so what a seller does here is READ,
 * and a grid of chips reads in one glance: "Brand: Levi's ✓", "Size: 34 ⚠",
 * "Colour — required" in amber, "+ Material" as a ghost. Tapping a chip
 * swaps in the control for that aspect -- the same Select, Input with
 * eBay's suggestions, or tick-box list the boxes used to be -- in a row
 * under the chips, with no floating layer. Enter commits and moves to the
 * next empty required chip; Escape closes (LISTING_REDESIGN.md, "Details").
 *
 * What is kept from the grid, because none of it was about the grid: the
 * refusal banner and the red ring on the aspect eBay named, "eBay asked for
 * these" for an aspect the category list does not carry, "Your own
 * specifics", the `sugg-<slug>` datalists, and the aria-label on the
 * add-your-own box. */

// How many empty recommended aspects show as ghost chips before "Show N
// more". Six is a row and a half: enough to suggest what else buyers filter
// by, not enough to bury the filled ones.
const GHOSTS = 6;

const norm = (s) => (s || "").trim().toLowerCase();

// The trust marker for one specific: ✓ = the AI read it off the item (tag,
// label, print) or it's unambiguous; ⚠ = a reasonable inference worth a
// glance; nothing = the seller typed or confirmed it (or it's empty).
export function ConfidenceMark({ row, className }) {
  if (!row || !(row.value || "").trim() || !row.confidence) return null;
  const high = row.confidence === "high";
  const label = high
    ? "Read from your photos by the AI"
    : "Inferred by the AI — worth a glance";
  return (
    <span
      title={label} aria-label={label} tabIndex={0}
      className={cn("inline-flex shrink-0 cursor-help outline-none",
        high ? "text-green" : "text-warning", className)}
    >
      {high ? <Check size={13} aria-hidden /> : <AlertTriangle size={13} aria-hidden />}
    </span>
  );
}

// One labelled group under the chips. The rule under the heading is what
// makes the page scannable without boxes.
function SpecGroup({ title, note, count, children }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-baseline flex-wrap gap-x-2 gap-y-0.5 pb-1.5 border-b border-line">
        <h4 className="text-[13px] font-bold text-ink">{title}</h4>
        {count != null && (
          <span className="font-display text-[12px] font-semibold text-ink-faint tabular-nums">{count}</span>
        )}
        {note && <span className="text-[12px] font-normal text-ink-faint">{note}</span>}
      </div>
      {children}
    </section>
  );
}

// A multi-select aspect: eBay shows these as tick boxes, so we do too. Ticking
// adds a value rather than replacing one, which is the only way an aspect like
// Features ("Breathable", "Pockets", "Water Resistant") ends up on the listing
// with more than one box ticked.
//
// Not wrapped in <Field>: that renders a <label>, and a label around a group of
// checkboxes hijacks every click inside it.
function AspectChecklist({ w, a }) {
  const [showAll, setShowAll] = useState(false);
  const [draft, setDraft] = useState("");
  // Whether eBay's list is law or advice. SELECTION_ONLY refuses anything not
  // on it; FREE_TEXT ships the same boxes but the values are only what eBay
  // SUGGESTS, so the seller must be able to tick a value of their own.
  const open = a.mode !== "SELECTION_ONLY";
  const selected = w.getSpecificValues(a.name);
  const picked = new Set(selected.map((v) => v.toLowerCase()));
  // One badge for the whole group, showing the least certain tick in it — a
  // group with four confident values and one guess still needs a look.
  const rows = w.form.item_specifics.filter(
    (s) => norm(s.name) === norm(a.name) && (s.value || "").trim());
  const row = rows.find((s) => s.confidence === "medium") || rows[0] || null;
  const missing = a.required && selected.length === 0;
  // Values the listing holds that eBay doesn't offer here (a seller's own, or
  // a category change) still get a box — otherwise they'd be invisible and
  // unremovable.
  const offList = selected.filter(
    (v) => !a.values.some((x) => x.toLowerCase() === v.toLowerCase()));
  const options = [...offList, ...a.values];
  // A long list stays collapsed to the ticked values plus the first handful:
  // Features can run to 60 boxes, and scrolling past them to reach the next
  // aspect is worse than one extra tap.
  const VISIBLE = 12;
  const shown = showAll
    ? options
    : options.filter((v, i) => i < VISIBLE || picked.has(v.toLowerCase()));
  const hidden = options.length - shown.length;

  return (
    <div className="flex flex-col gap-1.5 min-w-0">
      <span className="text-[13px] font-semibold text-ink flex items-center gap-1.5">
        {a.name}
        <span className="font-normal text-ink-faint inline-flex items-center gap-1.5">
          <span className="text-[12px]">
            {selected.length ? `${selected.length} selected`
              : open ? "tick all that apply — or add your own"
                : "tick all that apply"}
          </span>
          <ConfidenceMark row={row} />
          {missing && (
            <span className="text-[12px] font-semibold text-warning">Required</span>
          )}
        </span>
      </span>
      <div
        role="group"
        aria-label={a.name}
        className={cn(
          "flex flex-col gap-1.5 rounded-input border border-line bg-card px-3 py-2.5",
          missing && "ring-2 ring-warning/60",
        )}
      >
        {shown.map((v) => (
          <label key={v} className="flex items-start gap-2.5 text-[14px] text-ink cursor-pointer">
            <input
              type="checkbox"
              checked={picked.has(v.toLowerCase())}
              onChange={(e) => w.toggleSpecificValue(a.name, v, e.target.checked)}
              className="size-4 mt-0.5 shrink-0 accent-blue"
            />
            <span className="min-w-0">{v}</span>
          </label>
        ))}
        {hidden > 0 && (
          <button
            type="button"
            onClick={() => setShowAll(true)}
            className="self-start text-[12px] font-semibold text-ink-secondary underline underline-offset-2 cursor-pointer hover:text-ink"
          >
            Show {hidden} more
          </button>
        )}
        {/* An open list is a list of SUGGESTIONS, so the seller needs a way to
            tick something eBay never thought of — a feature this item has that
            the category's boxes don't name. Ticked values that aren't on
            eBay's list already get a box of their own (offList above), so what
            is added here stays visible and removable like any other tick.
            A closed list gets no such box: there, an off-list value is one
            eBay refuses. */}
        {open && (
          <div className="flex items-center gap-1.5 pt-0.5">
            <input
              type="text"
              value={draft}
              // Not the aspect's own name: "Add another features" / "Add
              // another country/region of manufacture" is what that produces.
              placeholder="Add your own"
              aria-label={`Add a ${a.name} value of your own`}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                // Enter inside a listing form would otherwise submit it --
                // and, in the chip editor, move to the next chip. Here it
                // adds the value and stays.
                if (e.key !== "Enter") return;
                e.preventDefault();
                e.stopPropagation();
                if (draft.trim()) w.toggleSpecificValue(a.name, draft.trim(), true);
                setDraft("");
              }}
              className="min-w-0 flex-1 bg-card text-ink border border-line rounded-input px-2.5 py-1 text-[13px] placeholder:text-ink-faint hover:border-line-strong focus:border-blue focus:outline-none focus:ring-2 focus:ring-blue/25 transition-colors"
            />
            <button
              type="button"
              disabled={!draft.trim()}
              onClick={() => {
                w.toggleSpecificValue(a.name, draft.trim(), true);
                setDraft("");
              }}
              className="shrink-0 rounded-full border border-line px-2.5 py-1 text-[12px] font-semibold text-ink-secondary cursor-pointer hover:border-blue hover:text-blue disabled:opacity-40 disabled:cursor-default disabled:hover:border-line disabled:hover:text-ink-secondary transition-colors"
            >
              Add
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/* One chip: the aspect's name and its value, with the trust mark, or what
 * is missing. A button, so it tabs and presses like one. */
function Chip({ a, value, row, refused, saidByEbay, open, onClick }) {
  const missing = a.required && !value;
  const ghost = !a.required && !value;
  const label = missing ? `${a.name} — required`
    : ghost ? `+ ${a.name}` : `${a.name}: ${value}`;
  return (
    <button
      type="button"
      data-chip={a.name}
      data-chip-state={refused ? "refused" : missing ? "missing" : ghost ? "ghost" : "filled"}
      aria-pressed={open}
      aria-label={refused
        ? `${label} — ${saidByEbay ? "eBay refused this" : "fix this to publish"}`
        : label}
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 max-w-full rounded-full border px-3 h-9 text-[13px]",
        "cursor-pointer transition-colors duration-150 text-left",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue/40",
        // Red beats the amber "required and empty" ring: one is a rule we
        // are predicting, the other is the answer the marketplace already
        // gave about this listing.
        refused ? "border-error ring-2 ring-error/70 bg-error-soft text-ink"
          : missing ? "border-warning bg-warning-soft text-warning font-semibold hover:border-warning"
            : ghost ? "border-dashed border-line text-ink-secondary hover:border-line-strong hover:text-ink"
              : "border-line bg-card text-ink hover:border-line-strong",
        open && "border-blue ring-2 ring-blue/25",
      )}
    >
      {missing || ghost ? (
        <span className="truncate">{label}</span>
      ) : (
        <>
          <span className="text-ink-secondary shrink-0">{a.name}:</span>
          <span className="font-semibold truncate">{value}</span>
          <ConfidenceMark row={row} />
        </>
      )}
      {refused && (
        <span className="shrink-0 text-[11.5px] font-bold text-error">
          {saidByEbay ? "eBay refused this" : "Fix this to publish"}
        </span>
      )}
    </button>
  );
}

/* The editor for the open chip: the aspect's own control, in a row under
 * the chips. Enter commits (the value is already on the listing -- every
 * keystroke went through `set`) and moves to the next empty required chip,
 * or closes when there is none; Escape closes. */
function ChipEditor({ a, children, onNext, onClose, hasNext }) {
  const ref = useRef(null);
  // Focus lands on the control when the row opens, so a chip pressed from
  // the keyboard can be answered from the keyboard.
  useEffect(() => {
    const first = ref.current?.querySelector("input:not([type=checkbox]), select, input[type=checkbox]");
    first?.focus();
  }, [a.name]);
  const onKeyDown = (e) => {
    if (e.defaultPrevented) return;
    if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
    if (e.key === "Enter" && e.target.type !== "checkbox") { e.preventDefault(); onNext(); }
  };
  return (
    <div
      ref={ref}
      role="group"
      aria-label={`Edit ${a.name}`}
      data-chip-editor={a.name}
      onKeyDown={onKeyDown}
      className="flex flex-col gap-3 rounded-input border border-blue/40 bg-blue-soft/30 p-3.5"
    >
      {children}
      <div className="flex items-center gap-2">
        <Button variant="soft" size="sm" onClick={onNext}>
          {hasNext ? "Next" : "Done"}
          {hasNext ? <ChevronRight aria-hidden /> : <CornerDownLeft aria-hidden />}
        </Button>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close the editor">
          <X aria-hidden /> Close
        </Button>
        <span className="ml-auto text-[11.5px] text-ink-faint hidden sm:inline">
          Enter — {hasNext ? "next required" : "done"} · Esc — close
        </span>
      </div>
    </div>
  );
}

export function DetailsCard({ w }) {
  const aspects = w.categoryMeta.aspects || [];
  const required = aspects.filter((a) => a.required);
  const recommendedAll = aspects.filter((a) => !a.required);
  // What the last publish attempt was REFUSED over, in this section's own
  // fields. A failed listing is the moment these matter most, and it was
  // the moment they were hardest to find. So the section opens itself,
  // stops hiding chips, repeats what eBay said where the chips are, rings
  // the one it named, and opens its editor.
  //
  // It opens via `expand` rather than `flagged` because eBay can name a field
  // in here while the FIRST error it returned points at another section, and
  // only one section may own the scroll. See Section.
  const refused = issuesFor(w.publishResult, "specifics");
  const refusedNames = new Set(refused.flatMap((i) => i.fields || [])
    .map(norm).filter(Boolean));
  // The pre-publish check raises the same issues in the same shape, and it is
  // worth showing them the same way — but eBay has not answered yet, so the
  // chip must not claim it refused anything.
  const saidByEbay = !w.publishResult?.preflight;

  // Brand mirrors the listing's own brand field, so a required Brand aspect
  // isn't "empty" just because no specifics row exists for it.
  const isBrand = (a) => norm(a.name) === "brand";
  const valueOf = (a) => w.getSpecific(a.name)
    || (isBrand(a) ? (w.form.brand || "").trim() : "");
  const rowOf = (a) => {
    const i = specificRowIndex(w.form.item_specifics, a.name);
    return i >= 0 ? w.form.item_specifics[i] : null;
  };

  // The chips, in reading order: required (empty ones amber), then the
  // filled recommended, then the first few empty recommended as ghosts.
  // Everything after a refusal, and after "Show N more".
  const [showAll, setShowAll] = useState(false);
  const showEvery = showAll || refused.length > 0;
  const filledRec = recommendedAll.filter((a) => valueOf(a));
  const emptyRec = recommendedAll.filter((a) => !valueOf(a));
  const ghosts = showEvery ? emptyRec : emptyRec.slice(0, GHOSTS);
  const hiddenCount = emptyRec.length - ghosts.length;
  const chips = [...required, ...filledRec, ...ghosts];

  const aspectNames = new Set(aspects.map((a) => norm(a.name)));
  // Free-form rows: everything not already shown as a category aspect.
  const freeRows = w.form.item_specifics
    .map((s, i) => ({ ...s, i }))
    .filter((s) => !aspectNames.has(norm(s.name)));

  // An aspect eBay refused over that this category's list does not contain and
  // the listing does not already carry. eBay adds required specifics to a
  // category from time to time, and our aspect list is cached — so a listing
  // that published fine can come back refused over a field with NO chip
  // anywhere on the page. Give it a box instead.
  const haveNames = new Set([
    ...aspectNames,
    ...w.form.item_specifics.map((s) => norm(s.name)),
  ].filter(Boolean));
  const unlisted = [...refusedNames]
    .filter((n) => n && !haveNames.has(n))
    .map((n) => (refused.flatMap((i) => i.fields || [])
      .find((f) => norm(f) === n) || n));

  const missingRequired = required.filter((a) => !valueOf(a)).length;
  const recommendedFilled = filledRec.length;

  // Which chip is open. A refusal opens the chip eBay named -- on mount,
  // and on the transition (state adjusted during render, so the editor is
  // in the same commit as the ring).
  const firstRefused = aspects.find((a) => refusedNames.has(norm(a.name)))?.name || null;
  const [open, setOpen] = useState(firstRefused);
  const [prevRefused, setPrevRefused] = useState(firstRefused);
  if (firstRefused !== prevRefused) {
    setPrevRefused(firstRefused);
    if (firstRefused) setOpen(firstRefused);
  }
  const openAspect = open ? aspects.find((a) => a.name === open) : null;
  // The next empty required aspect AFTER the open one, in chip order --
  // what Enter moves to. Forward only: a seller pressing Enter through the
  // chips is reading left to right, and a jump back to one they skipped on
  // purpose is a loop they cannot leave.
  const nextRequired = (from) => {
    const order = chips.map((a) => a.name);
    const at = order.indexOf(from);
    const after = order.slice(at + 1);
    const name = after.find((n) => {
      const a = aspects.find((x) => x.name === n);
      return a && a.required && !valueOf(a);
    });
    return name || null;
  };
  const goNext = () => setOpen(open ? nextRequired(open) : null);

  const setRow = (i, key, value) => {
    const specs = [...w.form.item_specifics];
    // Editing a value makes it the seller's own — drop the AI badge.
    specs[i] = key === "value"
      ? { ...specs[i], value, confidence: "" }
      : { ...specs[i], [key]: value };
    w.set("item_specifics", specs);
  };
  const removeRow = (i) => {
    w.set("item_specifics", w.form.item_specifics.filter((_, j) => j !== i));
  };

  // The control for one aspect -- what the chip editor holds.
  const renderControl = (a) => {
    // eBay's multi-select aspects — the item-specifics CHECKBOXES. A dropdown
    // can only ever hold one answer. CARDINALITY alone decides this, not the
    // mode: plenty of eBay's tick-box specifics come back FREE_TEXT + MULTI.
    if (a.cardinality === "MULTI" && a.values?.length) {
      return <AspectChecklist key={a.name} w={w} a={a} />;
    }
    const rowIndex = specificRowIndex(w.form.item_specifics, a.name);
    const row = rowIndex >= 0 ? w.form.item_specifics[rowIndex] : null;
    // MULTI-value aspects can hold several values; the field edits the
    // answer row and the rest show as removable chips.
    const extras = w.form.item_specifics
      .map((s, i) => ({ ...s, i }))
      .filter((s) => norm(s.name) === norm(a.name)
        && s.i !== rowIndex && (s.value || "").trim());
    const shown = row?.value || (isBrand(a) ? (w.form.brand || "") : "");
    const setValue = (v) => {
      // Brand lives on the listing itself — mirror it so edits flow back.
      if (isBrand(a)) w.set("brand", v);
      w.upsertSpecific(a.name, v);
    };
    // A DOM id for this aspect's suggestion list. Aspect names carry spaces
    // and slashes ("Country/Region of Manufacture"); a slug is unique on the
    // page because names are unique within a category.
    const listId = norm(a.name).replace(/[^a-z0-9]+/g, "-");
    const missing = a.required && !shown.trim();
    const refusedHere = refusedNames.has(norm(a.name));
    const ringCls = refusedHere ? "ring-2 ring-error/70"
      : missing ? "ring-2 ring-warning/60" : undefined;
    const badge = (
      <span className="inline-flex items-center gap-1.5">
        <ConfidenceMark row={row} />
        {refusedHere ? (
          <span className="text-[12px] font-semibold text-error">
            {saidByEbay ? "eBay refused this" : "Fix this to publish"}
          </span>
        ) : missing && (
          <span className="text-[12px] font-semibold text-warning">Required</span>
        )}
      </span>
    );
    return (
      <Field key={a.name} label={a.name} hint={badge}>
        {a.mode === "SELECTION_ONLY" && a.values?.length ? (
          <Select value={shown} className={ringCls}
            onChange={(e) => setValue(e.target.value)}>
            <option value="">— select —</option>
            {shown && !a.values.includes(shown) && (
              <option value={shown}>{shown}</option>
            )}
            {a.values.map((v) => <option key={v} value={v}>{v}</option>)}
          </Select>
        ) : (
          <>
            {/* eBay ships suggested values for a great many free-text
                aspects too. A datalist offers them without constraining:
                the box still takes anything, because here anything is
                legal. */}
            <Input
              value={shown}
              placeholder={a.name}
              className={ringCls}
              list={a.values?.length ? `sugg-${listId}` : undefined}
              onChange={(e) => setValue(e.target.value)}
            />
            {a.values?.length > 0 && (
              <datalist id={`sugg-${listId}`}>
                {a.values.map((v) => <option key={v} value={v} />)}
              </datalist>
            )}
          </>
        )}
        {extras.length > 0 && (
          <span className="flex flex-wrap items-center gap-1.5 mt-1.5">
            {extras.map((s) => (
              <span key={s.i}
                className="inline-flex items-center gap-1 rounded-full bg-bg-sunken border border-line px-2 py-0.5 text-[12px] font-semibold text-ink-secondary">
                {s.value}
                <button type="button" aria-label={`Remove ${a.name}: ${s.value}`}
                  onClick={() => removeRow(s.i)}
                  className="cursor-pointer text-ink-faint hover:text-error">
                  <X size={11} aria-hidden />
                </button>
              </span>
            ))}
          </span>
        )}
      </Field>
    );
  };

  const summary = aspects.length
    ? `${required.length - missingRequired} of ${required.length} required`
      + ` · ${recommendedFilled} of ${recommendedAll.length} recommended`
    : "";

  return (
    <Section
      id="specifics" title="Details"
      hint={aspects.length
        ? "What buyers filter by. Tap a chip to change it; the amber ones are required"
          + (w.isLive ? "" : " and block the publish until filled")
          + ". ✓ marks a value the AI read off the item, ⚠ one it inferred."
        : "Details buyers filter by — only the required ones gate publishing"}
      state={w.completion.specifics} flagged={w.fixTarget === "specifics"}
      expand={refused.length > 0}
    >
      <div className="flex flex-col gap-4">
        {/* What eBay said, where the chips are. */}
        {refused.length > 0 && (
          <div className="flex flex-col gap-2.5 rounded-input border border-error/45 bg-error-soft px-3.5 py-3">
            {refused.map((issue, i) => (
              <div key={`${issue.title}-${i}`} className="flex items-start gap-2.5">
                <AlertTriangle size={16} className="text-error shrink-0 mt-0.5" aria-hidden />
                <span className="text-[13px] text-ink flex-1 min-w-0">
                  <strong className="font-bold">{issue.title}</strong>
                  {issue.fix && <span className="block mt-0.5 text-ink-secondary">{issue.fix}</span>}
                </span>
              </div>
            ))}
          </div>
        )}
        {missingRequired > 0 && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-input border border-warning/40 bg-warning-soft px-3.5 py-2.5">
            <AlertTriangle size={16} className="text-warning shrink-0" aria-hidden />
            <span className="text-[13px] text-ink flex-1 min-w-0">
              <strong className="font-bold">
                {missingRequired} required {missingRequired === 1 ? "detail is" : "details are"} empty
              </strong>
              {w.isLive
                // A listing eBay is already showing: the update goes through
                // either way (a revise doesn't resend the category's aspect
                // list), so claiming it is blocked is simply untrue.
                ? " — your update still goes through; filling "
                  + (missingRequired === 1 ? "it" : "them")
                  + " puts the listing in more buyers' filters"
                : " — eBay won't accept the listing until "
                  + (missingRequired === 1 ? "it's filled in" : "they're filled in")}
            </span>
          </div>
        )}

        {aspects.length > 0 && (
          <div className="flex flex-col gap-3" data-chips>
            <p className="text-[12px] font-semibold text-ink-faint tabular-nums">{summary}</p>
            <div className="flex flex-wrap gap-2" role="list" aria-label="Item specifics">
              {chips.map((a) => (
                <span key={a.name} role="listitem" className="max-w-full">
                  <Chip
                    a={a}
                    value={valueOf(a)}
                    row={rowOf(a)}
                    refused={refusedNames.has(norm(a.name))}
                    saidByEbay={saidByEbay}
                    open={open === a.name}
                    onClick={() => setOpen(open === a.name ? null : a.name)}
                  />
                </span>
              ))}
              {hiddenCount > 0 && (
                <button
                  type="button"
                  onClick={() => setShowAll(true)}
                  className="inline-flex items-center gap-1 rounded-full px-3 h-9 text-[13px] font-semibold text-blue cursor-pointer hover:underline underline-offset-2"
                >
                  Show {hiddenCount} more <ChevronRight size={14} aria-hidden />
                </button>
              )}
            </div>
            {/* What a ghost chip MEANS: the fill has already read these
                photos against this whole list, so a chip still empty is one
                the photos could not settle — and the only thing that can
                fill it is the seller, who owns the item. */}
            {recommendedFilled < recommendedAll.length ? (
              <p className="text-[12px] text-ink-faint">
                The AI filled what your photos showed; the blanks are ones only you can answer.
              </p>
            ) : (
              <p className="text-[12px] text-ink-faint">Every detail is filled.</p>
            )}
            {openAspect && (
              <ChipEditor
                a={openAspect}
                hasNext={!!nextRequired(openAspect.name)}
                onNext={goNext}
                onClose={() => setOpen(null)}
              >
                {renderControl(openAspect)}
              </ChipEditor>
            )}
          </div>
        )}

        {unlisted.length > 0 && (
          <SpecGroup title="eBay asked for these" count={unlisted.length}>
            <div className="flex flex-col gap-2.5">
              <p className="text-[13px] text-ink-secondary">
                {saidByEbay
                  ? "eBay refused the listing over these and this category's field list doesn't carry them — eBay adds required specifics from time to time. Fill them in here."
                  : "These were named for this listing but aren't in the category's field list. Fill them in here."}
              </p>
              {unlisted.map((name) => (
                <Field key={name} label={name}>
                  <Input
                    value={w.getSpecific(name)}
                    placeholder={name}
                    className="ring-2 ring-error/70"
                    onChange={(e) => w.upsertSpecific(name, e.target.value)}
                  />
                </Field>
              ))}
            </div>
          </SpecGroup>
        )}

        {freeRows.length > 0 && (
          <SpecGroup title="Your own specifics" count={freeRows.length}>
            <div className="flex flex-col gap-2.5">
              {freeRows.map((s) => (
                <div key={s.i} className="flex gap-2.5 items-center">
                  <Input
                    value={s.name} placeholder="Name" className="flex-1"
                    aria-label="Specific name"
                    onChange={(e) => setRow(s.i, "name", e.target.value)}
                  />
                  <Input
                    value={s.value} placeholder="Value" className="flex-[1.4]"
                    aria-label="Specific value"
                    onChange={(e) => setRow(s.i, "value", e.target.value)}
                  />
                  <ConfidenceMark row={s} />
                  <Button variant="ghost" size="iconSm" aria-label="Remove specific"
                    onClick={() => removeRow(s.i)}>
                    <X size={15} />
                  </Button>
                </div>
              ))}
            </div>
          </SpecGroup>
        )}

        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 pt-0.5">
          {/* No AI button here, of any kind: the fill has already happened
              by the time this is on screen, and a chip still blank is one
              the photos could not answer. */}
          <Button
            variant="ghost" size="sm"
            onClick={() => w.set("item_specifics", [...w.form.item_specifics, { name: "", value: "" }])}
          >
            <Plus aria-hidden /> Add specific
          </Button>
          {/* The key, one quiet line — it explains the two marks a seller
              actually sees on the chips, and nothing else. */}
          {w.form.item_specifics.length > 0 && (
            <span className="text-[12px] text-ink-faint inline-flex items-center gap-3 flex-wrap">
              <span className="inline-flex items-center gap-1">
                <Check size={13} className="text-green" aria-hidden /> AI read it off the item
              </span>
              <span className="inline-flex items-center gap-1">
                <AlertTriangle size={13} className="text-warning" aria-hidden /> AI inferred it
              </span>
            </span>
          )}
        </div>
      </div>
    </Section>
  );
}
