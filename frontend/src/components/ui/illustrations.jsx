/* Illustration system — the Thryft Shop mascot, one character across every
   empty state and greeting panel. The art lives in frontend/public/brand/;
   the 1200px masters and the designer's placement notes are in
   frontend/assets/thryft-shop-brand-assets/.

   Exports are named for the SLOT each one fills, not for what it draws, so
   re-posing the mascot is a file swap and nothing here changes. Every current
   call site pairs the art with a heading that already carries the meaning, so
   they render decoratively (empty alt + aria-hidden) rather than making a
   screen reader announce the pose before every title.

   BoxIllustration is still the old flat SVG — no mascot pose was drawn for
   the "nothing unlisted" state yet. */

import { cn } from "@/lib/utils";

const stroke = "var(--text)";
const soft = "var(--brand-blue-soft)";

function Face({ x = 0, y = 0 }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <circle cx="-9" cy="0" r="2.6" fill={stroke} />
      <circle cx="9" cy="0" r="2.6" fill={stroke} />
      <path d="M-6 8 Q0 13 6 8" stroke={stroke} strokeWidth="3" strokeLinecap="round" fill="none" />
    </g>
  );
}

function Sparkle({ x, y, s = 1, color = "var(--brand-yellow)" }) {
  return (
    <path
      transform={`translate(${x} ${y}) scale(${s})`}
      d="M0 -7 L1.8 -1.8 L7 0 L1.8 1.8 L0 7 L-1.8 1.8 L-7 0 L-1.8 -1.8 Z"
      fill={color}
    />
  );
}

// The 640px WebP shown at 160px, per the brand README: transparent square
// canvas, aspect ratio preserved, and no tile or crop behind the art — the
// coral offset outline is what separates it from the page in both themes.
function Mascot({ pose, className, ...props }) {
  return (
    <img
      src={`/brand/thryft-mascot-${pose}.webp`}
      alt=""
      aria-hidden
      width={160}
      height={160}
      decoding="async"
      className={cn("size-40 object-contain shrink-0", className)}
      {...props}
    />
  );
}

// Home greeting panel.
export function WelcomeIllustration(props) {
  return <Mascot pose="welcome" {...props} />;
}

// The photo drop zone at the top of the sell flow.
export function PhotoUploadIllustration(props) {
  return <Mascot pose="photo-upload" {...props} />;
}

// Listings tabs with nothing to show, including the logged-out state.
export function ListingsIllustration(props) {
  return <Mascot pose="listings" {...props} />;
}

// Shop Mode, before the first scan.
export function ShopModeIllustration(props) {
  return <Mascot pose="shop-mode" {...props} />;
}

// Logged-out settings and account states.
export function AccountIllustration(props) {
  return <Mascot pose="account" {...props} />;
}

// A happy cardboard box — the mascot of empty inventories everywhere.
export function BoxIllustration(props) {
  return (
    <svg viewBox="0 0 160 130" width="160" height="130" role="img" aria-hidden {...props}>
      <ellipse cx="80" cy="118" rx="52" ry="8" fill={soft} />
      <rect x="34" y="46" width="92" height="66" rx="12" fill="var(--brand-yellow-soft)"
        stroke={stroke} strokeWidth="4" />
      <path d="M34 58 L18 34 Q16 30 21 30 L60 26" fill="var(--brand-yellow-soft)"
        stroke={stroke} strokeWidth="4" strokeLinejoin="round" />
      <path d="M126 58 L142 34 Q144 30 139 30 L100 26" fill="var(--brand-yellow-soft)"
        stroke={stroke} strokeWidth="4" strokeLinejoin="round" />
      <rect x="60" y="22" width="40" height="24" rx="8" fill="var(--brand-yellow-soft)"
        stroke={stroke} strokeWidth="4" />
      <Face x={80} y={78} />
      <Sparkle x={30} y={16} s={0.8} />
      <Sparkle x={138} y={70} s={0.6} color="var(--brand-blue)" />
    </svg>
  );
}
