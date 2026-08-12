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
    };
  });
  if (!geometry.hasTime || !geometry.hasStatus || geometry.overflow > 1 || geometry.artWidth < 80 || geometry.artHeight < 55) {
    throw new Error(`${label} upcoming card mismatch: ${JSON.stringify(geometry)}`);
  }
  if (/\b(?:GMT|BST|UTC)\b/i.test(geometry.timeText || '') || !/BDT|pending/i.test(geometry.timeText || '')) {
    throw new Error(`${label} event time is not Bangladesh-friendly: ${JSON.stringify(geometry)}`);
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

try {
  await check('desktop', { width: 1440, height: 900 });
  await check('tablet', { width: 1024, height: 768 });
  await check('mobile', { width: 390, height: 844 }, true);
  await check('landscape', { width: 844, height: 390 }, true);
  console.log('Event cards PASS: responsive upcoming design, schedule metadata, non-playback preview and close behavior');
} finally {
  await browser.close();
  localServer?.kill();
}
