import fs from 'node:fs';
import { chromium } from 'playwright-core';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const candidates = [
  process.env.CHROME_PATH,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/google-chrome-stable',
  '/usr/bin/chromium',
].filter(Boolean);
const executablePath = candidates.find((candidate) => fs.existsSync(candidate));
if (!executablePath) throw new Error('Chrome/Chromium executable not found');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function overlaps(left, right) {
  return left.left < right.right - 1 && left.right > right.left + 1 && left.top < right.bottom - 1 && left.bottom > right.top + 1;
}

const browser = await chromium.launch({ executablePath, headless: true, args: ['--autoplay-policy=no-user-gesture-required'] });
try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, serviceWorkers: 'block' });
  const page = await context.newPage();
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(() => Boolean(window.__clickTvRuntimeTest), null, { timeout: 30000 });

  const noticeBefore = await page.locator('#sticky-header-notice .marquee-text').evaluate((node) => getComputedStyle(node).transform);
  await page.waitForTimeout(1200);
  const noticeAfter = await page.locator('#sticky-header-notice .marquee-text').evaluate((node) => getComputedStyle(node).transform);
  assert(noticeBefore !== noticeAfter, `Notice did not move: ${noticeBefore}`);

  const attempts = await page.evaluate(() => window.__clickTvRuntimeTest.buildAttempts({
    id: 'v2-direct-first-check',
    name: 'V2 direct first check',
    url: 'https://example.test/live.m3u8',
    stream_type: 'hls',
    proxy_mode: 'direct_first',
  }));
  assert(attempts[0]?.route === 'direct', `Direct route was not first: ${JSON.stringify(attempts)}`);
  assert(attempts.filter((attempt) => attempt.route === 'proxy').length >= 4, `All configured proxies were not planned: ${JSON.stringify(attempts)}`);

  await page.evaluate(() => document.querySelector('#desktopMainNav [data-final-key="movies"]')?.click());
  await page.evaluate(() => document.querySelector('#desktopSubNav [data-final-key="movie:bangla"]')?.click());
  await page.locator('#sidebarList .movie-card').first().click();
  await page.waitForFunction(() => document.documentElement.classList.contains('movie-playback-context'), null, { timeout: 20000 });

  const controlIds = ['movieLockBtn', 'prevChBtn', 'movieRotateBtn', 'aspectBtn', 'skipBackBtn', 'playPauseBtn', 'skipFwdBtn', 'nextChBtn', 'muteBtn', 'speedBtn', 'qualityBtn', 'fullscreenBtn'];
  const geometry = async (targetPage = page) => targetPage.evaluate((ids) => Object.fromEntries(ids.map((id) => {
    const node = document.getElementById(id);
    if (!node || getComputedStyle(node).display === 'none') return [id, null];
    const rect = node.getBoundingClientRect();
    return [id, { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height }];
  })), controlIds);

  const portrait = await geometry();
  const portraitRects = Object.entries(portrait).filter(([, rect]) => rect);
  for (let left = 0; left < portraitRects.length; left += 1) {
    for (let right = left + 1; right < portraitRects.length; right += 1) {
      assert(!overlaps(portraitRects[left][1], portraitRects[right][1]), `Portrait controls overlap: ${portraitRects[left][0]} / ${portraitRects[right][0]}`);
    }
  }

  const landscapeContext = await browser.newContext({
    viewport: { width: 844, height: 390 },
    screen: { width: 844, height: 390 },
    isMobile: true,
    hasTouch: true,
    serviceWorkers: 'block',
  });
  const landscapePage = await landscapeContext.newPage();
  await landscapePage.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await landscapePage.waitForFunction(() => Boolean(window.__clickTvRuntimeTest), null, { timeout: 30000 });
  await landscapePage.evaluate(() => document.querySelector('#desktopMainNav [data-final-key="movies"]')?.click());
  await landscapePage.evaluate(() => document.querySelector('#desktopSubNav [data-final-key="movie:bangla"]')?.click());
  await landscapePage.locator('#sidebarList .movie-card').first().click();
  await landscapePage.waitForFunction(() => document.documentElement.classList.contains('movie-playback-context'), null, { timeout: 20000 });
  await landscapePage.locator('#fullscreenBtn').click();
  await landscapePage.waitForFunction(() => Boolean(document.fullscreenElement || document.webkitFullscreenElement), null, { timeout: 10000 });
  const landscape = await geometry(landscapePage);
  const landscapeState = await landscapePage.evaluate(() => ({
    rootClass: document.documentElement.className,
    orientationMatches: matchMedia('(orientation:landscape)').matches,
    rowDisplay: getComputedStyle(document.querySelector('.controls-row')).display,
    gridColumns: getComputedStyle(document.querySelector('.controls-row')).gridTemplateColumns,
  }));
  const play = landscape.playPauseBtn;
  assert(play && Math.abs((play.left + play.right) / 2 - 422) < 35, `Landscape play button is not centered: ${JSON.stringify({ play, landscapeState })}`);
  for (const [id, rect] of Object.entries(landscape)) {
    if (!rect) continue;
    assert(rect.left >= -1 && rect.right <= 845, `Landscape control outside player width: ${id} ${JSON.stringify(rect)}`);
  }
  await landscapeContext.close();

  console.log('Version-2 browser regression PASS: notice movement, direct-first/all-proxy planning, portrait no-overlap, centered landscape movie controls');
  await context.close();
} finally {
  await browser.close();
}
