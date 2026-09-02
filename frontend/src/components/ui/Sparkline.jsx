import { cn } from "@/lib/utils";

// Hand-rolled SVG micro-charts — no chart library (nothing else in the app
// needs one, and the CDN allowlist and bundle both stay clean). Both draw in
// currentColor, so the wrapper's token class (text-blue, text-green) colors
// them correctly in light and dark alike.

export function Sparkline({ points = [], width = 140, height = 36, className }) {
  if (!Array.isArray(points) || points.length < 2) return null;
  const max = Math.max(...points, 1);
  const step = width / (points.length - 1);
  const y = (v) => height - 3 - (v / max) * (height - 6);
  const d = points
    .map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${y(v).toFixed(1)}`)
    .join(" ");
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={cn("block w-full h-9", className)}
      preserveAspectRatio="none"
      aria-hidden
      focusable="false"
    >
      <path d={d} fill="none" stroke="currentColor" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function MiniBars({ values = [], width = 140, height = 36, className }) {
  if (!Array.isArray(values) || values.length === 0) return null;
  const max = Math.max(...values, 1);
  const gap = 2;
  const bar = Math.max(1, (width - gap * (values.length - 1)) / values.length);
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={cn("block w-full h-9", className)}
      preserveAspectRatio="none"
      aria-hidden
      focusable="false"
    >
      {values.map((v, i) => {
        const h = Math.max(1, (v / max) * (height - 2));
        return (
          <rect key={i} x={(i * (bar + gap)).toFixed(1)} y={(height - h).toFixed(1)}
            width={bar.toFixed(1)} height={h.toFixed(1)} rx="1"
            fill="currentColor" opacity={v === 0 ? 0.25 : 0.9} />
        );
      })}
    </svg>
  );
}
