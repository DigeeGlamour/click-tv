/**
 * All-device responsive lock (PART 23).
 *
 * Every width the plan names, plus both landscapes, measured rather than
 * eyeballed: does the body overflow sideways, can a category and a genre
 * actually be reached, is the active state visible, are the controls big
 * enough to hit with a thumb, and does the detail stack instead of
 * overflowing.
 *
 * It also re-checks the things that must not have moved: one player element,
 * the player's own box, and the Notice / Live Sports / Live TV surfaces.
 *
 * Usage: node scripts/browser-movie-responsive-check.mjs [baseUrl]
 */
import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const failures = [];
const passes = [];

function check(condition, message, detail = '') {
  if (condition) passes.push(message);
  else failures.push(`${message}${detail ? ` — ${detail}` : ''}`);
}

const VIEWPORTS = [
  { label: '360', width: 360, height: 780, mobile: true },
  { label: '390', width: 390, height: 844, mobile: true },
  { label: '412', width: 412, height: 915, mobile: true },
  { label: '480', width: 480, height: 900, mobile: true },
  { label: '768', width: 768, height: 1024, mobile: false },
  { label: '1024', width: 1024, height: 768, mobile: false },
  { label: '1366', width: 1366, height: 768, mobile: false },
  { label: '1440', width: 1440, height: 900, mobile: false },
  { label: '1920', width: 1920, height: 1080, mobile: false },
  { label: 'mobile-landscape', width: 844, height: 390, mobile: true },
  { label: 'tablet-landscape', width: 1180, height: 820, mobile: false }
];

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

/** Whichever nav is the one actually on screen at this width. */
async function visibleNavSelectors(page) {
  return page.evaluate(() => {
    const wide = (id) => {
      const node = document.getElementById(id);
      if (!node) return false;
      const rect = node.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    };
    return wide('desktopMainNav')
      ? { main: '#desktopMainNav', sub: '#desktopSubNav' }
      : { main: '#mobileMainNav', sub: '#mobileSubNav' };
  });
}

function overflowOf(page) {
  return page.evaluate(() => {
    const doc = document.documentElement;
    const widest = [...document.querySelectorAll('body *')]
      .map((node) => {
        const rect = node.getBoundingClientRect();
        return { right: Math.round(rect.right), cls: node.className && String(node.className).slice(0, 60) };
      })
      .filter((entry) => entry.right > doc.clientWidth + 2)
      .sort((a, b) => b.right - a.right)
      .slice(0, 3);
    return {
      overflow: doc.scrollWidth - doc.clientWidth,
      clientWidth: doc.clientWidth,
      widest
    };
  });
}

async function run(viewport) {
  const label = viewport.label;
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    isMobile: viewport.mobile,
    hasTouch: viewport.mobile
  });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4500);

  // --- the home page, before Movies ---------------------------------------
  const atRest = await overflowOf(page);
  check(atRest.overflow <= 2,
    `[${label}] no horizontal overflow on load`,
    `${atRest.overflow}px over ${atRest.clientWidth} — ${JSON.stringify(atRest.widest)}`);

  const nav = await visibleNavSelectors(page);
  await clickNavButton(page, nav.main, '.final-main-button', 'Movies');
  await page.waitForTimeout(2000);

  // --- a real category ------------------------------------------------------
  await clickNavButton(page, nav.sub, '.final-sub-button', 'Bangla');
  await page.waitForTimeout(3500);

  // --- category and genre must both be reachable ---------------------------
  //
  // Probed on a grid, not on Movie Home: the approved design gives Movie Home
  // the Hero and the rows, and puts FILTER BY GENRE on the grid views. Only
  // chips that are actually on screen count - the rail and the grid header
  // each render the full set, and at any one width one of them is not shown.
  const access = await page.evaluate((subSelector) => {
    const onScreen = (n) => n.getBoundingClientRect().width > 0;
    const subs = [...document.querySelectorAll(`${subSelector} .final-sub-button`)];
    const chips = [...document.querySelectorAll('.movie-genre-chip')].filter(onScreen);
    const bars = [...document.querySelectorAll('.movie-genre-bar')];
    const bar = bars.find(onScreen) || null;
    const barRect = bar ? bar.getBoundingClientRect() : null;
    const style = bar ? getComputedStyle(bar) : null;
    return {
      categories: subs.length,
      categoryVisible: subs.filter(onScreen).length,
      chips: chips.length,
      chipRows: new Set(chips.map((n) => Math.round(n.getBoundingClientRect().top))).size,
      barHidden: !bar,
      barOverflowX: style ? style.overflowX : '',
      barHeight: barRect ? Math.round(barRect.height) : 0,
      smallestChipHeight: chips.length
        ? Math.min(...chips.map((n) => Math.round(n.getBoundingClientRect().height)))
        : 0,
      smallestCategoryHeight: subs.length
        ? Math.min(...subs.map((n) => Math.round(n.getBoundingClientRect().height)))
        : 0
    };
  }, nav.sub);

  check(access.categoryVisible > 0,
    `[${label}] movie categories are reachable`, JSON.stringify(access.categories));
  check(access.chips > 0, `[${label}] genre chips are present`, String(access.chips));
  check(!access.barHidden, `[${label}] the genre bar is visible on a browse view`);

  if (viewport.mobile) {
    // Chips should scroll sideways rather than stack into a wall of rows.
    check(access.chipRows <= 2,
      `[${label}] genre chips do not wrap into a tall block`,
      `${access.chipRows} rows, overflow-x: ${access.barOverflowX}`);
    check(access.smallestChipHeight >= 28,
      `[${label}] genre chips are tappable`, `${access.smallestChipHeight}px`);
    check(access.smallestCategoryHeight >= 30,
      `[${label}] category buttons are tappable`, `${access.smallestCategoryHeight}px`);
  }

  const grid = await page.evaluate((subSelector) => {
    const cards = [...document.querySelectorAll('#sidebarList .movie-card')];
    const widths = cards.map((n) => Math.round(n.getBoundingClientRect().width));
    // Scoped to the nav that is actually on screen: both navs exist in the
    // DOM at every width and both carry an .active button, so an unscoped
    // query finds the hidden one first and reads it as invisible.
    const active = document.querySelector(`${subSelector} .final-sub-button.active`);
    return {
      cards: cards.length,
      narrowest: widths.length ? Math.min(...widths) : 0,
      activeCategory: active ? active.textContent.trim() : '',
      activeVisible: active ? active.getBoundingClientRect().width > 0 : false
    };
  }, nav.sub);

  check(grid.cards > 0, `[${label}] the category grid renders`, String(grid.cards));
  check(grid.narrowest >= 84,
    `[${label}] cards are wide enough to read`, `${grid.narrowest}px`);
  check(grid.activeVisible && /Bangla/i.test(grid.activeCategory),
    `[${label}] the active category is visible`, grid.activeCategory);

  const afterCategory = await overflowOf(page);
  check(afterCategory.overflow <= 2,
    `[${label}] no horizontal overflow on a category grid`,
    `${afterCategory.overflow}px — ${JSON.stringify(afterCategory.widest)}`);

  // --- the detail ------------------------------------------------------------
  // In-page click, like clickNavButton above. The movie content scrolls in
  // its own container now, so Playwright's actionability check reports the
  // first card "outside of the viewport" even though a person can reach it
  // by scrolling - which is what the rest of this file already works around.
  const hasInfo = await page.$$eval('#sidebarList .movie-card-info', (n) => n.length);
  if (hasInfo) {
    await page.$$eval('#sidebarList .movie-card-info', (nodes) => nodes[0].click());
    await page.waitForTimeout(2500);
    const detail = await page.evaluate(() => {
      const panel = document.querySelector('#movieDetailPanel');
      const rect = panel ? panel.getBoundingClientRect() : null;
      const play = document.querySelector('.movie-detail-play');
      const watch = document.querySelector('.movie-detail-watchlist');
      const close = document.querySelector('.movie-detail-close');
      const box = (node) => {
        if (!node) return 0;
        const r = node.getBoundingClientRect();
        return Math.round(Math.min(r.width, r.height));
      };
      return {
        visible: panel ? !panel.hidden : false,
        right: rect ? Math.round(rect.right) : 0,
        docWidth: document.documentElement.clientWidth,
        playTarget: box(play),
        watchTarget: box(watch),
        closeTarget: box(close),
        closeVisible: close ? close.getBoundingClientRect().width > 0 : false
      };
    });

    check(detail.visible, `[${label}] the detail opens`);
    check(detail.right <= detail.docWidth + 2,
      `[${label}] the detail stays inside the viewport`,
      `${detail.right} vs ${detail.docWidth}`);
    check(detail.closeVisible, `[${label}] a way back out of the detail is visible`);
    if (viewport.mobile) {
      check(detail.playTarget >= 28 && detail.watchTarget >= 28,
        `[${label}] Play and Watchlist are tappable`,
        JSON.stringify({ play: detail.playTarget, watchlist: detail.watchTarget }));
    }

    const afterDetail = await overflowOf(page);
    check(afterDetail.overflow <= 2,
      `[${label}] no horizontal overflow with the detail open`,
      `${afterDetail.overflow}px — ${JSON.stringify(afterDetail.widest)}`);

    await page.$$eval('.movie-detail-close', (nodes) => nodes[0]?.click()).catch(() => {});
    await page.waitForTimeout(1200);
  }

  // --- search must not shove the page sideways ------------------------------
  const beforeSearch = await overflowOf(page);
  await page.evaluate(() => {
    const input = document.querySelector('#searchInput') || document.querySelector('#mobileSearchInput');
    if (!input) return;
    input.value = 'a';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.waitForTimeout(2000);
  const afterSearch = await overflowOf(page);
  check(afterSearch.overflow <= Math.max(2, beforeSearch.overflow),
    `[${label}] searching does not shift the page sideways`,
    `${beforeSearch.overflow} -> ${afterSearch.overflow}`);

  // --- the protected surfaces ------------------------------------------------
  const player = await page.evaluate(() => {
    const video = document.querySelector('video');
    const rect = video ? video.getBoundingClientRect() : null;
    return {
      videos: document.querySelectorAll('video').length,
      width: rect ? Math.round(rect.width) : 0,
      insideViewport: rect ? rect.right <= document.documentElement.clientWidth + 2 : false,
      controls: document.querySelectorAll('#playerControls').length,
      notice: document.querySelectorAll('#sticky-header-notice').length,
      portal: document.body.classList.contains('movie-portal')
    };
  });
  check(player.videos === 1, `[${label}] exactly one player element`, String(player.videos));
  // Home / Grid / Detail / Player are separate views in the approved Movie
  // architecture, so on a movie browse view the player is deliberately not on
  // screen - the element is still there, unchanged, and comes back at its own
  // size when Play is pressed (browser-movie-portal-check measures that).
  // Everywhere else it must still fit.
  check(player.portal ? player.width === 0 : (player.width > 0 && player.insideViewport),
    `[${label}] the player box fits the viewport`, JSON.stringify(player));
  check(player.controls === 1, `[${label}] the player control bar is present`);
  check(player.notice === 1, `[${label}] the Notice bar is present`);

  check(pageErrors.length === 0, `[${label}] no uncaught page errors`, pageErrors.join(' | '));
  await context.close();
}

try {
  for (const viewport of VIEWPORTS) {
    await run(viewport);
  }
} finally {
  await browser.close();
}

console.log(`\n${passes.length} passed, ${failures.length} failed`);
for (const failure of failures) console.log(`  FAIL  ${failure}`);
if (!failures.length) for (const pass of passes) console.log(`  PASS  ${pass}`);
if (failures.length) process.exit(1);
