/**
 * The Featured Hero, in a real browser (Featured/Hero plan).
 *
 * The interesting properties of a Hero are all about when it MOVES and what
 * it CLAIMS, and neither can be checked by reading the source.
 *
 * Movement: it rotates on its own, a manual move restarts the clock rather
 * than shortening the next slide, a hidden tab stops it, the detail and the
 * player stop it, and coming back to Movie Home starts it again. Each of
 * those is measured here by watching which slide is on screen over time.
 *
 * Claims: every fact on screen has to be in the data file. The tests below
 * serve a crafted featured.json and then assert that a rating with no
 * source, a quality label for an unmeasured stream and a badge for a state
 * the title is not in are all absent - and that the fabrications a Hero is
 * tempted into (a stretched poster, a "Trending" label over an internal
 * pick, a stream URL in the payload) never appear.
 *
 * The last group is the handoff: Play must reach the existing player and a
 * series must reach the existing series detail. There is no second player.
 *
 * Usage: node scripts/browser-movie-hero-check.mjs [baseUrl]
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

/** Click a nav button by its label, polling because the nav re-renders. */
async function clickNav(page, container, selector, label) {
  const deadline = Date.now() + 30000;
  for (;;) {
    const seen = await page.$$eval(`${container} ${selector}`,
      (nodes) => nodes.map((n) => n.textContent.trim()));
    if (seen.some((t) => t.includes(label))) break;
    if (Date.now() > deadline) throw new Error(`${label} never appeared; saw ${JSON.stringify(seen)}`);
    await page.waitForTimeout(250);
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

function heroState(page) {
  return page.evaluate(() => {
    const panel = document.querySelector('#movieHeroPanel');
    if (!panel) return { present: false };
    const text = (sel) => {
      const node = panel.querySelector(sel);
      return node ? node.textContent.trim() : '';
    };
    const dots = [...panel.querySelectorAll('.movie-hero-dot')];
    const layers = [...panel.querySelectorAll('.movie-hero-bg')];
    const active = layers.find((l) => l.classList.contains('active'));
    const art = panel.querySelector('.movie-hero-art');
    const rect = panel.getBoundingClientRect();
    return {
      present: true,
      hidden: panel.hidden,
      title: text('.movie-hero-title'),
      kicker: text('.movie-hero-kicker'),
      meta: text('.movie-hero-meta'),
      desc: text('.movie-hero-desc'),
      badges: [...panel.querySelectorAll('.movie-hero-badge')].map((n) => n.textContent.trim()),
      tags: [...panel.querySelectorAll('.movie-hero-tag')].map((n) => n.textContent.trim()),
      buttons: [...panel.querySelectorAll('.movie-hero-play, .movie-hero-details')]
        .map((n) => n.textContent.trim()),
      dots: dots.length,
      activeDot: dots.findIndex((d) => d.classList.contains('active')),
      arrows: panel.querySelectorAll('.movie-hero-arrow').length,
      bgImage: active ? active.style.backgroundImage : '',
      posterFallback: active ? active.classList.contains('poster-fallback') : false,
      artSrc: art && !art.hidden ? art.getAttribute('src') || '' : '',
      right: Math.round(rect.right),
      docWidth: document.documentElement.clientWidth,
      height: Math.round(rect.height),
      html: panel.innerHTML
    };
  });
}

/**
 * Serve a crafted featured.json so the assertions are about known data.
 *
 * context.route, not page.route: the site registers a service worker, and a
 * fetch it mediates never reaches a page-level route. Silent, and it makes
 * a mocked test quietly assert against the real file instead.
 */
async function withFeatured(context, payload) {
  await context.route('**/data/movies/discovery/featured.json*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload)
    }));
}

const REAL_ID_PLACEHOLDER = '__REAL__';

/** A file shaped exactly like the builder's output. */
function featuredFile(items, extra = {}) {
  return {
    version: 1,
    updated_at: new Date().toISOString(),
    refresh_interval_hours: 12,
    hero_rotate_seconds: 6.5,
    label: 'FEATURED ON CLICK TV',
    slots: 5,
    count: items.length,
    items,
    ...extra
  };
}

function slot(id, overrides = {}) {
  return {
    id,
    identity: `id:${id}`,
    type: 'movie',
    source: 'auto',
    featured_rank: 1,
    featured_score: 20,
    name: `Title ${id}`,
    category: 'Hindi',
    year: 2026,
    poster: 'https://image.tmdb.org/t/p/w600_and_h900_bestv2/x.jpg',
    artwork_kind: 'poster_fallback',
    ...overrides
  };
}

async function openMovieHome(page) {
  const nav = await navSelectors(page);
  await clickNav(page, nav.main, '.final-main-button', 'Movies');
  await page.waitForTimeout(1500);
  return nav;
}

async function newPage(payload) {
  const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  if (payload) await withFeatured(context, payload);
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(3500);
  return { context, page, errors };
}

// ---------------------------------------------------------------------------
// 1. The real, generated file
// ---------------------------------------------------------------------------
{
  const { context, page, errors } = await newPage(null);
  await openMovieHome(page);
  await page.waitForTimeout(1500);
  const hero = await heroState(page);

  check(hero.present, '[real] the Hero panel exists in the document');
  check(!hero.hidden, '[real] the Hero is shown on Movie Home', JSON.stringify(hero.hidden));
  check(Boolean(hero.title), '[real] the Hero shows a real title', hero.title);
  check(/FEATURED ON CLICK TV/i.test(hero.kicker),
    '[real] the label is the one the plan fixes', hero.kicker);
  check(!/trending/i.test(hero.kicker),
    '[real] the Hero never calls itself Trending', hero.kicker);
  check(hero.buttons.some((t) => /Play/i.test(t)), '[real] a Play button is present',
    JSON.stringify(hero.buttons));
  check(hero.buttons.some((t) => /Details/i.test(t)), '[real] a Details button is present',
    JSON.stringify(hero.buttons));
  check(hero.right <= hero.docWidth + 2, '[real] the Hero stays inside the viewport',
    `${hero.right} vs ${hero.docWidth}`);
  check(hero.height > 100 && hero.height < 320,
    '[real] the Hero is a banner, not a page', `${hero.height}px`);

  // Nothing about playback may reach the browser through this file.
  const raw = await page.evaluate(async () => {
    const response = await fetch('data/movies/discovery/featured.json', { cache: 'no-store' });
    return response.text();
  });
  for (const field of ['"url"', '"backups"', '"headers"', '"playback_id"', '"proxy_mode"',
                       '"header_profile"', '"standby"', '"drm"']) {
    check(!raw.includes(field), `[real] featured.json carries no ${field}`);
  }
  check(!/\.m3u8|\.mkv|\.mp4/.test(raw), '[real] featured.json carries no stream file');

  // The browser must not have called a metadata provider for any of this.
  const external = await page.evaluate(() => window.__externalCalls || []);
  check(external.length === 0, '[real] no external metadata call was made from the browser');

  check(errors.length === 0, '[real] no uncaught page errors', errors.join(' | '));
  await context.close();
}

// ---------------------------------------------------------------------------
// 2. Rotation and timer behaviour
// ---------------------------------------------------------------------------
{
  const items = [slot('a'), slot('b', { name: 'Title b' }), slot('c', { name: 'Title c' })];
  // A short interval so the test measures behaviour, not patience. The
  // production value comes from the same field and is asserted separately.
  const { context, page, errors } = await newPage(featuredFile(items, { hero_rotate_seconds: 3 }));
  await openMovieHome(page);
  await page.waitForTimeout(800);

  const first = await heroState(page);
  check(first.dots === 3, '[rotate] one dot per slot', String(first.dots));
  check(first.arrows === 2, '[rotate] previous and next arrows are present', String(first.arrows));
  check(first.activeDot === 0, '[rotate] it starts on the first slot', String(first.activeDot));

  await page.waitForTimeout(3600);
  const second = await heroState(page);
  check(second.title !== first.title, '[rotate] it advances on its own',
    `${first.title} -> ${second.title}`);
  check(second.activeDot === 1, '[rotate] the dot follows the slide', String(second.activeDot));

  // A manual move must restart the clock: right after clicking Next, the
  // next automatic advance is a full interval away, not whatever was left.
  await page.click('.movie-hero-arrow:last-of-type');
  // Clicking leaves the pointer on the Hero, and hover pause is real
  // behaviour - so the pointer is moved away before timing anything.
  await page.mouse.move(2, 2);
  await page.waitForTimeout(300);
  const afterNext = await heroState(page);
  check(afterNext.activeDot === 2, '[rotate] Next moves one slide forward',
    String(afterNext.activeDot));
  await page.waitForTimeout(2200);
  const midway = await heroState(page);
  check(midway.activeDot === 2, '[rotate] a manual move resets the timer rather than shortening it',
    `still on ${midway.activeDot} after 2.2s of a 3s interval`);
  await page.waitForTimeout(1400);
  const afterFull = await heroState(page);
  check(afterFull.activeDot === 0, '[rotate] and it advances once the full interval has passed',
    String(afterFull.activeDot));

  // Previous, and wrap-around. From slot 0, back is the last slot.
  await page.click('.movie-hero-arrow:first-of-type');
  await page.mouse.move(2, 2);
  await page.waitForTimeout(250);
  check((await heroState(page)).activeDot === 2, '[rotate] Previous wraps to the last slot');

  // A dot is a direct move.
  await page.$$eval('.movie-hero-dot', (nodes) => nodes[1].click());
  await page.mouse.move(2, 2);
  await page.waitForTimeout(250);
  check((await heroState(page)).activeDot === 1, '[rotate] a dot jumps straight to its slot');

  // Hover pause, on a pointer that can actually hover.
  await page.hover('.movie-hero');
  await page.waitForTimeout(300);
  const hovered = await heroState(page);
  const hoverState = await page.evaluate(() => window.movieHeroTimerState());
  check(hoverState.paused === true && !hoverState.running,
    '[rotate] hovering the Hero pauses it on a desktop pointer',
    JSON.stringify(hoverState));
  await page.waitForTimeout(3600);
  check((await heroState(page)).activeDot === hovered.activeDot,
    '[rotate] and it really does not advance while hovered');
  await page.mouse.move(2, 2);
  await page.waitForTimeout(300);
  const left = await page.evaluate(() => window.movieHeroTimerState());
  check(left.paused === false && left.running,
    '[rotate] moving the pointer away starts it again', JSON.stringify(left));

  check(errors.length === 0, '[rotate] no uncaught page errors', errors.join(' | '));
  await context.close();
}

// ---------------------------------------------------------------------------
// 3. A hidden tab pauses; a visible one resumes
// ---------------------------------------------------------------------------
{
  const items = [slot('a'), slot('b'), slot('c')];
  const { context, page, errors } = await newPage(featuredFile(items, { hero_rotate_seconds: 3 }));
  await openMovieHome(page);
  await page.waitForTimeout(800);
  const before = await heroState(page);

  // Playwright cannot background a tab, so the document reports itself
  // hidden and the real visibilitychange listener runs - which is the code
  // path under test.
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
    Object.defineProperty(document, 'hidden', { value: true, configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.waitForTimeout(4200);
  const whileHidden = await heroState(page);
  check(whileHidden.activeDot === before.activeDot,
    '[visibility] a hidden tab stops the carousel',
    `${before.activeDot} -> ${whileHidden.activeDot} after 4.2s`);

  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    Object.defineProperty(document, 'hidden', { value: false, configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.waitForTimeout(3600);
  const afterVisible = await heroState(page);
  check(afterVisible.activeDot !== whileHidden.activeDot,
    '[visibility] and a visible tab starts it again',
    `${whileHidden.activeDot} -> ${afterVisible.activeDot}`);

  check(errors.length === 0, '[visibility] no uncaught page errors', errors.join(' | '));
  await context.close();
}

// ---------------------------------------------------------------------------
// 4. The detail and the player stop it; Movie Home restarts it
// ---------------------------------------------------------------------------
{
  const { context, page, errors } = await newPage(null);
  const nav = await openMovieHome(page);
  await page.waitForTimeout(1200);
  const onHome = await heroState(page);
  check(!onHome.hidden, '[scope] the Hero is on Movie Home');

  // A real title's detail, opened from the grid.
  const info = page.locator('#sidebarList .movie-card-info').first();
  if (await info.count()) {
    await info.click({ force: true });
    await page.waitForTimeout(2000);
    const heroStopped = await page.evaluate(() => ({
      detailOpen: !document.querySelector('#movieDetailPanel').hidden,
      hero: window.movieHeroTimerState ? window.movieHeroTimerState() : null
    }));
    check(heroStopped.detailOpen, '[scope] the movie detail opened');
    check(heroStopped.hero && heroStopped.hero.stopped === true && !heroStopped.hero.running,
      '[scope] opening the detail stops the Hero timer', JSON.stringify(heroStopped));

    await page.locator('.movie-detail-close').first().click({ force: true }).catch(() => {});
    await page.waitForTimeout(1200);
    const backOnHome = await heroState(page);
    check(!backOnHome.hidden, '[scope] closing the detail brings the Hero back');
  }

  // A different movie category is not Movie Home.
  await clickNav(page, nav.sub, '.final-sub-button', 'Bangla');
  await page.waitForTimeout(2500);
  check((await heroState(page)).hidden, '[scope] the Hero is not shown on a category grid');

  // Nor is Live TV or Live Sports.
  await clickNav(page, nav.main, '.final-main-button', 'Live TV');
  await page.waitForTimeout(2500);
  const onLiveTv = await heroState(page);
  check(onLiveTv.hidden, '[scope] the Hero is not shown on Live TV');
  const protectedSurfaces = await page.evaluate(() => ({
    videos: document.querySelectorAll('video').length,
    controls: document.querySelectorAll('#playerControls').length,
    notice: document.querySelectorAll('#sticky-header-notice').length
  }));
  check(protectedSurfaces.videos === 1, '[scope] exactly one player element, still',
    String(protectedSurfaces.videos));
  check(protectedSurfaces.controls === 1, '[scope] the player control bar is untouched');
  check(protectedSurfaces.notice === 1, '[scope] the Notice bar is untouched');

  // Back to Movie Home. Returning to Movies lands on the movie category
  // that was last open - Bangla, above - so Home is asked for by name.
  await clickNav(page, nav.main, '.final-main-button', 'Movies');
  await page.waitForTimeout(2000);
  const backNav = await navSelectors(page);
  await clickNav(page, backNav.sub, '.final-sub-button', 'Home');
  await page.waitForTimeout(2500);
  const restored = await page.evaluate(() => ({
    hidden: document.querySelector('#movieHeroPanel').hidden,
    stopped: window.movieHeroTimerState().stopped
  }));
  check(!restored.hidden, '[scope] returning to Movie Home restores the Hero');
  check(restored.stopped === false, '[scope] and restarts its rotation',
    JSON.stringify(restored));

  check(errors.length === 0, '[scope] no uncaught page errors', errors.join(' | '));
  await context.close();
}

// ---------------------------------------------------------------------------
// 5. Every fact on screen is in the file
// ---------------------------------------------------------------------------
{
  const items = [
    slot('no-rating'),
    slot('rated', {
      name: 'Rated Film',
      rating: 8.1,
      rating_source: 'TMDB',
      quality: 'Full HD',
      badges: ['NEW', 'PREMIUM'],
      plot: 'A real overview from the metadata cache.',
      genres: ['Action', 'Crime'],
      backdrop: 'https://image.tmdb.org/t/p/w1280/back.jpg',
      artwork_kind: 'backdrop'
    })
  ];
  const { context, page, errors } = await newPage(featuredFile(items, { hero_rotate_seconds: 900 }));
  await openMovieHome(page);
  await page.waitForTimeout(900);

  const first = await heroState(page);
  check(first.tags.length === 0 || !first.tags.some((t) => /IMDb|TMDB|\d\.\d/.test(t)),
    '[facts] a title with no rating shows no rating element', JSON.stringify(first.tags));
  check(!first.badges.length, '[facts] a title in no special state shows no badge',
    JSON.stringify(first.badges));
  check(!first.desc, '[facts] a title with no real overview shows no synopsis', first.desc);
  check(first.posterFallback,
    '[facts] a poster-only title is treated as a fallback, not stretched as a backdrop');
  check(Boolean(first.artSrc),
    '[facts] and its poster is still shown sharp at its own aspect ratio', first.artSrc);

  await page.$$eval('.movie-hero-dot', (nodes) => nodes[1].click());
  await page.waitForTimeout(400);
  const second = await heroState(page);
  check(second.tags.includes('TMDB 8.1'),
    '[facts] a rating always carries the source that issued it', JSON.stringify(second.tags));
  check(!second.tags.some((t) => /IMDb/i.test(t)),
    '[facts] a TMDB score is never relabelled as IMDb', JSON.stringify(second.tags));
  check(second.tags.includes('Full HD'),
    '[facts] the quality label is the one the file carries', JSON.stringify(second.tags));
  check(second.badges.join(',') === 'NEW,PREMIUM',
    '[facts] badges are exactly the ones in the file', JSON.stringify(second.badges));
  check(second.desc === 'A real overview from the metadata cache.',
    '[facts] the synopsis is the real overview', second.desc);
  check(/Action, Crime/.test(second.meta), '[facts] genres come from the file', second.meta);
  check(!second.posterFallback,
    '[facts] a real backdrop fills the frame sharp rather than blurred');

  check(errors.length === 0, '[facts] no uncaught page errors', errors.join(' | '));
  await context.close();
}

// ---------------------------------------------------------------------------
// 5b. A stale file stops claiming TRENDING (plan section 30)
// ---------------------------------------------------------------------------
{
  const fresh = [slot('t', { name: 'Trending Film', badges: ['NEW', 'TRENDING'] })];
  const { context, page, errors } = await newPage(
    featuredFile(fresh, { hero_rotate_seconds: 900 })
  );
  await openMovieHome(page);
  await page.waitForTimeout(900);
  check((await heroState(page)).badges.includes('TRENDING'),
    '[stale] a fresh file may show TRENDING');
  await context.close();
}

{
  const old = new Date(Date.now() - 5 * 24 * 3600 * 1000).toISOString();
  const { context, page, errors } = await newPage(featuredFile(
    [slot('t', { name: 'Trending Film', badges: ['NEW', 'TRENDING'] })],
    { hero_rotate_seconds: 900, updated_at: old }
  ));
  await openMovieHome(page);
  await page.waitForTimeout(900);
  const hero = await heroState(page);
  check(!hero.badges.includes('TRENDING'),
    '[stale] a five-day-old file no longer claims TRENDING', JSON.stringify(hero.badges));
  check(hero.badges.includes('NEW'),
    '[stale] but a badge that is still true survives', JSON.stringify(hero.badges));
  check(!hero.hidden && Boolean(hero.title),
    '[stale] and the slot itself stays - a good pick does not expire', hero.title);
  check(/FEATURED ON CLICK TV/i.test(hero.kicker),
    '[stale] the label is still the honest one', hero.kicker);
  check(errors.length === 0, '[stale] no uncaught page errors', errors.join(' | '));
  await context.close();
}

// ---------------------------------------------------------------------------
// 6. Nothing real, nothing shown
// ---------------------------------------------------------------------------
{
  const { context, page, errors } = await newPage(featuredFile([]));
  await openMovieHome(page);
  await page.waitForTimeout(1200);
  check((await heroState(page)).hidden,
    '[empty] an empty featured.json produces no Hero at all rather than an empty frame');
  const grid = await page.$$eval('#sidebarList .movie-card', (n) => n.length);
  check(grid > 0, '[empty] and the rest of Movie Home still works', String(grid));
  check(errors.length === 0, '[empty] no uncaught page errors', errors.join(' | '));
  await context.close();
}

{
  const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await context.route('**/data/movies/discovery/featured.json*', (route) =>
    route.fulfill({ status: 500, contentType: 'text/plain', body: 'nope' }));
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(3500);
  await openMovieHome(page);
  await page.waitForTimeout(1500);
  check((await heroState(page)).hidden, '[failure] a failed Hero fetch shows no Hero');
  const grid = await page.$$eval('#sidebarList .movie-card', (n) => n.length);
  check(grid > 0, '[failure] and never blocks the rest of Movie Home', String(grid));
  check(errors.length === 0, '[failure] no uncaught page errors', errors.join(' | '));
  await context.close();
}

// ---------------------------------------------------------------------------
// 7. The handoffs: the existing player, the existing series detail
// ---------------------------------------------------------------------------
{
  // A Hero built around a title that really is in this catalogue, so Play
  // has something real to resolve.
  const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(3000);
  const realId = await page.evaluate(async () => {
    const response = await fetch('data/movies/search-index.json', { cache: 'no-store' });
    const data = await response.json();
    const row = (data.items || []).find((item) => item.type === 'movie');
    return row ? row.id : '';
  });
  check(Boolean(realId), '[handoff] a real catalogue id was found for the test', realId);

  await context.route('**/data/movies/discovery/featured.json*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(featuredFile([slot(realId, { name: 'Real Film' })],
        { hero_rotate_seconds: 900 }))
    }));
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3500);
  await openMovieHome(page);
  await page.waitForTimeout(1500);

  const single = await heroState(page);
  check(!single.hidden, '[handoff] the Hero renders the real title', single.title);
  check(single.arrows === 0, '[handoff] one slot means no arrows and no rotation',
    String(single.arrows));

  // Details reaches the existing detail panel.
  await page.click('.movie-hero-details');
  await page.waitForTimeout(2500);
  const detail = await page.evaluate(() => ({
    open: !document.querySelector('#movieDetailPanel').hidden,
    title: document.querySelector('.movie-detail-title')?.textContent?.trim() || '',
    videos: document.querySelectorAll('video').length
  }));
  check(detail.open, '[handoff] Details opens the existing movie detail');
  check(detail.videos === 1, '[handoff] and creates no second player', String(detail.videos));
  await page.locator('.movie-detail-close').first().click({ force: true }).catch(() => {});
  await page.waitForTimeout(1200);

  // Play reaches the existing player entry point. Playwright's Chromium has
  // no H.264 decoder, so what is asserted is the handoff - the same player
  // element, given this item - not that pixels decoded.
  await page.evaluate(() => {
    window.__startPlaybackCalls = [];
    const original = window.startPlayback;
    if (typeof original === 'function') {
      window.startPlayback = function (item, userInitiated) {
        window.__startPlaybackCalls.push({ id: item?.id || '', userInitiated });
        return original.apply(this, arguments);
      };
    }
  });
  const wrapped = await page.evaluate(() => Array.isArray(window.__startPlaybackCalls));
  if (wrapped) {
    await page.click('.movie-hero-play');
    await page.waitForTimeout(2500);
    const calls = await page.evaluate(() => window.__startPlaybackCalls || []);
    check(calls.length === 1 && calls[0].id === realId,
      '[handoff] Play hands the resolved record to the existing startPlayback',
      JSON.stringify(calls));
    const stopped = await page.evaluate(() => window.movieHeroTimerState().stopped);
    check(stopped === true, '[handoff] and the Hero timer stops when the player opens',
      String(stopped));
  }
  const players = await page.evaluate(() => ({
    videos: document.querySelectorAll('video').length,
    controls: document.querySelectorAll('#playerControls').length
  }));
  check(players.videos === 1, '[handoff] still exactly one player element', String(players.videos));
  check(players.controls === 1, '[handoff] the player controls are still the same one');

  check(errors.length === 0, '[handoff] no uncaught page errors', errors.join(' | '));
  await context.close();
}

// ---------------------------------------------------------------------------
// 8. A series slot offers the series flow, never a play button
// ---------------------------------------------------------------------------
{
  const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(3000);
  const realSeries = await page.evaluate(async () => {
    const response = await fetch('data/movies/search-index.json', { cache: 'no-store' });
    const data = await response.json();
    const row = (data.items || []).find((item) => item.type === 'series');
    return row ? { id: row.id, name: row.name } : null;
  });
  check(Boolean(realSeries), '[series] a real series was found for the test',
    JSON.stringify(realSeries));

  if (realSeries) {
    await context.route('**/data/movies/discovery/featured.json*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(featuredFile([
          slot(realSeries.id, {
            type: 'series',
            name: realSeries.name,
            category: 'Premium',
            badges: ['SERIES'],
            default_season: 1
          })
        ], { hero_rotate_seconds: 900 }))
      }));
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3500);
    await openMovieHome(page);
    await page.waitForTimeout(1500);

    const hero = await heroState(page);
    check(hero.buttons.some((t) => /View Series/i.test(t)),
      '[series] a series slot offers View Series', JSON.stringify(hero.buttons));
    check(!hero.buttons.some((t) => /^Play$/i.test(t)),
      '[series] and never a bare Play, because no episode has been chosen yet',
      JSON.stringify(hero.buttons));
    check(hero.badges.includes('SERIES'), '[series] the SERIES badge is shown',
      JSON.stringify(hero.badges));
    check(/Web Series/.test(hero.meta), '[series] the meta row says it is a series', hero.meta);

    await page.click('.movie-hero-play');
    await page.waitForTimeout(3500);
    const afterClick = await page.evaluate(() => ({
      videos: document.querySelectorAll('video').length,
      seriesDetail: Boolean(document.querySelector('.series-episode-card'))
        || Boolean(document.querySelector('#sidebarList.series-detail-list'))
        || (document.querySelector('#sidebarCount')?.textContent || '').length > 0,
      heroStopped: window.movieHeroTimerState().stopped
    }));
    check(afterClick.videos === 1, '[series] no second player was created',
      String(afterClick.videos));
    check(afterClick.heroStopped === true, '[series] the Hero timer stopped on the handoff',
      String(afterClick.heroStopped));
  }

  check(errors.length === 0, '[series] no uncaught page errors', errors.join(' | '));
  await context.close();
}

// ---------------------------------------------------------------------------
// 9. Reduced motion
// ---------------------------------------------------------------------------
{
  const context = await browser.newContext({
    viewport: { width: 1366, height: 768 },
    reducedMotion: 'reduce'
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await withFeatured(context, featuredFile([slot('a'), slot('b'), slot('c')],
    { hero_rotate_seconds: 3 }));
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(3500);
  await openMovieHome(page);
  await page.waitForTimeout(900);

  const before = await heroState(page);
  await page.waitForTimeout(4500);
  const after = await heroState(page);
  check(after.activeDot === before.activeDot,
    '[reduced-motion] the carousel does not move on its own',
    `${before.activeDot} -> ${after.activeDot}`);
  check(after.arrows === 2 && after.dots === 3,
    '[reduced-motion] but every control is still there, so nothing is unreachable');

  await page.click('.movie-hero-arrow:last-of-type');
  await page.waitForTimeout(400);
  check((await heroState(page)).activeDot === 1,
    '[reduced-motion] and the arrows still work');

  const transitions = await page.evaluate(() => {
    const layer = document.querySelector('.movie-hero-bg');
    return layer ? getComputedStyle(layer).transitionDuration : '';
  });
  const longest = Math.max(...transitions.split(',').map((part) => parseFloat(part) || 0));
  check(longest <= 0.01, '[reduced-motion] the crossfade is off', transitions);

  check(errors.length === 0, '[reduced-motion] no uncaught page errors', errors.join(' | '));
  await context.close();
}

// ---------------------------------------------------------------------------
// 10. Every width the plan names
// ---------------------------------------------------------------------------
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

for (const viewport of VIEWPORTS) {
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    isMobile: viewport.mobile,
    hasTouch: viewport.mobile
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await withFeatured(context, featuredFile([
    slot('a', {
      name: 'A Very Long Featured Title That Would Wrap Onto Several Lines If Nothing Clamped It',
      plot: 'A long real overview that would take four or five lines at this width if the '
        + 'Hero let it, which is exactly what the two-line clamp is for.',
      rating: 7.4,
      rating_source: 'IMDb',
      quality: 'Full HD',
      badges: ['NEW']
    }),
    slot('b'),
    slot('c')
  ], { hero_rotate_seconds: 900 }));
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(3500);
  await openMovieHome(page);
  await page.waitForTimeout(1200);

  const label = viewport.label;
  const measured = await page.evaluate(() => {
    const panel = document.querySelector('#movieHeroPanel');
    const hero = panel ? panel.querySelector('.movie-hero') : null;
    if (!hero) return null;
    const rect = hero.getBoundingClientRect();
    const title = panel.querySelector('.movie-hero-title');
    const desc = panel.querySelector('.movie-hero-desc');
    const titleRect = title ? title.getBoundingClientRect() : null;
    const titleLine = title ? parseFloat(getComputedStyle(title).lineHeight) : 0;
    const descVisible = desc ? getComputedStyle(desc).display !== 'none' : false;
    const descRect = desc && descVisible ? desc.getBoundingClientRect() : null;
    const descLine = desc ? parseFloat(getComputedStyle(desc).lineHeight) : 0;
    const buttons = [...panel.querySelectorAll('.movie-hero-play, .movie-hero-details')];
    const arrows = [...panel.querySelectorAll('.movie-hero-arrow')];
    const box = (node) => {
      const r = node.getBoundingClientRect();
      return Math.round(Math.min(r.width, r.height));
    };
    return {
      right: Math.round(rect.right),
      left: Math.round(rect.left),
      height: Math.round(rect.height),
      docWidth: document.documentElement.clientWidth,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      titleLines: titleRect && titleLine ? Math.round(titleRect.height / titleLine) : 0,
      descVisible,
      descLines: descRect && descLine ? Math.round(descRect.height / descLine) : 0,
      smallestButton: buttons.length ? Math.min(...buttons.map(box)) : 0,
      smallestArrow: arrows.length ? Math.min(...arrows.map(box)) : 0,
      buttonsInside: buttons.every((n) => n.getBoundingClientRect().right
        <= document.documentElement.clientWidth + 2),
      // The controls must not sit on top of the action buttons.
      controlsClear: (() => {
        const controls = panel.querySelector('.movie-hero-controls');
        if (!controls || !buttons.length) return true;
        const c = controls.getBoundingClientRect();
        return buttons.every((n) => {
          const b = n.getBoundingClientRect();
          return b.right <= c.left + 1 || b.left >= c.right - 1
            || b.bottom <= c.top + 1 || b.top >= c.bottom - 1;
        });
      })()
    };
  });

  check(Boolean(measured), `[${label}] the Hero renders`);
  if (measured) {
    check(measured.overflow <= 2, `[${label}] the Hero causes no horizontal overflow`,
      `${measured.overflow}px`);
    check(measured.right <= measured.docWidth + 2 && measured.left >= -2,
      `[${label}] the Hero stays inside the viewport`,
      `${measured.left}..${measured.right} of ${measured.docWidth}`);
    check(measured.titleLines > 0 && measured.titleLines <= 2,
      `[${label}] a long title is clamped to two lines`, String(measured.titleLines));
    check(!measured.descVisible || measured.descLines <= 3,
      `[${label}] the synopsis never runs past three lines`, String(measured.descLines));
    if (viewport.mobile) {
      check(!measured.descVisible,
        `[${label}] the synopsis is dropped on a phone so the cards stay visible`);
      check(measured.smallestButton >= 28,
        `[${label}] the Hero buttons are tappable`, `${measured.smallestButton}px`);
      check(measured.smallestArrow >= 24,
        `[${label}] the Hero arrows are tappable`, `${measured.smallestArrow}px`);
      check(measured.height <= 240,
        `[${label}] the Hero stays compact enough to leave cards on screen`,
        `${measured.height}px`);
    }
    check(measured.buttonsInside, `[${label}] the action buttons are inside the viewport`);
    check(measured.controlsClear,
      `[${label}] the carousel controls do not sit on top of the buttons`);
  }
  check(errors.length === 0, `[${label}] no uncaught page errors`, errors.join(' | '));
  await context.close();
}

await browser.close();

console.log(`\n${passes.length} passed, ${failures.length} failed`);
for (const failure of failures) console.log(`  FAIL  ${failure}`);
if (!failures.length) for (const pass of passes) console.log(`  PASS  ${pass}`);
if (failures.length) process.exit(1);
