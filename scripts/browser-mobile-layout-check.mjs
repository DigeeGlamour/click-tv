import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const browser = await chromium.launch({ headless: true, args: ['--autoplay-policy=no-user-gesture-required'] });

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.locator('#mobileMainNav .final-main-button').first().waitFor({ state: 'visible', timeout: 30000 });
  await page.waitForTimeout(1000);

  const initial = await page.evaluate(() => {
    const notice = document.querySelector('#sticky-header-notice');
    const buttons = [...document.querySelectorAll('#mobileMainNav .final-main-button')];
    const active = document.querySelector('#mobileMainNav .final-main-button.active');
    const activeStyle = getComputedStyle(active);
    return {
      noticeVisible: Boolean(notice && !notice.hidden && getComputedStyle(notice).display !== 'none'),
      buttonCount: buttons.length,
      labels: buttons.map((button) => button.textContent.trim()),
      svgCount: buttons.filter((button) => button.querySelector('svg')).length,
      activeBackground: activeStyle.backgroundColor,
      activeBorderWidth: activeStyle.borderWidth,
      horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  });
  assert(initial.noticeVisible, `Mobile notice is hidden: ${JSON.stringify(initial)}`);
  assert(initial.buttonCount === 5 && initial.labels.at(-1) === 'Favorites', `Five-category nav missing: ${JSON.stringify(initial)}`);
  assert(initial.svgCount === 5, `Reference SVG icons missing: ${JSON.stringify(initial)}`);
  assert(initial.activeBorderWidth === '0px', `Selected category still has a box: ${JSON.stringify(initial)}`);
  assert(initial.horizontalOverflow <= 1, `Horizontal overflow: ${initial.horizontalOverflow}px`);

  await page.locator('#mobileMainNav [data-final-key="live-tv"]').click();
  await page.locator('#mobileSubNav [data-final-key="bangla"]').click();
  await page.locator('#sidebarList .channel-ref-card').first().waitFor({ state: 'visible', timeout: 30000 });
  await page.waitForTimeout(500);
  const channelLayout = await page.evaluate(() => {
    const cards = [...document.querySelectorAll('#sidebarList .channel-ref-card')];
    const a = cards[0]?.getBoundingClientRect();
    const b = cards[1]?.getBoundingClientRect();
    const scroll = document.querySelector('#sidebarScrollArea');
    const meta = document.querySelector('.video-meta')?.getBoundingClientRect();
    const panel = document.querySelector('.sidebar-section')?.getBoundingClientRect();
    return {
      cardCount: cards.length,
      twoColumns: Boolean(a && b && Math.abs(a.top - b.top) < 2 && b.left > a.left),
      canScroll: Boolean(scroll && scroll.scrollHeight > scroll.clientHeight + 5),
      overflowY: scroll ? getComputedStyle(scroll).overflowY : '',
      noOverlap: Boolean(meta && panel && panel.top >= meta.bottom - 1),
      metaHeight: meta?.height || 0,
    };
  });
  assert(channelLayout.twoColumns, `Live TV is not a double grid: ${JSON.stringify(channelLayout)}`);
  assert(channelLayout.canScroll && channelLayout.overflowY === 'auto', `Card area does not scroll: ${JSON.stringify(channelLayout)}`);
  assert(channelLayout.noOverlap, `Now Playing overlaps cards: ${JSON.stringify(channelLayout)}`);
  assert(channelLayout.metaHeight >= 58 && channelLayout.metaHeight <= 66, `Now Playing spacing is wrong: ${JSON.stringify(channelLayout)}`);

  const firstFavorite = page.locator('#sidebarList .channel-ref-card .card-fav-btn').first();
  await firstFavorite.click();
  await page.locator('#mobileMainNav [data-final-key="favorites"]').click();
  await page.locator('#sidebarList .sidebar-item').first().waitFor({ state: 'visible', timeout: 10000 });
  const favoriteCount = await page.locator('#sidebarList .sidebar-item').count();
  assert(favoriteCount >= 1, 'Favorited channel did not appear under Favorites');

  await page.locator('#mobileMainNav [data-final-key="sports"]').click();
  const sportsCentering = await page.evaluate(() => {
    const nav = document.querySelector('#mobileSubNavigation');
    const list = document.querySelector('#mobileSubNav');
    return { className: nav?.className || '', justify: list ? getComputedStyle(list).justifyContent : '' };
  });
  assert(sportsCentering.className.includes('sports-subnav') && sportsCentering.justify === 'center', `Sports tabs are not centered: ${JSON.stringify(sportsCentering)}`);
  await page.locator('#mobileSubNav [data-final-key="today-match"]').click();
  await page.waitForFunction(() => document.querySelector('.sidebar-section')?.classList.contains('event-list-mode'), null, { timeout: 15000 });
  const eventLayout = await page.evaluate(() => ({
    columns: getComputedStyle(document.querySelector('#sidebarList')).gridTemplateColumns,
    display: getComputedStyle(document.querySelector('#sidebarList')).display,
    sectionClass: document.querySelector('.sidebar-section')?.className || '',
    listClass: document.querySelector('#sidebarList')?.className || '',
  }));
  assert(eventLayout.display === 'flex', `Today Match must stay single-column: ${JSON.stringify(eventLayout)}`);

  await page.locator('#noticeCloseBtn').click();
  assert(await page.locator('#sticky-header-notice').isHidden(), 'Notice close button did not work');
  await page.reload({ waitUntil: 'domcontentloaded' });
  assert(await page.locator('#sticky-header-notice').isHidden(), 'Notice dismissal did not persist for this tab');
  const secondPage = await context.newPage();
  await secondPage.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  assert(await secondPage.locator('#sticky-header-notice').isVisible(), 'Notice was incorrectly hidden in a new tab');

  assert(pageErrors.length === 0, `Runtime errors: ${pageErrors.join(' | ')}`);
  console.log(`Mobile browser PASS: notice, exact icons, 5 categories, favorites, no active box, compact meta, double-grid cards, fixed-shell scrolling, centered sports tabs, event single-column`);
  await context.close();
} finally {
  await browser.close();
}

