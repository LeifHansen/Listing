import { useCallback, useEffect, useRef, useState } from "react";

/**
 * The state of a control that saves itself the moment it changes.
 *
 * Settings used to have one "Save defaults" button under six sections, and
 * the honest answer to it was hard to give: half the sections could commit
 * and half fail. A switch that saves itself has one answer, and this hook
 * holds it: idle → saving → saved (for two seconds) → idle, or error with
 * the message and nothing else assumed.
 *
 * The sequence counter is what makes a fast second change safe: a slow
 * answer to the first change must not overwrite the status of the second.
 */
export function useAutosave(save) {
  const [status, setStatus] = useState({ kind: "idle" });
  const seq = useRef(0);

  const run = useCallback(async (...args) => {
    seq.current += 1;
    const n = seq.current;
    setStatus({ kind: "saving" });
    try {
      const result = await save(...args);
      if (seq.current === n) setStatus({ kind: "saved" });
      return result;
    } catch (e) {
      if (seq.current === n) {
        setStatus({ kind: "error", message: (e && e.message) || "something went wrong" });
      }
      throw e;
    }
  }, [save]);

  useEffect(() => {
    if (status.kind !== "saved") return undefined;
    const t = setTimeout(() => setStatus({ kind: "idle" }), 2000);
    return () => clearTimeout(t);
  }, [status]);

  return { status, run };
}
