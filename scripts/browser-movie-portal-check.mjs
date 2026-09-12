/**
 * The approved Movie page architecture, in a real browser.
 *
 * The Movie page has to be a page: Movies opens a full Movie Home with the
 * player and NOW PLAYING gone, a 255px rail on the left carrying DISCOVERY &
 * PICKS / MY LIBRARY / BROWSE BY GENRE, and the Hero plus real discovery rows
 * filling the content area - not a portal squeezed into the channel column
 * beside a playing stream.
 *
 * The other half is what must NOT change. Home, Grid, Detail and Player are
 * separate views, so Play has to bring the existing player back exactly as it
 * was, and Live Sports, Live TV and the Notice must be untouched by any of it.
 *
 * Usage: node scripts/browser-movie-portal-check.mjs [baseUrl]
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

async function clickNav(page, container, selector, label, budgetMs = 40000) {
  const deadline = Date.now() + budgetMs;
  for (;;) {
    const seen = await page.$$eval(`${container} ${selector}`,
      (nodes) => nodes.map((n) => n.textContent.trim()));
    if (seen.some((t) => t.includes(label))) break;
    if (Date.now() > deadline) throw new Error(`${label} never appeared; saw ${JSON.stringify(seen)}`);
    await page.waitForTimeout(350);
  }
  await page.$$eval(`${container} ${selector}`, (nodes, text) => {
    const target = nodes.find((n) => n.textContent.trim().includes(text));
    if (target) target.click();
  }, label);
}

async function navSelectors(page) {
  return page.evaluate(() => {
    const visible = (id) => {
      const node = document.getElementById(id);
      if (!node) return false;
      const r = node.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    return visible('desktopMainNav')
      ? { main: '#desktopMainNav', sub: '#desktopSubNav' }
      : { main: '#mobileMainNav', sub: '#mobileSubNav' };
  });
}

function snapshot(page) {
  return page.evaluate(() => {
    const box = (sel) => {
      const n = document.querySelector(sel);
      if (!n) return { present: false, vis: false, w: 0, h: 0, x: 0 };
      const r = n.getBoundingClientRect();
      return {
        present: true,
        vis: r.width > 0 && r.height > 0,
        x: Math.round(r.x), w: Math.round(r.width), h: Math.round(r.height)
      };
    };
    return {
      portal: document.body.classList.contains('movie-portal'),
      video: box('.video-container-wrap'),
      nowPlaying: box('.video-meta'),
      rail: box('.desktop-category-rail'),
      content: box('.sidebar-section.side-panel'),
      hero: box('#movieHeroPanel'),
      detail: box('#movieDetailPanel'),
      grid: box('#sidebarList'),
      rows: document.querySelectorAll('#movieHomeSections .movie-row').length,
      homeSections: box('#movieHomeSections'),
      rowTitles: [...document.querySelectorAll('#movieHomeSections .movie-row-head h2')].map((n) => n.textContent.trim()),
      statusCards: document.querySelectorAll('.movie-status-card').length,
      mainNav: [...document.querySelectorAll('#desktopMainNav .final-main-button')].map((n) => n.textContent.trim()),
      railCaptions: [...document.querySelectorAll('.desktop-category-rail .movie-rail-caption')].map((n) => n.textContent.trim()),
      railItems: [...document.querySelectorAll('#desktopSubNav .final-sub-button')].map((n) => n.textContent.trim()),
      genreInRail: Boolean(document.querySelector('.desktop-category-rail .movie-rail-genres')),
      genreChips: document.querySelectorAll('.desktop-category-rail .movie-genre-chip').length,
      railCounts: [...document.querySelectorAll('#desktopSubNav [data-rail-count]')]
        .map((n) => n.dataset.railCount.trim()),
      railBadges: document.querySelectorAll('#desktopSubNav [data-rail-tone]').length,
      // The tag must not be inside the entry's name.
      railTextClean: [...document.querySelectorAll('#desktopSubNav .final-sub-button')]
        .every((n) => !/\d/.test(n.textContent)),
      gridHeader: box('#movieGridHeader'),
      gridBack: box('#movieGridBackBtn'),
      gridChips: document.querySelectorAll('#movieGridHeader .movie-genre-chip').length,
      header: box('.app-header'),
      movieSection: document.body.classList.contains('movie-section'),
      capsule: box('.desktop-main-navigation .final-main-nav'),
      capsuleRadius: (() => {
        const n = document.querySelector('.desktop-main-navigation .final-main-nav');
        return n ? getComputedStyle(n).borderRadius : '';
      })(),
      brandMark: box('.app-header .brand-mark'),
      searchPill: box('.app-header .search-wrap.desktop-search'),
      subscribe: box('.app-header #subscribeBtn'),
      videos: document.querySelectorAll('video').length,
      controlBars: document.querySelectorAll('#playerControls').length,
      notice: document.querySelectorAll('#sticky-header-notice').length,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      playing: !document.querySelector('video').paused
    };
  });
}

async function run(label, viewport, isMobile) {
  const context = await browser.newContext({ viewport, isMobile, hasTouch: isMobile });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const movieMedia = [];
  page.on('request', (r) => { if (/r2\.dev|\.mkv|\.mp4/i.test(r.url())) movieMedia.push(r.url().slice(0, 80)); });

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(6000);

  const landing = await snapshot(page);
  check(!landing.portal, `[${label}] the portal is off before Movies is opened`);
  check(landing.video.vis, `[${label}] the player is visible on the landing view`);
  check(landing.mainNav.length === 3, `[${label}] three top-level destinations`,
    JSON.stringify(landing.mainNav));
  check(!landing.mainNav.some((t) => /Drama/i.test(t)), `[${label}] Drama is gone from the nav`);
  check(!landing.mainNav.some((t) => /Favorites/i.test(t)), `[${label}] Favorites is gone from the nav`);

  const nav = await navSelectors(page);

  // --- A. Movies -> full Movie Home ----------------------------------------
  await clickNav(page, nav.main, '.final-main-button', 'Movies');
  await page.waitForTimeout(7000);
  const home = await snapshot(page);

  check(home.portal, `[${label}] Movies switches the page into the portal`);
  check(!home.video.vis, `[${label}] NO player on Movie Home`, JSON.stringify(home.video));
  check(!home.nowPlaying.vis, `[${label}] NO NOW PLAYING on Movie Home`, JSON.stringify(home.nowPlaying));
  check(home.hero.vis, `[${label}] the Featured Hero is on Movie Home`);
  check(home.rows >= 1, `[${label}] Movie Home has discovery rows`, String(home.rows));
  check(home.statusCards === 4, `[${label}] the four discovery shortcuts are present`,
    String(home.statusCards));
  check(home.overflow <= 2, `[${label}] no horizontal overflow`, `${home.overflow}px`);
  check(home.videos === 1, `[${label}] still exactly one player element`, String(home.videos));
  check(home.notice === 1, `[${label}] the Notice bar is present`);
  check(home.railItems.includes('My Watchlist'),
    `[${label}] My Watchlist is in the rail`, JSON.stringify(home.railItems.slice(-2)));

  if (!isMobile) {
    check(home.rail.vis && home.rail.w >= 200,
      `[${label}] the rail is a real sidebar`, `${home.rail.w}px`);
    check(home.content.w > home.rail.w * 3,
      `[${label}] the movie content is the main column, not a squeezed panel`,
      `rail ${home.rail.w} vs content ${home.content.w}`);
    check(home.hero.w > 900,
      `[${label}] the Hero spans the content width`, `${home.hero.w}px`);
    check(home.railCaptions.join('|') === 'DISCOVERY & PICKS|MY LIBRARY|BROWSE BY GENRE',
      `[${label}] the rail carries the three approved captions`,
      JSON.stringify(home.railCaptions));
    check(home.genreInRail, `[${label}] the genre pills live in the rail`);
    check(home.genreChips === 9, `[${label}] All Genres plus the eight genres`,
      String(home.genreChips));
    check(home.railItems.length === 13, `[${label}] twelve discovery entries plus the watchlist`,
      String(home.railItems.length));

    // The rail tags: four accent badges, and a real catalogue count on every
    // other category. A count is only ever a real number.
    check(home.railBadges === 4,
      `[${label}] HOT / bolt / NEW / star on the four shelves`, String(home.railBadges));
    check(home.railCounts.length >= 6,
      `[${label}] the categories carry their catalogue counts`, JSON.stringify(home.railCounts));
    check(home.railCounts.every((t) => /^\d+$/.test(t) && Number(t) > 0),
      `[${label}] every rail count is a real positive number`, JSON.stringify(home.railCounts));
    check(home.railTextClean,
      `[${label}] a tag never lands inside a rail entry's name`,
      JSON.stringify(home.railItems));

    // The header, as the approved design draws it.
    check(home.movieSection, `[${label}] the Movie section header is on`);
    check(home.header.h === 64, `[${label}] the header is 64px`, `${home.header.h}px`);
    check(home.capsuleRadius.startsWith('999'),
      `[${label}] the destinations sit in a capsule`, home.capsuleRadius);
    check(home.brandMark.w === 32 && home.brandMark.h === 32,
      `[${label}] the brand mark is 32px`, `${home.brandMark.w}x${home.brandMark.h}`);
    check(home.searchPill.w === 250 && home.searchPill.h === 38,
      `[${label}] the search pill is 250x38`, `${home.searchPill.w}x${home.searchPill.h}`);
    check(home.subscribe.vis && home.subscribe.h === 38,
      `[${label}] Subscribe is a 38px pill`, `${home.subscribe.h}px`);

    // Movie Home is the Hero and the rows; the filter header belongs to grids.
    check(!home.gridHeader.vis,
      `[${label}] no filter header on Movie Home`, JSON.stringify(home.gridHeader));
  }

  // --- B. a movie card opens the Detail, and does not play ------------------
  const cardOpened = await page.evaluate(() => {
    const card = document.querySelector('#movieHomeSections .movie-row-strip .movie-card');
    if (!card) return false;
    card.click();
    return true;
  });
  if (cardOpened) {
    await page.waitForTimeout(4000);
    const detail = await snapshot(page);
    check(detail.detail.vis, `[${label}] a card opens the Movie Detail`);
    check(movieMedia.length === 0,
      `[${label}] a card click starts NO playback`, movieMedia.slice(0, 2).join(' | '));
    check(detail.videos === 1, `[${label}] no second player was created`, String(detail.videos));
    // Detail is a view, not a panel appended under Movie Home.
    check(!detail.hero.vis, `[${label}] the Hero steps aside for the Detail`,
      JSON.stringify(detail.hero));
    check(!detail.homeSections.vis, `[${label}] the Home rows step aside for the Detail`,
      JSON.stringify(detail.homeSections));

    const detailFields = await page.evaluate(() => ({
      title: document.querySelector('.movie-detail-title')?.textContent?.trim() || '',
      facts: [...document.querySelectorAll('.movie-detail-fact dt')].map((n) => n.textContent.trim()),
      hasPlay: Boolean(document.querySelector('.movie-detail-play')),
      hasWatchlist: Boolean(document.querySelector('.movie-detail-watchlist'))
    }));
    check(Boolean(detailFields.title), `[${label}] the detail shows a real title`, detailFields.title);
    check(detailFields.hasPlay && detailFields.hasWatchlist,
      `[${label}] the detail offers Play and Watchlist`);

    // --- C. Play returns the existing player ------------------------------
    const playable = await page.evaluate(() => {
      const btn = document.querySelector('.movie-detail-play');
      return Boolean(btn) && !btn.disabled;
    });
    if (playable) {
      await page.click('.movie-detail-play');
      await page.waitForTimeout(5000);
      const player = await snapshot(page);
      check(!player.portal, `[${label}] Play leaves the portal`);
      check(player.video.vis, `[${label}] Play brings the existing player back`,
        JSON.stringify(player.video));
      check(player.videos === 1, `[${label}] still one player element`, String(player.videos));
      check(player.controlBars === 1, `[${label}] still one control bar`);
      check(player.notice === 1, `[${label}] the Notice survives the handoff`);

      // --- D. back to Movie Home ------------------------------------------
      const back = await navSelectors(page);
      await clickNav(page, back.main, '.final-main-button', 'Movies');
      await page.waitForTimeout(3000);
      const backNav = await navSelectors(page);
      await clickNav(page, backNav.sub, '.final-sub-button', 'Home');
      await page.waitForTimeout(6000);
      const returned = await snapshot(page);
      check(returned.portal, `[${label}] going back to Movie Home restores the portal`);
      check(!returned.video.vis, `[${label}] and hides the player again`);
      check(returned.hero.vis, `[${label}] and the Hero is back`);
    }
  }

  // --- E. a category is a grid, not rows -----------------------------------
  const navCat = await navSelectors(page);
  await clickNav(page, navCat.sub, '.final-sub-button', 'Bangla');
  await page.waitForTimeout(6000);
  const category = await snapshot(page);
  check(category.portal, `[${label}] a category stays inside the portal`);
  check(!category.video.vis, `[${label}] and shows no player`);
  check(category.rows === 0, `[${label}] a category shows the grid, not the home rows`,
    String(category.rows));
  check(category.grid.vis, `[${label}] the category grid is visible`);
  check(category.overflow <= 2, `[${label}] no overflow on a category`, `${category.overflow}px`);
  // The grid opens under FILTER BY GENRE with Back to Home, as approved.
  check(category.gridHeader.vis, `[${label}] a grid carries the filter header`,
    JSON.stringify(category.gridHeader));
  check(category.gridChips === 9, `[${label}] the filter header has all nine chips`,
    String(category.gridChips));
  check(category.gridBack.vis, `[${label}] the grid offers Back to Home`);

  // --- F. Web Series --------------------------------------------------------
  await clickNav(page, navCat.sub, '.final-sub-button', 'Web Series');
  await page.waitForTimeout(9000);
  const series = await page.evaluate(() => ({
    cards: document.querySelectorAll('#sidebarList .series-card, #sidebarList .movie-card').length,
    portal: document.body.classList.contains('movie-portal'),
    videoVisible: (() => { const n = document.querySelector('.video-container-wrap'); if (!n) return false; const r = n.getBoundingClientRect(); return r.width > 0; })()
  }));
  check(series.cards > 0, `[${label}] Web Series lists series`, String(series.cards));
  check(!series.videoVisible, `[${label}] Web Series shows no player`);

  // --- G. Live TV and Live Sports are untouched ----------------------------
  const navLive = await navSelectors(page);
  await clickNav(page, navLive.main, '.final-main-button', 'Live TV');
  await page.waitForTimeout(6000);
  const liveTv = await snapshot(page);
  check(!liveTv.portal, `[${label}] Live TV is never in the portal`);
  check(liveTv.video.vis, `[${label}] Live TV keeps its player`, JSON.stringify(liveTv.video));
  check(liveTv.videos === 1, `[${label}] Live TV has one player element`);
  check(liveTv.notice === 1, `[${label}] the Notice is on Live TV`);
  check(liveTv.overflow <= 2, `[${label}] no overflow on Live TV`, `${liveTv.overflow}px`);
  const liveTvItems = await page.$$eval('#sidebarList > *', (n) => n.length);
  check(liveTvItems > 0, `[${label}] Live TV lists channels`, String(liveTvItems));

  const sportsLabel = landing.mainNav.find((t) => /Sport/i.test(t));
  if (sportsLabel) {
    await clickNav(page, navLive.main, '.final-main-button', sportsLabel);
    await page.waitForTimeout(6000);
    const sports = await snapshot(page);
    check(!sports.portal, `[${label}] Live Sports is never in the portal`);
    check(sports.video.vis, `[${label}] Live Sports keeps its player`);
    check(sports.notice === 1, `[${label}] the Notice is on Live Sports`);
    check(sports.overflow <= 2, `[${label}] no overflow on Live Sports`, `${sports.overflow}px`);
  }

  check(errors.length === 0, `[${label}] no uncaught page errors`, errors.slice(0, 3).join(' | '));
  await context.close();
}

const VIEWPORTS = [
  ['1366', { width: 1366, height: 768 }, false],
  ['1440', { width: 1440, height: 900 }, false],
  ['1920', { width: 1920, height: 1080 }, false],
  ['390', { width: 390, height: 844 }, true]
];

try {
  for (const [label, viewport, isMobile] of VIEWPORTS) {
    await run(label, viewport, isMobile);
  }
} finally {
  await browser.close();
}

console.log(`\n${passes.length} passed, ${failures.length} failed`);
for (const failure of failures) console.log(`  FAIL  ${failure}`);
if (!failures.length) for (const pass of passes) console.log(`  PASS  ${pass}`);
if (failures.length) process.exit(1);
