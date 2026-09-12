/**
 * Loading / empty / error / offline states (PART 20).
 *
 * Every failure here is a real one: the JSON is actually made to 404, the
 * context is actually put offline, the poster URL actually does not resolve.
 * Nothing is stubbed at the function level, because the thing worth testing
 * is what a viewer on a bad connection sees.
 *
 * What it pins: a failure is bounded and offers a Retry; a failure never
 * blanks content that is already on screen; an empty state says which way
 * out to take; a broken poster leaves no broken-image icon; and a dead link
 * says so instead of quietly playing something else.
 *
 * Usage: node scripts/browser-movie-states-check.mjs [baseUrl]
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

async function readList(page) {
  return page.evaluate(() => ({
    cards: document.querySelectorAll('#sidebarList .movie-card, #sidebarList .series-card, #movieHomeSections .movie-card').length,
    skeletons: document.querySelectorAll('.movie-skeleton-card').length,
    message: document.querySelector('.movie-prompt-msg span')?.textContent?.trim() || '',
    retry: Boolean(document.querySelector('.movie-retry-btn')),
    actions: [...document.querySelectorAll('.movie-prompt-msg button')].map((b) => b.textContent.trim()),
    // Scoped to the list: the player keeps its own loader element in the
    // DOM permanently, and that one is not ours to count.
    spinning: document.querySelectorAll('#sidebarList .premium-loader').length
  }));
}

async function openMovies(page, navSelector) {
  await clickNavButton(page, navSelector, '.final-main-button', 'Movies');
  await page.waitForTimeout(1500);
}

// --- 1. a discovery JSON that 404s ----------------------------------------
async function runFetchFailure(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({ viewport });
  let attempts = 0;
  await context.route('**/data/movies/discovery/just-added.json*', (route) => {
    attempts += 1;
    route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
  });

  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  await openMovies(page, navSelector);

  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Just Added');
  await page.waitForTimeout(4000);
  const failed = await readList(page);

  check(failed.retry, `[${label}] a failed shelf offers a Retry`, JSON.stringify(failed));
  check(failed.spinning === 0, `[${label}] no spinner is left turning`, String(failed.spinning));
  check(attempts >= 2 && attempts <= 4,
    `[${label}] the retry is bounded, not a loop`, `${attempts} attempts`);
  check(pageErrors.length === 0, `[${label}] a 404 raises no page error`, pageErrors.join(' | '));

  // The page must still work: another section loads normally.
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Bangla');
  await page.waitForTimeout(3500);
  const other = await readList(page);
  check(other.cards > 0,
    `[${label}] one failed shelf does not break the rest of Movies`, JSON.stringify(other.cards));

  // And Retry actually re-fetches once the route is healthy again.
  await context.unroute('**/data/movies/discovery/just-added.json*');
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Just Added');
  await page.waitForTimeout(4000);
  const recovered = await readList(page);
  check(recovered.cards > 0,
    `[${label}] the shelf loads once the data is reachable again`, JSON.stringify(recovered));

  await context.close();
}

// --- 2. a failure that arrives with content already on screen --------------
// Movies load, the series merge for the same category fails. The films that
// did load must stay: turning a partial failure into an empty grid would be
// worse than the failure.
async function runRefreshFailureKeepsContent(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({ viewport });
  await context.route('**/data/series/**', (route) =>
    route.fulfill({ status: 503, contentType: 'application/json', body: '{}' }));

  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  await openMovies(page, navSelector);
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Bangla');
  await page.waitForTimeout(4500);

  const kept = await readList(page);
  check(kept.cards > 0,
    `[${label}] films stay on screen when the series half of the category fails`,
    JSON.stringify(kept));
  check(kept.spinning === 0, `[${label}] still no endless spinner in the list`, String(kept.spinning));
  check(pageErrors.length === 0, `[${label}] a partial failure raises no page error`, pageErrors.join(' | '));
  await context.close();
}

// --- 2b. an index that fails before it was ever cached ----------------------
async function runIndexFailure(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({ viewport });
  let attempts = 0;
  await context.route('**/data/movies/search-index.json*', (route) => {
    attempts += 1;
    route.fulfill({ status: 503, contentType: 'application/json', body: '{}' });
  });

  const page = await context.newPage();
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  await openMovies(page, navSelector);
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Home');
  await page.waitForTimeout(2500);

  // A genre browse needs the index, which is unreachable.
  await page.locator('.movie-genre-chip').nth(1).click({ force: true });
  await page.waitForTimeout(4500);
  const failed = await readList(page);

  check(failed.retry,
    `[${label}] an unreachable browse index offers a Retry, not a silent empty genre`,
    JSON.stringify(failed));
  check(attempts >= 2 && attempts <= 6,
    `[${label}] the index retry is bounded`, `${attempts} attempts`);
  check(failed.spinning === 0, `[${label}] no spinner left in the list`, String(failed.spinning));
  await context.close();
}

// --- 3. empty states carry the way out --------------------------------------
async function runEmptyStates(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  await openMovies(page, navSelector);

  // Watchlist: nothing saved yet.
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'My Watchlist');
  await page.waitForTimeout(2500);
  const watchlist = await readList(page);
  check(watchlist.cards === 0 && watchlist.actions.some((a) => /Browse Movies/i.test(a)),
    `[${label}] an empty watchlist offers Browse Movies`, JSON.stringify(watchlist));

  // Search with something no catalogue holds.
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Bangla');
  await page.waitForTimeout(3000);
  await page.evaluate(() => {
    const input = document.querySelector('#searchInput') || document.querySelector('#mobileSearchInput');
    if (!input) return;
    input.value = 'zzzzqqqqxxxx';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.waitForTimeout(1800);
  const search = await readList(page);
  check(search.cards === 0 && search.actions.length > 0,
    `[${label}] a search with no result offers a way to clear it`, JSON.stringify(search));
  check(!/undefined|NaN/.test(search.message),
    `[${label}] the empty message is real text`, search.message);

  await context.close();
}

// --- 4. broken artwork ------------------------------------------------------
async function runBrokenArtwork(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({ viewport });
  // Every poster host fails. Nothing about the layout may depend on them.
  await context.route(/\.(jpg|jpeg|png|webp)(\?.*)?$/i, (route) => route.abort());

  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  await openMovies(page, navSelector);
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Bangla');
  await page.waitForTimeout(4000);

  const artwork = await page.evaluate(() => {
    const images = [...document.querySelectorAll('#sidebarList img')];
    return {
      cards: document.querySelectorAll('#sidebarList .movie-card').length,
      placeholders: document.querySelectorAll('#sidebarList .movie-poster-placeholder').length,
      broken: images.filter((img) => img.complete && img.naturalWidth === 0).length
    };
  });

  check(artwork.cards > 0, `[${label}] cards still render with no artwork at all`, String(artwork.cards));
  check(artwork.broken === 0,
    `[${label}] no broken-image icon is left on screen`, String(artwork.broken));
  check(artwork.placeholders > 0,
    `[${label}] a poster that fails becomes an honest placeholder`, String(artwork.placeholders));
  check(pageErrors.length === 0, `[${label}] broken artwork raises no page error`, pageErrors.join(' | '));

  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check(overflow <= 1, `[${label}] no horizontal overflow without artwork`, `${overflow}px`);
  await context.close();
}

// --- 5. a detail for something that is gone ---------------------------------
async function runUnavailableDetail(label, viewport, navSelector) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  await openMovies(page, navSelector);

  const before = await page.evaluate(() =>
    document.querySelector('#metaTitle')?.textContent?.trim() || '');
  await page.evaluate(() => window.showMovieDetailUnavailable('A Withdrawn Film'));
  await page.waitForTimeout(1200);

  const gone = await page.evaluate(() => ({
    visible: !document.querySelector('#movieDetailPanel')?.hidden,
    text: document.querySelector('.movie-detail-gone')?.textContent?.trim() || '',
    back: Boolean(document.querySelector('.movie-detail-back')),
    playing: document.querySelector('#metaTitle')?.textContent?.trim() || '',
    videos: document.querySelectorAll('video').length
  }));

  check(gone.visible && /not available/i.test(gone.text),
    `[${label}] a withdrawn title says so`, gone.text.slice(0, 60));
  check(gone.back, `[${label}] it offers Back to Movies`);
  check(gone.playing === before,
    `[${label}] it does NOT auto-play something else instead`,
    JSON.stringify({ before, now: gone.playing }));
  check(gone.videos === 1, `[${label}] still exactly one player element`);

  await page.locator('.movie-detail-back').click({ force: true });
  await page.waitForTimeout(2500);
  const back = await page.evaluate(() => ({
    panelHidden: Boolean(document.querySelector('#movieDetailPanel')?.hidden),
    cards: document.querySelectorAll('#sidebarList .movie-card, #sidebarList .series-card, #movieHomeSections .movie-card').length
  }));
  check(back.panelHidden, `[${label}] Back closes the unavailable state`);
  // Movie Home is rows now, not one flat grid, so real content can be in
  // either place - what this holds is that Back lands on content at all.
  check(back.cards > 0, `[${label}] Back lands on real movie content`, String(back.cards));
  await context.close();
}

// --- 6. offline ------------------------------------------------------------
async function runOffline(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4500);
  await openMovies(page, navSelector);
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Bangla');
  await page.waitForTimeout(3500);
  const online = await readList(page);
  check(online.cards > 0, `[${label}] content loaded while online`, String(online.cards));

  await context.setOffline(true);
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Hindi');
  await page.waitForTimeout(5000);
  const offline = await readList(page);

  // Either the service worker served its cached copy, or the viewer is told
  // plainly. What must not happen is a blank page or an endless spinner.
  check(offline.cards > 0 || offline.retry || offline.message.length > 0,
    `[${label}] offline gives cached content or a plain message, never a blank`,
    JSON.stringify(offline));
  check(offline.spinning === 0, `[${label}] offline leaves no spinner turning`, String(offline.spinning));
  check(pageErrors.length === 0, `[${label}] offline raises no page error`, pageErrors.join(' | '));

  await context.setOffline(false);
  await context.close();
}

// --- 7. reduced motion ------------------------------------------------------
async function runReducedMotion(label, viewport, navSelector) {
  const context = await browser.newContext({ viewport, reducedMotion: 'reduce' });
  const page = await context.newPage();
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  await openMovies(page, navSelector);

  const motion = await page.evaluate(() => {
    window.showMovieSkeleton('grid', 4);
    const node = document.querySelector('.movie-skeleton-poster');
    if (!node) return { found: false };
    return { found: true, animation: getComputedStyle(node).animationName };
  });
  check(motion.found, `[${label}] the skeleton renders`);
  check(motion.animation === 'none',
    `[${label}] the skeleton does not animate when less motion was asked for`,
    String(motion.animation));
  await context.close();
}

try {
  await runFetchFailure('desktop', { width: 1280, height: 800 }, '#desktopMainNav', '#desktopSubNav');
  await runRefreshFailureKeepsContent('desktop', { width: 1280, height: 800 }, '#desktopMainNav', '#desktopSubNav');
  await runIndexFailure('desktop', { width: 1280, height: 800 }, '#desktopMainNav', '#desktopSubNav');
  await runEmptyStates('desktop', { width: 1280, height: 800 }, '#desktopMainNav', '#desktopSubNav');
  await runBrokenArtwork('desktop', { width: 1280, height: 800 }, '#desktopMainNav', '#desktopSubNav');
  await runUnavailableDetail('desktop', { width: 1280, height: 800 }, '#desktopMainNav');
  await runOffline('desktop', { width: 1280, height: 800 }, '#desktopMainNav', '#desktopSubNav');
  await runReducedMotion('desktop', { width: 1280, height: 800 }, '#desktopMainNav');
  await runBrokenArtwork('mobile', { width: 390, height: 844 }, '#mobileMainNav', '#mobileSubNav');
  await runEmptyStates('mobile', { width: 390, height: 844 }, '#mobileMainNav', '#mobileSubNav');
} finally {
  await browser.close();
}

console.log(`\n${passes.length} passed, ${failures.length} failed`);
for (const pass of passes) console.log(`  PASS  ${pass}`);
for (const failure of failures) console.log(`  FAIL  ${failure}`);
if (failures.length) process.exit(1);
