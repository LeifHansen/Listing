/**
 * Generates the default Open Graph card (public/og-default.png).
 *
 * Run with `node scripts/build-og.mjs`. The OUTPUT IS COMMITTED — CI does not
 * regenerate it, because rendering the wordmark needs Fredoka installed as a
 * system font and a build that silently falls back to DejaVu would ship an
 * off-brand card without anything failing. Regenerate locally when the brand
 * changes, and commit the result.
 *
 * Colors are read from frontend/src/styles/tokens.css so this cannot drift
 * from the brand either.
 */
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { execSync } from "node:child_process";
import { homedir } from "node:os";
import path from "node:path";
import sharp from "sharp";

const root = path.resolve(import.meta.dirname, "..");
const tokensPath = path.resolve(root, "../frontend/src/styles/tokens.css");

/** Pull a custom property out of the app's token file. */
function token(name) {
  const css = readFileSync(tokensPath, "utf8");
  const match = css.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{3,8})`));
  if (!match) throw new Error(`token --${name} not found in ${tokensPath}`);
  return match[1];
}

const c = {
  bg: token("bg"),
  ink: token("text"),
  inkSecondary: token("text-secondary"),
  blue: token("brand-blue"),
  red: token("brand-red"),
  yellow: token("brand-yellow"),
  green: token("brand-green"),
};

const FONTS = {
  "Fredoka-SemiBold.ttf": "https://fonts.gstatic.com/s/fredoka/v17/X7nP4b87HvSqjb_WIi2yDCRwoQ_k7367_B-i2yQag0-mac3OLyXMFg.ttf",
  "Fredoka-Bold.ttf": "https://fonts.gstatic.com/s/fredoka/v17/X7nP4b87HvSqjb_WIi2yDCRwoQ_k7367_B-i2yQag0-mac3OFiXMFg.ttf",
};

/** Install Fredoka for fontconfig if it is not already present. */
function ensureFonts() {
  let installed = "";
  try {
    installed = execSync("fc-list", { encoding: "utf8" });
  } catch {
    /* fontconfig missing entirely — the check below will fail loudly */
  }
  if (installed.toLowerCase().includes("fredoka")) return;

  const dir = path.join(homedir(), ".fonts");
  mkdirSync(dir, { recursive: true });
  for (const [file, url] of Object.entries(FONTS)) {
    const dest = path.join(dir, file);
    if (!existsSync(dest)) {
      console.log(`fetching ${file}…`);
      execSync(`curl -sS -L -o "${dest}" "${url}"`, { stdio: "inherit" });
    }
  }
  execSync("fc-cache -f", { stdio: "ignore" });

  const after = execSync("fc-list", { encoding: "utf8" });
  if (!after.toLowerCase().includes("fredoka")) {
    throw new Error(
      "Fredoka is still not visible to fontconfig — refusing to render the " +
        "card in a fallback font. Install the font and re-run.",
    );
  }
}

const W = 1200;
const H = 630;

const svg = `
<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
  <defs>
    <radialGradient id="wash-a" cx="14%" cy="0%" r="62%">
      <stop offset="0%" stop-color="${c.yellow}" stop-opacity="0.20" />
      <stop offset="100%" stop-color="${c.yellow}" stop-opacity="0" />
    </radialGradient>
    <radialGradient id="wash-b" cx="88%" cy="6%" r="58%">
      <stop offset="0%" stop-color="${c.blue}" stop-opacity="0.18" />
      <stop offset="100%" stop-color="${c.blue}" stop-opacity="0" />
    </radialGradient>
    <linearGradient id="bar" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="${c.red}" />
      <stop offset="33%" stop-color="${c.yellow}" />
      <stop offset="66%" stop-color="${c.green}" />
      <stop offset="100%" stop-color="${c.blue}" />
    </linearGradient>
  </defs>

  <rect width="${W}" height="${H}" fill="${c.bg}" />
  <rect width="${W}" height="${H}" fill="url(#wash-a)" />
  <rect width="${W}" height="${H}" fill="url(#wash-b)" />

  <text x="96" y="330" font-family="Fredoka" font-weight="700" font-size="82" fill="${c.ink}">Thryft Shop</text>
  <text x="98" y="396" font-family="Fredoka" font-weight="600" font-size="34" fill="${c.inkSecondary}">Snap it · AI writes it · list it everywhere.</text>
  <text x="98" y="452" font-family="Fredoka" font-weight="600" font-size="27" fill="${c.blue}">eBay · Etsy · Depop</text>

  <rect x="0" y="${H - 14}" width="${W}" height="14" fill="url(#bar)" />
</svg>`;

ensureFonts();

const logo = await sharp(path.resolve(root, "public/logo-512.webp"))
  .resize(232, 232, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
  .png()
  .toBuffer();

const out = path.resolve(root, "public/og-default.png");
await sharp(Buffer.from(svg))
  .composite([{ input: logo, top: 190, left: 830 }])
  .png({ compressionLevel: 9 })
  .toFile(out);

console.log(`wrote ${path.relative(root, out)}`);
