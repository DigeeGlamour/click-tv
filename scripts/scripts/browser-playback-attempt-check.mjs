import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const browser = await chromium.launch({ headless: true, args: ['--autoplay-policy=no-user-gesture-required'] });

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function auditVisibleItems(page, label) {
  await page.waitForFunction(() => window.__clickTvRuntimeTest?.currentItemsPlaybackAudit, null, { timeout: 30000 });
  const result = await page.evaluate(() => window.__clickTvRuntimeTest.currentItemsPlaybackAudit());
  assert(result.length > 0, `${label}: no items loaded`);
  const invalid = result.filter((item) => !item.navigatesToSeries && (!item.playable || item.attemptCount < 1));
  assert(!invalid.length, `${label}: instant-failure items found: ${JSON.stringify(invalid.slice(0, 8))}`);
  return result.length;
}

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.locator('#desktopMainNav .final-main-button').first().waitFor({ state: 'visible', timeout: 30000 });

  const totals = {};
  await page.locator('#desktopMainNav [data-final-key="live-tv"]').click();
  for (const key of ['bangla', 'indian', 'cartoon', 'islamic', 'infotainments', 'foreign-news', 'others']) {
    await page.locator(`#desktopSubNav [data-final-key="${key}"]`).click();
    await page.waitForTimeout(250);
    totals[key] = await auditVisibleItems(page, `Live TV/${key}`);
  }

  await page.locator('#desktopMainNav [data-final-key="sports"]').click();
  await page.locator('#desktopSubNav [data-final-key="sports-channel"]').click();
  await page.waitForTimeout(250);
  totals.sports = await auditVisibleItems(page, 'Live Sports/Sports');

  await page.locator('#desktopMainNav [data-final-key="movies"]').click();
  for (const key of ['bangla', 'hindi', 'english', 'dubbed', 'south-indian', 'premium', 'mix']) {
    const tab = page.locator(`#desktopSubNav [data-final-key="movie:${key}"]`);
    if (!(await tab.count())) continue;
    await tab.click();
    await page.waitForTimeout(350);
    totals[`movie:${key}`] = await auditVisibleItems(page, `Movies/${key}`);
  }

  await page.locator('#desktopMainNav [data-final-key="live-tv"]').click();
  await page.locator('#desktopSubNav [data-final-key="bangla"]').click();
  const channelCard = page.locator('#sidebarList .sidebar-item').first();
  await channelCard.click();
  await page.waitForTimeout(500);
  const channelSession = await page.evaluate(() => window.__clickTvRuntimeTest.playbackSessionSnapshot());
  assert(channelSession?.planLength > 0 && channelSession.attemptsRun > 0,
    `Channel click did not start a real attempt: ${JSON.stringify(channelSession)}`);

  await page.locator('#desktopMainNav [data-final-key="movies"]').click();
  await page.locator('#desktopSubNav [data-final-key="movie:bangla"]').click();
  await page.waitForFunction(() => window.__clickTvRuntimeTest.currentItemsPlaybackAudit()
    .some((item) => item.playable && item.attemptCount > 0), null, { timeout: 30000 });
  const playableMovieUid = await page.evaluate(() => {
    const items = window.__clickTvRuntimeTest.currentItemsPlaybackAudit();
    const index = items.findIndex((item) => item.playable && item.attemptCount > 0);
    return index;
  });
  assert(playableMovieUid >= 0, 'Bangla movie list has no playable movie');
  await page.locator('#sidebarList .movie-card').nth(playableMovieUid).click();
  await page.waitForTimeout(500);
  const movieSession = await page.evaluate(() => window.__clickTvRuntimeTest.playbackSessionSnapshot());
  assert(movieSession?.planLength > 0 && movieSession.attemptsRun > 0,
    `Movie click did not start a real attempt: ${JSON.stringify(movieSession)}`);

  assert(!pageErrors.length, `Browser errors: ${pageErrors.join(' | ')}`);
  console.log(`Playback-attempt PASS: ${JSON.stringify(totals)}; channel=${JSON.stringify(channelSession)}; movie=${JSON.stringify(movieSession)}`);
} finally {
  await browser.close();
}
