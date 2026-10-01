import { useCallback, useState } from "react";
import { Field, Select, Toggle } from "@/components/ui/fields";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { useSettings } from "./context";
import { useAutosave } from "./useAutosave";
import { SaveStatus } from "./SaveStatus";
import { DEFAULTS_UNAVAILABLE, PanelUnavailable } from "./PanelUnavailable";

/**
 * The controls for the account's new-listing defaults (`prefs`).
 *
 * Every one of them sits behind PrefsGate, which is the three-state rule
 * of this screen: a read still loading shows a shimmer, a read that failed
 * shows the warning and a retry, and ONLY an answered read shows controls.
 * A switch rendered after a failed read is a statement about the account
 * made on the strength of having failed to find out.
 */
export function PrefsGate({ height = "h-10", children }) {
  const { prefs, prefsError, loadPrefs } = useSettings();
  if (prefsError) {
    return <PanelUnavailable message={DEFAULTS_UNAVAILABLE} onRetry={loadPrefs} />;
  }
  if (prefs === null) return <Skeleton className={`${height} rounded-tile`} />;
  return children;
}

/** A switch that saves itself. Off is sent as a real 0: the server merges,
 * and a key dropped instead of sent would leave the last "on" standing. */
export function PrefToggle({ k, label, note, help }) {
  const { prefs, savePref } = useSettings();
  const save = useCallback((on) => savePref(k, on ? 1 : 0), [k, savePref]);
  const { status, run } = useAutosave(save);
  // Optimistic while the save is in flight; a failure snaps back to the
  // saved value, with the reason beside the switch.
  const [pending, setPending] = useState(null);
  const checked = pending ?? Boolean(prefs?.[k]);
  const change = (on) => {
    setPending(on);
    run(on).catch(() => {}).finally(() => setPending(null));
  };
  return (
    <div className="flex flex-col gap-1.5">
      <Toggle
        checked={checked}
        onChange={change}
        label={label}
        note={note}
        help={help}
        disabled={status.kind === "saving"}
      />
      <SaveStatus status={status} onRetry={() => change(checked)} className="pl-14" />
    </div>
  );
}

/** A select that saves itself. Not optimistic: it is disabled while the
 * answer is on its way, which a select reads naturally. */
export function PrefSelect({ k, label, note, children, parse = (v) => v }) {
  const { prefs, savePref } = useSettings();
  const save = useCallback((value) => savePref(k, value), [k, savePref]);
  const { status, run } = useAutosave(save);
  const [last, setLast] = useState(null);
  const change = (value) => {
    setLast(value);
    run(parse(value)).catch(() => {});
  };
  return (
    <Field label={label} note={note}>
      <Select
        value={prefs?.[k] ?? ""}
        disabled={status.kind === "saving"}
        onChange={(e) => change(e.target.value)}
      >
        {children}
      </Select>
      <SaveStatus status={status} onRetry={() => last !== null && change(last)} />
    </Field>
  );
}

/**
 * The Save button for a card of typed fields. `onSave` returns the promise;
 * the status beside the button says how it went. Named by card ("Save offer
 * limits") rather than a bare "Save", so the smoke test can tell them apart
 * and prove none is offered for defaults that could not be read.
 */
export function CardSave({ label, onSave, disabled, className }) {
  const { status, run } = useAutosave(onSave);
  return (
    <div className={`flex flex-wrap items-center gap-3 ${className || ""}`}>
      <Button
        variant="primary"
        size="sm"
        disabled={disabled}
        loading={status.kind === "saving"}
        onClick={() => run().catch(() => {})}
      >
        {label}
      </Button>
      <SaveStatus status={status} />
    </div>
  );
}
