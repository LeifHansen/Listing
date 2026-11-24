/* The device queue: the photos an upload asked to have their backgrounds
   taken off ON THIS DEVICE, worked through one at a time.

   The server's photo pass keeps each such photo as shot and writes it down
   (/api/cutout/pending). This works through the list -- the photo's source
   bytes in, a matte from this device's engine, the cutout saved through
   /api/cutout/apply -- serially, because the engine is one GPU or one
   Neural Engine and two photos at once only slow each other down.

   Nothing here is urgent and nothing is lost: a photo not reached yet is
   still the photo as shot, and still on the list. Close the app mid-pile
   and the rest wait for the next time it opens; nothing is handed to
   Photoroom because the app was closed. A photo the device cannot cut goes
   to Photoroom only for a seller who switched cloud removal on (and once);
   for anyone else it is marked as given up with the reason, and stays as
   shot.

   It also stays out of the seller's way: it pauses while the app is in the
   background (the OS would freeze it anyway), skips past the photo open in
   the studio, and stops on sign-out. */
import { api } from "@/lib/api";
import { DEVICE_CODES, LocalCutoutError } from "./errors";
import { checkLocal, cutPhoto, localUsable } from "./service";

const IDLE = { running: false, total: 0, done: 0, failed: 0, current: null,
               stopped: false, message: "" };

export function createCutoutQueue({ onPhotoDone, onFinished, cloudAllowed } = {}) {
  let state = { ...IDLE };
  let pending = [];           // [{session_id, name, kind}]
  let editing = null;         // "sid/name" open in the studio
  let generation = 0;         // bumps on stop/reset: a run from before ends
  let paused = false;
  let wake = null;
  let cloudOff = false;       // tokens ran out mid-run: no more cloud tries
  // Photos already taken in hand this session, done or given up on. The
  // server's pending list can lag a photo that was just cut (or still list
  // one being cut); without this a refresh in that moment cut it twice.
  const handled = new Set();
  const listeners = new Set();

  const emit = (next) => {
    state = { ...state, ...next };
    for (const fn of listeners) {
      try { fn(); } catch (e) { /* never breaks the run */ }
    }
  };

  const key = (p) => `${p.session_id}/${p.name}`;

  function waitWhilePaused(gen) {
    if (!paused) return Promise.resolve();
    return new Promise((resolve) => {
      wake = () => { wake = null; resolve(); };
      if (gen !== generation) resolve();
    });
  }

  async function run(gen) {
    emit({ running: true, stopped: false, message: "" });
    while (gen === generation && pending.length) {
      await waitWhilePaused(gen);
      if (gen !== generation) return;
      // The photo open in the studio waits its turn at the back: cutting it
      // now would be saved over by the studio's own Save a moment later.
      let i = pending.findIndex((p) => key(p) !== editing);
      if (i < 0) {
        await new Promise((r) => setTimeout(r, 2000));
        continue;
      }
      const photo = pending.splice(i, 1)[0];
      handled.add(key(photo));
      emit({ current: photo });
      try {
        const allowCloud = !cloudOff && !!cloudAllowed?.();
        const saved = await cutPhoto({ sessionId: photo.session_id, name: photo.name,
                                       origin: "queue", allowCloud });
        if (gen !== generation) return;
        emit({ done: state.done + 1 });
        try { onPhotoDone?.(photo, saved); } catch (e) { /* display only */ }
      } catch (e) {
        if (gen !== generation) return;
        if (e?.status === 402) cloudOff = true;
        const code = e instanceof LocalCutoutError ? e.code : "";
        // The DEVICE cannot do this at all (unsupported, no Play services):
        // every other photo would fail the same way, so the run ends here and
        // the rest stay pending for a device that can.
        if (code && DEVICE_CODES.has(code) && !cloudAllowed?.()) {
          pending.unshift(photo);
          handled.delete(key(photo));
          emit({ running: false, current: null, message: e.message });
          return;
        }
        emit({ failed: state.failed + 1, message: e?.message || "" });
        // Given up on this photo: say so, so it is not offered again. A 409
        // already moved the row (photo changed, listing live) server side;
        // a 404 is a photo that is gone.
        if (e?.status !== 409 && e?.status !== 404) {
          try {
            await api("/api/cutout/skip", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ session_id: photo.session_id, name: photo.name,
                                     reason: e?.message || "" }),
            });
          } catch (err) { /* stays pending; offered again next time */ }
        }
      }
    }
    if (gen !== generation) return;
    emit({ running: false, current: null });
    try { onFinished?.(state); } catch (e) { /* display only */ }
  }

  function enqueue(photos) {
    const list = (photos || []).filter((p) => p?.session_id && p?.name);
    const known = new Set(pending.map(key));
    const fresh = list.filter((p) => !known.has(key(p)) && !handled.has(key(p)));
    if (!fresh.length) return;
    pending.push(...fresh);
    if (state.running) {
      emit({ total: state.total + fresh.length });
    } else {
      cloudOff = false;
      emit({ done: 0, failed: 0, total: pending.length });
      run(generation);
    }
  }

  return {
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    getState() { return state; },
    pendingIds() { return new Set(pending.map((p) => p.session_id)); },

    /** Read what waits for this device and start on it (no-op while a run is
        going: what it finds is appended). */
    async refresh() {
      const local = await checkLocal();
      if (!localUsable(local)) return state;
      let photos;
      try {
        photos = (await api("/api/cutout/pending")).photos || [];
      } catch (e) {
        return state;   // checked again on the next open
      }
      enqueue(photos);
      return state;
    },

    /** Photos an upload on THIS device just asked to have cut: start on
        them now rather than waiting for the next refresh. */
    add(photos) {
      enqueue(photos);
    },

    /** The seller pressed Stop: finish nothing more this time. What is left
        stays pending on the server for the next open. */
    stop() {
      generation += 1;
      pending = [];
      handled.clear();
      if (wake) wake();
      emit({ running: false, current: null, stopped: true });
    },

    /** Sign-out: forget everything. */
    reset() {
      generation += 1;
      pending = [];
      handled.clear();
      editing = null;
      if (wake) wake();
      state = { ...IDLE };
      emit({});
    },

    setPaused(value) {
      paused = !!value;
      if (!paused && wake) wake();
    },

    /** The studio opened (or closed, with null) this photo. */
    setEditing(sessionId, name) {
      editing = sessionId && name ? `${sessionId}/${name}` : null;
    },
  };
}
