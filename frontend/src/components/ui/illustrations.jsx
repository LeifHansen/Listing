/* Illustration system — minimal, flat, friendly, rounded, slightly cartoonish.
   No gradients, no realism. Each draws from the token palette so it adapts to
   dark mode automatically. These are placeholders ready to be swapped for
   final SVG assets — keep the names + viewBox and drop new art in. */

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

// A camera with a wink of a flash — photo-first workflows.
export function CameraIllustration(props) {
  return (
    <svg viewBox="0 0 160 130" width="160" height="130" role="img" aria-hidden {...props}>
      <ellipse cx="80" cy="118" rx="52" ry="8" fill={soft} />
      <rect x="26" y="42" width="108" height="68" rx="16" fill="var(--brand-blue-soft)"
        stroke={stroke} strokeWidth="4" />
      <path d="M58 42 L64 28 Q65 25 69 25 H91 Q95 25 96 28 L102 42"
        fill="var(--brand-blue-soft)" stroke={stroke} strokeWidth="4" strokeLinejoin="round" />
      <circle cx="80" cy="76" r="22" fill="var(--card)" stroke={stroke} strokeWidth="4" />
      <circle cx="80" cy="76" r="10" fill="var(--brand-blue)" />
      <circle cx="117" cy="56" r="4" fill="var(--brand-red)" />
      <Sparkle x={26} y={20} s={0.9} />
      <Sparkle x={140} y={26} s={0.7} color="var(--brand-green)" />
    </svg>
  );
}

// A tiny happy robot holding a sparkle — the AI helper.
export function RobotIllustration(props) {
  return (
    <svg viewBox="0 0 160 130" width="160" height="130" role="img" aria-hidden {...props}>
      <ellipse cx="80" cy="120" rx="46" ry="7" fill={soft} />
      <line x1="80" y1="14" x2="80" y2="28" stroke={stroke} strokeWidth="4" strokeLinecap="round" />
      <circle cx="80" cy="12" r="5" fill="var(--brand-red)" />
      <rect x="42" y="28" width="76" height="56" rx="16" fill="var(--brand-blue-soft)"
        stroke={stroke} strokeWidth="4" />
      <Face x={80} y={52} />
      <rect x="54" y="90" width="52" height="26" rx="10" fill="var(--brand-green-soft)"
        stroke={stroke} strokeWidth="4" />
      <line x1="42" y1="52" x2="28" y2="52" stroke={stroke} strokeWidth="4" strokeLinecap="round" />
      <line x1="118" y1="52" x2="132" y2="52" stroke={stroke} strokeWidth="4" strokeLinecap="round" />
      <Sparkle x={30} y={40} s={0.8} />
      <Sparkle x={136} y={36} s={1} />
    </svg>
  );
}

// A clipboard with a big friendly check — done states, drafts.
export function ClipboardIllustration(props) {
  return (
    <svg viewBox="0 0 160 130" width="160" height="130" role="img" aria-hidden {...props}>
      <ellipse cx="80" cy="120" rx="46" ry="7" fill={soft} />
      <rect x="44" y="22" width="72" height="92" rx="14" fill="var(--brand-green-soft)"
        stroke={stroke} strokeWidth="4" />
      <rect x="62" y="12" width="36" height="18" rx="8" fill="var(--card)"
        stroke={stroke} strokeWidth="4" />
      <path d="M62 68 L76 82 L100 52" stroke="var(--brand-green)" strokeWidth="7"
        strokeLinecap="round" strokeLinejoin="round" fill="none" />
      <Sparkle x={30} y={34} s={0.8} color="var(--brand-blue)" />
      <Sparkle x={132} y={80} s={0.7} />
    </svg>
  );
}

// A price tag with a barcode sticker — listings & pricing.
export function TagIllustration(props) {
  return (
    <svg viewBox="0 0 160 130" width="160" height="130" role="img" aria-hidden {...props}>
      <ellipse cx="80" cy="120" rx="46" ry="7" fill={soft} />
      <path d="M52 30 L98 30 Q104 30 108 34 L134 62 Q138 66 134 71 L96 112 Q92 116 87 112 L36 68
        Q32 64 34 58 L44 36 Q46 30 52 30 Z" fill="var(--brand-red-soft)"
        stroke={stroke} strokeWidth="4" strokeLinejoin="round" />
      <circle cx="58" cy="46" r="7" fill="var(--card)" stroke={stroke} strokeWidth="4" />
      <g stroke={stroke} strokeWidth="3" strokeLinecap="round">
        <line x1="72" y1="62" x2="72" y2="80" />
        <line x1="80" y1="66" x2="80" y2="86" />
        <line x1="88" y1="62" x2="88" y2="80" />
        <line x1="96" y1="68" x2="96" y2="84" />
      </g>
      <Sparkle x={128} y={28} s={0.9} />
      <Sparkle x={26} y={86} s={0.7} color="var(--brand-green)" />
    </svg>
  );
}

// A magnifying glass over a little shop — Shop Mode.
export function SearchIllustration(props) {
  return (
    <svg viewBox="0 0 160 130" width="160" height="130" role="img" aria-hidden {...props}>
      <ellipse cx="80" cy="120" rx="46" ry="7" fill={soft} />
      <rect x="30" y="58" width="64" height="52" rx="10" fill="var(--brand-yellow-soft)"
        stroke={stroke} strokeWidth="4" />
      <path d="M24 58 L36 36 H88 L100 58 Q100 70 88 70 Q76 70 76 58 Q76 70 62 70
        Q48 70 48 58 Q48 70 36 70 Q24 70 24 58 Z" fill="var(--brand-red-soft)"
        stroke={stroke} strokeWidth="4" strokeLinejoin="round" />
      <rect x="46" y="80" width="20" height="30" rx="4" fill="var(--card)" stroke={stroke} strokeWidth="4" />
      <circle cx="108" cy="72" r="22" fill="var(--brand-blue-soft)" stroke={stroke} strokeWidth="4" opacity="0.92" />
      <line x1="124" y1="88" x2="140" y2="104" stroke={stroke} strokeWidth="6" strokeLinecap="round" />
      <Sparkle x={116} y={30} s={0.9} />
    </svg>
  );
}

