import { chromium } from 'playwright';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const root = process.cwd();

const real = JSON.parse(await readFile(path.join(root, 'working', 'fixture_real_items.json'), 'utf8'));

function channel(id, name) {
  return {
    id, name, normalized_name: name.toLowerCase(),
    verified: true, verification_status: 'verified_global',
    playback_types: ['native'], renderer: 'native',
    streams: [{
      id: `${id}-1`, role: 'primary', playback_type: 'native',
      url: 'https://example.invalid/live.m3u8',
      verified: true, verification_status: 'verified_global',
      resolution: 'HD',
    }],
  };
}

function baseFields(overrides) {
  return {
    verification_mode: 'global', verification_status: 'verified_global',
    verification_badge: 'Verified', verified: true, publish_allowed: true,
    source_pipeline: 'today_match', original_source_pipeline: 'today_match',
    content_kind: 'event', category: 'today_match',
    status: 'LIVE_NOW', schedule_status: 'LIVE_NOW', schedule_verified: true,
    start_time: '2020-01-01T00:00:00+00:00', end_time: '2099-01-01T00:00:00+00:00',
    url: 'https://example.invalid/live.m3u8',
    header_profile: 'android_tv', proxy_mode: 'proxy_only', stream_type: 'hls',
    ...overrides,
  };
}

const fourChannel = baseFields({
  id: 'synthetic-four-channel-football',
  name: 'Club Tijuana vs Cruz Azul',
  competition: 'Liga MX',
  sport_type: 'football',
  provider_poster_url: 'https://picsum.photos/seed/tijuana-cruzazul/400/300',
  channels: [
    channel('synthetic-four-channel-football--tudn', 'TUDN'),
    channel('synthetic-four-channel-football--espn', 'ESPN'),
    channel('synthetic-four-channel-football--fox', 'Fox Sports'),
    channel('synthetic-four-channel-football--azteca', 'Azteca 7'),
  ],
  default_channel_id: 'synthetic-four-channel-football--tudn',
});

const tennisTwoChannel = baseFields({
  id: 'synthetic-tennis-cincinnati',
  name: 'Cincinnati Open Final',
  competition: 'ATP Cincinnati',
  sport_type: 'tennis',
  provider_poster_url: 'https://picsum.photos/seed/cincinnati-open/400/300',
  channels: [
    channel('synthetic-tennis-cincinnati--tennis-channel', 'Tennis Channel'),
    channel('synthetic-tennis-cincinnati--espn', 'ESPN2'),
  ],
  default_channel_id: 'synthetic-tennis-cincinnati--tennis-channel',
});

const longTextStress = baseFields({
  id: 'synthetic-long-text-stress',
  name: 'International Championship Trophy Grand Final Second Leg Extra Time Replay Decider Match',
  competition: 'The Extremely Long International Invitational Championship Qualifying Series 2026',
  sport_type: 'cricket',
  provider_poster_url: 'https://picsum.photos/seed/long-text-stress/400/300',
  channels: [
    channel('synthetic-long-text-stress--willow', 'Willow'),
    channel('synthetic-long-text-stress--star', 'Star Sports 1'),
  ],
  default_channel_id: 'synthetic-long-text-stress--willow',
});

// The real fixtures were fetched once and their own end_time has since
// passed, which correctly hides them as ENDED (isEventEnded checks end_time
// regardless of schedule_verified) - that is real, unmodified product
// behavior. Refreshed here only so this validation run exercises today's
// live window, same as the synthetic items already do.
for (const item of [real.sli, real.two_ch, real.one_ch]) {
  item.start_time = '2020-01-01T00:00:00+00:00';
  item.end_time = '2099-01-01T00:00:00+00:00';
}

const items = [
  real.sli,          // 1: cricket, 9 channels (real production data)
  fourChannel,       // 2: football, 4 channels (synthetic)
  real.two_ch,       // 3: football, 2 channels (real production data)
  tennisTwoChannel,  // 4: tennis, 2 channels (synthetic)
  real.one_ch,       // 5: football, 1 channel / hero card (real production data)
  longTextStress,    // 6: cricket, long league + long title stress test
];

const browser = await chromium.launch({ headless: true, args: ['--disable-web-security'] });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' });
const page = await context.newPage();
const consoleErrors = [];
page.on('pageerror', (error) => consoleErrors.push(String(error?.message || error)));

await page.route('**/data/manifest.json*', async (route) => {
  const response = await route.fetch();
  const body = await response.json();
  body.today_match = { count: items.length, visible: true, url: '/data/today-match-test.json' };
  await route.fulfill({ response, json: body });
});
await page.route('**/data/today-match-test.json*', async (route) => {
  await route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ type: 'today_match', count: items.length, items }),
  });
});

const results = [];
const check = (label, ok, detail) => {
  results.push({ label, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} - ${label}${detail ? ' :: ' + JSON.stringify(detail) : ''}`);
};

await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.locator('#sidebarList').waitFor({ state: 'visible', timeout: 20000 });
await page.waitForTimeout(600);

// Capture player/sidebar geometry BEFORE navigating to Today Match, to prove
// the hard lock (player + sidebar size/position unchanged) directly.
const beforeGeometry = await page.evaluate(() => {
  const player = document.querySelector('#videoContainer') || document.querySelector('.video-container');
  const sidebar = document.querySelector('.sidebar-section') || document.querySelector('#sidebarList')?.closest('aside, .sidebar-section');
  const rect = (el) => el ? (({ x, y, width, height }) => ({ x, y, width, height }))(el.getBoundingClientRect()) : null;
  return { player: rect(player), sidebar: rect(sidebar) };
});

await page.locator('#desktopMainNav [data-final-key="sports"]').click();
await page.locator('#desktopSubNav [data-final-key="today-match"]').click();
await page.locator('.tm-card-v2').first().waitFor({ state: 'visible', timeout: 20000 });
await page.waitForTimeout(500);

const afterGeometry = await page.evaluate(() => {
  const player = document.querySelector('#videoContainer') || document.querySelector('.video-container');
  const sidebar = document.querySelector('.sidebar-section') || document.querySelector('#sidebarList')?.closest('aside, .sidebar-section');
  const rect = (el) => el ? (({ x, y, width, height }) => ({ x, y, width, height }))(el.getBoundingClientRect()) : null;
  return { player: rect(player), sidebar: rect(sidebar) };
});
check('18/19: player and sidebar geometry unchanged after opening Today Match',
  JSON.stringify(beforeGeometry) === JSON.stringify(afterGeometry),
  { before: beforeGeometry, after: afterGeometry });

// 1-16: structural + content assertions across all six cards.
const cardData = await page.evaluate(() => {
  const cols = Array.from(document.querySelectorAll('#sidebarList.tm-columns > .tm-col'));
  return cols.map((col) => Array.from(col.children).map((node) => {
    const card = node.classList.contains('tm-card-v2') ? node : node.querySelector('.tm-card-v2');
    const shell = node.classList.contains('event-card-shell') ? node : null;
    const serial = card?.querySelector('.tm-serial');
    const serialRect = serial?.getBoundingClientRect();
    const cardRect = card?.getBoundingClientRect();
    const info = card?.querySelector('.tm-info');
    const infoAfter = info ? getComputedStyle(info, '::after') : null;
    const league = card?.querySelector('.tm-league');
    const channels = node.querySelectorAll('.event-channel-chip.tm-channel');
    return {
      title: card?.querySelector('.tm-title')?.textContent?.trim(),
      category: card?.querySelector('.tm-category')?.textContent?.trim(),
      serialText: serial?.textContent?.trim(),
      serialFloatsOutside: serialRect && cardRect
        ? (serialRect.top < cardRect.top && serialRect.left < cardRect.left)
        : false,
      leagueColor: league ? getComputedStyle(league).color : null,
      accentLineWidth: infoAfter ? infoAfter.width : null,
      accentLineBackground: infoAfter ? infoAfter.backgroundImage : null,
      isOneChannel: card?.classList.contains('tm-one-channel') || false,
      channelCount: channels.length,
      hasVisibleChannelGrid: node.querySelector('.tm-channels') !== null,
      forbidden: {
        date: /\bdate\b/i.test(node.textContent || ''),
        countdown: node.querySelector('.event-status-pill, [data-clock="countdown"]') !== null,
        watchButton: node.querySelector('.event-card-action') !== null,
        favoriteButton: node.querySelector('[data-favorite-id], .card-fav-btn') !== null,
      },
    };
  }));
});
const flat = cardData.flat();
check('3/4: correct card count rendered across the two columns', flat.length === items.length, { count: flat.length });
check('7/16: single-channel card hides the channel grid entirely',
  flat.some((c) => c.isOneChannel && !c.hasVisibleChannelGrid && c.channelCount === 0), flat.filter((c) => c.isOneChannel));
check('1/2: a 6+ and a 4-channel card both show their full grid',
  flat.some((c) => c.channelCount >= 6) && flat.some((c) => c.channelCount === 4),
  flat.map((c) => c.channelCount));
check('3: a 2-channel card shows exactly two buttons', flat.some((c) => c.channelCount === 2), null);
check('7/9: cricket, football and tennis category badges all present',
  ['CRICKET', 'FOOTBALL', 'TENNIS'].every((s) => flat.some((c) => c.category === s)),
  flat.map((c) => c.category));
check('14: red accent line present under the title on every card',
  flat.every((c) => c.accentLineWidth && c.accentLineWidth !== '0px' && /255, ?49, ?88/.test(c.accentLineBackground || '')),
  flat.map((c) => c.accentLineWidth));
check('15: league name is the yellow #FFD24A on every card',
  flat.every((c) => c.leagueColor === 'rgb(255, 210, 74)'),
  flat.map((c) => c.leagueColor));
check('serial badge floats outside the card corner on every card (no column-count clipping)',
  flat.every((c) => c.serialFloatsOutside), flat.map((c) => c.serialFloatsOutside));
// Cards are split left/right by index parity (1,3,5.. left; 2,4,6.. right),
// so within-column order is what must be sequential, not the flattened list.
const leftSerials = cardData[0].map((c) => Number(c.serialText));
const rightSerials = cardData[1].map((c) => Number(c.serialText));
const oddExpected = Array.from({ length: leftSerials.length }, (_, i) => i * 2 + 1);
const evenExpected = Array.from({ length: rightSerials.length }, (_, i) => i * 2 + 2);
check('3/4: serial order is correct within each column (odd left, even right)',
  JSON.stringify(leftSerials) === JSON.stringify(oddExpected) &&
  JSON.stringify(rightSerials) === JSON.stringify(evenExpected),
  { leftSerials, rightSerials });
check('DO-NOT-SHOW fields absent from every card',
  flat.every((c) => !c.forbidden.date && !c.forbidden.countdown && !c.forbidden.watchButton && !c.forbidden.favoriteButton),
  flat.map((c) => c.forbidden));

// 17: masonry columns have no row-created vertical empty gaps (each column's
// own children stack with only the intended 9px gap between them).
const gapCheck = await page.evaluate(() => {
  const cols = Array.from(document.querySelectorAll('#sidebarList.tm-columns > .tm-col'));
  return cols.map((col) => {
    const kids = Array.from(col.children);
    const gaps = [];
    for (let i = 1; i < kids.length; i += 1) {
      const prevRect = kids[i - 1].getBoundingClientRect();
      const rect = kids[i].getBoundingClientRect();
      gaps.push(Math.round(rect.top - prevRect.bottom));
    }
    return gaps;
  });
});
check('17: no oversized gaps between stacked cards in either column',
  gapCheck.every((col) => col.every((gap) => gap >= 0 && gap <= 12)), gapCheck);

// Clean "first look" screenshot - the natural state a real user sees on
// opening the tab, before any manual interaction re-pins a different card.
const sidebarHandleInitial = await page.locator('.sidebar-section').first().elementHandle();
if (sidebarHandleInitial) await sidebarHandleInitial.screenshot({ path: 'working/today-match-crimson-initial.png' });

// 10/12: channel selection switching - click a channel in the 4-channel card,
// confirm only one .is-selected globally and its card gets is-playing-event.
const fourChannelCard = page.locator('.tm-card-v2', { hasText: 'Club Tijuana vs Cruz Azul' });
const fourChannelShell = fourChannelCard.locator('xpath=..');
await fourChannelShell.locator('.event-channel-chip', { hasText: 'ESPN' }).click();
await page.waitForTimeout(300);
const afterFirstClick = await page.evaluate(() => ({
  selectedCount: document.querySelectorAll('.event-channel-chip.is-playing').length,
  selectedText: document.querySelector('.event-channel-chip.is-playing')?.textContent?.trim(),
  playingShells: document.querySelectorAll('.event-card-shell.is-playing-event').length,
}));
check('10/11/12: exactly one selected channel globally, on the clicked card',
  afterFirstClick.selectedCount === 1 && afterFirstClick.selectedText === 'ESPN', afterFirstClick);

// 13: switch to a different channel on a different card - the old selection
// must turn ash and the highlight must move.
const twoChannelCard = page.locator('.tm-card-v2', { hasText: real.two_ch.name.replace(/�/g, '') }).first();
const twoChannelShell = twoChannelCard.locator('xpath=..');
const secondChannelButtons = await twoChannelShell.locator('.event-channel-chip').allTextContents();
await twoChannelShell.locator('.event-channel-chip').nth(1).click();
await page.waitForTimeout(300);
const afterSecondClick = await page.evaluate(() => ({
  selectedCount: document.querySelectorAll('.event-channel-chip.is-playing').length,
  selectedText: document.querySelector('.event-channel-chip.is-playing')?.textContent?.trim(),
}));
check('12/13: previous selection cleared, exactly one selected after switching cards',
  afterSecondClick.selectedCount === 1 && afterSecondClick.selectedText === secondChannelButtons[1],
  { afterSecondClick, secondChannelButtons });

// 16: single-channel hero card selection - clicking it makes it the playing
// card, with no visible channel button anywhere on it.
const heroCard = page.locator('.tm-card-v2.tm-one-channel').first();
await heroCard.click();
await page.waitForTimeout(300);
const heroState = await page.evaluate(() => {
  const hero = document.querySelector('.tm-card-v2.tm-one-channel');
  return {
    isActive: hero?.classList.contains('active') || false,
    hasChannelGrid: hero?.querySelector('.tm-channels') !== null,
    globallySelectedCount: document.querySelectorAll('.event-channel-chip.is-playing').length,
  };
});
check('16: one-channel card becomes the playing card with no visible channel button',
  heroState.isActive && !heroState.hasChannelGrid, heroState);
check('11: only one channel button is red/selected across the whole list even after hero click',
  heroState.globallySelectedCount <= 1, heroState);

// 8: hover state on a card visibly changes it (lift + border/glow).
const hoverTarget = page.locator('.tm-card-v2').first();
const beforeHover = await hoverTarget.evaluate((el) => getComputedStyle(el).transform);
await hoverTarget.hover();
await page.waitForTimeout(300);
const afterHover = await hoverTarget.evaluate((el) => getComputedStyle(el.closest('.event-card-shell') || el).transform);
check('8: hover state visibly transforms the card/shell', beforeHover !== afterHover, { beforeHover, afterHover });

await page.screenshot({ path: 'working/today-match-crimson.png', fullPage: false });
const sidebarHandle = await page.locator('.sidebar-section').first().elementHandle();
if (sidebarHandle) await sidebarHandle.screenshot({ path: 'working/today-match-crimson-sidebar.png' });

console.log(`console errors: ${consoleErrors.length}`);
if (consoleErrors.length) console.log(consoleErrors.slice(0, 5));

const failed = results.filter((r) => !r.ok);
await browser.close();
if (failed.length) {
  console.log(`\n${failed.length} CHECK(S) FAILED`);
  process.exit(1);
}
console.log('\nALL CHECKS PASSED');
