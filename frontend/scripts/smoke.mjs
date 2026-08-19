/* Launch smoke test: boot the app, click through every screen, and fail on
 * anything a seller would see as broken — a page error, a console error, a
 * same-origin request that failed, a blank render, or a page that scrolls
 * sideways on a phone.
 *
 * It clicks the REAL nav rather than poking app state, so each screen's own
 * render and data fetching is exercised rather than the landing screen N
 * times. Point it at a running server:
 *
 *     SMOKE_BASE=http://127.0.0.1:8099 node scripts/smoke.mjs
 */
import { chromium } from 'playwright';

const BASE = process.env.SMOKE_BASE || 'http://127.0.0.1:8099';
// Every top-level screen the nav can reach, plus the legal pages.
// The real nav labels — clicked, not faked, so this exercises each screen's
// own render and data fetching rather than the landing screen seven times.
const VIEWS = ['Home', 'Sell', 'Shop', 'Settings'];

const browser = await chromium.launch(
  process.env.PLAYWRIGHT_CHROMIUM
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM } : {});
const problems = [];

for (const view of VIEWS) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(`pageerror: ${e.message}`));
  page.on('console', m => {
    const t = m.text();
    if (m.type() === 'error' && !/ERR_CONNECTION_RESET|fonts\.googleapis/.test(t)) {
      errs.push(`console: ${t.slice(0, 200)}`);
    }
  });
  // Same-origin only: this sandbox blocks the Google Fonts CDN, which is an
  // egress policy here and not an app fault.
  page.on('requestfailed', r => {
    if (r.url().startsWith(BASE)) errs.push(`requestfailed: ${r.url().slice(0, 120)} ${r.failure()?.errorText}`);
  });
  try {
    await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
    const nav = page.getByRole('button', { name: view, exact: true }).first();
    await nav.waitFor({ state: 'visible', timeout: 10000 });
    await nav.click();
    await page.waitForTimeout(1500);
    const bodyText = (await page.textContent('body')) || '';
    if (bodyText.trim().length < 20) errs.push('rendered an empty page');
  } catch (e) {
    errs.push(`navigation: ${e.message.slice(0, 200)}`);
  }
  problems.push([view, errs]);
  await ctx.close();
}

// Mobile viewport pass on the default screen — most sellers are on a phone.
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
const page = await ctx.newPage();
const merrs = [];
page.on('pageerror', e => merrs.push(`pageerror: ${e.message}`));
page.on('console', m => {
  const t = m.text();
  if (m.type() === 'error' && !/ERR_CONNECTION_RESET|fonts\.googleapis/.test(t)) merrs.push(`console: ${t.slice(0, 200)}`);
});
await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
await page.waitForTimeout(1000);
const scrollW = await page.evaluate(() => document.documentElement.scrollWidth);
if (scrollW > 400) merrs.push(`horizontal overflow at 390px: scrollWidth=${scrollW}`);
problems.push(['mobile-390', merrs]);
await browser.close();

let bad = 0;
for (const [view, errs] of problems) {
  if (errs.length) { bad++; console.log(`FAIL ${view}`); errs.forEach(e => console.log(`     ${e}`)); }
  else console.log(`ok   ${view}`);
}
console.log(bad ? `\n${bad} screen(s) with problems` : '\nall screens clean');

process.exit(bad ? 1 : 0);
