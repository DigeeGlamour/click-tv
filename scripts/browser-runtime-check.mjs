import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const browser = await chromium.launch({
  headless: true,
  args: ['--autoplay-policy=no-user-gesture-required', '--disable-web-security'],
});

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
  const desktop = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    extraHTTPHeaders: { Origin: 'https://clicktv.pages.dev' },
  });
  const page = await openCheckedPage(desktop, 'desktop');

  const desktopGeometry = await page.evaluate(() => {
    const rect = (selector) => document.querySelector(selector)?.getBoundingClientRect();
    const notice = document.querySelector('#sticky-header-notice');
    const noticeStyle = getComputedStyle(notice);
    return {
      noticeHeight: rect('#sticky-header-notice')?.height || 0,
      noticeFontSize: Number.parseFloat(noticeStyle.fontSize || '0'),
      headerHeight: rect('.app-header')?.height || 0,
      headerBorderBottom: Number.parseFloat(getComputedStyle(document.querySelector('.app-header')).borderBottomWidth || '0'),
      nowPlayingHeight: rect('.video-meta')?.height || 0,
      leftRailWidth: rect('.desktop-category-rail')?.width || 0,
      rightPanelWidth: rect('.sidebar-section.side-panel')?.width || 0,
      subtitleDisplay: getComputedStyle(document.querySelector('.video-meta .meta-subtitle-row')).display,
    };
  });
  if (Math.abs(desktopGeometry.noticeHeight - 22) > 1 || desktopGeometry.noticeFontSize < 10) {
    throw new Error(`Desktop notice geometry differs from the demo: ${JSON.stringify(desktopGeometry)}`);
  }
  if (Math.abs(desktopGeometry.headerHeight - 64) > 1 || desktopGeometry.headerBorderBottom > 0) {
    throw new Error(`Desktop header geometry mismatch: ${JSON.stringify(desktopGeometry)}`);
  }
  if (Math.abs(desktopGeometry.nowPlayingHeight - 72) > 2 || desktopGeometry.subtitleDisplay !== 'none') {
    throw new Error(`Now Playing row is not compact: ${JSON.stringify(desktopGeometry)}`);
  }
  if (Math.abs(desktopGeometry.leftRailWidth - 215) > 2 || desktopGeometry.rightPanelWidth < 330 || desktopGeometry.rightPanelWidth > 460) {
    throw new Error(`Desktop side columns differ from the demo: ${JSON.stringify(desktopGeometry)}`);
  }

  await page.locator('#noticeCloseBtn').click();
  await page.waitForFunction(() => document.querySelector('#sticky-header-notice')?.hidden === true);

  await page.locator('#searchBtnSubmit').click();
  await page.waitForTimeout(500);
  const desktopSearchState = await page.evaluate(() => {
    const input = document.querySelector('#searchInput');
    const wrap = input?.closest('.search-wrap');
    return {
      visible: Boolean(input && getComputedStyle(input).opacity !== '0' && input.getBoundingClientRect().width > 80),
      open: Boolean(wrap?.classList.contains('search-open')),
      focused: document.activeElement === input,
    };
  });
  if (!desktopSearchState.visible || !desktopSearchState.open || !desktopSearchState.focused) {
    throw new Error(`Desktop search did not stay open: ${JSON.stringify(desktopSearchState)}`);
  }

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

  const protectedPlaybackResult = await page.evaluate(async () => {
    const hooks = window.__clickTvRuntimeTest;
    const originalFetch = window.fetch;
    let drmRequests = 0;
    window.fetch = async (input, init) => {
      if (String(input).includes('/drm?id=ctv_test_protected')) {
        drmRequests += 1;
        return new Response(JSON.stringify({ drm: { type: 'clearkey', clear_keys: { kid: 'key' } } }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return originalFetch(input, init);
    };
    try {
      const drm = await hooks.resolveProtectedDrm('ctv_test_protected', 'https://proxy.example');
      const groups = hooks.movieQualityGroups({
        id: 'protected-4k-test',
        _sources: [{
          playback_id: 'ctv_test_4k',
          resolution_height: 2160,
          stream_type: 'dash',
          proxy_mode: 'proxy_only',
          protected_source: true,
        }],
      });
      return { drmRequests, drmType: drm?.type || '', groups };
    } finally {
      window.fetch = originalFetch;
    }
  });
  const protected4KSource = protectedPlaybackResult.groups?.[0]?.sources?.[0];
  if (protectedPlaybackResult.drmRequests !== 1 || protectedPlaybackResult.drmType !== 'clearkey') {
    throw new Error(`Protected DRM bootstrap mismatch: ${JSON.stringify(protectedPlaybackResult)}`);
  }
  if (!protected4KSource || protected4KSource.playback_id !== 'ctv_test_4k' || protected4KSource.proxy_mode !== 'proxy_only') {
    throw new Error(`Protected 4K source contract mismatch: ${JSON.stringify(protectedPlaybackResult)}`);
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
  await page.locator('#desktopSubNav [data-final-key="indian"]').click();
  await page.locator('#searchInput').fill('10 TV');
  await page.waitForTimeout(350);
  const tenTv = page.locator('.sidebar-item', { hasText: '10 TV' }).first();
  try {
    await tenTv.waitFor({ state: 'visible', timeout: 20000 });
  } catch (error) {
    const diagnostic = await page.locator('#sidebarList').textContent();
    throw new Error(`10 TV not rendered after Indian selection: ${String(diagnostic || '').slice(0, 500)}`, { cause: error });
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
    extraHTTPHeaders: { Origin: 'https://clicktv.pages.dev' },
  });
  const mobilePage = await openCheckedPage(mobile, 'mobile');
  const mobileGeometry = await mobilePage.evaluate(() => ({
    noticeHeight: document.querySelector('#sticky-header-notice')?.getBoundingClientRect().height || 0,
    noticeFontSize: Number.parseFloat(getComputedStyle(document.querySelector('#sticky-header-notice')).fontSize || '0'),
    horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }));
  if (Math.abs(mobileGeometry.noticeHeight - 26) > 1 || mobileGeometry.noticeFontSize < 9 || mobileGeometry.horizontalOverflow > 1) {
    throw new Error(`Mobile geometry mismatch: ${JSON.stringify(mobileGeometry)}`);
  }
  const searchToggleVisible = await mobilePage.locator('#mobileBottomSearchBtn').isVisible();
  if (!searchToggleVisible) throw new Error('Mobile bottom search toggle is not visible');
  await mobilePage.locator('#mobileBottomSearchBtn').click();
  await mobilePage.locator('#mobileSearchInput').fill('10 TV');
  await mobilePage.waitForTimeout(500);
  if (!(await mobilePage.locator('#mobileSearchInput').isVisible())) {
    throw new Error('Mobile search field did not stay open');
  }

  console.log('Browser runtime PASS: desktop/mobile geometry, notice close, persistent search, protected DRM/4K planning, live playback, forced proxy failover, PWA');
  await desktop.close();
  await mobile.close();
} finally {
  await browser.close();
}
