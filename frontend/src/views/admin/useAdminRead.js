import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

// One GET as a read-state object: { kind: "loading" | "unavailable" |
// "ready", data, reload }. The loading state is DERIVED (res.key vs path)
// rather than set in the effect, which keeps the compiler's
// set-state-in-effect rule happy and gives the reload path keep-previous-
// value semantics for free: after an action, the old numbers stay on screen
// until the fresh ones land, instead of flashing a skeleton.
export function useAdminRead(path) {
  const [res, setRes] = useState({ key: null, kind: "loading", data: null });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let alive = true;
    api(path)
      .then((data) => { if (alive) setRes({ key: path, kind: "ready", data }); })
      .catch(() => { if (alive) setRes({ key: path, kind: "unavailable", data: null }); });
    return () => { alive = false; };
  }, [path, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const current = res.key === path ? res : { kind: "loading", data: null };
  return { kind: current.kind, data: current.data, reload };
}
