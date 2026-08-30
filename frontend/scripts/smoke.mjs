/* Launch smoke test: boot the app and walk it the way a seller does.
 *
 * Two passes. The first clicks through every screen SIGNED OUT, which is the
 * landing anyone arrives on. The second signs a seller up through the real
 * dialog and walks the same screens again — and that is where the app
 * actually does its work: signed out, every screen renders its empty state
 * and fetches almost nothing, so a walk that never logs in is a walk past the
 * data. Both passes fail on anything a seller would see as broken: a page
 * error, a console error, a same-origin request that failed, a blank render,
 * or a page that scrolls sideways on a phone.
 *
 * It clicks the REAL nav and the REAL sign-up form rather than poking app
 * state or minting a token, so each screen's own render and data fetching is
 * exercised, and so is the one flow every seller goes through exactly once.
 *
 *     SMOKE_BASE=http://127.0.0.1:8099 node scripts/smoke.mjs
 *
 * The server needs a database (DATABASE_URL) — signing up is half of what
 * this tests. Without one the signup is refused and this FAILS rather than
 * skipping: a smoke run that quietly drops its signed-in half is the same
 * "green stops meaning anything" trap the CI jobs already document.
 */
import { chromium } from 'playwright';

const BASE = process.env.SMOKE_BASE || 'http://127.0.0.1:8099';
// Every top-level screen the nav can reach. The real nav labels — clicked,
// not faked, so this exercises each screen's own render and data fetching
// rather than the landing screen four times.
const VIEWS = ['Home', 'Sell', 'Shop', 'Settings'];
const PASSWORD = 'smoke-password-123';

const browser = await chromium.launch(
  process.env.PLAYWRIGHT_CHROMIUM
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM } : {});
const problems = [];

// Set while a journey is DELIBERATELY breaking a request, so the browser's
// own complaint about it is not counted as the app misbehaving. Nulled the
// moment that journey is done: anything it does not match is still a failure.
let expected = null;

/** Attach the four listeners that decide whether a page is broken. */
function watch(page, errs) {
  page.on('pageerror', e => errs.push(`pageerror: ${e.message}`));
  page.on('console', m => {
    const t = m.text();
    if (m.type() !== 'error') return;
    if (/ERR_CONNECTION_RESET|fonts\.googleapis/.test(t)) return;
    if (expected && expected.test(t)) return;
    errs.push(`console: ${t.slice(0, 200)}`);
  });
  // Same-origin only: this sandbox blocks the Google Fonts CDN, which is an
  // egress policy here and not an app fault.
  page.on('requestfailed', r => {
    if (!r.url().startsWith(BASE)) return;
    const text = `${r.url()} ${r.failure()?.errorText}`;
    if (expected && expected.test(text)) return;
    errs.push(`requestfailed: ${r.url().slice(0, 120)} ${r.failure()?.errorText}`);
  });
}

/** Let the app finish reacting: no in-flight requests, then one paint.
 *
 * Every fixed `waitForTimeout` after a click is a bet on how fast the runner
 * is, and this branch has now been bitten by that three times (see the traps
 * list in REMEDIATION_STATUS). `networkidle` waits for the fetches the click
 * started; the short sleep after it covers the React render they feed. Kept
 * generous rather than tight -- it costs nothing when things are fast.
 */
async function settle(page, ms = 400) {
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

async function visit(page, view, errs) {
  const nav = page.getByRole('button', { name: view, exact: true }).first();
  await nav.waitFor({ state: 'visible', timeout: 10000 });
  await nav.click();
  await settle(page);
  const bodyText = (await page.textContent('body')) || '';
  if (bodyText.trim().length < 20) errs.push('rendered an empty page');
  return bodyText;
}

// ---------------------------------------------------------------- signed out
// A fresh context per screen, so one screen's leftover state cannot mask the
// next one's failure to fetch.
for (const view of VIEWS) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const errs = [];
  watch(page, errs);
  try {
    await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
    await visit(page, view, errs);
  } catch (e) {
    errs.push(`navigation: ${e.message.slice(0, 200)}`);
  }
  problems.push([`signed-out ${view}`, errs]);
  await ctx.close();
}

// ----------------------------------------------------------------- signed in
// One context for the whole journey: signing up, walking the app, changing a
// preference and signing out are one seller's session, and the point is what
// carries between them.
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();
// One shared buffer the listeners write into, DRAINED after each step so the
// errors land under the step that caused them. Pushing this array itself was
// the bug: `problems` stored a reference, so a later step's console noise
// appeared under "first run", and the `signedIn` guards below -- which used
// to read its length -- turned themselves off for the rest of the run.
const seen = [];
watch(page, seen);
const drain = () => seen.splice(0);
const email = `smoke+${Date.now()}@example.com`;
let signedIn = false;

try {
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
  await page.getByRole('button', { name: 'Log in', exact: true }).first().click();
  await page.getByRole('tab', { name: 'Sign up' }).click();
  await page.locator('input[type=email]').fill(email);
  await page.locator('input[type=password]').fill(PASSWORD);
  await page.getByRole('button', { name: 'Create account' }).click();
  // Waited on rather than slept through: a fixed pause is a bet on how fast
  // the runner is, and this step is a signup plus the whole signed-in boot
  // fetch. A timeout here still lands in the branch below, which says which
  // of the two things went wrong.
  await page.getByRole('button', { name: 'Create account' })
    .waitFor({ state: 'detached', timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(1500);   // let the boot fetches paint

  // Signed in means the dialog closed AND the account is on screen. Checking
  // only that the dialog closed would pass on a dismissed dialog.
  if (await page.getByRole('button', { name: 'Create account' }).count()) {
    const shown = (await page.textContent('body')) || '';
    seen.push(
      /database/i.test(shown)
        ? 'sign-up refused: the server has no database. Set DATABASE_URL — '
          + 'the signed-in half of this test cannot run without one.'
        : 'sign-up did not complete: the dialog is still open');
  } else if (!((await page.textContent('body')) || '').includes(email)) {
    seen.push('signed up, but the account is not shown anywhere');
  }
} catch (e) {
  seen.push(`sign-up: ${e.message.slice(0, 200)}`);
}
const signup = drain();
signedIn = !signup.length;
problems.push(['first run (sign up)', signup]);

// The same four screens, now with a seller behind them. Each one fetches for
// real here: listings, notifications, marketplaces, tokens, eBay status.
if (signedIn) {
  for (const view of VIEWS) {
    const errs = [];
    try {
      await visit(page, view, errs);
    } catch (e) {
      errs.push(`navigation: ${e.message.slice(0, 200)}`);
    }
    // Anything the shared listeners caught during THIS screen belongs to it.
    errs.push(...drain());
    problems.push([`signed-in ${view}`, errs]);
  }
}

// --- the theme has to survive a reload -------------------------------------
//
// It is applied twice: by an inline script in index.html before first paint,
// and by the store once React mounts. They read the seller's choice through
// completely different code, so they drift — and when they do, nothing fails.
// The app simply opens in light mode for a seller who chose dark, on every
// load, with the toggle agreeing. Only a real reload catches it.
const theme = [];
if (signedIn) {
  try {
    await page.getByRole('button', { name: 'Home', exact: true }).first().click();
    await page.waitForTimeout(500);
    const isDark = () => page.evaluate(
      () => document.documentElement.classList.contains('dark'));

    await page.getByRole('button', { name: /switch to dark mode/i }).first().click();
    await page.waitForTimeout(300);
    if (!await isDark()) theme.push('the dark-mode toggle did not turn dark mode on');

    await page.reload({ waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(500);
    if (!await isDark()) {
      theme.push('dark mode was chosen, then lost on reload');
    } else {
      // Only worth asking once the first leg held. With the page back in
      // light mode the toggle reads "switch to dark", so the locator below
      // would spend its whole timeout finding nothing and report a second
      // failure that is really the first one again.
      await page.getByRole('button', { name: /switch to light mode/i }).first().click();
      await page.waitForTimeout(300);
      await page.reload({ waitUntil: 'networkidle', timeout: 30000 });
      await page.waitForTimeout(500);
      if (await isDark()) theme.push('light mode was chosen, then came back dark on reload');
    }
  } catch (e) {
    theme.push(`theme: ${e.message.slice(0, 200)}`);
  }
  theme.push(...drain());
  problems.push(['theme survives a reload', theme]);
}

// --- a store we could not read is not an empty store ------------------------
//
// The one journey here that needs the server to misbehave, so the browser
// does it: page.route() answers /api/listings with a 503 without the app
// knowing the difference. That is the whole outage, from the seller's side.
//
// Every number on the dashboard is counted off that one read, so a failure
// makes them all zero -- and a zero on this screen is a decision: nothing
// live is a reason to go list something, nothing sold this week is a reason
// to cut prices. Both, on a morning when the database was briefly slow.
const outage = [];
if (signedIn) {
  // The 503 below is this test's doing, so the browser's complaint about it
  // is not a finding. Scoped to this journey and cleared straight after.
  expected = /503|api\/listings/;
  try {
    await page.route('**/api/listings*', r => r.fulfill({
      status: 503, contentType: 'application/json',
      body: JSON.stringify({ detail: "We couldn't load your listings just now." }),
    }));
    await page.reload({ waitUntil: 'networkidle', timeout: 30000 });
    await settle(page);

    const dash = (await page.textContent('body')) || '';
    if (!/couldn.t check/i.test(dash)) {
      outage.push('the store could not be read and the tiles did not say so');
    }
    for (const claim of ['everything currently live', 'nothing in the last 7 days',
                         'open one to finish & publish']) {
      if (dash.includes(claim)) outage.push(`tile still claims "${claim}" after a failed read`);
    }

    // And the listings area itself, one card down and one click away.
    await page.getByRole('button', { name: /Active on eBay/ }).first().click();
    await settle(page);
    const list = (await page.textContent('body')) || '';
    if (!/couldn.t load your listings/i.test(list)) {
      outage.push('the listings area did not say the read failed');
    }
    if (list.includes('Nothing live yet')) {
      outage.push('a failed read rendered as the empty store');
    }
    // The tab badges sit directly above that explanation and are counted off
    // the same empty page, so an outage used to read "Active 0" and "we
    // couldn't load your listings" at once, six lines apart.
    if (/Active\s*0/.test(list)) {
      outage.push('a tab still badges a count after a failed read');
    }
  } catch (e) {
    outage.push(`outage: ${e.message.slice(0, 200)}`);
  }
  await page.unroute('**/api/listings*').catch(() => {});
  // Drained INTO the journey, not discarded: `expected` above already
  // dropped the noise this test made, so anything still here is the app
  // reacting badly to an outage -- which is exactly what this asks about.
  outage.push(...drain());
  expected = null;
  problems.push(['an outage is not an empty store', outage]);
}

// --- a setting we could not read is not a setting ---------------------------
//
// The same question the dashboard one asks, on the screen where the answer is
// editable. Settings shows the seller's saved defaults; if the read fails and
// the panels render anyway, the app's own fallbacks appear as the seller's
// choices -- and Save then posts them, so a failed READ becomes an edit
// nobody made. Every panel here has a tri-state (loading / couldn't ask /
// answered) covered directly in lib/settingsSections.test.js; this is the
// part that unit test cannot see, which is whether the SCREEN is wired to it.
const prefsOut = [];
if (signedIn) {
  expected = /503|api\/prefs/;
  try {
    await page.route('**/api/prefs', r => r.fulfill({
      status: 503, contentType: 'application/json',
      body: JSON.stringify({ detail: "We couldn't read your saved settings." }),
    }));
    await page.reload({ waitUntil: 'networkidle', timeout: 30000 });
    await visit(page, 'Settings', prefsOut);
    await settle(page);

    const body = (await page.textContent('body')) || '';
    // Three panels each render this, so it survives one of them regressing --
    // which is what the fallback check below is for. Together they cover both
    // "no panel said anything" and "one panel said it anyway".
    if (!/couldn.t load your saved defaults/i.test(body)) {
      prefsOut.push('the defaults could not be read and the panels did not say so');
    }
    // The fallbacks, verbatim from the controls those panels render. Any of
    // them on screen means a value the seller never chose is being shown as
    // one they did -- and it is one Save away from being stored as such.
    for (const fallback of ['Off — only when I toggle Promote on a listing',
                            'Median Pricing', 'Package weight — lb']) {
      if (body.includes(fallback)) {
        prefsOut.push(`a default is shown as saved after a failed read: "${fallback}"`);
      }
    }
    // A panel that never leaves its shimmer is the other failure: the seller
    // waits on an answer that already came back and was an error.
    if (!await page.getByRole('button', { name: 'Try again' }).count()) {
      prefsOut.push('no way to retry the read that failed');
    }

    // And the Save button, which is the reason any of this matters. With the
    // defaults unread and eBay unconnected there is nothing safe to send, so
    // the honest answer is that nothing was saved. It used to say "Defaults
    // saved" -- a green tick, on a screen saying it could not read them.
    await page.getByRole('button', { name: 'Save defaults' }).first().click();
    await settle(page);
    const after = (await page.textContent('body')) || '';
    if (/defaults saved/i.test(after)) {
      prefsOut.push('Save reported "Defaults saved" having sent nothing');
    }
    if (!/nothing was saved/i.test(after)) {
      prefsOut.push('Save sent nothing and did not say so');
    }
  } catch (e) {
    prefsOut.push(`settings outage: ${e.message.slice(0, 200)}`);
  }
  await page.unroute('**/api/prefs').catch(() => {});
  prefsOut.push(...drain());
  expected = null;
  problems.push(['an unread setting is not a saved one', prefsOut]);
}

// --- the rest of a big store can actually be reached ------------------------
//
// The server caps the listings page and says so. Saying so is honest and, on
// its own, useless: the older listings are not on the page, not in the tab
// counts, and not findable by the search box, which filters what is already
// loaded. For a seller past the cap those records do not exist in this app.
//
// The two pages are served by page.route() rather than by making four
// thousand listings -- the server half is proved against a real database in
// test_the_older_listings_can_be_reached.py. What only a browser can answer
// is whether the button appears, whether clicking it APPENDS rather than
// replaces, and whether it stops.
const paging = [];
if (signedIn) {
  const card = (id) => ({ id, user_id: 'u', status: 'draft',
                          listing: { title: `Paged item ${id}` } });
  try {
    await page.route('**/api/listings*', (r) => {
      const second = r.request().url().includes('before=');
      r.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify(second
          ? { listings: [card('p3'), card('p4')], authed: true,
              db: { configured: true, connected: true }, truncated: false,
              total: 4, next_cursor: null }
          : { listings: [card('p1'), card('p2')], authed: true,
              db: { configured: true, connected: true }, truncated: true,
              total: 4, next_cursor: 'Y3Vyc29y' }),
      });
    });
    await page.reload({ waitUntil: 'networkidle', timeout: 30000 });
    // Not `visit()`: the nav button carries a draft-count badge, so with
    // listings on screen its accessible name is no longer just "Sell" and the
    // exact match that walk uses stops finding it.
    await page.getByRole('button', { name: 'Sell' }).first().click();
    await settle(page);

    const first = (await page.textContent('body')) || '';
    if (!first.includes('2 of 4')) {
      paging.push('a cut page did not say what it was cut from');
    }
    if (first.includes('Paged item p3')) {
      paging.push('page two was on screen before it was asked for');
    }
    const more = page.getByRole('button', { name: /Load older listings/ });
    if (!await more.count()) {
      paging.push('a cut page offered no way to reach the rest of the store');
    } else {
      await more.first().click();
      await settle(page);
      const after = (await page.textContent('body')) || '';
      // Appended, not replaced: everything on this screen -- the tab counts,
      // the search, the bulk checkboxes -- reads the loaded list, so a page
      // that REPLACED would hide the first two all over again.
      for (const id of ['p1', 'p2', 'p3', 'p4']) {
        if (!after.includes(`Paged item ${id}`)) {
          paging.push(`loading more lost ${id}`);
        }
      }
      // And it has to stop. The button lives inside the was-cut notice, so
      // what this actually checks is that a fully loaded store stops saying
      // it was cut -- which is the same claim from the other end, and the one
      // a seller reads. Both are gone or neither is.
      if (/more than we can show/i.test(after)) {
        paging.push('the whole store is loaded and the page still says it was cut');
      }
      if (await page.getByRole('button', { name: /Load older listings/ }).count()) {
        paging.push('the last page still offered more');
      }
    }
  } catch (e) {
    paging.push(`paging: ${e.message.slice(0, 200)}`);
  }
  await page.unroute('**/api/listings*').catch(() => {});
  paging.push(...drain());
  problems.push(['the rest of the store can be reached', paging]);
}

// --- an edit held back from eBay is a question, not silence ----------------
//
// P0-08's user-facing half, and the only part of it a browser can answer. The
// three-way merge is right to send NEITHER value when the seller and eBay
// both changed a field -- picking one silently is how a fix made in Seller
// Hub gets overwritten by a stale copy -- but refusing to choose is only half
// an answer. The seller edited a title here, pressed Update, was told "Your
// eBay listing has been updated", and their title never left the building.
//
// So: the question has to be ON SCREEN, above the fold, with both values and
// a way to answer; the answer has to reach the server; and a refused answer
// has to say so, because a "keep mine" that did not persist means the
// seller's value is STILL not going to eBay, which is the silence this
// exists to end.
//
// The listing is served by page.route(). Everything under test is real: the
// editor, the banner, the click, the request it makes and what the screen
// does with each reply.
const conflict = [];
if (signedIn) {
  const LID = 'conflicted-1';
  const listing = { title: 'Vintage denim jacket', price: 48, quantity: 1 };
  const CONFLICTS = [{ field: 'title', label: 'Title',
                       mine: 'Vintage denim jacket',
                       ebay: 'Vintage Levis denim jacket 1980s' }];
  let resolveCalls = 0;
  try {
    // Three patterns, most specific registered LAST: Playwright consults
    // handlers newest-first, and `*` in a glob does not cross a `/` -- so
    // `**/api/listings*` matches the list and its query string but never
    // `/api/listings/<id>`, which is how the first version of this quietly
    // talked to the real server instead.
    await page.route('**/api/listings*', (r) => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        listings: [{ id: LID, user_id: 'u', status: 'published', listing }],
        authed: true, db: { configured: true, connected: true },
        truncated: false, total: 1, next_cursor: null }),
    }));
    await page.route('**/api/listings/*', (r) => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ id: LID, user_id: 'u', status: 'published',
                             listing, conflicts: CONFLICTS }),
    }));
    // The first answer is refused, because "it saved" is the easy half.
    await page.route(`**/api/listings/${LID}/resolve-conflict`, (r) => {
      resolveCalls += 1;
      if (resolveCalls === 1) {
        return r.fulfill({ status: 503, contentType: 'application/json',
          body: JSON.stringify({ detail: "Couldn't save that choice just now. "
                                         + "Try again in a moment." }) });
      }
      return r.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ ok: true, listing, conflicts: [],
          message: "Saved. It'll go to eBay the next time you update this "
                   + "listing." }) });
    });
    expected = /503|resolve-conflict/;

    await page.reload({ waitUntil: 'networkidle', timeout: 30000 });
    await page.getByRole('button', { name: 'Sell' }).first().click();
    await settle(page);
    await page.getByText('Vintage denim jacket').first().click();
    await settle(page);

    const editor = (await page.textContent('body')) || '';
    if (!/changed in two places/i.test(editor)) {
      conflict.push('an edit held back from eBay was not raised with the seller');
    }
    // Both values, because the choice is not answerable without them.
    if (!editor.includes('Vintage Levis denim jacket 1980s')) {
      conflict.push("the banner did not show what eBay's copy says");
    }
    const keepMine = page.getByRole('button', { name: /Keep your/ });
    if (!await keepMine.count()) {
      conflict.push('the question was raised with no way to answer it');
    } else {
      // A refused answer must not read as a settled one.
      await keepMine.first().click();
      await settle(page);
      const afterFail = (await page.textContent('body')) || '';
      if (!/couldn.t save that choice/i.test(afterFail)) {
        conflict.push('an answer the server refused was not reported as refused');
      }
      if (!/changed in two places/i.test(afterFail)) {
        conflict.push('a refused answer cleared the question anyway');
      }
      // And the retry settles it.
      await page.getByRole('button', { name: /Keep your/ }).first().click();
      await settle(page);
      const afterOk = (await page.textContent('body')) || '';
      if (/changed in two places/i.test(afterOk)) {
        conflict.push('an answered conflict stayed on screen');
      }
      if (!/go to eBay the next time/i.test(afterOk)) {
        conflict.push('nothing told the seller what happens to their answer');
      }
    }
    if (resolveCalls !== 2) {
      conflict.push(`the answer reached the server ${resolveCalls} times, expected 2`);
    }
  } catch (e) {
    conflict.push(`conflict: ${e.message.slice(0, 200)}`);
  }
  await page.unroute(`**/api/listings/${LID}/resolve-conflict`).catch(() => {});
  await page.unroute('**/api/listings/*').catch(() => {});
  await page.unroute('**/api/listings*').catch(() => {});
  conflict.push(...drain());
  expected = null;
  problems.push(['a held-back edit is a question, not silence', conflict]);
}

// --- a store from another eBay account is explained, and unlinked ----------
//
// P0-04's user-facing half. A seller who connects a SECOND eBay account has
// records here that belong to the first one, and this app deliberately leaves
// them alone -- it will not sync, edit or end a listing on a store the
// current token does not own. Left unexplained that reads as the app losing
// track of the seller's listings.
//
// The banner counts in SQL over the whole store; the button that answers it
// releases at most one pass' worth. So the second thing this asks is whether
// a partial unlink SAYS it was partial: without that, the banner simply
// returns with a smaller number and no reason, which is how a seller learns
// to distrust the button.
const foreign = [];
if (signedIn) {
  let released = 0;
  // This journey claims a connection the server does not have, so the panels
  // that then ask eBay for policies and a ship-from location are answered
  // 400 by the real backend. That is this test's doing, not the app's.
  // Scoped here and cleared straight after; the assertions below are about
  // what the screen SAYS, which those 400s do not touch.
  expected = /400 \(Bad Request\)/;
  try {
    await page.route('**/api/ebay/status', (r) => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ connected: true, username: 'current-seller',
                             foreign_listings: released ? 0 : 4,
                             unowned_listings: 0, oauth_missing: [] }),
    }));
    await page.route('**/api/ebay/release-foreign-listings', (r) => {
      released += 1;
      return r.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ released: 3, released_unowned: 0,
                               remaining: 1 }) });
    });
    await page.reload({ waitUntil: 'networkidle', timeout: 30000 });
    await visit(page, 'Settings', foreign);
    await settle(page);

    const before = (await page.textContent('body')) || '';
    if (!/isn.t the one connected/i.test(before)) {
      foreign.push('listings from another eBay account were not explained');
    }
    if (!/4 listings/i.test(before)) {
      foreign.push('the banner did not say how many');
    }
    const unlink = page.getByRole('button', { name: /Unlink from the old account/ });
    if (!await unlink.count()) {
      foreign.push('nothing offered a way out of the old account');
    } else {
      await unlink.first().click();
      // Its own confirm first: unlinking drops the eBay link on real records.
      await page.getByRole('button', { name: 'Unlink them' }).first().click();
      await settle(page);
      const after = (await page.textContent('body')) || '';
      if (!/still to go|press it again/i.test(after)) {
        foreign.push('a partial unlink reported itself as a finished one');
      }
      if (released !== 1) {
        foreign.push(`the unlink reached the server ${released} times, expected 1`);
      }
    }
  } catch (e) {
    foreign.push(`foreign account: ${e.message.slice(0, 200)}`);
  }
  await page.unroute('**/api/ebay/release-foreign-listings').catch(() => {});
  await page.unroute('**/api/ebay/status').catch(() => {});
  // Same reason as the journey above: drop the faked connection from the
  // store's cache before the next one runs.
  await page.reload({ waitUntil: 'networkidle', timeout: 30000 })
    .catch(() => {});
  await page.waitForTimeout(1000);
  foreign.push(...drain());
  expected = null;
  problems.push(['another account\'s listings are explained', foreign]);
}

// --- a publish we could not hear the end of is not a failure ---------------
//
// The last of the audit's journeys, and the one with the sharpest consumer
// consequence. When a write to eBay goes out and no answer comes back, the
// listing may or may not exist over there. "It failed, try again" produces a
// duplicate live listing the seller pays fees on; "it worked" leaves them
// looking for a listing that may not be there. The only honest answer names
// the doubt and sends them to check.
//
// The backend already settles this (P0-07, P2-03) and its own tests cover the
// wording. What no test covers is whether the EDITOR says it: the screen has
// its own success path, its own toast and its own published-screen swap, and
// any of the three could quietly turn "we do not know" into one or the other.
const unknown = [];
if (signedIn) {
  const LID = 'live-1';
  const listing = { title: 'Copper kettle', price: 30, quantity: 1,
                    ebay_listing_id: '110000000001' };
  const LOST = "We lost contact with eBay while sending this, so we can't "
             + "tell whether it went through. Check your eBay listings before "
             + "trying again.";
  let published = 0;
  try {
    expected = /400 \(Bad Request\)/;
    await page.route('**/api/ebay/status', (r) => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ connected: true, username: 'current-seller',
                             foreign_listings: 0, unowned_listings: 0,
                             oauth_missing: [] }),
    }));
    await page.route('**/api/listings*', (r) => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        listings: [{ id: LID, user_id: 'u', status: 'published', listing }],
        authed: true, db: { configured: true, connected: true },
        truncated: false, total: 1, next_cursor: null }),
    }));
    await page.route('**/api/listings/*', (r) => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ id: LID, user_id: 'u', status: 'published',
                             listing, conflicts: [] }),
    }));
    await page.route(`**/api/save/${LID}`, (r) => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ ok: true, listing }),
    }));
    await page.route('**/api/publish', (r) => {
      published += 1;
      // The shape the server sends when a write's outcome is unknown: not
      // published, and NOT reported as a refusal either.
      return r.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ ok: false, published: false,
                               outcome_unknown: true, message: LOST,
                               issues: [] }) });
    });

    await page.reload({ waitUntil: 'networkidle', timeout: 30000 });
    await page.getByRole('button', { name: 'Sell' }).first().click();
    await settle(page);
    await page.getByText('Copper kettle').first().click();
    await settle(page);

    const update = page.getByRole('button', { name: /Update Live Listing/ });
    if (!await update.count()) {
      unknown.push('a live listing did not offer to update itself');
    } else {
      await update.first().click();
      // It asks before spending a revise; the confirm is part of the flow.
      const go = page.getByRole('button', { name: /Update|Publish|Yes/ });
      if (await go.count() > 1) await go.last().click();
      await settle(page);

      const after = (await page.textContent('body')) || '';
      if (published < 1) {
        unknown.push('the update never reached the server');
      }
      // The three things that must not happen.
      // The success screen's own words, not a paraphrase, and deliberately
      // not "Live on eBay" -- the listing CARD carries that badge
      // permanently, so matching it would be a tripwire that cannot trip.
      if (/Listing published!|Listing updated!/.test(after)) {
        unknown.push('an unknown outcome was reported as a live listing');
      }
      if (!/can.t tell whether it went through|lost contact with ebay/i.test(after)) {
        unknown.push('the seller was not told the outcome is unknown');
      }
      if (!/check your ebay listings/i.test(after)) {
        unknown.push('nothing told the seller what to do about it');
      }
    }
  } catch (e) {
    unknown.push(`unknown outcome: ${e.message.slice(0, 200)}`);
  }
  await page.unroute('**/api/publish').catch(() => {});
  await page.unroute(`**/api/save/${LID}`).catch(() => {});
  await page.unroute('**/api/listings/*').catch(() => {});
  await page.unroute('**/api/listings*').catch(() => {});
  await page.unroute('**/api/ebay/status').catch(() => {});
  // Back to the real server before the next journey. The store caches the
  // eBay connection state, and leaving a faked "connected" behind makes the
  // panels below ask eBay for things the real backend refuses -- 400s that
  // land under whichever journey runs next, blaming it for this one's fake.
  await page.reload({ waitUntil: 'networkidle', timeout: 30000 })
    .catch(() => {});
  await page.waitForTimeout(1000);
  unknown.push(...drain());
  expected = null;
  problems.push(['an unknown publish outcome says so', unknown]);
}

// --- deleting an account asks, warns, and takes a password ------------------
//
// The most irreversible button in the app, and the two things that guard it
// are on opposite sides of the wire. In the browser: the dialog must warn that
// anything already published STAYS LIVE on eBay -- deleting here removes only
// this app's copy, and a seller who does not know that walks away from
// listings still taking orders. That warning keys off `counted`, so the read
// failing must make it MORE cautious, not less: no count is named, and the
// warning is given anyway. On the server: a session token is not enough, so a
// wrong password has to be refused there, not merely disabled here.
//
// Nothing is deleted. The wrong password is the point, and the account is
// still standing at the end -- which the log-out journey below then uses.
const del = [];
if (signedIn) {
  expected = /503|account\/summary/;
  try {
    await page.route('**/api/account/summary', r => r.fulfill({
      status: 503, contentType: 'application/json',
      body: JSON.stringify({ detail: "We couldn't count your listings." }),
    }));
    await visit(page, 'Settings', del);
    await page.getByRole('button', { name: /Delete my account/ }).first().click();
    await settle(page);

    // Scoped to the dialog, not the page. The card BEHIND it carries the same
    // sentence permanently, so a body-wide match would pass on a dialog that
    // said nothing at all -- a tripwire that can never trip, which is worse
    // than no test.
    const box = page.getByRole('dialog');
    const dialog = (await box.textContent()) || '';
    if (!/stays live on eBay/i.test(dialog)) {
      del.push('the count could not be read and the dialog dropped the eBay warning');
    }
    if (/\d+ listings? and every photo/i.test(dialog)) {
      del.push('the dialog named a listing count it never read');
    }
    // The confirm button stays out of reach until a password is typed, so a
    // stray tap on a borrowed phone cannot do this.
    const confirmBtn = box.getByRole('button', { name: 'Delete permanently' });
    if (!await confirmBtn.isDisabled()) {
      del.push('delete is clickable with no password typed');
    }

    // And the half only the server can answer. A leaked session must not be
    // enough: the wrong password is refused THERE.
    expected = /40[0-9]|account\/delete|account\/summary|503/;
    await page.locator('input[type=password]').last().fill('not-the-password');
    await confirmBtn.click();
    await settle(page);
    if (!await page.getByRole('button', { name: 'Delete permanently' }).count()) {
      del.push('the wrong password closed the dialog');
    }
    await page.getByRole('button', { name: 'Keep my account' }).click();
    await page.waitForTimeout(500);

    // Still here. If the account had gone, this reload would land on the
    // signed-out landing page -- which is the failure this journey exists to
    // catch, and the reason it uses a wrong password rather than the real one.
    await page.reload({ waitUntil: 'networkidle', timeout: 30000 });
    await settle(page);
    if (!((await page.textContent('body')) || '').includes(email)) {
      del.push('a refused delete took the account anyway');
    }
  } catch (e) {
    del.push(`delete account: ${e.message.slice(0, 200)}`);
  }
  await page.unroute('**/api/account/summary').catch(() => {});
  del.push(...drain());
  expected = null;
  problems.push(['deleting asks before it erases', del]);
}

// --- signing out has to end the session ------------------------------------
//
// Not just clear the greeting. The reload is the half that matters: without
// it this passes on a logout that empties React state and leaves the session
// standing on the server, which on a shared machine is someone else's store
// one refresh away.
const out = [];
if (signedIn) {
  try {
    await page.getByText('Log out').first().click();
    await page.getByRole('button', { name: 'Log in', exact: true })
      .first().waitFor({ state: 'visible', timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(500);
    if (((await page.textContent('body')) || '').includes(email)) {
      out.push('logged out, but the previous account is still on screen');
    }
    await page.reload({ waitUntil: 'networkidle', timeout: 30000 });
    await settle(page);
    if (((await page.textContent('body')) || '').includes(email)) {
      out.push('logged out, but a reload brought the previous account back');
    }
    if (!await page.getByRole('button', { name: 'Log in', exact: true }).count()) {
      out.push('logged out, but there is no way to log back in');
    }
  } catch (e) {
    out.push(`log out: ${e.message.slice(0, 200)}`);
  }
  out.push(...drain());
  problems.push(['log out ends the session', out]);
}
await ctx.close();

// --------------------------------------------------------------- phone-sized
// Most sellers are on a phone.
const mctx = await browser.newContext({
  viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
const mpage = await mctx.newPage();
const merrs = [];
watch(mpage, merrs);
// Guarded like every other leg: an unreachable server here used to end the
// run in a Node stack trace instead of the report, which hides the eleven
// results already collected behind the twelfth one's failure.
try {
  await mpage.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
  await mpage.waitForTimeout(1000);
  const scrollW = await mpage.evaluate(() => document.documentElement.scrollWidth);
  if (scrollW > 400) merrs.push(`horizontal overflow at 390px: scrollWidth=${scrollW}`);
} catch (e) {
  merrs.push(`navigation: ${e.message.slice(0, 200)}`);
}
problems.push(['mobile-390', merrs]);
await browser.close();

let bad = 0;
for (const [name, errs] of problems) {
  if (errs.length) { bad++; console.log(`FAIL ${name}`); errs.forEach(e => console.log(`     ${e}`)); }
  else console.log(`ok   ${name}`);
}
console.log(bad ? `\n${bad} check(s) with problems` : '\nall checks clean');

process.exit(bad ? 1 : 0);
