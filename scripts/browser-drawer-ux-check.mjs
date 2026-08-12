import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const browser = await chromium.launch({ headless: true, args: ['--disable-web-security'] });

async function verifyViewport(label, viewport, isMobile = false) {
  const context = await browser.newContext({ viewport, hasTouch: isMobile });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.locator('#sidebarList').waitFor({ state: 'visible', timeout: 20000 });
  await page.waitForTimeout(900);
  await page.locator('#fullscreenBtn').click();
  await page.waitForFunction(() => document.fullscreenElement?.id === 'videoContainer');
  await page.evaluate(() => document.querySelector('#fsDrawerToggle').click());
  await page.locator('#fsDrawer.open').waitFor({ state: 'visible' });

  const initial = await page.evaluate(() => ({
    title: document.querySelector('#fsDrawerTitle')?.textContent?.trim(),
    count: document.querySelector('#fsDrawerCount')?.textContent?.trim(),
    expanded: document.querySelector('#fsDrawerToggle')?.getAttribute('aria-expanded'),
    hidden: document.querySelector('#fsDrawer')?.getAttribute('aria-hidden'),
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }));
  if (!initial.title || !/item/.test(initial.count || '') || initial.expanded !== 'true' || initial.hidden !== 'false' || initial.overflow > 1) {
    throw new Error(`${label} initial drawer state mismatch: ${JSON.stringify(initial)}`);
  }

  const search = page.locator('#fsDrawerSearch');
  await search.fill('zzzz-clicktv-no-result');
  await page.waitForTimeout(600);
  if (!(await page.locator('.fs-drawer-status.empty').isVisible())) throw new Error(`${label} empty search state missing`);
  if (await page.locator('#fsDrawerClear').getAttribute('hidden') !== null) throw new Error(`${label} clear button did not appear`);

  await search.fill('somoy');
  await page.waitForTimeout(600);
  const matched = await page.locator('.fs-drawer-match').count();
  if (!matched) throw new Error(`${label} search highlight missing`);
  await search.press('Backspace');
  const afterBackspace = await search.inputValue();
  if (afterBackspace !== 'somo') throw new Error(`${label} Backspace was intercepted: ${afterBackspace}`);
  await search.press('ArrowDown');
  if (!(await page.evaluate(() => document.activeElement?.classList.contains('fs-drawer-item')))) {
    throw new Error(`${label} ArrowDown did not move focus to the first result`);
  }
  await search.focus();

  await search.press('Escape');
  if ((await search.inputValue()) !== '' || !(await page.locator('#fsDrawer').isVisible())) {
    throw new Error(`${label} first Escape did not clear and keep drawer open`);
  }
  await search.press('Escape');
  const closed = await page.evaluate(() => ({
    open: document.querySelector('#fsDrawer')?.classList.contains('open'),
    focus: document.activeElement?.id,
    expanded: document.querySelector('#fsDrawerToggle')?.getAttribute('aria-expanded'),
  }));
  if (closed.open || closed.focus !== 'fsDrawerToggle' || closed.expanded !== 'false') {
    throw new Error(`${label} second Escape/focus return mismatch: ${JSON.stringify(closed)}`);
  }
  if (errors.length) throw new Error(`${label} runtime errors: ${errors.join(' | ')}`);
  await context.close();
}

try {
  await verifyViewport('desktop', { width: 1440, height: 900 });
  await verifyViewport('tablet', { width: 1024, height: 768 });
  await verifyViewport('mobile', { width: 390, height: 844 }, true);
  await verifyViewport('mobile-landscape', { width: 844, height: 390 }, true);
  console.log('Drawer UX PASS: desktop/tablet/mobile/landscape, count, search, empty state, highlight, Backspace, two-step Escape and focus return');
} finally {
  await browser.close();
}
