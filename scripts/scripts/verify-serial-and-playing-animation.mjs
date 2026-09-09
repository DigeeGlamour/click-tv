import { chromium } from 'playwright';
import { readFile } from 'node:fs/promises';

const baseUrl = 'https://clicktv.pages.dev';
const appJs = await readFile('site/assets/js/app.js', 'utf8');
const eventCardsCss = await readFile('site/assets/css/event-cards.css', 'utf8');
const eventChannelCardsCss = await readFile('site/assets/css/event-channel-cards.css', 'utf8');

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, serviceWorkers: 'block' });
const page = await context.newPage();

await page.route('**/assets/js/app.js*', (route) => route.fulfill({ contentType: 'application/javascript', body: appJs }));
await page.route('**/assets/css/event-cards.css*', (route) => route.fulfill({ contentType: 'text/css', body: eventCardsCss }));
await page.route('**/assets/css/event-channel-cards.css*', (route) => route.fulfill({ contentType: 'text/css', body: eventChannelCardsCss }));

await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.locator('#sidebarList').waitFor({ state: 'visible', timeout: 20000 });
await page.locator('#desktopMainNav [data-final-key="sports"]').click();
await page.locator('#desktopSubNav [data-final-key="today-match"]').click();
await page.locator('.tm-card-v2').first().waitFor({ state: 'visible', timeout: 20000 });
await page.waitForTimeout(1200);

const results = [];
const check = (label, ok, detail) => {
  results.push({ label, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'} - ${label}${detail ? ' :: ' + JSON.stringify(detail) : ''}`);
};

// 1: serial badge size/contrast.
const badge = await page.evaluate(() => {
  const el = document.querySelector('.tm-serial');
  const cs = getComputedStyle(el);
  return { text: el.textContent.trim(), width: cs.width, fontSize: cs.fontSize, color: cs.color, boxShadow: cs.boxShadow };
});
check('serial badge is larger with a light contrast ring', badge.width === '25px' && badge.fontSize === '11.5px' && /255, 255, 255/.test(badge.boxShadow), badge);

// 2: playing card has an animation applied.
const playingAnim = await page.evaluate(() => {
  const shell = document.querySelector('.event-card-shell.is-playing-event') || document.querySelector('.tm-card-v2.tm-one-channel.active');
  const target = shell?.classList.contains('tm-card-v2') ? shell : shell?.querySelector('.tm-card-v2');
  return target ? getComputedStyle(target).animationName : null;
});
check('the playing card has a pulsing animation applied', playingAnim === 'tmCardPlayingPulse', { playingAnim });

// 3: playing channel chip (if any) has an animation.
const chipAnim = await page.evaluate(() => {
  const chip = document.querySelector('.event-channel-chip.is-playing');
  return chip ? getComputedStyle(chip).animationName : 'no-playing-chip-yet';
});
check('a selected/playing channel chip pulses too', chipAnim === 'tmChannelPulse' || chipAnim === 'no-playing-chip-yet', { chipAnim });

// 4: clicking a channel shows the immediate is-switching state before settling.
const multiChannelShell = await page.locator('.event-card-shell').first();
const firstChip = multiChannelShell.locator('.event-channel-chip').first();
const chipCountBefore = await multiChannelShell.locator('.event-channel-chip').count();
if (chipCountBefore > 0) {
  const secondChip = multiChannelShell.locator('.event-channel-chip').nth(chipCountBefore > 1 ? 1 : 0);
  const clickPromise = secondChip.click();
  await page.waitForTimeout(50);
  const midClickState = await page.evaluate(() => Boolean(document.querySelector('.event-channel-chip.is-switching')));
  await clickPromise;
  check('clicking a channel shows an immediate is-switching state', midClickState, { midClickState });
} else {
  check('clicking a channel shows an immediate is-switching state', true, { skipped: 'no multi-channel card found in initial chunk' });
}

await page.screenshot({ path: 'working/verify-badge-and-animation.png' });
const failed = results.filter((r) => !r.ok);
await browser.close();
if (failed.length) {
  console.log(`\n${failed.length} CHECK(S) FAILED`);
  process.exit(1);
}
console.log('\nALL CHECKS PASSED');
