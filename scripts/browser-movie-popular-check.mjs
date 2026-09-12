/**
 * Internal analytics and Popular on Click TV (PART 22).
 *
 * Two things are worth testing here and both are about honesty.
 *
 * The row must never claim to be something it is not. It counts playback on
 * Click TV itself; presenting that as international Trending would tell every
 * viewer something false. The label comes from the data file, and this test
 * reads what is actually on screen.
 *
 * The payload must never carry what it must not carry. The beacon is
 * intercepted and its body inspected: no stream URL, no backup, no header, no
 * token. And with the endpoint failing, the site must carry on as if
 * analytics did not exist.
 *
 * Usage: node scripts/browser-movie-popular-check.mjs [baseUrl]
 */
import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const failures = [];
const passes = [];

function check(condition, message, detail = '') {
  if (condition) passes.push(message);
  else failures.push(`${message}${detail ? ` — ${detail}` : ''}`);
}

const browser = await chromium.launch({ headless: true });
const TELEMETRY_ORIGIN = 'https://telemetry.test';

async function clickNavButton(page, containerSelector, buttonSelector, label) {
  const deadline = Date.now() + 30000;
  for (;;) {
    const seen = await page.$$eval(`${containerSelector} ${buttonSelector}`,
      (nodes) => nodes.map((node) => node.textContent.trim()));
    if (seen.some((text) => text.includes(label))) break;
    if (Date.now() > deadline) throw new Error(`${label} never appeared; saw ${JSON.stringify(seen)}`);
    await page.waitForTimeout(300);
  }
  await page.$$eval(`${containerSelector} ${buttonSelector}`, (nodes, text) => {
    const target = nodes.find((node) => node.textContent.trim().includes(text));
    if (target) target.click();
  }, label);
}

function popularDocument(ids) {
  return {
    version: 1,
    updated_at: new Date().toISOString(),
    label: 'Popular on Click TV',
    signal: 'internal_qualified_plays',
    signal_note: 'Counted from playback on Click TV itself. This is NOT an international trending list.',
    window_days: 7,
    minimum_qualified_plays: 3,
    count: ids.length,
    excluded_inactive: 0,
    items: ids.map((id, index) => ({
      id: id.id, name: id.name, category: id.category || 'Mix',
      qualified_plays: 9 - index, content_type: 'movie'
    }))
  };
}

async function readRow(page) {
  return page.evaluate(() => {
    const panel = document.querySelector('#moviePopularPanel');
    return {
      hidden: Boolean(panel?.hidden),
      heading: panel?.querySelector('.movie-continue-title')?.textContent?.trim() || '',
      cards: panel ? panel.querySelectorAll('.movie-related-card').length : 0,
      names: panel
        ? [...panel.querySelectorAll('.movie-related-card strong')].map((n) => n.textContent.trim())
        : [],
      text: panel?.textContent || ''
    };
  });
}

/** With no popular file published at all - which is today's real state. */
async function runNoData(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({ viewport });
  await context.route('**/popular-clicktv.json*', (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));

  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  await clickNavButton(page, navSelector, '.final-main-button', 'Movies');
  await page.waitForTimeout(1500);
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Home');
  await page.waitForTimeout(4000);

  const row = await readRow(page);
  const home = await page.evaluate(() =>
    document.querySelectorAll('#sidebarList .movie-card, #sidebarList .series-card, #movieHomeSections .movie-card').length);

  check(row.hidden && row.cards === 0,
    `[${label}] with no data the row is absent, not an empty shelf`, JSON.stringify(row));
  check(home > 0, `[${label}] Movie Home still works without it`, String(home));
  check(pageErrors.length === 0, `[${label}] a missing popular file raises no error`, pageErrors.join(' | '));

  // The sidebar must not have gained a category for this.
  const nav = await page.$$eval(`${subNavSelector} .final-sub-button`,
    (nodes) => nodes.map((n) => n.textContent.trim()));
  check(!nav.some((entry) => /popular/i.test(entry)),
    `[${label}] no Popular category was added to the sidebar`, JSON.stringify(nav));
  await context.close();
}

/** With a real row published. */
async function runWithData(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  await clickNavButton(page, navSelector, '.final-main-button', 'Movies');
  await page.waitForTimeout(1500);

  // Real ids from the published index, so the cards resolve to real titles.
  const picks = await page.evaluate(async () => {
    const index = await window.loadMovieBrowseIndex();
    return (index || []).filter((row) => (row.type || 'movie') === 'movie').slice(0, 3)
      .map((row) => ({ id: row.id, name: row.name, category: row.category }));
  });
  check(picks.length >= 2, `[${label}] real titles were available to publish`, String(picks.length));

  await context.route('**/popular-clicktv.json*', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(popularDocument(picks))
  }));

  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Home');
  await page.waitForTimeout(4500);
  const row = await readRow(page);

  check(!row.hidden && row.cards === picks.length,
    `[${label}] a real row is shown`, JSON.stringify({ hidden: row.hidden, cards: row.cards }));
  check(/popular on click tv/i.test(row.heading),
    `[${label}] it is labelled Popular on Click TV`, row.heading);
  check(!/trending/i.test(row.heading),
    `[${label}] it is never labelled Trending`, row.heading);
  check(!/international/i.test(row.text),
    `[${label}] nothing on the row claims an international signal`);
  check(row.names[0] === picks[0].name,
    `[${label}] the most-played title leads the row`,
    JSON.stringify({ wanted: picks[0].name, got: row.names[0] }));

  // Opening one goes to that title's detail.
  await page.locator('#moviePopularPanel .movie-related-card').first().click({ force: true });
  await page.waitForTimeout(3000);
  const opened = await page.evaluate(() => ({
    visible: !document.querySelector('#movieDetailPanel')?.hidden,
    title: document.querySelector('.movie-detail-title')?.textContent?.trim() || ''
  }));
  check(opened.visible && opened.title === picks[0].name,
    `[${label}] a card opens that exact title`, JSON.stringify(opened));

  // Leaving Movies takes the row with it.
  await clickNavButton(page, navSelector, '.final-main-button', 'Live TV');
  await page.waitForTimeout(2500);
  const away = await readRow(page);
  check(away.hidden, `[${label}] the row does not follow the viewer to Live TV`);

  check(pageErrors.length === 0, `[${label}] no page errors`, pageErrors.join(' | '));
  await context.close();
}

/** What the beacon actually carries, and what happens when it fails. */
async function runPayload(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({ viewport });
  const bodies = [];
  let healthChecks = 0;
  let eventPosts = 0;

  // A telemetry endpoint that exists, is healthy, and then fails every event.
  await context.route('**/runtime-config.json*', async (route) => {
    const response = await route.fetch();
    let config = {};
    try { config = await response.json(); } catch (_) { config = {}; }
    config.telemetry_url = TELEMETRY_ORIGIN;
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(config) });
  });
  await context.route(`${TELEMETRY_ORIGIN}/health`, (route) => {
    healthChecks += 1;
    route.fulfill({
      status: 200, contentType: 'application/json',
      headers: { 'Access-Control-Allow-Origin': '*' },
      body: JSON.stringify({ ok: true, kv_bound: true })
    });
  });
  await context.route(`${TELEMETRY_ORIGIN}/event`, (route) => {
    eventPosts += 1;
    bodies.push(route.request().postData() || '');
    // Every single one fails. The site must not care.
    route.fulfill({ status: 500, contentType: 'text/plain', body: 'nope' });
  });

  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(5000);
  await clickNavButton(page, navSelector, '.final-main-button', 'Movies');
  await page.waitForTimeout(1500);
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Bangla');
  await page.waitForTimeout(3500);

  check(healthChecks > 0, `[${label}] the telemetry health check ran`, String(healthChecks));

  // Open a detail: that is a detail_open event.
  const info = page.locator('#sidebarList .movie-card-info').first();
  if (await info.count()) {
    await info.click({ force: true });
    await page.waitForTimeout(2500);
  }

  const posted = bodies.filter(Boolean);
  check(posted.length > 0, `[${label}] a usage event was actually sent`, String(bodies.length));

  if (posted.length) {
    const parsed = posted.map((body) => { try { return JSON.parse(body); } catch (_) { return {}; } });
    const combined = JSON.stringify(parsed);
    const forbidden = ['http://', 'https://', 'backups', 'headers', 'authorization',
      'cookie', 'token', 'api_key', 'playback_id', '.mkv', '.m3u8'];
    const leaked = forbidden.filter((needle) => combined.toLowerCase().includes(needle));
    check(leaked.length === 0,
      `[${label}] the payload carries no URL, header, token or playback id`,
      JSON.stringify(leaked));

    const keys = [...new Set(parsed.flatMap((row) => Object.keys(row)))].sort();
    const allowed = ['content_type', 'episode_number', 'event_type', 'item_id',
      'season_number', 'series_id', 'session_id', 'ts'];
    check(keys.every((key) => allowed.includes(key)),
      `[${label}] only the agreed fields are sent`, JSON.stringify(keys));
    check(parsed.some((row) => row.event_type === 'detail_open'),
      `[${label}] opening a detail reports detail_open`,
      JSON.stringify(parsed.map((r) => r.event_type)));
  }

  // The endpoint has failed on every request. The site must be unaffected.
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Hindi');
  await page.waitForTimeout(3500);
  const stillWorks = await page.evaluate(() => ({
    cards: document.querySelectorAll('#sidebarList .movie-card, #sidebarList .series-card, #movieHomeSections .movie-card').length,
    videos: document.querySelectorAll('video').length
  }));
  check(stillWorks.cards > 0,
    `[${label}] with analytics failing on every call the site carries on`,
    JSON.stringify(stillWorks));
  check(stillWorks.videos === 1, `[${label}] the player is untouched by analytics`);
  check(pageErrors.length === 0,
    `[${label}] a failing analytics endpoint raises no page error`, pageErrors.join(' | '));

  // And it does not hammer: the same event for the same item is sent once.
  const beforeRepeat = eventPosts;
  if (await info.count()) {
    await clickNavButton(page, subNavSelector, '.final-sub-button', 'Bangla');
    await page.waitForTimeout(3000);
    for (let n = 0; n < 3; n += 1) {
      await page.locator('#sidebarList .movie-card-info').first().click({ force: true });
      await page.waitForTimeout(900);
      await page.locator('.movie-detail-close').first().click({ force: true }).catch(() => {});
      await page.waitForTimeout(600);
    }
  }
  check(eventPosts - beforeRepeat <= 2,
    `[${label}] re-opening the same detail does not re-report it`,
    `${eventPosts - beforeRepeat} extra posts`);

  await context.close();
}

try {
  await runNoData('desktop', { width: 1280, height: 800 }, '#desktopMainNav', '#desktopSubNav');
  await runWithData('desktop', { width: 1280, height: 800 }, '#desktopMainNav', '#desktopSubNav');
  await runPayload('desktop', { width: 1280, height: 800 }, '#desktopMainNav', '#desktopSubNav');
  await runWithData('mobile', { width: 390, height: 844 }, '#mobileMainNav', '#mobileSubNav');
} finally {
  await browser.close();
}

console.log(`\n${passes.length} passed, ${failures.length} failed`);
for (const pass of passes) console.log(`  PASS  ${pass}`);
for (const failure of failures) console.log(`  FAIL  ${failure}`);
if (failures.length) process.exit(1);
