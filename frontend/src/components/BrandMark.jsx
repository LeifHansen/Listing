import { useState } from "react";
import { Zap } from "lucide-react";
import { cn } from "@/lib/utils";

// The Thryft Shop logo, at the size it is actually drawn. Every in-app use is
// between 32px and 80px CSS, so 160 covers 2x -- and the source art is a
// 1254x1254 PNG weighing 574KB, which is more than any JS chunk in the bundle
// and was being downloaded to fill an 8-pixel-tall sidebar slot. The original
// stays in public/ as the master; nothing on a hot path points at it.
export const BRAND_LOGO = "/logo-160.webp";

// The logo (transparent background) for small spots. No card/box behind it, so
// it blends on cream (light) and navy (dark). Falls back to the Zap glyph if
// the asset ever goes missing, so nothing renders broken.
export function BrandMark({ className = "size-9" }) {
  const [imgOk, setImgOk] = useState(true);
  if (imgOk) {
    return (
      <img
        src={BRAND_LOGO}
        alt=""
        onError={() => setImgOk(false)}
        className={cn("object-contain shrink-0", className)}
      />
    );
  }
  return (
    <span className={cn("grid place-items-center bg-blue text-on-accent shrink-0 rounded-[13px]", className)}>
      <Zap size={19} strokeWidth={2.4} aria-hidden />
    </span>
  );
}
