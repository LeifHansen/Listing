/* The photo studio's layer + history model.
 *
 * Three behaviours this pins down, each of which was a real defect:
 *   - a stroke is a joined path, not a row of stamps (a quick drag used to
 *     leave a dotted trail with gaps to go back over);
 *   - Restore erases the MASK so the original shows through, which is the
 *     whole mechanism that lets a seller get back a strap the cutout ate;
 *   - undo is replay, not a pixel snapshot, so the history is affordable.
 */
import { describe, expect, it, beforeEach } from "vitest";
import {
  ERASE, RESTORE, applySnapshot, compose, createHistory, drawStroke,
  layersFrom, pushOperation, pushStroke, replay, snapshot,
} from "./editorLayers";

function fakeImage(width = 100, height = 80) {
  const c = document.createElement("canvas");
  c.width = width;
  c.height = height;
  return c;
}

function calls(canvas) {
  return canvas.getContext("2d").calls;
}

let layers;
beforeEach(() => { layers = layersFrom(fakeImage()); });

describe("layers", () => {
  it("starts with the cutout equal to the original, so nothing changes", () => {
    expect(layers.width).toBe(100);
    expect(layers.height).toBe(80);
    // The mask starts filled: with no strokes the cutout is what shows.
    const fills = calls(layers.mask).filter((c) => c.op === "fillRect");
    expect(fills).toHaveLength(1);
    expect(fills[0].args).toEqual([0, 0, 100, 80]);
  });

  it("composes original, then the masked cutout, then the paint", () => {
    const target = document.createElement("canvas");
    compose(layers, target);
    const drawn = calls(target).filter((c) => c.op === "drawImage");
    expect(drawn[0].args[0]).toBe(layers.original);
    expect(drawn[2].args[0]).toBe(layers.paint);
    // The middle draw is the scratch copy of the cutout, masked — never the
    // cutout layer itself, which has to survive being re-masked on every undo.
    expect(drawn[1].args[0]).not.toBe(layers.cutout);
  });
});

describe("strokes", () => {
  it("joins its points into one path instead of stamping circles", () => {
    drawStroke(layers, { tool: ERASE, radius: 10, points: [[0, 0], [5, 5], [9, 2]] });
    const ops = calls(layers.paint).map((c) => c.op);
    expect(ops).toContain("moveTo");
    expect(ops.filter((o) => o === "lineTo")).toHaveLength(2);
    expect(ops).not.toContain("arc");
  });

  it("stamps a dot for a single tap, which has no path to join", () => {
    drawStroke(layers, { tool: ERASE, radius: 10, points: [[4, 4]] });
    expect(calls(layers.paint).map((c) => c.op)).toContain("arc");
  });

  it("restore erases the mask so the original shows through", () => {
    drawStroke(layers, { tool: RESTORE, radius: 8, points: [[1, 1], [2, 2]] });
    const stroke = calls(layers.mask).find((c) => c.op === "stroke");
    expect(stroke.gco).toBe("destination-out");
    // ...and it does not touch the paint layer.
    expect(calls(layers.paint).filter((c) => c.op === "stroke")).toHaveLength(0);
  });

  it("erase lays white on top rather than cutting the mask", () => {
    drawStroke(layers, { tool: ERASE, radius: 8, points: [[1, 1], [2, 2]] });
    expect(calls(layers.paint).find((c) => c.op === "stroke").gco).toBe("source-over");
    expect(calls(layers.mask).filter((c) => c.op === "stroke")).toHaveLength(0);
  });

  it("ignores an empty stroke rather than drawing a stray dot", () => {
    const before = calls(layers.paint).length;
    drawStroke(layers, { tool: ERASE, radius: 8, points: [] });
    expect(calls(layers.paint)).toHaveLength(before);
  });
});

describe("replay", () => {
  it("rebuilds both layers from the stroke list", () => {
    const strokes = [
      { tool: ERASE, radius: 5, points: [[1, 1], [2, 2]] },
      { tool: RESTORE, radius: 5, points: [[3, 3], [4, 4]] },
    ];
    replay(layers, strokes);
    // The mask is refilled first (so a removed stroke's hole closes up), and
    // the paint layer is cleared.
    expect(calls(layers.mask).filter((c) => c.op === "fillRect").length)
      .toBeGreaterThanOrEqual(2);
    expect(calls(layers.paint).some((c) => c.op === "clearRect")).toBe(true);
    expect(calls(layers.paint).filter((c) => c.op === "stroke")).toHaveLength(1);
    expect(calls(layers.mask).filter((c) => c.op === "stroke")).toHaveLength(1);
  });
});

describe("history", () => {
  it("undoes strokes by dropping the last one, not by restoring pixels", () => {
    const history = createHistory();
    const a = { tool: ERASE, radius: 5, points: [[1, 1]] };
    pushStroke(history, a);
    expect(history.undo).toHaveLength(1);
    expect(history.undo[0].kind).toBe("stroke");
    expect(history.undo[0].stroke).toBe(a);
  });

  it("a new stroke discards the redo branch", () => {
    const history = createHistory();
    pushStroke(history, { tool: ERASE, radius: 5, points: [[1, 1]] });
    history.redo.push(history.undo.pop());
    pushStroke(history, { tool: ERASE, radius: 5, points: [[2, 2]] });
    expect(history.redo).toHaveLength(0);
  });

  it("caps itself, because operation entries hold full-size canvases", () => {
    const history = createHistory();
    for (let i = 0; i < 40; i++) {
      pushStroke(history, { tool: ERASE, radius: 5, points: [[i, i]] });
    }
    expect(history.undo.length).toBeLessThanOrEqual(24);
    // The most recent edit survives; the oldest is what gets dropped.
    expect(history.undo.at(-1).stroke.points[0]).toEqual([39, 39]);
  });

  it("keeps operations and strokes in one ordered stack", () => {
    const history = createHistory();
    pushStroke(history, { tool: ERASE, radius: 5, points: [[1, 1]] });
    pushOperation(history, snapshot(layers, []), snapshot(layers, []));
    expect(history.undo.map((e) => e.kind)).toEqual(["stroke", "operation"]);
  });
});

describe("snapshots", () => {
  it("copies the stroke list rather than aliasing it", () => {
    const strokes = [{ tool: ERASE, radius: 5, points: [[1, 1]] }];
    const snap = snapshot(layers, strokes);
    strokes[0].points.push([9, 9]);
    expect(snap.strokes[0].points).toHaveLength(1);
  });

  it("restores size and replays the strokes it was taken with", () => {
    const snap = snapshot(layers, [{ tool: RESTORE, radius: 5, points: [[1, 1], [2, 2]] }]);
    const other = layersFrom(fakeImage(40, 40));
    const restored = applySnapshot(other, snap);
    expect(other.width).toBe(100);
    expect(other.height).toBe(80);
    expect(restored).toHaveLength(1);
    expect(calls(other.mask).filter((c) => c.op === "stroke")).toHaveLength(1);
  });
});
