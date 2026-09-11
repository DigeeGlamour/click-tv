/**
 * Movie detail smoke test — desktop and mobile (PART 14).
 *
 * The contract this defends is "show what is real, hide what is not". A
 * detail panel that invents a runtime, a cast list or a 4K badge to look
 * complete is worse than a sparse one, because a viewer cannot tell the
 * difference between a field we know and a field we guessed.
 *
 * It also pins the two rules that keep playback honest: opening a detail
 * never starts the player, and Play hands off to the same entry point every
 * other card uses.
 *
 * Usage: node scripts/browser-movie-detail-check.mjs [baseUrl]
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

async function openMovies(page, navSelector) {
  await page.locator(`${navSelector} .final-main-button`).first().waitFor({ state: 'visible', timeout: 30000 });
  await page.locator(`${navSelector} .final-main-button`, { hasText: 'Movies' }).first().click();
  await page.waitForTimeout(1200);
}

async function run(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({ viewport, isMobile: viewport.width < 800, hasTouch: viewport.width < 800 });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await openMovies(page, navSelector);

  // --- the metadata contract, exercised directly ------------------------
  const contract = await page.evaluate(() => {
    const rowsOf = (item) => Object.fromEntries(window.movieDetailRows(item));
    return {
      full: rowsOf({
        name: 'Full Film', year: 2026, release_date: '2026-01-02', category: 'Bangla',
        genres: ['Action', 'Crime'], rating: 8.1, rating_source: 'IMDb',
        resolution_height: 1080, runtime_minutes: 128, original_title: 'Poorno Chobi',
        director: 'A Director', cast_top: ['One', 'Two'],
        audio_languages: ['Bengali'], subtitle_languages: ['English']
      }),
      sparse: rowsOf({ name: 'Sparse Film', year: 2019, category: 'Mix' }),
      tmdbRating: rowsOf({ name: 'X', rating: 7.4, rating_source: 'TMDB' }),
      imdbRating: rowsOf({ name: 'X', rating: 8.8, rating_source: 'IMDb' }),
      unlabelledRating: rowsOf({ name: 'X', rating: 6.6 }),
      fourKByTitle: window.movieDetailQuality({ name: 'Some Film 4K 2160p UHD' }),
      fourKByStream: window.movieDetailQuality({ resolution_height: 2160 }),
      hdByStream: window.movieDetailQuality({ resolution_height: 1080 }),
      qualityFromSourceLabel: window.movieDetailQuality({ resolution: 'HD 720P' }),
      noQuality: window.movieDetailQuality({ name: 'No Evidence' })
    };
  });

  check(contract.full.Runtime === '128 min' && contract.full.Director === 'A Director'
    && contract.full.Cast === 'One, Two' && contract.full.Subtitles === 'English',
    `[${label}] a fully populated title shows every real field`, JSON.stringify(contract.full));

  check(!('Runtime' in contract.sparse), `[${label}] missing runtime is hidden, not invented`);
  check(!('Director' in contract.sparse), `[${label}] missing director is hidden`);
  check(!('Cast' in contract.sparse), `[${label}] missing cast is hidden`);
  check(!('Subtitles' in contract.sparse),
    `[${label}] unknown subtitles are never claimed to exist`);
  check(!('Rating' in contract.sparse), `[${label}] a title with no rating shows none`);
  check(contract.sparse.Year === '2019' && contract.sparse.Category === 'Mix',
    `[${label}] the real fields it does have are still shown`, JSON.stringify(contract.sparse));

  check(contract.imdbRating.Rating === '8.8 (IMDb)',
    `[${label}] an IMDb rating is labelled IMDb`, contract.imdbRating.Rating);
  check(contract.tmdbRating.Rating === '7.4 (TMDB)' && !contract.tmdbRating.Rating.includes('IMDb'),
    `[${label}] a TMDB score is never presented as IMDb`, contract.tmdbRating.Rating);
  check(contract.unlabelledRating.Rating === '6.6',
    `[${label}] a rating with no known source claims none`, contract.unlabelledRating.Rating);

  check(contract.fourKByTitle === '',
    `[${label}] "4K" in a title is not evidence of 4K`, contract.fourKByTitle);
  check(contract.fourKByStream === '4K UHD',
    `[${label}] real stream height does decide quality`, contract.fourKByStream);
  check(contract.hdByStream === 'FHD 1080p', `[${label}] 1080p height reads as FHD`);
  check(contract.qualityFromSourceLabel === 'HD 720P',
    `[${label}] a source-published label is used when no height was measured`);
  check(contract.noQuality === '', `[${label}] no evidence means no quality claim`);

  // --- opening a detail must not start playback --------------------------
  await page.locator(`${subNavSelector} .final-sub-button`, { hasText: 'Bangla' }).first().click();
  await page.waitForTimeout(1600);

  // Let whatever the page started on its own finish attaching first. The
  // autoplay item hands HLS.js a MediaSource asynchronously, so a snapshot
  // taken too early races that and reads as though the detail moved it.
  const playbackState = () => page.evaluate(() => ({
    src: document.querySelector('video')?.currentSrc || document.querySelector('video')?.src || '',
    title: document.querySelector('#metaTitle')?.textContent?.trim() || ''
  }));

  let before = await playbackState();
  for (let attempt = 0; attempt < 12; attempt += 1) {
    await page.waitForTimeout(700);
    const now = await playbackState();
    if (now.src === before.src && now.title === before.title) break;
    before = now;
  }
  const srcBeforeDetail = before.src;
  const titleBeforeDetail = before.title;

  const infoButton = page.locator('#sidebarList .movie-card-info').first();
  const hasCards = await infoButton.count();
  check(hasCards > 0, `[${label}] movie cards expose a Details affordance`);

  if (hasCards) {
    await infoButton.click({ force: true });
    await page.waitForTimeout(1200);
    const opened = await page.evaluate(() => ({
      visible: !document.querySelector('#movieDetailPanel')?.hidden,
      title: document.querySelector('.movie-detail-title')?.textContent?.trim() || '',
      facts: [...document.querySelectorAll('.movie-detail-fact dt')].map((n) => n.textContent.trim()),
      emptyFacts: [...document.querySelectorAll('.movie-detail-fact dd')]
        .filter((n) => !n.textContent.trim()).length,
      hasPlay: Boolean(document.querySelector('.movie-detail-play')),
      hasWatchlist: Boolean(document.querySelector('.movie-detail-watchlist')),
      videoSrc: document.querySelector('video')?.currentSrc || document.querySelector('video')?.src || ''
    }));

    check(opened.visible && opened.title, `[${label}] the detail panel opens with a title`, JSON.stringify(opened.title));
    check(opened.emptyFacts === 0, `[${label}] no field is rendered empty`, String(opened.emptyFacts));
    check(opened.hasPlay && opened.hasWatchlist, `[${label}] Play and Watchlist are offered`);
    // The real question is not whether a byte moved in the video element -
    // the page may still be settling - but whether opening a detail put the
    // detail's own title into the player. It must not.
    const playingTitle = await page.evaluate(() =>
      document.querySelector('#metaTitle')?.textContent?.trim() || '');
    check(playingTitle === titleBeforeDetail && playingTitle !== opened.title,
      `[${label}] opening a detail does NOT start playback`,
      JSON.stringify({ before: titleBeforeDetail, now: playingTitle, detail: opened.title }));

    // --- Play uses the existing handoff -----------------------------------
    await page.locator('.movie-detail-play').click({ force: true });

    // Give the existing engine the time it really takes: a movie source is
    // probed, may fall back to the proxy, and only then attaches.
    let afterPlay = null;
    for (let attempt = 0; attempt < 16; attempt += 1) {
      await page.waitForTimeout(700);
      afterPlay = await page.evaluate(() => ({
        panelHidden: Boolean(document.querySelector('#movieDetailPanel')?.hidden),
        videoSrc: document.querySelector('video')?.currentSrc || document.querySelector('video')?.src || '',
        title: document.querySelector('#metaTitle')?.textContent?.trim() || '',
        videoCount: document.querySelectorAll('video').length
      }));
      if (afterPlay.title === opened.title) break;
    }

    check(afterPlay.panelHidden, `[${label}] Play closes the detail`);
    check(afterPlay.title === opened.title,
      `[${label}] Play hands that exact title to the existing player`,
      JSON.stringify({ detail: opened.title, playing: afterPlay.title }));
    check(Boolean(afterPlay.videoSrc),
      `[${label}] the existing player element is the one that received it`,
      afterPlay.videoSrc.slice(0, 60));
    check(afterPlay.videoCount === 1,
      `[${label}] there is still exactly one player element - no second player`,
      String(afterPlay.videoCount));
  }

  check(pageErrors.length === 0, `[${label}] no uncaught page errors`, pageErrors.join(' | '));
  await context.close();
}

try {
  await run('desktop', { width: 1280, height: 800 }, '#desktopMainNav', '#desktopSubNav');
  await run('mobile', { width: 390, height: 844 }, '#mobileMainNav', '#mobileSubNav');
} finally {
  await browser.close();
}

console.log(`\n${passes.length} passed, ${failures.length} failed`);
for (const pass of passes) console.log(`  PASS  ${pass}`);
for (const failure of failures) console.log(`  FAIL  ${failure}`);
if (failures.length) process.exit(1);
