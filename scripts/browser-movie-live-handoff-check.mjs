/**
 * Going to Movies stops a live stream. Going around Movies does not stop a movie.
 *
 * A channel or an event keeps decoding when the header switches to Movies,
 * and on desktop the movie portal hides the player column entirely - so the
 * sound and the bandwidth carry on with no control anywhere on screen to
 * stop them. This was real: production showed the Movies tab active, the NOW
 * PLAYING strip reading a live fixture, and live segments still being
 * fetched.
 *
 * The other half matters just as much. A movie must keep playing while the
 * viewer moves around inside Movies, or the portal would be unusable.
 *
 * Usage: node scripts/browser-movie-live-handoff-check.mjs [baseUrl]
 */
import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const failures = [];
const passes = [];

function check(condition, message, detail = '') {
  if (condition) passes.push(message);
  else failures.push(`${message}${detail ? ` — ${detail}` : ''}`);
}

const PLAYER_STATE = `(() => {
  const video = document.querySelector('video');
  return {
    // The src ATTRIBUTE, not currentSrc. Chromium keeps reporting the last
    // currentSrc after a teardown even with nothing loaded - measured:
    // readyState 4 -> 0, networkState 2 -> 0, src attribute blob -> null,
    // buffered 0, and currentSrc still holding the old blob string. The
    // element is what has to be empty, not that leftover label.
    srcAttr: video?.getAttribute('src') || '',
    readyState: video ? video.readyState : null,
    networkState: video ? video.networkState : null,
    buffered: video ? video.buffered.length : null,
    paused: video ? video.paused : null,
    videos: document.querySelectorAll('video').length,
    nowPlaying: document.getElementById('metaTitle')?.textContent?.trim() || '',
    playbackActive: document.body.classList.contains('playback-active'),
    movieContext: document.documentElement.classList.contains('movie-playback-context'),
  };
})()`;

const browser = await chromium.launch();

async function openGroup(page, label) {
  const nav = page.locator('.final-main-button:visible', { hasText: label }).first();
  await nav.waitFor({ state: 'visible', timeout: 30000 });
  await nav.click();
}

for (const viewport of [
  { label: 'desktop', width: 1440, height: 900 },
  { label: 'mobile', width: 390, height: 844 },
]) {
  const label = viewport.label;
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    isMobile: viewport.width < 800,
    hasTouch: viewport.width < 800,
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));

  // Any media the page fetches AFTER we start watching, so a stream that
  // carries on is visible as traffic rather than inferred from the DOM.
  let mediaAfterSwitch = [];
  page.on('request', (request) => {
    const url = request.url();
    if (/\.m3u8|\.ts(\?|$)|\/hls\?url=|\.mp4|\.mkv|r2\.dev/i.test(url)) {
      mediaAfterSwitch.push(url.slice(0, 70));
    }
  });

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
  // The landing view starts a live channel by itself; give it time to attach.
  await page.waitForTimeout(9000);

  const live = await page.evaluate(PLAYER_STATE);
  check(live.videos === 1, `[${label}] exactly one player element on the live view`,
    String(live.videos));

  if (!live.nowPlaying) {
    // Nothing is playing, so there is nothing to hand off. Say so rather than
    // passing a test that never ran.
    check(false, `[${label}] a live stream is playing before the handoff`,
      'nothing was playing; the rest of this check could not run');
  } else {
    // --- Movies must stop it ------------------------------------------------
    mediaAfterSwitch = [];
    await openGroup(page, 'Movies');
    await page.waitForTimeout(6000);
    const settled = mediaAfterSwitch.length;
    await page.waitForTimeout(5000);

    const after = await page.evaluate(PLAYER_STATE);
    check(after.paused !== false, `[${label}] the live stream is not still playing`,
      JSON.stringify({ paused: after.paused, src: after.srcAttr }));
    check(!after.srcAttr, `[${label}] the player has let go of the live source`,
      after.srcAttr);
    check(after.readyState === 0 && after.networkState === 0 && after.buffered === 0,
      `[${label}] the media element holds no data and no connection`,
      JSON.stringify({ readyState: after.readyState, networkState: after.networkState, buffered: after.buffered }));
    check(!after.nowPlaying, `[${label}] NOW PLAYING no longer names the live stream`,
      after.nowPlaying);
    check(!after.playbackActive,
      `[${label}] the page no longer says a stream is decoding`);
    check(mediaAfterSwitch.length === settled,
      `[${label}] no further live segments are fetched once Movies is open`,
      `${mediaAfterSwitch.length - settled} more request(s): ${mediaAfterSwitch.slice(settled, settled + 2).join(' | ')}`);
    check(after.videos === 1, `[${label}] still exactly one player element`,
      String(after.videos));
  }

  // --- a movie must NOT be stopped by moving around Movies -----------------
  const card = page.locator('.movie-card:visible').first();
  if (await card.count()) {
    await card.click();
    await page.waitForTimeout(2500);
    const play = page.locator('.btn-play-white:visible, .movie-detail-play:visible').first();
    if (await play.count()) {
      await play.click();
      await page.waitForTimeout(9000);
      const playing = await page.evaluate(PLAYER_STATE);
      if (playing.nowPlaying) {
        // Navigate inside Movies - the rail's Home entry - and the movie stays.
        const home = page.locator('.final-sub-button:visible', { hasText: 'Home' }).first();
        if (await home.count()) {
          await home.click();
          await page.waitForTimeout(4000);
          const stillThere = await page.evaluate(PLAYER_STATE);
          check(stillThere.videos === 1,
            `[${label}] moving around Movies creates no second player`,
            String(stillThere.videos));
          check(stillThere.nowPlaying === playing.nowPlaying || !stillThere.nowPlaying,
            `[${label}] moving around Movies does not swap the movie for something else`,
            JSON.stringify({ was: playing.nowPlaying, now: stillThere.nowPlaying }));
        }
      }
    }
  }

  check(errors.length === 0, `[${label}] no uncaught page errors`,
    errors.slice(0, 2).join(' | '));
  await context.close();
}

await browser.close();

for (const message of passes) console.log(`  PASS  ${message}`);
for (const message of failures) console.log(`  FAIL  ${message}`);
console.log(`\n${passes.length} passed, ${failures.length} failed`);
process.exit(failures.length ? 1 : 0);
