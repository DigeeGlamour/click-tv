import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const browser = await chromium.launch({ headless: true });

async function openCheckedPage(context, label) {
  const page = await context.newPage();
  const runtimeErrors = [];
  page.on('pageerror', (error) => runtimeErrors.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    const localWorkerCors = baseUrl.startsWith('http://127.0.0.1') && /workers\.dev.*CORS policy/i.test(message.text());
    if (message.type() === 'error' && !localWorkerCors && !/Failed to load resource|ERR_FAILED|ERR_BLOCKED_BY_ORB/i.test(message.text())) {
      runtimeErrors.push(`console: ${message.text()}`);
    }
  });
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.locator('#sidebarList').waitFor({ state: 'visible', timeout: 20000 });
  await page.waitForTimeout(1200);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (overflow > 1) throw new Error(`${label} horizontal overflow: ${overflow}px`);
  if (runtimeErrors.length) throw new Error(`${label} runtime errors: ${runtimeErrors.join(' | ')}`);
  return page;
}

try {
  const desktop = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await openCheckedPage(desktop, 'desktop');

  const serviceWorkerReady = await page.evaluate(async () => {
    if (!('serviceWorker' in navigator)) return false;
    const registration = await Promise.race([
      navigator.serviceWorker.ready,
      new Promise((resolve) => setTimeout(() => resolve(null), 7000)),
    ]);
    return Boolean(registration?.active);
  });
  if (!serviceWorkerReady) throw new Error('Service Worker did not become active');

  await page.locator('#desktopMainNav [data-final-key="movies"]').click();
  await page.locator('#desktopSubNav [data-final-key="movie:english"]').click();
  await page.locator('#searchInput').fill('Corpsing');
  await page.waitForFunction(() => {
    const list = document.querySelector('#sidebarList');
    return list && /Corpsing/i.test(list.textContent || '');
  }, null, { timeout: 30000 });
  const searchResult = await page.locator('#sidebarList').textContent();
  if (!/Corpsing/i.test(searchResult || '')) throw new Error('Full English movie search missed Corpsing');

  await page.locator('#searchInput').fill('');
  await page.waitForTimeout(350);
  await page.locator('#desktopMainNav [data-final-key="live-tv"]').click();
  await page.locator('#desktopSubNav [data-final-key="bangla"]').click();
  await page.locator('#searchInput').fill('10 TV');
  await page.waitForTimeout(350);
  const tenTv = page.locator('.sidebar-item', { hasText: '10 TV' }).first();
  try {
    await tenTv.waitFor({ state: 'visible', timeout: 20000 });
  } catch (error) {
    const diagnostic = await page.locator('#sidebarList').textContent();
    throw new Error(`10 TV not rendered after Bangla selection: ${String(diagnostic || '').slice(0, 500)}`, { cause: error });
  }
  await tenTv.click();
  await page.waitForFunction(() => {
    const video = document.querySelector('#videoPlayer');
    return video && video.readyState >= 2;
  }, null, { timeout: 35000 });

  const mobile = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  const mobilePage = await openCheckedPage(mobile, 'mobile');
  const searchToggleVisible = await mobilePage.locator('#mobileSearchToggleBtn').isVisible();
  if (!searchToggleVisible) throw new Error('Mobile search toggle is not visible');
  await mobilePage.locator('#mobileSearchToggleBtn').click();
  await mobilePage.locator('#mobileSearchInput').fill('10 TV');
  if (!(await mobilePage.locator('#mobileSearchInput').isVisible())) {
    throw new Error('Mobile search field did not open');
  }

  console.log('Browser runtime PASS: desktop UI, full movie search, live playback, PWA, mobile UI');
  await desktop.close();
  await mobile.close();
} finally {
  await browser.close();
}
