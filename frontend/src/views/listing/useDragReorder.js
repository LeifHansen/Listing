import { useCallback, useEffect, useRef, useState } from "react";

/* useDragReorder — press a tile and carry it to a new place in a grid.
 *
 * This grid is three-across squares of ~110px on a phone, where the drag
 * gesture, the page scroll and the taps all share the same few pixels. Two
 * earlier attempts (a pointer implementation and @dnd-kit) lost to that and
 * were replaced by buttons; the buttons then covered the photos. What makes
 * this one hold:
 *
 *  - On touch, nothing lifts until the finger has RESTED for HOLD_MS. A
 *    finger that moves before then is scrolling, and the gesture is handed
 *    back to the page untouched. Only once a tile is lifted is the page's
 *    scroll held off, with a non-passive touchmove listener installed at the
 *    press so it is in place before the first move. (`touch-action` cannot
 *    do this: it has to be set before the gesture starts, so it would have
 *    to be `none` always and the grid would never scroll.)
 *  - With a mouse or a pen there is no hold; the tile lifts as soon as the
 *    pointer has moved SLOP pixels, and a smaller movement is a click.
 *  - The slots never move, only their contents, so which slot the pointer is
 *    over is read off the geometry captured at the lift, not by hit-testing
 *    tiles that are mid-animation.
 *  - The pointer is captured by the pressed tile, so the drag survives
 *    leaving the grid and ends on the release wherever that happens.
 *
 * Arrow keys move a focused tile one place, Home/End to either end: the
 * grid has no visible reorder controls, so this is the keyboard's way.
 *
 * `onMove(from, to)` is called once, on the drop or the key, with slot
 * indices. `drag`, while a tile is lifted, is `{ from, over, rect, dx, dy }`:
 * the slot it came from, the slot it is over, where it sat on screen at the
 * lift and how far it has been carried since — enough to draw the ghost and
 * preview the order. `tileProps(index)` goes on each tile; `wasDrag()` is
 * true for the click that ends a drag, so a tap-to-open handler can ignore it.
 */

export const SLOP = 8;       // px: more than this before a hold is a scroll
export const HOLD_MS = 220;  // touch: how long a finger rests before a lift

const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

export function useDragReorder({ count, onMove }) {
  const [drag, setDrag] = useState(null);
  const g = useRef(null);          // the gesture in progress, if any
  const tiles = useRef(new Map()); // slot index -> element
  const lastWasDrag = useRef(false);

  const onMoveRef = useRef(onMove);
  useEffect(() => { onMoveRef.current = onMove; });

  const registerTile = useCallback((index) => (el) => {
    if (el) tiles.current.set(index, el);
    else tiles.current.delete(index);
  }, []);

  const release = useCallback(() => {
    const cur = g.current;
    if (!cur) return;
    clearTimeout(cur.timer);
    document.removeEventListener("touchmove", cur.block);
    try { cur.el.releasePointerCapture?.(cur.id); } catch { /* already gone */ }
    g.current = null;
    setDrag(null);
  }, []);

  // A press that ends with the page (or the tab) going away must not leave a
  // tile lifted, nor the scroll blocker installed.
  useEffect(() => release, [release]);

  const slotAt = (cur, p) => {
    let best = cur.from;
    let bestD = Infinity;
    for (const [i, r] of cur.rects) {
      if (p.x >= r.left && p.x <= r.right && p.y >= r.top && p.y <= r.bottom) return i;
      const d = dist(p, { x: (r.left + r.right) / 2, y: (r.top + r.bottom) / 2 });
      if (d < bestD) { bestD = d; best = i; }
    }
    return best;
  };

  const lift = (cur) => {
    cur.lifted = true;
    lastWasDrag.current = true;
    cur.rects = new Map();
    for (const [i, el] of tiles.current) cur.rects.set(i, el.getBoundingClientRect());
    try { cur.el.setPointerCapture?.(cur.id); } catch { /* jsdom, or the pointer went away */ }
    setDrag({
      from: cur.from, over: cur.from,
      rect: cur.rects.get(cur.from), dx: 0, dy: 0,
    });
  };

  const tileProps = useCallback((index) => ({
    ref: registerTile(index),
    onPointerDown: (e) => {
      if (count < 2) return;
      if (e.button !== 0 || g.current) return;
      // The bin is its own control; a press on it is not the start of a drag.
      if (e.target.closest?.("button[data-no-drag]")) return;
      lastWasDrag.current = false;
      const cur = {
        id: e.pointerId, el: e.currentTarget, from: index,
        start: { x: e.clientX, y: e.clientY }, lifted: false, timer: null,
        // Installed now, non-passive, so it exists before the first move
        // after a lift. It only acts once the tile is up.
        block: (te) => { if (g.current?.lifted) te.preventDefault(); },
      };
      document.addEventListener("touchmove", cur.block, { passive: false });
      g.current = cur;
      if (e.pointerType === "touch") {
        cur.timer = setTimeout(() => { if (g.current === cur) lift(cur); }, HOLD_MS);
      }
    },
    onPointerMove: (e) => {
      const cur = g.current;
      if (!cur || e.pointerId !== cur.id) return;
      const p = { x: e.clientX, y: e.clientY };
      if (!cur.lifted) {
        if (dist(p, cur.start) < SLOP) return;
        // Moved before the hold: a touch is scrolling, a mouse is dragging.
        if (e.pointerType === "touch") { release(); return; }
        lift(cur);
      }
      const over = slotAt(cur, p);
      setDrag((d) => (d && {
        ...d, over, dx: p.x - cur.start.x, dy: p.y - cur.start.y,
      }));
    },
    onPointerUp: (e) => {
      const cur = g.current;
      if (!cur || e.pointerId !== cur.id) return;
      if (cur.lifted) {
        const over = slotAt(cur, { x: e.clientX, y: e.clientY });
        if (over !== cur.from) onMoveRef.current(cur.from, over);
      }
      release();
    },
    onPointerCancel: (e) => {
      if (g.current && e.pointerId === g.current.id) release();
    },
    // Android raises a context menu on a long press; while a press is in
    // progress that menu is not what the finger meant.
    onContextMenu: (e) => { if (g.current) e.preventDefault(); },
    onKeyDown: (e) => {
      if (count < 2) return;
      const to = {
        ArrowLeft: index - 1, ArrowUp: index - 1,
        ArrowRight: index + 1, ArrowDown: index + 1,
        Home: 0, End: count - 1,
      }[e.key];
      if (to === undefined) return;
      e.preventDefault();
      if (to >= 0 && to < count && to !== index) onMoveRef.current(index, to);
    },
  }), [count, registerTile, release]);

  const wasDrag = useCallback(() => lastWasDrag.current, []);

  return { drag, tileProps, wasDrag };
}

/** `items` with the one at `from` carried to `to`. */
export function moved(items, from, to) {
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) {
    return items;
  }
  const next = [...items];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}
