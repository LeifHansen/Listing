// Generates the Capacitor source assets in frontend/assets/ from brand-exact
// vector art: the app icon plus the light and dark splash screens.
//
// The icon is NOT the wordmark. "Thryft Shop" is unreadable at the ~60pt an
// iOS home screen actually renders, so the icon is the logo's price-tag
// character redrawn full-bleed — the one brand glyph that survives being tiny,
// and the same character the mascot illustrations are built from.
//
// Colours are sampled from thryft-shop-logo-final.png, not guessed:
//   navy #01254f · cream #fef0da · gold #feba29 · coral #fe5e4d · teal #2cb1b4
//
// Run: npm run brand:assets   (then `npx capacitor-assets generate` consumes
// the output — ios-prepare.sh does both for you.)

import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, "../assets");

const NAVY = "#01254f";
const CREAM = "#fef0da";
const GOLD = "#feba29";
const CORAL = "#fe5e4d";
const TEAL = "#2cb1b4";

// App canvas navy (tokens.css --bg in dark mode). Deeper than the logo's ink
// so the tag's navy outline still separates against it.
const CANVAS_DARK = "#101a2e";
// Warm cream canvas (tokens.css --bg in light mode).
const CANVAS_LIGHT = "#f8f3e7";

// Tag body geometry, matching the logo's proportions (172x272 -> 340x520):
// chamfered shoulders, rounded foot, hole near the top.
const W = 340;
const H = 520;
const CHAMFER = 104;
const FOOT = 30;
const HOLE_Y = -H / 2 + 76;

const BODY = `
  M ${-W / 2 + CHAMFER} ${-H / 2}
  L ${W / 2 - CHAMFER} ${-H / 2}
  L ${W / 2} ${-H / 2 + CHAMFER}
  L ${W / 2} ${H / 2 - FOOT}
  Q ${W / 2} ${H / 2} ${W / 2 - FOOT} ${H / 2}
  L ${-W / 2 + FOOT} ${H / 2}
  Q ${-W / 2} ${H / 2} ${-W / 2} ${H / 2 - FOOT}
  L ${-W / 2} ${-H / 2 + CHAMFER}
  Z`;

// The cord: a closed teardrop whose tail meets the hole. Drawn BEHIND the tag
// so the tail tucks under the shoulder and it reads as threaded through,
// exactly as it does in the logo.
function cord() {
  return `
  <path d="M 0 ${HOLE_Y} C -78 ${HOLE_Y - 58} -74 ${HOLE_Y - 152} 4 ${HOLE_Y - 152}
           C 78 ${HOLE_Y - 152} 74 ${HOLE_Y - 58} 0 ${HOLE_Y} Z"
        fill="none" stroke="${TEAL}" stroke-width="34" stroke-linejoin="round" />`;
}

// Everything that sits on the tag face. Laid out so nothing collides: hole at
// the top, sparkle clear to its right, hanger through the middle, face at the
// foot.
function tagFace(ground) {
  return `
    <!-- hole -->
    <circle cx="0" cy="${HOLE_Y}" r="26" fill="${ground}"
            stroke="${NAVY}" stroke-width="20" />

    <!-- sparkle, upper right, clear of the hanger -->
    <path d="M 112 -166 L 126 -128 L 164 -114 L 126 -100 L 112 -62
             L 98 -100 L 60 -114 L 98 -128 Z"
          fill="${TEAL}" stroke="${NAVY}" stroke-width="13"
          stroke-linejoin="round" />

    <!-- hanger: hook, then the triangle -->
    <path d="M 0 -96 q 30 -2 30 22 q 0 18 -30 30"
          fill="none" stroke="${NAVY}" stroke-width="19"
          stroke-linecap="round" />
    <path d="M 0 -44 L -102 26 L 102 26 Z" fill="${CREAM}"
          stroke="${NAVY}" stroke-width="19" stroke-linejoin="round" />

    <!-- face -->
    <g fill="${CREAM}" stroke="${NAVY}" stroke-width="17">
      <circle cx="-54" cy="112" r="31" />
      <circle cx="54" cy="112" r="31" />
    </g>
    <circle cx="-47" cy="119" r="13" fill="${NAVY}" />
    <circle cx="61" cy="119" r="13" fill="${NAVY}" />
    <path d="M -48 172 q 48 46 96 0" fill="none" stroke="${NAVY}"
          stroke-width="20" stroke-linecap="round" />`;
}

// The art spans from the top of the cord to the foot of the coral offset;
// its midpoint sits above the tag's own centre. Shifting by that midpoint
// first means the later scale and rotate both act about the true centre, so
// the mark stays centred in the square at any scale.
const ART_TOP = HOLE_Y - 152 - 17; // cord apex + half its stroke
const ART_BOTTOM = H / 2 + 13 + 23; // tag foot + half stroke + coral offset
const ART_MID = (ART_TOP + ART_BOTTOM) / 2;

function mark({ scale = 1, tilt = -8, ground = CANVAS_DARK } = {}) {
  return `
  <g transform="rotate(${tilt}) scale(${scale}) translate(0 ${-ART_MID})">
    ${cord()}
    <!-- coral offset: the brand's signature separation, per the brand README -->
    <path d="${BODY}" transform="translate(21 23)" fill="${CORAL}"
          stroke="${CORAL}" stroke-width="26" stroke-linejoin="round" />
    <path d="${BODY}" fill="${GOLD}" stroke="${NAVY}" stroke-width="26"
          stroke-linejoin="round" />
    ${tagFace(ground)}
  </g>`;
}

// Near full-bleed: an app icon is rendered at ~60pt, so every wasted pixel of
// margin costs legibility. iOS masks the square to a rounded rect, which trims
// the corners but not the middle, so the mark stays inside a safe radius.
function iconSvg() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  <rect width="1024" height="1024" fill="${CANVAS_DARK}" />
  <g transform="translate(512 512)">
    ${mark({ scale: 1.24, tilt: -8 })}
  </g>
</svg>`;
}

// Splash: the same mark on the brand canvas, light or dark. Capacitor
// scales this 2732x2732 square down per device, so the art must sit well
// inside the centre — the edges get cropped on most aspect ratios.
function splashSvg({ dark }) {
  const bg = dark ? CANVAS_DARK : CANVAS_LIGHT;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="2732" height="2732" viewBox="0 0 2732 2732">
  <rect width="2732" height="2732" fill="${bg}" />
  <g transform="translate(1366 1366)">
    ${mark({ scale: 1.34, tilt: -8, ground: bg })}
  </g>
</svg>`;
}

async function shoot(browser, svg, size, file) {
  const page = await browser.newPage({
    viewport: { width: size, height: size },
    deviceScaleFactor: 1,
  });
  await page.setContent(
    `<body style="margin:0;padding:0;line-height:0">${svg}</body>`,
    { waitUntil: "load" },
  );
  const buf = await page.screenshot({ omitBackground: false });
  await page.close();
  await writeFile(file, buf);
  return buf.length;
}

// Same escape hatch as scripts/smoke.mjs: CI and sandboxes ship their own
// Chromium rather than the one `playwright install` would fetch.
const browser = await chromium.launch(
  process.env.PLAYWRIGHT_CHROMIUM
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM } : {});
await mkdir(OUT, { recursive: true });

const jobs = [
  ["icon.png", iconSvg(), 1024],
  ["splash.png", splashSvg({ dark: false }), 2732],
  ["splash-dark.png", splashSvg({ dark: true }), 2732],
];

for (const [name, svg, size] of jobs) {
  const bytes = await shoot(browser, svg, size, path.join(OUT, name));
  console.log(`  ${name.padEnd(16)} ${size}x${size}  ${(bytes / 1024).toFixed(1)}KB`);
}

await browser.close();
console.log("\nWrote 3 assets to frontend/assets/.");
