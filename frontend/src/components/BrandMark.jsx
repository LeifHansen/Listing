import { useState } from "react";
import { Zap } from "lucide-react";
import { cn } from "@/lib/utils";

// The Thryft Shop logo (uploaded to frontend/public/). One asset, used
// app-wide: sidebar, login dialog, loading states, favicon.
export const BRAND_LOGO = "/thryft-shop-logo-final.png";

// Square-tile rendition of the logo for small spots. Falls back to the Zap
// glyph if the asset ever goes missing, so nothing renders broken.
export function BrandMark({ className = "size-9 rounded-[13px]" }) {
  const [imgOk, setImgOk] = useState(true);
  if (imgOk) {
    return (
      <img
        src={BRAND_LOGO}
        alt=""
        onError={() => setImgOk(false)}
        className={cn("object-cover shrink-0 shadow-card bg-card", className)}
      />
    );
  }
  return (
    <span className={cn("grid place-items-center bg-blue text-on-accent shrink-0 shadow-card", className)}>
      <Zap size={19} strokeWidth={2.4} aria-hidden />
    </span>
  );
}
