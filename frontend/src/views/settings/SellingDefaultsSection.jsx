import { useCallback, useState } from "react";
import { Handshake, Megaphone, PackageOpen, TrendingUp } from "lucide-react";
import { CONDITIONS, conditionLabel } from "@/lib/conditions";
import { SectionHeader } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/fields";
import { cn } from "@/lib/utils";
import { useSettings } from "./context";
import { CardSave, PrefsGate, PrefSelect, PrefToggle } from "./PrefControls";

/**
 * What every NEW listing starts with: whether it takes offers (and at what
 * limits), whether it is promoted (and at what rate), where the AI prices
 * it, and the condition and quantity it is drafted with. None of these has
 * an account-level home on eBay, which is why they live in this app.
 */
export function SellingDefaultsSection() {
  return (
    <div className="flex flex-col gap-7">
      <div>
        <SectionHeader icon={Handshake} title="Offers" />
        <PrefsGate height="h-16">
          <BestOfferCard />
        </PrefsGate>
      </div>
      <div className="pt-7 border-t border-line">
        <SectionHeader icon={Megaphone} title="Promoted Listings" />
        <PrefsGate height="h-16">
          <PromotedListingsCard />
        </PrefsGate>
      </div>
      <div className="pt-7 border-t border-line">
        <SectionHeader icon={TrendingUp} title="Pricing strategy" />
        <PrefsGate height="h-16">
          <PricingStrategyPicker />
        </PrefsGate>
      </div>
      <div className="pt-7 border-t border-line">
        <SectionHeader icon={PackageOpen} title="Listing defaults" />
        <PrefsGate height="h-24">
          <ListingDefaultsFields />
        </PrefsGate>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- offers

const pct = (v) => {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : 0;
};

/** The limits, in the seller's own money on a $50 item. */
function limitsExample(accept, decline) {
  const parts = [];
  if (accept) parts.push(`$${(50 * accept / 100).toFixed(2)} and up is accepted for you`);
  if (decline) parts.push(`under $${(50 * decline / 100).toFixed(2)} is declined for you`);
  if (!parts.length) return "There’s no minimum — every offer reaches you to accept, counter or decline.";
  return `On a $50 item: ${parts.join("; ")}; the rest come to you.`;
}

function BestOfferCard() {
  const { prefs, savePrefs } = useSettings();
  const on = Boolean(prefs.allow_offers);
  const [accept, setAccept] = useState(prefs.best_offer_auto_accept_pct || "");
  const [decline, setDecline] = useState(prefs.best_offer_auto_decline_pct || "");
  const a = pct(accept);
  const d = pct(decline);
  const inverted = a > 0 && d > 0 && d >= a;
  const outOfRange = a > 100 || d > 100;
  const problem = inverted
    ? "Auto-decline has to be below auto-accept — otherwise every offer would be declined before you saw it."
    : outOfRange ? "A limit is a percentage of the asking price, so 100 is the most it can be." : "";
  const save = useCallback(() => savePrefs({
    best_offer_auto_accept_pct: a, best_offer_auto_decline_pct: d,
  }), [savePrefs, a, d]);

  return (
    <div className="flex flex-col gap-5 max-w-lg">
      <PrefToggle
        k="allow_offers"
        label="Allow offers on new listings"
        help="There’s no minimum unless you set one below: eBay passes every offer to you to accept, counter, or decline. Auctions don’t take offers."
        note="Buyers can send an offer on every new fixed-price listing. Listings already live, and auctions, are left as they are."
      />
      {on && (
        <div className="flex flex-col gap-4 pl-14">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Auto-accept at" hint="% of price">
              <Input
                type="number" min="1" max="100" step="1" inputMode="numeric"
                placeholder="e.g. 90" value={accept}
                aria-label="Auto-accept offers at this percentage of the price"
                onChange={(e) => setAccept(e.target.value)}
              />
            </Field>
            <Field label="Auto-decline below" hint="% of price">
              <Input
                type="number" min="1" max="100" step="1" inputMode="numeric"
                placeholder="e.g. 60" value={decline}
                aria-label="Auto-decline offers below this percentage of the price"
                onChange={(e) => setDecline(e.target.value)}
              />
            </Field>
          </div>
          <p className={cn("text-xs leading-relaxed", problem ? "text-error font-medium" : "text-ink-secondary")}>
            {problem || limitsExample(a, d)}
            {!problem && (a || d) ? " Set at publish from each listing’s price; a later price edit on eBay doesn’t move them." : ""}
          </p>
          <CardSave label="Save offer limits" onSave={save} disabled={Boolean(problem)} />
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------ promoted listings

function PromotedListingsCard() {
  const { prefs, savePrefs } = useSettings();
  const on = Boolean(prefs.auto_promote);
  const [rate, setRate] = useState(prefs.auto_promote_rate || "");
  const r = pct(rate);
  const problem = r > 100 ? "An ad rate is a percentage of the sale price, so 100 is the most it can be."
    : (r > 0 && r < 2) ? "eBay’s lowest ad rate is 2%." : "";
  const save = useCallback(() => savePrefs({ auto_promote_rate: r }), [savePrefs, r]);
  return (
    <div className="flex flex-col gap-5 max-w-lg">
      <PrefToggle
        k="auto_promote"
        label="Auto-promote new listings"
        note="Each new listing is promoted the moment it publishes. eBay charges the ad rate only when the item sells through the ad."
      />
      {on && (
        <div className="flex flex-col gap-4 pl-14">
          <Field
            label="Ad rate" hint="% of sale price"
            note="Blank uses eBay’s suggested rate for each listing; when eBay suggests none, the listing stays unpromoted rather than billed at a rate you never saw."
          >
            <Input
              type="number" min="2" max="100" step="0.1" inputMode="decimal"
              placeholder="eBay’s suggestion" value={rate} className="max-w-44"
              onChange={(e) => setRate(e.target.value)}
            />
          </Field>
          {problem && <p className="text-xs text-error font-medium">{problem}</p>}
          <CardSave label="Save ad rate" onSave={save} disabled={Boolean(problem)} />
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------- pricing strategy

// Where on the market range the AI prices every draft and comp suggestion.
export const PRICING_STRATEGIES = [
  { value: "quick_flip", label: "Quick Flip", blurb: "Priced at the low end of comps to sell fast." },
  { value: "median", label: "Median Pricing", blurb: "Typical middle-of-market price." },
  { value: "long_sale", label: "Long Sale", blurb: "Priced at the high end — patient, maximizes the sale." },
];

function PricingStrategyPicker() {
  const { prefs, savePref } = useSettings();
  const save = useCallback((value) => savePref("pricing_strategy", value), [savePref]);
  // The segmented control saves on tap; the status line under it answers.
  const [status, setStatus] = useState({ kind: "idle" });
  const current = PRICING_STRATEGIES.find((s) => s.value === prefs.pricing_strategy);
  const pick = async (value) => {
    setStatus({ kind: "saving" });
    try {
      await save(value);
      setStatus({ kind: "saved" });
      setTimeout(() => setStatus((s) => (s.kind === "saved" ? { kind: "idle" } : s)), 2000);
    } catch (e) {
      setStatus({ kind: "error", message: e.message || "something went wrong" });
    }
  };
  return (
    <div className="flex flex-col gap-2 max-w-lg">
      <div role="radiogroup" aria-label="Pricing strategy"
           className="grid grid-cols-3 gap-1 p-1 rounded-button bg-bg-sunken">
        {PRICING_STRATEGIES.map((s) => {
          const active = current?.value === s.value;
          return (
            <button
              key={s.value} type="button" role="radio" aria-checked={active}
              disabled={status.kind === "saving"}
              onClick={() => pick(s.value)}
              className={cn(
                "h-10 rounded-[12px] text-[13px] font-semibold cursor-pointer transition-colors duration-150",
                active ? "bg-card text-ink shadow-card" : "text-ink-secondary hover:text-ink",
              )}
            >
              {s.label}
            </button>
          );
        })}
      </div>
      <p className="text-xs text-ink-secondary leading-relaxed">
        {current ? current.blurb : "Pick one — until you do, drafts get no pricing hint."}
      </p>
      <span role="status" aria-live="polite" className="text-xs min-h-4">
        {status.kind === "saving" && <span className="text-ink-faint">Saving…</span>}
        {status.kind === "saved" && <span className="text-green font-semibold">Saved</span>}
        {status.kind === "error" && <span className="text-error">Couldn’t save — {status.message}</span>}
      </span>
    </div>
  );
}

// ------------------------------------------------------ listing defaults

function ListingDefaultsFields() {
  const { prefs, savePrefs } = useSettings();
  const [quantity, setQuantity] = useState(prefs.quantity || "");
  const save = useCallback(() => savePrefs({ quantity: Number(quantity) || 0 }), [savePrefs, quantity]);
  return (
    <div className="flex flex-col gap-5 max-w-lg">
      <PrefSelect
        k="condition"
        label="Condition"
        note="Set one to always use it; otherwise the AI judges from the photos."
      >
        <option value="">Let the AI decide (from photos)</option>
        {CONDITIONS.map((c) => (
          <option key={c} value={c}>{conditionLabel(c)}</option>
        ))}
      </PrefSelect>
      <Field label="Quantity" note="Only applied when listing more than one of an item. Package weight and size are set per listing on its Shipping card.">
        <div className="flex flex-wrap items-center gap-3">
          <Input
            type="number" min="1" step="1" inputMode="numeric" className="max-w-32"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
          />
          <CardSave label="Save quantity" onSave={save} />
        </div>
      </Field>
    </div>
  );
}
