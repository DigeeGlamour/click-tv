/**
 * Movie browse smoke test — desktop, tablet and mobile (PART 12).
 *
 * Drives the real page in a real browser against real generated data. What
 * it is actually defending:
 *
 *   - the movie navigation is the exact order the Category/Genre reference
 *     fixes, with Premium and Mix both present and distinct;
 *   - the eight genre chips exist, are reachable at every breakpoint, and
 *     stay in sync wherever they are shown;
 *   - changing category resets the genre (invisible filter state is what
 *     makes a grid look broken) while changing genre keeps the category;
 *   - an empty combination offers a way out instead of a blank page;
 *   - Live Sports and Live TV navigation is byte-for-byte what it was.
 *
 * Usage: node scripts/browser-movie-browse-check.mjs [baseUrl]
 */
import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const failures = [];
const passes = [];

function check(condition, message, detail = '') {
  if (condition) passes.push(message);
  else failures.push(`${message}${detail ? ` — ${detail}` : ''}`);
}

const EXPECTED_NAV = [
  'Home', 'Trending', 'Just Added', 'Latest', 'Bangla', 'Hindi', 'English',
  'South Indian', 'Dubbed Movie', 'Web Series', 'Premium', 'Mix', 'My Watchlist'
];
const EXPECTED_GENRES = [
  'All Genres', 'Action', 'Comedy', 'Horror', 'Romance', 'Thriller',
  'Animation', 'Sci-Fi', 'Crime'
];

const browser = await chromium.launch({ headless: true });

/**
 * State as the viewer can see it - the active navigation item and the
 * active chip - rather than an internal variable. If the two ever
 * disagree, the visible one is the bug.
 */
async function readVisibleState(page, subNavSelector) {
  return page.evaluate((selector) => ({
    category: document.querySelector(`${selector} .final-sub-button.active`)?.textContent?.trim() || '',
    genre: document.querySelector('#movieGenreBar .movie-genre-chip.active')?.textContent?.trim() || ''
  }), subNavSelector);
}

async function openMovies(page, navSelector) {
  await page.locator(`${navSelector} .final-main-button`).first().waitFor({ state: 'visible', timeout: 30000 });
  await page.locator(`${navSelector} .final-main-button`, { hasText: 'Movies' }).first().click();
  await page.waitForTimeout(900);
}

async function run(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({
    viewport,
    isMobile: viewport.width < 800,
    hasTouch: viewport.width < 800
  });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (msg) => {
    if (msg.type() === 'error' && !/favicon|404|Failed to load resource/i.test(msg.text())) {
      pageErrors.push(msg.text());
    }
  });

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await openMovies(page, navSelector);

  // --- navigation order -----------------------------------------------
  const navLabels = await page.$$eval(`${subNavSelector} .final-sub-button`,
    (nodes) => nodes.map((n) => n.textContent.trim()));
  check(JSON.stringify(navLabels) === JSON.stringify(EXPECTED_NAV),
    `[${label}] movie navigation is in the exact reference order`,
    `got ${JSON.stringify(navLabels)}`);
  check(navLabels.includes('Premium') && navLabels.includes('Mix'),
    `[${label}] Premium and Mix are both visible and distinct`);

  // --- genre chips ------------------------------------------------------
  const genreLabels = await page.$$eval('#movieGenreBar .movie-genre-chip',
    (nodes) => nodes.map((n) => n.textContent.trim()));
  check(JSON.stringify(genreLabels) === JSON.stringify(EXPECTED_GENRES),
    `[${label}] the eight genre chips plus All Genres are present`,
    `got ${JSON.stringify(genreLabels)}`);

  const genreVisible = await page.locator('#movieGenreBar').isVisible();
  check(genreVisible, `[${label}] genre control is reachable at this breakpoint`);

  // --- genre selection keeps the category -------------------------------
  await page.locator(`${subNavSelector} .final-sub-button`, { hasText: 'Bangla' }).first().click();
  await page.waitForTimeout(700);
  const afterCategory = await readVisibleState(page, subNavSelector);
  check(afterCategory.category === 'Bangla',
    `[${label}] selecting Bangla sets the category`, JSON.stringify(afterCategory));

  await page.locator('#movieGenreBar .movie-genre-chip', { hasText: 'Horror' }).first().click();
  await page.waitForTimeout(700);
  const afterGenre = await readVisibleState(page, subNavSelector);
  check(afterGenre.category === 'Bangla' && afterGenre.genre === 'Horror',
    `[${label}] genre change preserves the category (Bangla + Horror)`,
    JSON.stringify(afterGenre));

  const activeChip = await page.$$eval('#movieGenreBar .movie-genre-chip.active',
    (nodes) => nodes.map((n) => n.textContent.trim()));
  check(activeChip.length === 1 && activeChip[0] === 'Horror',
    `[${label}] exactly one genre chip shows as active`, JSON.stringify(activeChip));

  // --- empty combination offers a way out --------------------------------
  const emptyRecovery = await page.evaluate(() => {
    const list = document.querySelector('#sidebarList');
    return {
      hasCards: Boolean(list && list.querySelector('[data-uid]')),
      hasClear: Boolean(document.querySelector('.movie-clear-genre')),
      message: document.querySelector('.list-message')?.textContent?.trim() || ''
    };
  });
  check(emptyRecovery.hasCards || emptyRecovery.hasClear,
    `[${label}] an empty genre result offers a recovery action`,
    JSON.stringify(emptyRecovery));

  // --- category change resets the genre ----------------------------------
  await page.locator(`${subNavSelector} .final-sub-button`, { hasText: 'Hindi' }).first().click();
  await page.waitForTimeout(700);
  const afterSwitch = await readVisibleState(page, subNavSelector);
  check(afterSwitch.category === 'Hindi' && afterSwitch.genre === 'All Genres',
    `[${label}] changing category resets the genre to All`, JSON.stringify(afterSwitch));

  // --- protected surfaces -------------------------------------------------
  const liveNav = await page.$$eval(`${navSelector} .final-main-button`,
    (nodes) => nodes.map((n) => n.textContent.trim()));
  check(JSON.stringify(liveNav) === JSON.stringify(['Live Sports', 'Live TV', 'Movies', 'Drama', 'Favorites']),
    `[${label}] main navigation (Live Sports / Live TV) is unchanged`,
    JSON.stringify(liveNav));

  await page.locator(`${navSelector} .final-main-button`, { hasText: 'Live TV' }).first().click();
  await page.waitForTimeout(700);
  const genreHiddenElsewhere = await page.locator('#movieGenreBar').isHidden();
  check(genreHiddenElsewhere, `[${label}] the genre bar is hidden outside the movie view`);

  if (viewport.width < 800) {
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check(overflow <= 1, `[${label}] no horizontal page overflow`, `overflow ${overflow}px`);
  }

  check(pageErrors.length === 0, `[${label}] no uncaught page errors`, pageErrors.join(' | '));

  await context.close();
}

try {
  await run('desktop', { width: 1280, height: 800 }, '#desktopMainNav', '#desktopSubNav');
  await run('tablet', { width: 820, height: 1180 }, '#mobileMainNav', '#mobileSubNav');
  await run('mobile', { width: 390, height: 844 }, '#mobileMainNav', '#mobileSubNav');
} finally {
  await browser.close();
}

console.log(`\n${passes.length} passed, ${failures.length} failed`);
for (const pass of passes) console.log(`  PASS  ${pass}`);
for (const failure of failures) console.log(`  FAIL  ${failure}`);
if (failures.length) process.exit(1);
