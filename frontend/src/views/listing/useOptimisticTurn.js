import { useCallback, useEffect, useRef, useState } from "react";

/* useOptimisticTurn — the quarter-turn a photo shows the moment its rotate
 * button is tapped, held until the re-encoded file is the one on screen.
 *
 * Shared by the editor's PhotoTile and the listing card, which both show a
 * photo whose bytes the server rewrites in place on a rotate. Rotation
 * applies instantly: the <img> is turned with CSS ahead of the server, and
 * that turn holds until the rotated file has loaded, so there is no flash of
 * the old orientation in between.
 *
 * "Until the rotated file has loaded" is the whole trick, and it is not the
 * same moment as "until `version` changes". The version bumps the instant the
 * server answers, which only points the <img> at a new URL; the browser keeps
 * painting the OLD bytes until that fetch comes back. So the spin is tracked
 * against the version it was applied to and cleared by the load event of a
 * LATER one — never by the version prop on its own, which fires a frame after
 * the rotate lands and produced exactly that flash on every single rotate:
 * turned, snapped back, turned again.
 *
 * `version` is the cache-buster the <img> is currently asked for (its `?v=`);
 * `onRotate` asks the server for the turn and rejects if it did not happen.
 * Returns the degrees to show (`spin`), whether a rotate is in flight, the
 * tap handler, and `settle` — which goes on the <img>'s onLoad AND onError,
 * since a turn held over a broken image is meaningless and would otherwise
 * never be cleared.
 */

// The `?v=` an <img> actually painted, read off the element's own src. React
// state has by then moved on to the version being fetched, and asking it
// instead is the confusion this hook exists to avoid.
export function loadedVersion(src) {
  const m = /[?&]v=(\d+)/.exec(src || "");
  return m ? Number(m[1]) : null;
}

export function useOptimisticTurn({ version, onRotate }) {
  const [rotating, setRotating] = useState(false);
  // Degrees applied optimistically, ahead of the server.
  const [spin, setSpin] = useState(0);
  // The photo version those degrees sit ON TOP OF — i.e. the bytes the spin
  // is correcting. `settle` below compares the version that actually painted
  // against this one, which is what makes "the turn is in the file now" a
  // fact rather than a guess about timing.
  const spunOn = useRef(version);

  // Belt and braces: if the load event never arrives — a decode failure, the
  // new file 404ing, a load the browser coalesces away — the CSS turn still
  // has to come off, or the photo sits at 180 degrees for a 90 degree rotate.
  // Armed only while a spin is outstanding against a version that has already
  // been superseded, so in the normal case (onLoad settles it a few hundred
  // milliseconds later) this arms and clears without ever firing.
  useEffect(() => {
    if (!spin || version === spunOn.current) return undefined;
    const timer = setTimeout(() => {
      spunOn.current = version;
      setSpin(0);
    }, 8000);
    return () => clearTimeout(timer);
  }, [spin, version]);

  const rotate = useCallback(async () => {
    if (rotating || !onRotate) return;
    setRotating(true);
    // Restored on failure along with the degrees: a spin that is taken back
    // must leave the photo pointing at the same bytes it was before, or the
    // next load settles the wrong turn against the wrong file.
    const before = spunOn.current;
    spunOn.current = version;
    setSpin((deg) => deg + 90);
    try {
      await onRotate();
    } catch (e) {
      // The saved photo did not turn, so neither should the one on screen.
      // Only reachable because the callers rethrow after their toast.
      spunOn.current = before;
      setSpin((deg) => deg - 90);
    } finally {
      setRotating(false);
    }
  }, [rotating, onRotate, version]);

  // The re-encoded file is on screen, so the turn is in the pixels and the
  // CSS one comes off. Guarded on the version that ACTUALLY loaded: the
  // photo's first load, and any reload of the version the spin was applied
  // to, is still the un-turned photo, and clearing there is the flash this
  // is built to avoid.
  const settle = useCallback((e) => {
    const loaded = loadedVersion(e?.currentTarget?.src);
    if (loaded === null || loaded === spunOn.current) return;
    spunOn.current = loaded;
    setSpin(0);
  }, []);

  return { spin, rotating, rotate, settle };
}
