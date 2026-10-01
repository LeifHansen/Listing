import { useCallback, useState } from "react";
import { CheckCircle2, MapPin } from "lucide-react";
import { useApp } from "@/store";
import { Field, Input, Select } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";
import { Skeleton } from "@/components/ui/Skeleton";
import { useSettings } from "./context";
import { useAutosave } from "./useAutosave";
import { SaveStatus } from "./SaveStatus";
import { PanelUnavailable } from "./PanelUnavailable";
import { CardSave, PrefsGate, PrefToggle } from "./PrefControls";

// The dispatch windows eBay offers on a US shipping policy. Mirrors
// ebay_auth.HANDLING_DAY_CHOICES; the server refuses anything else.
const HANDLING_DAYS = [0, 1, 2, 3, 4, 5, 10, 15, 20, 30];

const handlingLabel = (d) => (d === 0 ? "Same business day"
  : `${d} business day${d === 1 ? "" : "s"}`);

/** The eBay inventory location the account ships from, as one line. */
function locationLine(overview, key) {
  const locations = overview?.locations || [];
  const loc = locations.find((l) => l.merchantLocationKey === key) || locations[0];
  if (!loc) return "";
  const a = (loc.location && loc.location.address) || {};
  return [a.city, a.stateOrProvince, a.postalCode].filter(Boolean).join(", ");
}

/**
 * Where listings ship from, which shipping policy they publish under, how
 * fast the seller promises to send, and whether they go abroad.
 *
 * The policy and the handling time live on eBay (the policy IS the promise
 * eBay scores), so the select and the handling-time control write there;
 * the international switch is this app's, read at publish.
 */
export function ShippingSection() {
  const { ebay } = useApp();
  const {
    data, loading, loadedHere, policies, loadPolicies,
    savePolicy, saveZip, saveHandlingTime, overview,
  } = useSettings();

  if (!ebay.connected) {
    return (
      <div className="flex flex-col gap-5">
        <p className="text-sm text-ink-secondary">
          Connect your eBay account first — your ship-from location and shipping
          policy come from there.
        </p>
        <PrefsGate>
          <InternationalToggle />
        </PrefsGate>
      </div>
    );
  }
  if (!loading && policies.kind === "unavailable" && !data) {
    return <PanelUnavailable message={policies.message} onRetry={loadPolicies} />;
  }
  if (loading || !data) return <Skeleton className="h-32 rounded-tile" />;

  const selected = data.selected || {};
  const fulfillment = data.policies?.fulfillment || [];
  const current = fulfillment.find((p) => p.id === selected.fulfillment_policy_id);

  return (
    <div className="flex flex-col gap-6 max-w-lg">
      <ZipField
        key={data.ship_from_postal || ""}
        initial={data.ship_from_postal || ""}
        locationSet={Boolean(data.location_set)}
        locationText={locationLine(overview, selected.merchant_location_key)}
        onSave={saveZip}
        enabled={loadedHere}
      />

      <PolicySelect
        field="fulfillment_policy_id"
        label="Shipping policy"
        options={fulfillment}
        value={selected.fulfillment_policy_id || ""}
        unavailable={policies.kind === "unavailable"}
        note="Applied to every new listing. It sets the carrier service — a listing can override it on its own Shipping card."
        onSave={savePolicy}
      />

      {current ? (
        <HandlingTime key={current.id} policy={current} onSave={saveHandlingTime} />
      ) : (
        <p className="text-xs text-ink-secondary -mt-3">
          Pick a shipping policy to set handling time.
        </p>
      )}

      {policies.kind === "unavailable" && (
        <PanelUnavailable message={policies.message} onRetry={loadPolicies} />
      )}

      <div className="pt-5 border-t border-line">
        <PrefsGate>
          <InternationalToggle />
        </PrefsGate>
      </div>
    </div>
  );
}

function InternationalToggle() {
  return (
    <PrefToggle
      k="ebay_international_shipping"
      label="Use eBay International Shipping on new listings"
      note="Every sale goes to eBay’s US shipping hub with a domestic label; eBay carries it abroad and the buyer pays that leg. You need to be enrolled in the program on eBay. Listings already live are left as they are."
    />
  );
}

function ZipField({ initial, locationSet, locationText, onSave, enabled }) {
  const [postal, setPostal] = useState(initial);
  const save = useCallback(() => onSave(postal), [onSave, postal]);
  return (
    <Field
      label={
        <span className="inline-flex items-center gap-1.5">
          <MapPin size={14} aria-hidden /> Ship-from ZIP code
        </span>
      }
      note={locationSet
        ? "Where eBay tells buyers the item ships from. Change the ZIP and save to move it."
        : "Required to publish — eBay needs a location to ship from. Enter your ZIP and save; we create it on eBay for you."}
    >
      <div className="flex flex-wrap items-center gap-3">
        <Input
          inputMode="numeric" placeholder="e.g. 90210" className="max-w-44"
          value={postal}
          onChange={(e) => setPostal(e.target.value)}
        />
        <CardSave label="Save ZIP" onSave={save} disabled={!enabled} />
      </div>
      {locationSet && (
        <TagPill tone="green" className="self-start mt-1">
          <CheckCircle2 size={12} aria-hidden />
          {locationText ? `Ships from ${locationText}` : "eBay ship-from location is set"}
        </TagPill>
      )}
    </Field>
  );
}

/** A default-policy select that saves itself. Shared with Returns & payment. */
export function PolicySelect({ field, label, options, value, unavailable, note, terms, onSave }) {
  const save = useCallback((id) => onSave(field, id), [field, onSave]);
  const { status, run } = useAutosave(save);
  const [last, setLast] = useState(null);
  const change = (id) => { setLast(id); run(id).catch(() => {}); };
  // "None on eBay yet" is a claim about the seller's account, so it is only
  // made when eBay answered. A failed load says so instead.
  const empty = unavailable
    ? `We couldn’t check your ${label.toLowerCase()} just now.`
    : `No ${label.toLowerCase()} on eBay yet.`;
  return (
    <Field label={label} note={options.length ? note : empty}>
      <Select
        value={value}
        disabled={status.kind === "saving"}
        onChange={(e) => change(e.target.value)}
      >
        <option value="">— none —</option>
        {options.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}{p.summary ? ` · ${p.summary}` : ""}
          </option>
        ))}
      </Select>
      {terms && <span className="text-xs text-ink font-medium">{terms}</span>}
      <SaveStatus status={status} onRetry={() => last !== null && change(last)} />
    </Field>
  );
}

function HandlingTime({ policy, onSave }) {
  const { status, run } = useAutosave(onSave);
  const [last, setLast] = useState(null);
  const change = (days) => { setLast(days); run(days).catch(() => {}); };
  const known = policy.handling_days != null;
  return (
    <Field
      label="Handling time"
      note="How fast you promise to ship after payment. eBay measures every listing under this policy against it — live ones too — and rewards 1 business day in search."
    >
      <Select
        value={known ? String(policy.handling_days) : ""}
        disabled={status.kind === "saving"}
        onChange={(e) => change(Number(e.target.value))}
        aria-label="Handling time"
      >
        {!known && <option value="">Not readable from this policy</option>}
        {HANDLING_DAYS.map((d) => (
          <option key={d} value={String(d)}>{handlingLabel(d)}</option>
        ))}
      </Select>
      <SaveStatus status={status} onRetry={() => last !== null && change(last)} />
    </Field>
  );
}
