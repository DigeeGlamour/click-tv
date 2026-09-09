import { chromium } from 'playwright';
import { spawn } from 'node:child_process';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const localServer = process.argv[2] ? null : spawn(
  'python', ['-m', 'http.server', '4173', '--directory', 'dist'],
  { cwd: new URL('..', import.meta.url), stdio: 'ignore', windowsHide: true }
);
if (localServer) await new Promise((resolve) => setTimeout(resolve, 900));
const browser = await chromium.launch({ headless: true, args: ['--disable-web-security'] });

async function check(label, viewport, mobile = false) {
  const context = await browser.newContext({ viewport, hasTouch: mobile, serviceWorkers: 'block' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.locator('#sidebarList').waitFor({ state: 'visible', timeout: 20000 });
  await page.waitForTimeout(900);

  await page.locator(`${mobile ? '#mobileMainNav' : '#desktopMainNav'} [data-final-key="sports"]`).click();
  const upcoming = page.locator(`${mobile ? '#mobileSubNav' : '#desktopSubNav'} [data-final-key="upcoming"]`);
  await upcoming.click();
  await page.locator('.event-ref-card').first().waitFor({ state: 'visible', timeout: 20000 });
  const geometry = await page.locator('.event-ref-card').first().evaluate((card) => {
    const art = card.querySelector('.event-card-art')?.getBoundingClientRect();
    const rect = card.getBoundingClientRect();
    const title = card.querySelector('.event-card-title');
    const competition = card.querySelector('.event-card-competition');
    const time = card.querySelector('.event-card-time');
    const fontSize = (element) => element ? Number.parseFloat(getComputedStyle(element).fontSize) : 0;
    return {
      width: rect.width,
      height: rect.height,
      artWidth: art?.width || 0,
      artHeight: art?.height || 0,
      hasTime: Boolean(card.querySelector('.event-card-time')),
      hasStatus: Boolean(card.querySelector('.event-status-pill')),
      timeText: card.querySelector('.event-card-time')?.textContent?.trim(),
      statusText: card.querySelector('.event-status-pill')?.textContent?.trim(),
      action: card.querySelector('.event-card-action')?.textContent?.trim(),
      overflow: card.scrollWidth - card.clientWidth,
      titleFontSize: fontSize(title),
      competitionFontSize: fontSize(competition),
      timeFontSize: fontSize(time),
      titleClipped: Boolean(title && title.scrollHeight > title.clientHeight + 1),
      timeClipped: Boolean(time && time.scrollWidth > time.clientWidth + 1),
    };
  });
  if (!geometry.hasTime || !geometry.hasStatus || geometry.overflow > 1 || geometry.artWidth < 80 || geometry.artHeight < 55) {
    throw new Error(`${label} upcoming card mismatch: ${JSON.stringify(geometry)}`);
  }
  if (/\b(?:GMT|BST|UTC)\b/i.test(geometry.timeText || '') || !/BDT|pending/i.test(geometry.timeText || '')) {
    throw new Error(`${label} event time is not Bangladesh-friendly: ${JSON.stringify(geometry)}`);
  }
  if (label === 'desktop' && (
    geometry.titleFontSize < 14
    || geometry.competitionFontSize < 10
    || geometry.timeFontSize < 9
    || geometry.timeClipped
  )) {
    throw new Error(`${label} event text is too small or clipped: ${JSON.stringify(geometry)}`);
  }

  const before = await page.locator('#metaTitle').textContent();
  await page.locator('.event-ref-card').first().click();
  await page.locator('#eventPreviewOverlay.show').waitFor({ state: 'visible' });
  const preview = await page.evaluate(() => ({
    title: document.querySelector('#eventPreviewTitle')?.textContent?.trim(),
    time: document.querySelector('#eventPreviewTime')?.textContent?.trim(),
    hidden: document.querySelector('#eventPreviewOverlay')?.getAttribute('aria-hidden'),
    meta: document.querySelector('#metaTitle')?.textContent,
  }));
  if (!preview.title || !preview.time || preview.hidden !== 'false' || preview.meta !== before) {
    throw new Error(`${label} metadata-only preview incorrectly changed playback: ${JSON.stringify(preview)}`);
  }
  await page.locator('#eventPreviewClose').click();
  if (await page.locator('#eventPreviewOverlay').getAttribute('aria-hidden') !== 'true') throw new Error(`${label} preview did not close`);

  if (errors.length) throw new Error(`${label} runtime errors: ${errors.join(' | ')}`);
  await context.close();
}

async function checkEndedEventIsHidden() {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    serviceWorkers: 'block',
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/data/today-match.json*', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        type: 'today_match',
        count: 1,
        items: [{
          id: 'browser-ended-event',
          name: 'Browser Ended Event Must Be Hidden',
          category: 'today_match',
          source_pipeline: 'today_match',
          status: 'ENDED',
          schedule_status: 'ENDED',
          start_time: '2026-08-12T01:00:00+00:00',
          end_time: '2026-08-12T02:00:00+00:00',
          url: 'https://example.invalid/ended.m3u8',
          publish_allowed: true,
        }],
      }),
    });
  });
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.locator('#sidebarList').waitFor({ state: 'visible', timeout: 20000 });
  await page.locator('#desktopMainNav [data-final-key="sports"]').click();
  await page.locator('#desktopSubNav [data-final-key="today-match"]').click();
  await page.waitForTimeout(500);
  const endedCards = page.locator('.event-ref-card', {
    hasText: 'Browser Ended Event Must Be Hidden',
  });
  if (await endedCards.count()) {
    const details = await endedCards.first().evaluate((card) => ({
      text: card.textContent,
      uid: card.dataset.uid,
      html: card.outerHTML.slice(0, 1200),
      now: new Date().toISOString(),
    }));
    details.runtime = await page.evaluate(() => ({
      view: state.view,
      current: state.currentItems.map((item) => ({
        name: item.name,
        status: item.status,
        schedule_status: item.schedule_status,
        end_time: item.end_time,
        ended: isEventEnded(item),
      })),
      filtered: state.filteredItems.map((item) => item.name),
    }));
    throw new Error(`ENDED event remained visible in the Today Match list: ${JSON.stringify(details)}`);
  }
  if (errors.length) throw new Error(`ended-event runtime errors: ${errors.join(' | ')}`);
  await context.close();
}

async function checkPlayableTodayActionSaysWatch() {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    serviceWorkers: 'block',
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/data/today-match.json*', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        type: 'today_match',
        count: 1,
        items: [{
          id: 'browser-playable-starting-soon',
          name: 'Playable Today Match',
          category: 'today_match',
          source_pipeline: 'today_match',
          status: 'STARTING_SOON',
          schedule_status: 'STARTING_SOON',
          start_time: '2099-08-12T01:00:00+00:00',
          end_time: '2099-08-12T05:00:00+00:00',
          url: 'https://example.invalid/live.m3u8',
          verified: true,
          publish_allowed: true,
        }],
      }),
    });
  });
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.locator('#sidebarList').waitFor({ state: 'visible', timeout: 20000 });
  await page.locator('#desktopMainNav [data-final-key="sports"]').click();
  await page.locator('#desktopSubNav [data-final-key="today-match"]').click();
  const card = page.locator('.event-ref-card', { hasText: 'Playable Today Match' });
  await card.waitFor({ state: 'visible', timeout: 20000 });
  const action = await card.locator('.event-card-action').textContent();
  if (action?.trim() !== 'Watch') {
    throw new Error(`playable Today Match action must be Watch, received: ${action}`);
  }
  if (errors.length) throw new Error(`Today action runtime errors: ${errors.join(' | ')}`);
  await context.close();
}

async function checkVerifiedMultiDayEventStaysLive() {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    serviceWorkers: 'block',
  });
  const page = await context.newPage();
  await page.route('**/data/today-match.json*', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        type: 'today_match',
        count: 1,
        items: [{
          id: 'browser-verified-multiday-live',
          name: 'Verified Multi-day Test Match',
          category: 'today_match',
          source_pipeline: 'today_match',
          status: 'LIVE_NOW',
          schedule_status: 'LIVE_NOW',
          schedule_verified: true,
          start_time: '2020-01-01T00:00:00+00:00',
          end_time: '2099-01-01T00:00:00+00:00',
          url: 'https://example.invalid/live.m3u8',
          verified: true,
          publish_allowed: true,
        }],
      }),
    });
  });
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.locator('#sidebarList').waitFor({ state: 'visible', timeout: 20000 });
  await page.locator('#desktopMainNav [data-final-key="sports"]').click();
  await page.locator('#desktopSubNav [data-final-key="today-match"]').click();
  const card = page.locator('.event-ref-card', { hasText: 'Verified Multi-day Test Match' });
  await card.waitFor({ state: 'visible', timeout: 20000 });
  const badge = (await card.locator('.event-status-pill').textContent())?.trim();
  if (badge !== 'LIVE NOW') {
    throw new Error(`verified multi-day Today event was downgraded to ${badge}`);
  }
  await context.close();
}

try {
  await check('desktop', { width: 1440, height: 900 });
  await check('tablet', { width: 1024, height: 768 });
  await check('mobile', { width: 390, height: 844 }, true);
  await check('landscape', { width: 844, height: 390 }, true);
  await checkEndedEventIsHidden();
  await checkPlayableTodayActionSaysWatch();
  await checkVerifiedMultiDayEventStaysLive();
  console.log('Event cards PASS: responsive upcoming design, schedule metadata, non-playback preview and close behavior');
} finally {
  await browser.close();
  localServer?.kill();
}
