/* The condition, asked the way eBay asks it.

   For most of eBay it is one question -- New, Used, For parts -- and this
   is the dropdown it always was, offering what the category takes. For a
   trading card it is two: "Graded or Ungraded?" first, and then either the
   grading service + grade (+ certification number) or the card's condition
   on eBay's ladder (Near Mint or Better / Excellent / Very Good / Poor, or
   the CCG played-ness wording). eBay refuses a card that stops at the first
   answer, so the second step appears the moment a condition that carries
   one is picked -- with eBay's own labels, eBay's own ladder and eBay's own
   ids (lib/conditions explains where they come from).

   Renders a FRAGMENT: the parent supplies the grid, so the same control sits
   beside the price on a bulk card and in the editor's condition row. Step 1
   is one cell; each second-step answer is another. */
import { Field, Input, Select } from "@/components/ui/fields";
import { cn } from "@/lib/utils";
import {
  CONDITIONS, conditionLabel, descriptorProblems, descriptorsFor, fitDescriptors,
  hasDescriptors, withDescriptor,
} from "@/lib/conditions";

/* The step-1 options: what the category offers once we know, the generic
   list until then -- plus whatever the listing is currently set to, always.
   A controlled <select> whose value isn't among its options renders BLANK,
   and a condition that looks unset is how a seller "fixes" a field that was
   already right. */
export function conditionOptions(conditions, current) {
  const options = (conditions && conditions.length)
    ? conditions.map((c) => ({ value: c.enum, label: c.label || conditionLabel(c.enum) }))
    : CONDITIONS.map((c) => ({ value: c, label: conditionLabel(c) }));
  return options.some((o) => o.value === current) || !current
    ? options
    : [{ value: current, label: conditionLabel(current) }, ...options];
}

// A label above a control in the editor; a bare control with an aria-label
// (and a placeholder that names it) on a bulk card, where labels don't fit.
function Cell({ labels, label, hint, help, className, children }) {
  if (labels) {
    return (
      <Field label={label} hint={hint} help={help} className={className}>
        {children}
      </Field>
    );
  }
  return <div className={cn("min-w-0", className)}>{children}</div>;
}

/**
 * @param conditions  eBay's condition list for the category
 *                    ([{enum, label, descriptors}]), or null/[] when unknown.
 * @param checked     false when the lookup could not run (help text says so).
 * @param condition   the listing's condition enum.
 * @param descriptors the listing's condition_descriptors.
 * @param onChange    ({condition, condition_descriptors}) => void -- always
 *                    both, because changing the first answer changes which
 *                    second answers still apply.
 * @param fixLevel    undefined | "warn" | "true" -- the ring the blocked
 *                    control(s) get. Only the control that is actually wrong
 *                    rings: the step-1 select when the condition is missing
 *                    or refused, a second-step control when its answer is.
 * @param labels      true for the editor (Field labels), false for a card.
 */
export function ConditionPicker({
  conditions, checked = true, condition, descriptors, onChange, fixLevel, labels = true,
  className, cellClassName,
}) {
  const list = Array.isArray(conditions) ? conditions : [];
  const current = String(condition || "");
  const options = conditionOptions(list, current);
  const meta = descriptorsFor(list, current);
  const fitted = fitDescriptors(descriptors, meta);
  const problems = descriptorProblems(fitted, meta);
  const twoStep = hasDescriptors(list);

  const conditionOk = !!current && (!list.length || list.some((c) => c.enum === current));
  // A ring with nothing specific to hang it on (eBay named "condition" in a
  // refusal) lands on step 1.
  const step1Fix = fixLevel && (!conditionOk || !problems.length) ? fixLevel : undefined;
  const fixFor = (d) => (fixLevel && problems.some((p) => p.descriptor.id === d.id)
    ? fixLevel : undefined);

  const pickCondition = (next) => onChange({
    condition: next,
    condition_descriptors: fitDescriptors(fitted, descriptorsFor(list, next)),
  });
  const answer = (d, value) => onChange({
    condition: current,
    condition_descriptors: withDescriptor(fitted, meta, d, value),
  });
  const valueOf = (d) => {
    const hit = fitted.find((x) => x.id === d.id);
    return d.free_text ? (hit?.text || "") : (hit?.values?.[0] || "");
  };

  // "Graded or Ungraded?" -- the first question, in eBay's words for the
  // category, when there is a second.
  const question = twoStep && list.length <= 3
    ? list.map((c) => c.label || conditionLabel(c.enum)).join(" or ") + "?"
    : undefined;
  const help = checked === false
    ? "We couldn’t check which conditions eBay allows in this category, so these are the general ones."
    : undefined;

  return (
    <>
      <Cell labels={labels} label="Condition" hint={question && `(${question})`}
        help={help} className={cn(className, cellClassName)}>
        <Select
          aria-label={labels ? undefined : (question ? `Condition — ${question}` : "Condition")}
          title={labels ? undefined : help}
          value={current}
          needsFix={step1Fix}
          onChange={(e) => pickCondition(e.target.value)}
        >
          {options.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </Select>
      </Cell>
      {meta.map((d) => (
        <Cell key={d.id} labels={labels} label={d.name}
          hint={labels && !d.required ? "(optional)" : undefined}
          className={cn(className, cellClassName, d.free_text && "col-span-2")}>
          {d.free_text ? (
            <Input
              aria-label={labels ? undefined : d.name}
              placeholder={d.required ? d.name : `${d.name} (optional)`}
              maxLength={d.max_length || undefined}
              value={valueOf(d)}
              needsFix={fixFor(d)}
              onChange={(e) => answer(d, e.target.value)}
            />
          ) : (
            <Select
              aria-label={labels ? undefined : d.name}
              value={valueOf(d)}
              needsFix={fixFor(d)}
              onChange={(e) => answer(d, e.target.value)}
            >
              <option value="">{labels ? "Choose…" : `${d.name}…`}</option>
              {(d.values || []).map((v) => (
                <option key={v.id} value={v.id}>{v.name}</option>
              ))}
            </Select>
          )}
        </Cell>
      ))}
    </>
  );
}
