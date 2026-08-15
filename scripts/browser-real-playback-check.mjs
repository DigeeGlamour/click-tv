import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const channelLimit = Number(process.argv[3] || 12);
const movieLimit = Number(process.argv[4] || 6);
const playbackTimeout = Number(process.argv[5] || 12000);
const browser = await chromium.launch({
  headless: true,
  args: ['--autoplay-policy=no-user-gesture-required', '--disable-web-security'],
});

async function waitForRealMedia(page, title, timeout = 12000) {
  const startedAt = await page.evaluate(() => Number(document.getElementById('videoPlayer')?.currentTime || 0));
  try {
    await page.waitForFunction((initial) => {
      const video = document.getElementById('videoPlayer');
      return Boolean(video && video.readyState >= 2 && video.videoWidth > 0 && video.currentTime > initial + 0.25);
    }, startedAt, { timeout });
    return { title, ok: true, ...(await page.evaluate(() => {
      const video = document.getElementById('videoPlayer');
      return { readyState: video.readyState, currentTime: video.currentTime, width: video.videoWidth, height: video.videoHeight };
    })) };
  } catch (_) {
    return { title, ok: false, ...(await page.evaluate(() => {
      const video = document.getElementById('videoPlayer');
      const error = document.getElementById('videoError');
      return {
        readyState: video?.readyState || 0,
        currentTime: video?.currentTime || 0,
        mediaError: video?.error?.message || '',
        playerError: error?.textContent?.replace(/\s+/g, ' ').trim() || '',
        session: window.__clickTvRuntimeTest?.playbackSessionSnapshot?.() || null,
      };
    })) };
  }
}

async function auditCards(page, selector, limit) {
  const count = Math.min(await page.locator(selector).count(), limit);
  const results = [];
  for (let index = 0; index < count; index += 1) {
    const card = page.locator(selector).nth(index);
    const title = (await card.textContent()).replace(/\s+/g, ' ').trim();
    await card.click();
    results.push(await waitForRealMedia(page, title, playbackTimeout));
  }
  return results;
}

try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    extraHTTPHeaders: { Origin: 'https://clicktv.pages.dev' },
  });
  const page = await context.newPage();
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.locator('#desktopMainNav .final-main-button').first().waitFor({ state: 'visible', timeout: 30000 });

  await page.locator('#desktopMainNav [data-final-key="live-tv"]').click();
  await page.locator('#desktopSubNav [data-final-key="bangla"]').click();
  await page.locator('#sidebarList .channel-ref-card').first().waitFor({ state: 'visible', timeout: 30000 });
  const channels = await auditCards(page, '#sidebarList .channel-ref-card', channelLimit);

  await page.locator('#desktopMainNav [data-final-key="movies"]').click();
  await page.locator('#desktopSubNav [data-final-key="movie:bangla"]').click();
  await page.locator('#sidebarList .movie-card').first().waitFor({ state: 'visible', timeout: 30000 });
  const movies = await auditCards(page, '#sidebarList .movie-card', movieLimit);

  const failed = [...channels, ...movies].filter((result) => !result.ok);
  console.log(JSON.stringify({ channels, movies, failed: failed.length }, null, 2));
  if (failed.length) process.exitCode = 1;
} finally {
  await browser.close();
}
