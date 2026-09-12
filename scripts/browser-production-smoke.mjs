/**
 * Production smoke test against the live site, as a real visitor.
 *
 * This is not a unit test and does not mock anything: it opens
 * https://clicktv.pages.dev in a real browser, clicks what a person clicks,
 * and reports what is actually on screen.
 *
 * Two halves. The new Movie system has to work - Home, Hero, categories,
 * genres, search, detail, series, deep links. And the surfaces that were
 * already there have to be untouched - one player element of the same size,
 * the same control bar, the Notice, Live Sports, Today/Upcoming Match and
 * Live TV.
 *
 * Usage: node scripts/browser-production-smoke.mjs [baseUrl]
 */
import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'https://clicktv.pages.dev';
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
    await page.waitForTimeout(400);
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

/** The player box, measured. Compared before and after the movie work. */
function playerBox(page) {
  return page.evaluate(() => {
    const video = document.querySelector('video');
    const rect = video ? video.getBoundingClientRect() : null;
    const controls = document.querySelector('#playerControls');
    const cRect = controls ? controls.getBoundingClientRect() : null;
    return {
      videos: document.querySelectorAll('video').length,
      width: rect ? Math.round(rect.width) : 0,
      height: rect ? Math.round(rect.height) : 0,
      controlBars: document.querySelectorAll('#playerControls').length,
      controlsWidth: cRect ? Math.round(cRect.width) : 0,
      notice: document.querySelectorAll('#sticky-header-notice').length,
      // The engine hooks the protected plan names, still present.
      hasResolution: Boolean(document.querySelector('#currentResolutionBadge')),
      hasVolume: Boolean(document.querySelector('#volumeSlider')),
      hasFullscreen: Boolean(document.querySelector('#fullscreenBtn')),
      hasSeek: Boolean(document.querySelector('#progressContainer'))
    };
  });
}

async function run(label, viewport, isMobile) {
  const context = await browser.newContext({
    viewport, isMobile, hasTouch: isMobile,
    userAgent: isMobile
      ? 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Mobile Safari/537.36'
      : undefined
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  // The movie catalogue is served from an R2 bucket; a live channel never
  // is. So a request to it is proof the movie itself started.
  const movieMediaRequests = [];
  page.on('request', (r) => {
    if (/r2\.dev|\.mkv|\.mp4/i.test(r.url())) movieMediaRequests.push(r.url().slice(0, 90));
  });

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(7000);

  // --- the protected surfaces, on arrival ---------------------------------
  const before = await playerBox(page);
  check(before.videos === 1, `[${label}] exactly one player element`, String(before.videos));
  check(before.controlBars === 1, `[${label}] one player control bar`, String(before.controlBars));
  check(before.notice === 1, `[${label}] the Notice bar is present`, String(before.notice));
  check(before.width > 0 && before.height > 0,
    `[${label}] the player has a real box`, `${before.width}x${before.height}`);
  check(before.hasResolution, `[${label}] the resolution control is present`);
  check(before.hasFullscreen, `[${label}] the fullscreen control is present`);
  check(before.hasSeek, `[${label}] the seek bar is present`);

  const nav = await navSelectors(page);

  // --- Live Sports / Today / Upcoming / Live TV ----------------------------
  const mainLabels = await page.$$eval(`${nav.main} .final-main-button`,
    (n) => n.map((x) => x.textContent.trim()));
  check(mainLabels.some((t) => /Movies/i.test(t)), `[${label}] Movies is in the nav`,
    JSON.stringify(mainLabels));
  check(mainLabels.some((t) => /Live TV/i.test(t)), `[${label}] Live TV is in the nav`);
  check(mainLabels.some((t) => /Sport|Match/i.test(t)), `[${label}] Sports is in the nav`,
    JSON.stringify(mainLabels));

  // --- Movies -> Movie Home -------------------------------------------------
  await clickNav(page, nav.main, '.final-main-button', 'Movies');
  await page.waitForTimeout(6000);

  const home = await page.evaluate(() => {
    const panel = document.querySelector('#movieHeroPanel');
    const cards = document.querySelectorAll('#sidebarList .movie-card').length;
    const chips = document.querySelectorAll('.movie-genre-chip').length;
    const heroTitle = panel ? panel.querySelector('.movie-hero-title') : null;
    const kicker = panel ? panel.querySelector('.movie-hero-kicker') : null;
    const dots = panel ? panel.querySelectorAll('.movie-hero-dot').length : 0;
    const buttons = panel
      ? [...panel.querySelectorAll('.movie-hero-play, .movie-hero-details')].map((n) => n.textContent.trim())
      : [];
    return {
      heroVisible: panel ? !panel.hidden : false,
      heroTitle: heroTitle ? heroTitle.textContent.trim() : '',
      kicker: kicker ? kicker.textContent.trim() : '',
      dots,
      buttons,
      cards,
      chips,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth
    };
  });

  check(home.cards > 0, `[${label}] Movie Home shows a grid`, String(home.cards));
  check(home.heroVisible, `[${label}] the Featured Hero is displayed`);
  check(Boolean(home.heroTitle), `[${label}] the Hero shows a real title`, home.heroTitle);
  check(/FEATURED ON CLICK TV/i.test(home.kicker),
    `[${label}] the Hero label is the honest one`, home.kicker);
  check(!/trending/i.test(home.kicker), `[${label}] the Hero never says Trending`, home.kicker);
  check(home.dots === 5, `[${label}] five Featured slots`, String(home.dots));
  check(home.buttons.length >= 1, `[${label}] the Hero has action buttons`,
    JSON.stringify(home.buttons));
  check(home.chips > 0, `[${label}] genre chips are present`, String(home.chips));
  check(home.overflow <= 2, `[${label}] no horizontal overflow on Movie Home`,
    `${home.overflow}px`);

  // --- the Hero rotates -----------------------------------------------------
  const firstSlide = home.heroTitle;
  await page.waitForTimeout(8000);
  const second = await page.evaluate(() => {
    const t = document.querySelector('#movieHeroPanel .movie-hero-title');
    return t ? t.textContent.trim() : '';
  });
  check(second !== firstSlide, `[${label}] the Hero rotates on its own`,
    `${firstSlide} -> ${second}`);

  // --- Hero Details -> the existing detail ---------------------------------
  const hasDetails = await page.$('.movie-hero-details');
  if (hasDetails) {
    await page.click('.movie-hero-details');
    await page.waitForTimeout(5000);
    const detail = await page.evaluate(() => {
      const panel = document.querySelector('#movieDetailPanel');
      return {
        open: panel ? !panel.hidden : false,
        title: document.querySelector('.movie-detail-title')?.textContent?.trim() || '',
        hasPlay: Boolean(document.querySelector('.movie-detail-play')),
        videos: document.querySelectorAll('video').length,
        url: location.search
      };
    });
    check(detail.open, `[${label}] Hero Details opens the movie detail`, detail.title);
    check(detail.hasPlay, `[${label}] the detail offers Play`);
    check(detail.videos === 1, `[${label}] no second player was created`, String(detail.videos));
    check(/movie=/.test(detail.url), `[${label}] the URL names what is on screen`, detail.url);

    // --- deep link reload --------------------------------------------------
    const deepUrl = page.url();
    await page.goto(deepUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(8000);
    const reloaded = await page.evaluate(() => ({
      open: !document.querySelector('#movieDetailPanel').hidden,
      title: document.querySelector('.movie-detail-title')?.textContent?.trim() || ''
    }));
    check(reloaded.open, `[${label}] a deep link survives a reload`, reloaded.title);
    check(reloaded.title === detail.title, `[${label}] and lands on the same title`,
      `${detail.title} vs ${reloaded.title}`);
    check(movieMediaRequests.length === 0,
      `[${label}] a deep link opens the detail without starting the movie`,
      movieMediaRequests.slice(0, 2).join(' | '));

    await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(7000);
    await clickNav(page, nav.main, '.final-main-button', 'Movies');
    await page.waitForTimeout(5000);
  }

  // --- a real category ------------------------------------------------------
  const nav2 = await navSelectors(page);
  await clickNav(page, nav2.sub, '.final-sub-button', 'Bangla');
  await page.waitForTimeout(6000);
  const category = await page.evaluate(() => ({
    cards: document.querySelectorAll('#sidebarList .movie-card').length,
    heroHidden: document.querySelector('#movieHeroPanel').hidden,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth
  }));
  check(category.cards > 0, `[${label}] a category grid renders`, String(category.cards));
  check(category.heroHidden, `[${label}] the Hero is Movie-Home only`);
  check(category.overflow <= 2, `[${label}] no overflow on a category`, `${category.overflow}px`);

  // --- a genre --------------------------------------------------------------
  const genreClicked = await page.evaluate(() => {
    const chip = [...document.querySelectorAll('.movie-genre-chip')]
      .find((n) => /Action/i.test(n.textContent));
    if (chip) { chip.click(); return true; }
    return false;
  });
  if (genreClicked) {
    await page.waitForTimeout(6000);
    const genre = await page.evaluate(() => ({
      cards: document.querySelectorAll('#sidebarList .movie-card').length,
      message: document.querySelector('.movie-prompt-msg')?.textContent?.trim() || ''
    }));
    // Genre metadata is not backfilled yet, so an honest empty state is the
    // correct answer here - what must NOT happen is a crash or a blank page.
    check(genre.cards > 0 || genre.message.length > 0,
      `[${label}] a genre gives either results or an honest empty state`,
      `${genre.cards} cards, message: ${genre.message.slice(0, 60)}`);
  }

  // --- search ---------------------------------------------------------------
  // From Movie Home: searching out of a genre-browse view is a different
  // path and not what this row is about.
  const navSearch = await navSelectors(page);
  await clickNav(page, navSearch.sub, '.final-sub-button', 'Home');
  await page.waitForTimeout(5000);
  await page.evaluate(() => {
    const input = document.querySelector('#searchInput') || document.querySelector('#mobileSearchInput');
    if (input) {
      input.value = 'a';
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
  await page.waitForTimeout(5000);
  const search = await page.evaluate(() => ({
    cards: document.querySelectorAll('#sidebarList .movie-card').length,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth
  }));
  check(search.cards > 0, `[${label}] search returns results`, String(search.cards));
  check(search.overflow <= 2, `[${label}] search does not shift the page`, `${search.overflow}px`);

  // --- web series -----------------------------------------------------------
  await page.evaluate(() => {
    const input = document.querySelector('#searchInput') || document.querySelector('#mobileSearchInput');
    if (input) { input.value = ''; input.dispatchEvent(new Event('input', { bubbles: true })); }
  });
  await page.waitForTimeout(2500);
  const nav3 = await navSelectors(page);
  await clickNav(page, nav3.sub, '.final-sub-button', 'Web Series');
  await page.waitForTimeout(8000);
  const seriesList = await page.evaluate(() => ({
    cards: document.querySelectorAll('#sidebarList .series-card, #sidebarList .movie-card').length
  }));
  check(seriesList.cards > 0, `[${label}] Web Series lists series`, String(seriesList.cards));

  if (seriesList.cards > 0) {
    await page.evaluate(() => {
      const card = document.querySelector('#sidebarList .series-card, #sidebarList .movie-card-info, #sidebarList .movie-card');
      if (card) card.click();
    });
    await page.waitForTimeout(8000);
    const episodes = await page.evaluate(() => ({
      episodeCards: document.querySelectorAll('.series-episode-card').length,
      detailList: Boolean(document.querySelector('#sidebarList.series-detail-list')),
      videos: document.querySelectorAll('video').length
    }));
    check(episodes.episodeCards > 0 || episodes.detailList,
      `[${label}] a series opens its season/episode list`,
      JSON.stringify(episodes));
    check(episodes.videos === 1, `[${label}] series flow creates no second player`,
      String(episodes.videos));
  }

  // --- back to the protected sections --------------------------------------
  const nav4 = await navSelectors(page);
  await clickNav(page, nav4.main, '.final-main-button', 'Live TV');
  await page.waitForTimeout(7000);
  const liveTv = await page.evaluate(() => ({
    items: document.querySelectorAll('#sidebarList .sidebar-item, #sidebarList .movie-card').length,
    subs: document.querySelectorAll('#desktopSubNav .final-sub-button, #mobileSubNav .final-sub-button').length,
    heroHidden: document.querySelector('#movieHeroPanel').hidden
  }));
  check(liveTv.items > 0, `[${label}] Live TV lists channels`, String(liveTv.items));
  check(liveTv.subs > 0, `[${label}] Live TV categories are present`, String(liveTv.subs));
  check(liveTv.heroHidden, `[${label}] the movie Hero never appears on Live TV`);

  const sportsLabel = mainLabels.find((t) => /Today Match/i.test(t))
    || mainLabels.find((t) => /Sport|Match/i.test(t));
  if (sportsLabel) {
    await clickNav(page, nav4.main, '.final-main-button', sportsLabel);
    await page.waitForTimeout(7000);
    const sports = await page.evaluate(() => ({
      items: document.querySelectorAll('#sidebarList .sidebar-item, #sidebarList .match-card, #sidebarList > *').length,
      heroHidden: document.querySelector('#movieHeroPanel').hidden,
      notice: document.querySelectorAll('#sticky-header-notice').length
    }));
    check(sports.items > 0, `[${label}] ${sportsLabel} renders`, String(sports.items));
    check(sports.heroHidden, `[${label}] the movie Hero never appears on Sports`);
    check(sports.notice === 1, `[${label}] the Notice bar survives every view`);
  }

  // --- the player box is unchanged at the end ------------------------------
  const after = await playerBox(page);
  check(after.videos === 1, `[${label}] still exactly one player element`, String(after.videos));
  check(after.controlBars === 1, `[${label}] still one control bar`);
  check(after.width === before.width && after.height === before.height,
    `[${label}] the player box is the same size as on arrival`,
    `${before.width}x${before.height} -> ${after.width}x${after.height}`);
  check(after.notice === 1, `[${label}] the Notice bar is still present`);

  check(errors.length === 0, `[${label}] no uncaught page errors`, errors.slice(0, 3).join(' | '));
  await context.close();
}

try {
  await run('desktop', { width: 1366, height: 768 }, false);
  await run('mobile', { width: 390, height: 844 }, true);
} finally {
  await browser.close();
}

console.log(`\n${passes.length} passed, ${failures.length} failed`);
for (const failure of failures) console.log(`  FAIL  ${failure}`);
if (!failures.length) for (const pass of passes) console.log(`  PASS  ${pass}`);
if (failures.length) process.exit(1);
