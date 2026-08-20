/* Illustration system — the Thryft Shop mascot, one character across every
   empty state and greeting panel. The art lives in frontend/public/brand/;
   the 1200px masters and the designer's placement notes are in
   frontend/assets/thryft-shop-brand-assets/.

   Exports are named for the SLOT each one fills, not for what it draws, so
   re-posing the mascot is a file swap and nothing here changes. Every current
   call site pairs the art with a heading that already carries the meaning, so
   they render decoratively (empty alt + aria-hidden) rather than making a
   screen reader announce the pose before every title. */

import { cn } from "@/lib/utils";

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

// Every listings empty state — live, unlisted, inactive, sold, logged-out —
// plus the dashboard's recent-listings card. One pose covers them all until
// the brand set grows a second listings-flavoured one.
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
