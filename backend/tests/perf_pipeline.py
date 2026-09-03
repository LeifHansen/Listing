"""Manual timing harness for the photo pass (not collected by pytest).

Times optimize() as shot and with the background removed, per photo, over
synthetic phone-sized photos, so a change to backend/services/images.py can
be judged in numbers before it ships:

    python backend/tests/perf_pipeline.py            # real model if rembg is installed
    REMBG_MODEL=u2netp python backend/tests/perf_pipeline.py

Without rembg a synthetic matte stands in for the model, which isolates the
code this repo owns from the inference itself.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image, ImageDraw  # noqa: E402

from backend.services import images  # noqa: E402


def _photo(path: Path, size=(4032, 3024)) -> Path:
    img = Image.new("RGB", size, (236, 234, 230))
    w, h = size
    ImageDraw.Draw(img).ellipse((w // 4, h // 4, 3 * w // 4, 3 * h // 4), fill=(60, 70, 90))
    img.save(path, "JPEG", quality=92)
    return path


def main() -> None:
    try:
        import rembg  # noqa: F401
        real = True
    except ImportError:
        real = False
        images._mask = lambda rgb, wait=None: rgb.convert("L").point(lambda v: 255 if v < 128 else 0)
    n = int(os.getenv("PERF_PHOTOS", "5") or 5)
    with tempfile.TemporaryDirectory() as tmp:
        src = _photo(Path(tmp) / "src.jpg")
        for label, remove_bg in (("as shot", False), ("cut out", True)):
            if remove_bg and real:
                images.optimize(src, Path(tmp) / "warm", remove_bg=True)  # model load
            t = time.perf_counter()
            for i in range(n):
                images.optimize(src, Path(tmp) / f"out_{i}", remove_bg=remove_bg)
            per = (time.perf_counter() - t) / n
            print(f"{label:8s} {per * 1000:8.0f} ms/photo"
                  + (f"  (model {images.engine_state()['model']}, "
                     f"inference {images.engine_state()['last_inference_seconds']}s)"
                     if remove_bg and real else "  (synthetic matte)" if remove_bg else ""))


if __name__ == "__main__":
    main()
