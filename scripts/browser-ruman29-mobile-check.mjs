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
    const ids = ['movieLockBtn', 'prevChBtn', 'skipBackBtn', 'playPauseBtn', 'skipFwdBtn', 'nextChBtn', 'movieRotateBtn'];
    return {
      movieContext: document.documentElement.classList.contains('movie-playback-context'),
      visible: ids.map((id) => [id, getComputedStyle(document.getElementById(id)).display !== 'none']),
      bufferLabels: [...document.querySelectorAll('#networkMenu .menu-item')].map((node) => node.textContent.replace(/\s+/g, ' ').trim()),
    };
  });
  assert(controls.movieContext && controls.visible.every(([, visible]) => visible), `Mobile movie controls missing: ${JSON.stringify(controls)}`);

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
  console.log(`Ruman-29 mobile PASS: ${JSON.stringify({ shell, cards, controls, liveBuffers })}`);
} finally {
  await browser.close();
}
