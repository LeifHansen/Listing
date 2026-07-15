import { useState } from "react";
import { motion } from "framer-motion";
import { AlertTriangle, Check, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/fields";

/* The AI's "please verify / fill in" list, made actionable: answer each blank
   right here and one click routes every answer into the correct real field
   (item specifics, dimensions, condition description, …) via the AI refine —
   no scrolling around the form hunting for where each blank lives. */

export function MissingInfo({ w }) {
  // De-dupe so two identical questions can't share a React key or an answer.
  const items = [...new Set(w.form.missing_info || [])];
  // Answers are keyed by the QUESTION TEXT, not the array index — dismissing a
  // question used to shift every later index down while answers stayed put, so
  // typed answers got attached to the wrong question (silent data corruption).
  const [answers, setAnswers] = useState({});

  if (!items.length) return null;

  const dismiss = (q) => {
    w.set("missing_info", items.filter((x) => x !== q));
    setAnswers((a) => {
      const next = { ...a };
      delete next[q];
      return next;
    });
  };

  const answered = items
    .map((q) => ({ q, a: (answers[q] || "").trim() }))
    .filter((x) => x.a);

  const apply = async () => {
    if (!answered.length) return;
    const prompt =
      "The seller answered these outstanding questions about the item. " +
      "Update the correct listing fields and item specifics with each answer " +
      "(don't lose any other data), and remove the answered questions from " +
      "missing_info:\n" +
      answered.map((x) => `- ${x.q}: ${x.a}`).join("\n");
    const ok = await w.refine(prompt);
    if (ok) setAnswers({});
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-card bg-warning-soft border border-warning/30 p-5"
    >
      <p className="font-bold text-sm text-ink flex items-center gap-2">
        <AlertTriangle size={17} className="text-warning" aria-hidden />
        The AI wasn't sure about a few things — answer here and it fills in the right fields
      </p>
      <ul className="mt-4 flex flex-col gap-2.5">
        {items.map((q) => (
          <li key={q} className="flex flex-col sm:flex-row sm:items-center gap-2">
            <span className="text-sm text-ink sm:w-2/5 min-w-0">{q}</span>
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <Input
                value={answers[q] || ""}
                placeholder="Type the answer…"
                className="h-10 bg-card/70"
                onChange={(e) => setAnswers((a) => ({ ...a, [q]: e.target.value }))}
                onKeyDown={(e) => { if (e.key === "Enter") apply(); }}
              />
              <Button
                variant="ghost" size="iconSm" className="shrink-0"
                aria-label={`Dismiss "${q}" — already correct`}
                title="Already correct / not applicable"
                onClick={() => dismiss(q)}
              >
                <X size={15} />
              </Button>
            </div>
          </li>
        ))}
      </ul>
      <div className="mt-4 flex items-center gap-3">
        <Button variant="primary" size="md" onClick={apply} disabled={!answered.length}>
          <Sparkles aria-hidden /> Apply answer{answered.length === 1 ? "" : "s"}
          {answered.length > 0 && ` (${answered.length})`}
        </Button>
        <span className="text-xs text-ink-secondary hidden sm:inline-flex items-center gap-1">
          <Check size={12} aria-hidden /> the ✕ marks one as already correct
        </span>
      </div>
    </motion.div>
  );
}
