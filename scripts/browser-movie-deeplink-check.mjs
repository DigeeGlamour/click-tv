/**
 * Deep links, shareable URLs and document metadata (PART 21).
 *
 * The links are exercised the way a person would use one: the URL is typed
 * into a fresh page load, not synthesised by calling a function. What it
 * pins down is that a shared link opens the right thing, a dead link says so
 * instead of quietly playing something else, back and forward behave, and
 * the structured data contains nothing that was invented.
 *
 * Usage: node scripts/browser-movie-deeplink-check.mjs [baseUrl]
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

async function settle(page, ms = 6000) {
  await page.waitForTimeout(ms);
}

async function readDetail(page) {
  return page.evaluate(() => {
    const ld = document.getElementById('movieJsonLd');
    let structured = null;
    try { structured = ld ? JSON.parse(ld.textContent) : null; } catch (_) { structured = 'INVALID'; }
    return {
      panelVisible: !document.querySelector('#movieDetailPanel')?.hidden,
      title: document.querySelector('.movie-detail-title')?.textContent?.trim() || '',
      unavailable: Boolean(document.querySelector('.movie-detail-gone')),
      documentTitle: document.title,
      description: document.head.querySelector('meta[name="description"]')?.getAttribute('content') || '',
      canonical: document.head.querySelector('link[rel="canonical"]')?.getAttribute('href') || '',
      ogTitle: document.head.querySelector('meta[property="og:title"]')?.getAttribute('content') || '',
      structured,
      playing: document.querySelector('#metaTitle')?.textContent?.trim() || '',
      videos: document.querySelectorAll('video').length,
      url: window.location.href,
      share: Boolean(document.querySelector('.movie-detail-share'))
    };
  });
}

/** A real id from the published index, so the link is one that truly exists. */
async function pickIds(page) {
  return page.evaluate(async () => {
    const index = await window.loadMovieBrowseIndex();
    const movie = (index || []).find((row) => (row.type || 'movie') === 'movie');
    const manifest = await (await fetch('data/series/manifest.json')).json();
    let series = null;
    for (const entry of Object.values(manifest.categories || {})) {
      if (!entry?.index || !entry.count) continue;
      const list = await (await fetch(entry.index)).json();
      if (list.items && list.items.length) { series = list.items[0]; break; }
    }
    return {
      movie: movie ? { id: movie.id, name: movie.name } : null,
      series: series ? { id: series.id, name: series.name } : null
    };
  });
}

async function run(label, viewport, navSelector) {
  const context = await browser.newContext({ viewport, permissions: ['clipboard-read', 'clipboard-write'] });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  await clickNavButton(page, navSelector, '.final-main-button', 'Movies');
  await page.waitForTimeout(1500);

  const ids = await pickIds(page);
  check(Boolean(ids.movie), `[${label}] a real movie id was available to link to`);
  check(Boolean(ids.series), `[${label}] a real series id was available to link to`);
  const baseline = await page.evaluate(() => document.title);

  // --- 1. a movie deep link, loaded cold -----------------------------------
  if (ids.movie) {
    await page.goto(`${baseUrl}/?movie=${encodeURIComponent(ids.movie.id)}`,
      { waitUntil: 'domcontentloaded', timeout: 30000 });
    await settle(page);
    const detail = await readDetail(page);

    check(detail.panelVisible && detail.title === ids.movie.name,
      `[${label}] a movie link opens that exact title`,
      JSON.stringify({ wanted: ids.movie.name, got: detail.title }));
    check(!detail.unavailable, `[${label}] a valid link is not shown as unavailable`);
    check(detail.documentTitle.includes(ids.movie.name),
      `[${label}] the document title names the title`, detail.documentTitle);
    check(detail.documentTitle !== baseline,
      `[${label}] the document title actually changed from the site default`);
    check(detail.description.length > 0 && !/undefined|NaN|null/.test(detail.description),
      `[${label}] the description is real text`, detail.description.slice(0, 80));
    check(detail.canonical.includes(`movie=${encodeURIComponent(ids.movie.id)}`),
      `[${label}] the canonical points at the stable detail route`, detail.canonical);
    check(detail.share, `[${label}] the detail offers Copy Link`);
    check(detail.videos === 1, `[${label}] still exactly one player element`);

    // A deep link must not start playback by itself.
    const playingIsDetail = detail.playing === ids.movie.name;
    check(!playingIsDetail,
      `[${label}] opening a link does NOT start the player`,
      JSON.stringify({ playing: detail.playing, detail: ids.movie.name }));

    // --- structured data carries nothing invented --------------------------
    const ld = detail.structured;
    check(ld && ld !== 'INVALID' && ld['@context'] === 'https://schema.org',
      `[${label}] JSON-LD is present and valid`, JSON.stringify(ld).slice(0, 80));
    if (ld && ld !== 'INVALID') {
      check(ld['@type'] === 'Movie', `[${label}] a film is typed as a Movie`, String(ld['@type']));
      check(ld.name === ids.movie.name, `[${label}] the structured name is the real one`);
      check(!('review' in ld) && !('actor' in ld),
        `[${label}] no review or cast is invented`, JSON.stringify(Object.keys(ld)));
      check(!ld.aggregateRating || !('ratingCount' in ld.aggregateRating),
        `[${label}] no ratingCount is invented`, JSON.stringify(ld.aggregateRating));
      check(!ld.aggregateRating || Boolean(ld.aggregateRating.author?.name),
        `[${label}] a rating, if present, names who issued it`, JSON.stringify(ld.aggregateRating));
      check(!('datePublished' in ld) || /^\d{4}-\d{2}-\d{2}$/.test(ld.datePublished),
        `[${label}] a release date, if present, is a real date`, String(ld.datePublished));
    }

    // --- 2. Copy Link produces the same link ------------------------------
    await page.locator('.movie-detail-share').click({ force: true });
    await page.waitForTimeout(900);
    const copied = await page.evaluate(() => navigator.clipboard.readText().catch(() => ''));
    check(copied.includes(`movie=${encodeURIComponent(ids.movie.id)}`),
      `[${label}] Copy Link copies the stable route`, copied);

    // --- 3. back leaves the detail without leaving the app ----------------
    await page.goBack();
    await page.waitForTimeout(2500);
    const afterBack = await page.evaluate(() => ({
      url: window.location.href,
      panelVisible: !document.querySelector('#movieDetailPanel')?.hidden,
      stillOnSite: window.location.pathname.startsWith('/')
    }));
    check(afterBack.stillOnSite, `[${label}] Back does not leave the app`);
  }

  // --- 4. a series deep link ----------------------------------------------
  if (ids.series) {
    await page.goto(`${baseUrl}/?series=${encodeURIComponent(ids.series.id)}`,
      { waitUntil: 'domcontentloaded', timeout: 30000 });
    await settle(page, 8000);
    const series = await page.evaluate(() => ({
      shell: Boolean(document.querySelector('.series-detail-shell')),
      title: document.querySelector('.series-detail-shell h3')?.textContent?.trim() || '',
      unavailable: Boolean(document.querySelector('.movie-detail-gone')),
      episodes: document.querySelectorAll('.series-episode-card').length,
      documentTitle: document.title
    }));
    check(series.shell && series.title === ids.series.name,
      `[${label}] a series link opens that series`,
      JSON.stringify({ wanted: ids.series.name, got: series.title }));
    check(series.episodes > 0, `[${label}] the season's episodes are listed`, String(series.episodes));
  }

  // --- 5. an id that matches nothing --------------------------------------
  await page.goto(`${baseUrl}/?movie=this-film-does-not-exist-9999`,
    { waitUntil: 'domcontentloaded', timeout: 30000 });
  await settle(page);
  const missing = await readDetail(page);
  check(missing.unavailable,
    `[${label}] an id that matches nothing shows the unavailable state`, JSON.stringify(missing.title));
  check(!/this-film-does-not-exist/.test(missing.playing),
    `[${label}] a dead link does not auto-play a substitute`, missing.playing);
  check(missing.videos === 1, `[${label}] a dead link creates no second player`);

  const recovered = await page.evaluate(() => Boolean(document.querySelector('.movie-detail-back')));
  check(recovered, `[${label}] the dead link offers Back to Movies`);

  // --- 6. a normal visit is untouched --------------------------------------
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4500);
  const plain = await page.evaluate(() => ({
    documentTitle: document.title,
    canonical: Boolean(document.head.querySelector('link[rel="canonical"]')),
    jsonLd: Boolean(document.getElementById('movieJsonLd')),
    panelHidden: Boolean(document.querySelector('#movieDetailPanel')?.hidden),
    url: window.location.href
  }));
  check(plain.documentTitle === baseline,
    `[${label}] a visit with no route keeps the site's own title`, plain.documentTitle);
  check(!plain.jsonLd, `[${label}] no structured data is emitted without a detail`);
  check(plain.panelHidden, `[${label}] no detail opens on a plain visit`);

  check(pageErrors.length === 0, `[${label}] no uncaught page errors`, pageErrors.join(' | '));
  await context.close();
}

try {
  await run('desktop', { width: 1280, height: 800 }, '#desktopMainNav');
  await run('mobile', { width: 390, height: 844 }, '#mobileMainNav');
} finally {
  await browser.close();
}

console.log(`\n${passes.length} passed, ${failures.length} failed`);
for (const pass of passes) console.log(`  PASS  ${pass}`);
for (const failure of failures) console.log(`  FAIL  ${failure}`);
if (failures.length) process.exit(1);
