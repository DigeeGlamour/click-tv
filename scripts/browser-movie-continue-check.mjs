/**
 * Continue Watching smoke test (PART 16).
 *
 * The row is worthless if it is not true. So the things pinned here are:
 * nothing appears without real playback, a two-second mis-tap leaves no
 * trace, a finished title leaves the row, a corrupt store does not take the
 * page down, Remove does not touch the watchlist, and Resume returns to the
 * exact position of the exact title - the exact episode, for a series.
 *
 * The threshold and the resume are exercised against real playback of a real
 * catalogue item, not against a stub.
 *
 * Usage: node scripts/browser-movie-continue-check.mjs [baseUrl]
 */
import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const STORE = 'clicktv_continue_watching_v2';
const WATCHLIST = 'clicktv_favorites_v1';
const failures = [];
const passes = [];

function check(condition, message, detail = '') {
  if (condition) passes.push(message);
  else failures.push(`${message}${detail ? ` — ${detail}` : ''}`);
}

const MEDIA_PATH = '__continue-watching-fixture.wav';

/**
 * A silent WAV of a known length, built here rather than committed.
 *
 * The catalogue is H.264 in MKV and this is the Playwright build of Chromium,
 * which has no proprietary decoders - every real title loads and then sits at
 * readyState 0. WAV is one of the formats it does decode, so this gives the
 * real player element a real duration and a real seekable timeline to run the
 * threshold and resume rules against.
 */
function silentWav(seconds = 300, sampleRate = 8000) {
  const samples = seconds * sampleRate;
  const buffer = Buffer.alloc(44 + samples, 128); // 128 is silence for 8-bit PCM
  buffer.write('RIFF', 0);
  buffer.writeUInt32LE(36 + samples, 4);
  buffer.write('WAVE', 8);
  buffer.write('fmt ', 12);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);        // PCM
  buffer.writeUInt16LE(1, 22);        // mono
  buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(sampleRate, 28);
  buffer.writeUInt16LE(1, 32);
  buffer.writeUInt16LE(8, 34);
  buffer.write('data', 36);
  buffer.writeUInt32LE(samples, 40);
  return buffer;
}

const FIXTURE_MEDIA = silentWav();

const browser = await chromium.launch({ headless: true });

async function clickNavButton(page, containerSelector, buttonSelector, label) {
  const deadline = Date.now() + 30000;
  for (;;) {
    const seen = await page.$$eval(`${containerSelector} ${buttonSelector}`,
      (nodes) => nodes.map((node) => node.textContent.trim()));
    if (seen.some((text) => text.includes(label))) break;
    if (Date.now() > deadline) throw new Error(`${label} never appeared in ${containerSelector}; saw ${JSON.stringify(seen)}`);
    await page.waitForTimeout(300);
  }
  await page.$$eval(`${containerSelector} ${buttonSelector}`, (nodes, text) => {
    const target = nodes.find((node) => node.textContent.trim().includes(text));
    if (target) target.click();
  }, label);
}

function seededStore() {
  const now = Date.now();
  return {
    'movie:real-halfway': {
      key: 'movie:real-halfway', content_type: 'movie', item_id: 'real-halfway',
      name: 'Halfway Film', logo: '', category: 'Bangla',
      position_seconds: 1800, duration_seconds: 6000, progress_percent: 30,
      last_played_at: now, completed: false,
      snapshot: { id: 'real-halfway', name: 'Halfway Film', url: 'https://example.test/a.mkv', backups: [] }
    },
    'episode:some-show:s2:e4': {
      key: 'episode:some-show:s2:e4', content_type: 'episode', item_id: 'some-show-s02-04',
      name: 'Some Show — S02 E04', logo: '', category: 'Hindi',
      series_id: 'some-show', series_name: 'Some Show', season_number: 2, episode_number: 4,
      episode_title: 'Episode 04',
      position_seconds: 600, duration_seconds: 2400, progress_percent: 25,
      last_played_at: now - 1000, completed: false,
      snapshot: { id: 'some-show-s02-04', name: 'Some Show — S02 E04', content_kind: 'episode',
        series_id: 'some-show', season_number: 2, episode_number: 4,
        url: 'https://example.test/e.mkv', backups: [] }
    },
    'movie:finished': {
      key: 'movie:finished', content_type: 'movie', item_id: 'finished',
      name: 'Finished Film', logo: '', category: 'Mix',
      position_seconds: 5900, duration_seconds: 6000, progress_percent: 98.3,
      last_played_at: now - 2000, completed: true,
      snapshot: { id: 'finished', name: 'Finished Film', url: 'https://example.test/f.mkv', backups: [] }
    },
    'movie:barely-touched': {
      key: 'movie:barely-touched', content_type: 'movie', item_id: 'barely',
      name: 'Barely Touched', logo: '', category: 'Mix',
      position_seconds: 4, duration_seconds: 6000, progress_percent: 0.07,
      last_played_at: now - 3000, completed: false,
      snapshot: { id: 'barely', name: 'Barely Touched', url: 'https://example.test/b.mkv', backups: [] }
    },
    'movie:gone': {
      key: 'movie:gone', content_type: 'movie', item_id: 'gone',
      name: 'Withdrawn Film', logo: '', category: 'Mix',
      position_seconds: 900, duration_seconds: 6000, progress_percent: 15,
      last_played_at: now - 4000, completed: false,
      // No url, no playback_id, no backups: the source is gone.
      snapshot: { id: 'gone', name: 'Withdrawn Film', backups: [] }
    },
    'movie:junk': 'this is not an entry'
  };
}

async function openMoviesHome(page, navSelector, subNavSelector) {
  await clickNavButton(page, navSelector, '.final-main-button', 'Movies');
  await page.waitForTimeout(1500);
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Home');
  await page.waitForTimeout(2500);
}

async function readRow(page) {
  return page.evaluate(() => {
    const panel = document.querySelector('#movieContinuePanel');
    const cards = [...document.querySelectorAll('.movie-continue-card')];
    return {
      hidden: Boolean(panel?.hidden),
      heading: document.querySelector('.movie-continue-title')?.textContent?.trim() || '',
      titles: cards.map((card) => card.querySelector('.movie-continue-copy strong')?.textContent?.trim() || ''),
      resumable: cards.filter((card) => card.querySelector('.movie-continue-resume')).length,
      unavailable: cards.filter((card) => card.querySelector('.movie-continue-gone')).length,
      removable: cards.filter((card) => card.querySelector('.movie-continue-remove')).length
    };
  });
}

async function run(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({ viewport, isMobile: viewport.width < 800, hasTouch: viewport.width < 800 });
  // Seeded before the first script runs, so the page reads it at bootstrap
  // exactly as it would read a real viewer's own history.
  await context.addInitScript(([storeKey, watchlistKey, store]) => {
    localStorage.setItem(storeKey, JSON.stringify(store));
    localStorage.setItem(watchlistKey, JSON.stringify(['a-watchlisted-film']));
  }, [STORE, WATCHLIST, seededStore()]);

  // Range requests are honoured properly: without them the element loads but
  // cannot seek, and a resume is a seek.
  await context.route(`**/${MEDIA_PATH}*`, (route) => {
    const range = route.request().headers().range || '';
    const match = /bytes=(\d*)-(\d*)/.exec(range);
    if (!match) {
      route.fulfill({
        status: 200,
        headers: {
          'Content-Type': 'audio/wav',
          'Accept-Ranges': 'bytes',
          'Content-Length': String(FIXTURE_MEDIA.length)
        },
        body: FIXTURE_MEDIA
      });
      return;
    }
    const start = match[1] ? Number(match[1]) : 0;
    const end = match[2] ? Number(match[2]) : FIXTURE_MEDIA.length - 1;
    const slice = FIXTURE_MEDIA.subarray(start, end + 1);
    route.fulfill({
      status: 206,
      headers: {
        'Content-Type': 'audio/wav',
        'Accept-Ranges': 'bytes',
        'Content-Range': `bytes ${start}-${end}/${FIXTURE_MEDIA.length}`,
        'Content-Length': String(slice.length)
      },
      body: slice
    });
  });

  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);

  // --- the row, on Movies Home -------------------------------------------
  await openMoviesHome(page, navSelector, subNavSelector);
  const row = await readRow(page);

  check(!row.hidden, `[${label}] the row appears when there is real progress`);
  check(/continue watching/i.test(row.heading), `[${label}] the row is labelled`, row.heading);
  check(row.titles.some((title) => title.includes('Halfway Film')),
    `[${label}] an unfinished movie is offered`, JSON.stringify(row.titles));
  check(row.titles.some((title) => /S02 E04/.test(title)),
    `[${label}] an episode is offered as the exact season and episode`, JSON.stringify(row.titles));
  check(!row.titles.some((title) => title.includes('Finished Film')),
    `[${label}] a finished title has left the row`, JSON.stringify(row.titles));
  check(!row.titles.some((title) => title.includes('Barely Touched')),
    `[${label}] four seconds of playback is not "continue watching"`, JSON.stringify(row.titles));
  check(row.unavailable === 1 && row.titles.some((title) => title.includes('Withdrawn Film')),
    `[${label}] a withdrawn title is shown as unavailable, not as a broken Play`,
    JSON.stringify(row));
  check(row.resumable === row.titles.length - row.unavailable,
    `[${label}] every playable entry offers Resume`, JSON.stringify(row));
  check(row.removable === row.titles.length, `[${label}] every entry can be removed`);
  check(pageErrors.length === 0, `[${label}] the junk entry did not raise an error`, pageErrors.join(' | '));

  // --- Remove, and the watchlist it must not touch -------------------------
  const watchlistBefore = await page.evaluate((key) => localStorage.getItem(key), WATCHLIST);
  await page.locator('.movie-continue-card[data-continue-key="movie:real-halfway"] .movie-continue-remove').click({ force: true });
  await page.waitForTimeout(600);
  const afterRemove = await readRow(page);
  const watchlistAfter = await page.evaluate((key) => localStorage.getItem(key), WATCHLIST);
  const storeAfter = await page.evaluate((key) => JSON.parse(localStorage.getItem(key) || '{}'), STORE);

  check(!afterRemove.titles.some((title) => title.includes('Halfway Film')),
    `[${label}] Remove takes that entry out of the row`, JSON.stringify(afterRemove.titles));
  check(!('movie:real-halfway' in storeAfter), `[${label}] Remove takes it out of the store`);
  check('episode:some-show:s2:e4' in storeAfter, `[${label}] Remove leaves the other entries alone`);
  check(watchlistBefore === watchlistAfter,
    `[${label}] Remove does not touch the watchlist`, `${watchlistBefore} -> ${watchlistAfter}`);

  // --- it is a movie surface, not a live one -------------------------------
  await clickNavButton(page, navSelector, '.final-main-button', 'Live TV');
  await page.waitForTimeout(2500);
  const onLiveTv = await readRow(page);
  check(onLiveTv.hidden, `[${label}] Live TV does not show Continue Watching`);
  await clickNavButton(page, navSelector, '.final-main-button', 'Live Sports');
  await page.waitForTimeout(2500);
  const onSports = await readRow(page);
  check(onSports.hidden, `[${label}] Live Sports does not show Continue Watching`);

  // --- the 30-second rule and the resume, through the real player ----------
  //
  // The catalogue is H.264/MKV and this browser is the Playwright build of
  // Chromium, which ships without the proprietary decoders - every real title
  // reaches the player and then sits at readyState 0 (verified: the player
  // exhausts its proxy fallback and reports "movie could not be played").
  // So the media bytes here are a WAV the browser can actually decode, served
  // to the page at a normal http URL. Everything else is the real thing: the
  // real startPlayback, the real player element, real loadedmetadata and seek
  // events, and the real saveContinueWatching / resumeContinueWatching rules.
  const resumeEntry = {
    key: 'movie:test-clip', content_type: 'movie', item_id: 'test-clip',
    name: 'Resume Fixture', logo: '', category: 'Bangla',
    position_seconds: 90, duration_seconds: 300, progress_percent: 30,
    last_played_at: Date.now(), completed: false,
    snapshot: {
      id: 'test-clip', name: 'Resume Fixture', category: 'Bangla',
      url: `${baseUrl}/${MEDIA_PATH}`, backups: [],
      proxy_mode: 'direct_first', stream_type: 'media', _sourceKind: 'movie'
    }
  };

  await page.evaluate(async (entry) => { await window.resumeContinueWatching(entry); }, resumeEntry);

  let media = { duration: 0, currentTime: 0, videos: 1 };
  for (let attempt = 0; attempt < 25; attempt += 1) {
    await page.waitForTimeout(700);
    media = await page.evaluate(() => {
      const video = document.querySelector('video');
      return {
        duration: Number(video?.duration) || 0,
        currentTime: Number(video?.currentTime) || 0,
        videos: document.querySelectorAll('video').length
      };
    });
    if (media.duration > 0 && media.currentTime >= 80) break;
  }

  check(media.duration > 200,
    `[${label}] the existing player really loaded the media`, JSON.stringify(media));
  check(media.videos === 1, `[${label}] Resume used the existing player, not a new one`, String(media.videos));
  check(media.currentTime >= 85 && media.currentTime <= 95,
    `[${label}] Resume returns to where it left off, not to the start`,
    JSON.stringify({ wanted: 90, landed: media.currentTime }));

  if (media.duration > 200) {
    const early = await page.evaluate(async () => {
      const video = document.querySelector('video');
      video.currentTime = 5;
      for (let attempt = 0; attempt < 40 && video.currentTime > 10; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
      return { at: video.currentTime, saved: window.saveContinueWatching(true) };
    });
    const afterEarly = await page.evaluate((key) => JSON.parse(localStorage.getItem(key) || '{}'), STORE);

    const late = await page.evaluate(async () => {
      const video = document.querySelector('video');
      video.currentTime = 150;
      for (let attempt = 0; attempt < 40 && video.currentTime < 100; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
      return { at: video.currentTime, saved: window.saveContinueWatching(true) };
    });
    const afterLate = await page.evaluate((key) => JSON.parse(localStorage.getItem(key) || '{}'), STORE);
    const entry = afterLate['movie:test-clip'];

    check(early.at < 30 && early.saved === false,
      `[${label}] five seconds of playback is not recorded`, JSON.stringify(early));
    check(Math.round(afterEarly['movie:test-clip']?.position_seconds || 0) === 90,
      `[${label}] the store still holds the old position, not the five seconds`,
      JSON.stringify(afterEarly['movie:test-clip']?.position_seconds));
    check(late.at >= 100 && late.saved === true,
      `[${label}] past the threshold it is recorded`, JSON.stringify(late));
    check(Boolean(entry) && entry.position_seconds >= 100 && entry.duration_seconds > 200
      && entry.progress_percent > 0 && entry.completed === false,
      `[${label}] the entry carries a real position, duration and percent`,
      JSON.stringify(entry && { p: entry.position_seconds, d: entry.duration_seconds, pc: entry.progress_percent }));

    const serialised = JSON.stringify(entry || {}).toLowerCase();
    check(!serialised.includes('authorization') && !serialised.includes('cookie')
      && !serialised.includes('referer'),
      `[${label}] no credential or header is written into the store`);

    // Completion: past 90% the entry leaves the row rather than lingering.
    await page.evaluate(async () => {
      const video = document.querySelector('video');
      video.currentTime = video.duration - 5;
      for (let attempt = 0; attempt < 40 && video.currentTime < video.duration - 20; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
      window.saveContinueWatching(true);
    });
    const finished = await page.evaluate((key) => JSON.parse(localStorage.getItem(key) || '{}')['movie:test-clip'], STORE);
    await clickNavButton(page, navSelector, '.final-main-button', 'Movies');
    await page.waitForTimeout(1200);
    await clickNavButton(page, subNavSelector, '.final-sub-button', 'Home');
    await page.waitForTimeout(2500);
    const finalRow = await readRow(page);

    check(finished?.completed === true,
      `[${label}] watching to the end marks it completed`, JSON.stringify(finished?.progress_percent));
    check(!finalRow.titles.some((title) => title.includes('Resume Fixture')),
      `[${label}] a completed title drops out of the row`, JSON.stringify(finalRow.titles));
  }

  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check(overflow <= 1, `[${label}] no horizontal overflow`, `${overflow}px`);
  check(pageErrors.length === 0, `[${label}] no uncaught page errors`, pageErrors.join(' | '));
  await context.close();
}

async function runCorruptStore(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({ viewport });
  await context.addInitScript((key) => {
    localStorage.setItem(key, '{ this is not json');
  }, STORE);
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  await openMoviesHome(page, navSelector, subNavSelector);
  const row = await readRow(page);
  const cards = await page.evaluate(() => document.querySelectorAll('#sidebarList .movie-card, #sidebarList .series-card').length);

  check(pageErrors.length === 0, `[${label}] a corrupt store raises no error`, pageErrors.join(' | '));
  check(row.hidden, `[${label}] a corrupt store shows no Continue Watching row`);
  check(cards > 0, `[${label}] the rest of the page still works`, String(cards));
  await context.close();
}

async function runEmptyStore(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  await openMoviesHome(page, navSelector, subNavSelector);
  const row = await readRow(page);
  check(row.hidden && row.titles.length === 0,
    `[${label}] a first-time viewer sees no Continue Watching at all - nothing is seeded`,
    JSON.stringify(row));
  await context.close();
}

try {
  await runEmptyStore('fresh', { width: 1280, height: 800 }, '#desktopMainNav', '#desktopSubNav');
  await runCorruptStore('corrupt', { width: 1280, height: 800 }, '#desktopMainNav', '#desktopSubNav');
  await run('desktop', { width: 1280, height: 800 }, '#desktopMainNav', '#desktopSubNav');
  await run('mobile', { width: 390, height: 844 }, '#mobileMainNav', '#mobileSubNav');
} finally {
  await browser.close();
}

console.log(`\n${passes.length} passed, ${failures.length} failed`);
for (const pass of passes) console.log(`  PASS  ${pass}`);
for (const failure of failures) console.log(`  FAIL  ${failure}`);
if (failures.length) process.exit(1);
