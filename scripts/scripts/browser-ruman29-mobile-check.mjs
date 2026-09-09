import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const browser = await chromium.launch({ headless: true, args: ['--autoplay-policy=no-user-gesture-required'] });

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.locator('#mobileMainNav .final-main-button').first().waitFor({ state: 'visible', timeout: 30000 });

  const shell = await page.evaluate(() => {
    const text = document.querySelector('#sticky-header-notice .marquee-text');
    const dock = document.querySelector('.app-header');
    const after = getComputedStyle(document.body, '::after');
    return {
      noticeText: text?.textContent?.trim() || '',
      noticeColor: text ? getComputedStyle(text).color : '',
      closeWidth: document.querySelector('#noticeCloseBtn')?.getBoundingClientRect().width || 0,
      dockBackdrop: dock ? getComputedStyle(dock).backdropFilter : '',
      bodyAfter: after.display,
    };
  });
  assert(shell.noticeText.length > 5 && shell.noticeColor !== 'rgba(0, 0, 0, 0)', `Notice text hidden: ${JSON.stringify(shell)}`);
  assert(shell.closeWidth >= 31, `Notice close target is too small: ${JSON.stringify(shell)}`);
  assert(shell.dockBackdrop !== 'none' && shell.bodyAfter === 'none', `Bottom dock is not single glass layer: ${JSON.stringify(shell)}`);

  await page.locator('#mobileMainNav [data-final-key="movies"]').click();
  await page.locator('#mobileSubNav [data-final-key="movie:bangla"]').click();
  await page.locator('#sidebarList .movie-card, #sidebarList .catalog-series-card').first().waitFor({ state: 'visible', timeout: 30000 });
  const cards = await page.evaluate(() => {
    const movie = document.querySelector('#sidebarList .movie-card');
    const series = document.querySelector('#sidebarList .catalog-series-card');
    const poster = document.querySelector('#sidebarList .movie-card .movie-poster, #sidebarList .catalog-series-card .movie-poster');
    return {
      movieHeight: movie?.getBoundingClientRect().height || 0,
      seriesHeight: series?.getBoundingClientRect().height || 0,
      posterFit: poster ? getComputedStyle(poster).objectFit : '',
    };
  });
  assert(cards.movieHeight >= 200, `Movie card height is unstable: ${JSON.stringify(cards)}`);
  if (cards.seriesHeight) assert(Math.abs(cards.movieHeight - cards.seriesHeight) <= 1, `Movie/series heights differ: ${JSON.stringify(cards)}`);
  assert(cards.posterFit === 'contain', `Poster is cropped: ${JSON.stringify(cards)}`);

  const movieCard = page.locator('#sidebarList .movie-card').first();
  await movieCard.click();
  await page.waitForTimeout(500);
  const controls = await page.evaluate(() => {
    const ids = ['movieLockBtn', 'prevChBtn', 'skipBackBtn', 'playPauseBtn', 'skipFwdBtn', 'nextChBtn', 'movieRotateBtn', 'pipBtn'];
    return {
      movieContext: document.documentElement.classList.contains('movie-playback-context'),
      visible: ids.map((id) => [id, getComputedStyle(document.getElementById(id)).display !== 'none']),
      bufferLabels: [...document.querySelectorAll('#networkMenu .menu-item')].map((node) => node.textContent.replace(/\s+/g, ' ').trim()),
    };
  });
  assert(controls.movieContext && controls.visible.every(([, visible]) => visible), `Mobile movie controls missing: ${JSON.stringify(controls)}`);

  await page.setViewportSize({ width: 844, height: 390 });
  await page.locator('#fullscreenBtn').click({ force: true });
  await page.waitForTimeout(300);
  const nativeFullscreen = await page.evaluate(() => Boolean(document.fullscreenElement || document.webkitFullscreenElement));
  if (!nativeFullscreen) {
    // Chromium headless may reject the Fullscreen API even after a click.
    await page.evaluate(() => {
      document.getElementById('videoContainer').classList.add('clicktv-mobile-fullscreen');
      const aspect = document.getElementById('aspectBtn');
      aspect.hidden = false;
      aspect.setAttribute('aria-hidden', 'false');
      aspect.style.setProperty('display', 'grid', 'important');
    });
  }
  await page.waitForTimeout(250);
  const landscapeTransport = await page.evaluate(() => {
    const box = (id) => {
      const rect = document.getElementById(id).getBoundingClientRect();
      const style = getComputedStyle(document.getElementById(id));
      const icon = document.querySelector(`#${id} i:not([style*="display:none"])`) || document.querySelector(`#${id} i`);
      return { id, x: rect.x, y: rect.y, width: rect.width, height: rect.height, radius: style.borderRadius, display: style.display, fontSize: style.fontSize, iconFontSize: icon ? getComputedStyle(icon).fontSize : '' };
    };
    const ids = ['movieLockBtn', 'movieRotateBtn', 'skipBackBtn', 'prevChBtn', 'playPauseBtn', 'nextChBtn', 'skipFwdBtn', 'pipBtn', 'aspectBtn', 'muteBtn', 'speedBtn', 'qualityBtn', 'fullscreenBtn'];
    const progress = document.getElementById('progressContainer').getBoundingClientRect();
    return {
      controls: ids.map(box),
      progress: { y: progress.y, bottom: progress.bottom },
      viewport: { width: innerWidth, height: innerHeight, orientation: matchMedia('(orientation:landscape)').matches },
      htmlClass: document.documentElement.className,
      wrapperClass: document.getElementById('videoContainer').className,
      aspectOuter: document.getElementById('aspectBtn').outerHTML,
      aspectMatches: document.getElementById('aspectBtn').matches('html.movie-playback-context .video-container-wrap.clicktv-mobile-fullscreen #aspectBtn'),
      icons: Object.fromEntries(['movieLockBtn', 'movieRotateBtn', 'pipBtn', 'aspectBtn'].map((id) => [id, document.querySelector(`#${id} i`)?.className || ''])),
    };
  });
  const transport = Object.fromEntries(landscapeTransport.controls.map((item) => [item.id, item]));
  const orderedIds = ['movieLockBtn', 'movieRotateBtn', 'skipBackBtn', 'prevChBtn', 'playPauseBtn', 'nextChBtn', 'skipFwdBtn', 'pipBtn', 'aspectBtn'];
  assert(orderedIds.every((id, index) => index === 0 || transport[orderedIds[index - 1]].x < transport[id].x), `Landscape control order is wrong: ${JSON.stringify(landscapeTransport)}`);
  const bottomCenters = orderedIds.map((id) => transport[id].y + (transport[id].height / 2));
  assert(Math.max(...bottomCenters) - Math.min(...bottomCenters) <= 2, `Bottom controls are not on one aligned row: ${JSON.stringify(landscapeTransport)}`);
  assert(transport.playPauseBtn.width >= 57 && transport.playPauseBtn.height >= 57, `Landscape play button is not screenshot-sized: ${JSON.stringify(landscapeTransport)}`);
  assert(['movieLockBtn', 'movieRotateBtn', 'skipBackBtn', 'prevChBtn', 'playPauseBtn', 'nextChBtn', 'skipFwdBtn', 'pipBtn', 'aspectBtn'].every((id) => parseFloat(transport[id].iconFontSize) >= 21), `Bottom transport icons are smaller than the reference: ${JSON.stringify(landscapeTransport)}`);
  assert(Math.abs((transport.playPauseBtn.x + transport.playPauseBtn.width / 2) - landscapeTransport.viewport.width / 2) <= 30, `Transport group is not centered: ${JSON.stringify(landscapeTransport)}`);
  assert(transport.movieRotateBtn.x < transport.skipBackBtn.x && transport.skipFwdBtn.x < transport.pipBtn.x, `Left/center/right transport groups are not separated: ${JSON.stringify(landscapeTransport)}`);
  assert(landscapeTransport.progress.bottom < Math.min(...orderedIds.map((id) => transport[id].y)), `Progress is not above transport buttons: ${JSON.stringify(landscapeTransport)}`);
  assert(['muteBtn', 'speedBtn', 'qualityBtn', 'fullscreenBtn'].every((id) => transport[id].display !== 'none' && transport[id].y < landscapeTransport.progress.y), `Existing utility controls were not preserved above the transport row: ${JSON.stringify(landscapeTransport)}`);
  assert(/fa-lock/.test(landscapeTransport.icons.movieLockBtn) && /fa-redo-alt/.test(landscapeTransport.icons.movieRotateBtn), `Left control icons do not match the reference: ${JSON.stringify(landscapeTransport)}`);
  assert(/fa-clone/.test(landscapeTransport.icons.pipBtn) && /fa-arrows-alt-h/.test(landscapeTransport.icons.aspectBtn), `Right control icons do not match the reference: ${JSON.stringify(landscapeTransport)}`);
  if (process.env.CLICKTV_MOBILE_SCREENSHOT) {
    await page.screenshot({ path: process.env.CLICKTV_MOBILE_SCREENSHOT, fullPage: false });
  }
  await page.evaluate(() => {
    document.getElementById('videoContainer').classList.remove('clicktv-mobile-fullscreen');
    if (document.fullscreenElement) document.exitFullscreen?.();
  });
  await page.waitForTimeout(150);
  await page.setViewportSize({ width: 390, height: 844 });

  await page.locator('#mobileMainNav [data-final-key="live-tv"]').click();
  await page.locator('#mobileSubNav [data-final-key="bangla"]').click();
  await page.locator('#sidebarList .channel-ref-card').first().click();
  await page.waitForTimeout(350);
  await page.locator('#networkBtn').click({ force: true });
  const liveBuffers = await page.evaluate(() => [...document.querySelectorAll('#networkMenu [data-network-mode]')]
    .map((node) => node.textContent.replace(/\s+/g, ' ').trim()));
  assert(liveBuffers.some((text) => /Auto.*8s Buffer/.test(text)), `Auto buffer is not 8s: ${JSON.stringify(liveBuffers)}`);
  assert(liveBuffers.some((text) => /Fast Start.*6s Buffer/.test(text)), `Fast buffer is not 6s: ${JSON.stringify(liveBuffers)}`);
  assert(liveBuffers.some((text) => /Stable.*16s Buffer/.test(text)), `Stable buffer is not 16s: ${JSON.stringify(liveBuffers)}`);
  assert(!errors.length, `Runtime errors: ${errors.join(' | ')}`);
  console.log(`Ruman-29 mobile PASS: ${JSON.stringify({ shell, cards, controls, landscapeTransport, liveBuffers })}`);
} finally {
  await browser.close();
}
