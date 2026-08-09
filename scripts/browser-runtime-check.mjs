import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const browser = await chromium.launch({ headless: true });

async function currentEnglishMovieProbe() {
  const indexResponse = await fetch(new URL('/data/movies/english/index.json', baseUrl));
  if (!indexResponse.ok) throw new Error(`English movie index HTTP ${indexResponse.status}`);
  const index = await indexResponse.json();
  const pages = Array.isArray(index?.pages) ? index.pages : [];
  const pagePath = pages.at(-1)?.path;
  if (!pagePath) throw new Error('English movie index has no page path');
  const pageResponse = await fetch(new URL(`/${String(pagePath).replace(/^\/+/, '')}`, baseUrl));
  if (!pageResponse.ok) throw new Error(`English movie page HTTP ${pageResponse.status}`);
  const page = await pageResponse.json();
  const items = Array.isArray(page?.items) ? page.items : [];
  const probe = [...items].reverse().find((item) => String(item?.name || '').trim());
  if (!probe) throw new Error('English movie page has no searchable title');
  return String(probe.name).trim();
}

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
  const movieProbe = await currentEnglishMovieProbe();
  const desktop = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await openCheckedPage(desktop, 'desktop');

  const failoverResult = await page.evaluate(() => {
    const hooks = window.__clickTvRuntimeTest;
    if (!hooks) throw new Error('Local runtime test hooks are unavailable');
    hooks.resetProxyHealth();
    const targetUrl = 'https://media.example/live/master.m3u8';
    const item = {
      name: 'Proxy failover test',
      _sources: [{
        url: targetUrl,
        stream_type: 'hls',
        proxy_mode: 'proxy_only',
        resolution_height: 1080,
      }],
    };
    const before = hooks.buildAttempts(item).filter((attempt) => attempt.route === 'proxy');
    if (before.length < 2) throw new Error(`Expected at least two proxy attempts, received ${before.length}`);
    hooks.markProxyFailure(before[0].proxy, targetUrl);
    const after = hooks.buildAttempts(item).filter((attempt) => attempt.route === 'proxy');
    return { before, after, failedProxy: before[0].proxy };
  });
  if (!failoverResult.after.length || failoverResult.after.some((attempt) => attempt.proxy === failoverResult.failedProxy)) {
    throw new Error('Forced proxy failure did not move playback planning to healthy fallback proxies');
  }

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
  await page.locator('#searchInput').fill(movieProbe);
  await page.waitForFunction((expectedTitle) => {
    const list = document.querySelector('#sidebarList');
    return list && (list.textContent || '').toLocaleLowerCase().includes(expectedTitle.toLocaleLowerCase());
  }, movieProbe, { timeout: 30000 });
  const searchResult = await page.locator('#sidebarList').textContent();
  if (!String(searchResult || '').toLocaleLowerCase().includes(movieProbe.toLocaleLowerCase())) {
    throw new Error(`Full English movie search missed current title: ${movieProbe}`);
  }

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

  console.log('Browser runtime PASS: desktop UI, full movie search, live playback, forced proxy failover, PWA, mobile UI');
  await desktop.close();
  await mobile.close();
} finally {
  await browser.close();
}
