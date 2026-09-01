/* A recording 2D context.
 *
 * jsdom has no canvas implementation, and pulling in node-canvas to get one
 * would mean a native build in CI for tests that do not actually need pixels:
 * what matters about the photo studio's layer model is WHICH layer each
 * stroke goes to, WHICH composite operation it uses, and whether a stroke is
 * drawn as a joined path or a series of stamps. All three are visible in the
 * call log. */
class RecordingContext {
  constructor(canvas) {
    this.canvas = canvas;
    this.calls = [];
    this.globalCompositeOperation = "source-over";
    this.fillStyle = "#000";
    this.strokeStyle = "#000";
    this.lineWidth = 1;
    this.lineCap = "butt";
    this.lineJoin = "miter";
    this._stack = [];
  }

  _log(op, args) {
    // fillStyle rides along because one thing a caller can get wrong is the
    // COLOUR it paints under a photo: a canvas exported as JPEG composites
    // what it cannot store onto black, so "there is a fill before the draw"
    // is only half the contract (see lib/api.downscaleForUpload).
    this.calls.push({ op, args, gco: this.globalCompositeOperation,
                      fillStyle: this.fillStyle });
  }

  save() { this._stack.push(this.globalCompositeOperation); }
  restore() { this.globalCompositeOperation = this._stack.pop() || "source-over"; }
  clearRect(...a) { this._log("clearRect", a); }
  fillRect(...a) { this._log("fillRect", a); }
  beginPath() { this._log("beginPath", []); }
  moveTo(...a) { this._log("moveTo", a); }
  lineTo(...a) { this._log("lineTo", a); }
  arc(...a) { this._log("arc", a); }
  fill() { this._log("fill", []); }
  stroke() { this._log("stroke", []); }
  drawImage(src, ...a) { this._log("drawImage", [src, ...a]); }
  getImageData(x, y, w, h) {
    return { data: new Uint8ClampedArray(Math.max(1, w * h * 4)), width: w, height: h };
  }
  putImageData(...a) { this._log("putImageData", a); }
}

Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
  configurable: true,
  value(kind) {
    if (kind !== "2d") return null;
    if (!this.__ctx) this.__ctx = new RecordingContext(this);
    return this.__ctx;
  },
});
