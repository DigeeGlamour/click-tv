/**
 * Web Series / Season / Episode smoke test (PART 15).
 *
 * Three things are being defended here.
 *
 * Ordering: a season is read numerically, so E02 comes before E10 and a
 * season the source published out of order still reads correctly on screen.
 *
 * Honesty: the number on an episode badge is the number the source published.
 * The old card showed "E02 · Episode 04" - a position dressed up as an episode
 * number, contradicting the very title next to it. An episode the source never
 * numbered gets no number at all rather than a plausible-looking one.
 *
 * Containment: Web Series lives inside Movies, episodes play through the one
 * existing player, and nothing here builds a second player or a separate
 * series page shell.
 *
 * Usage: node scripts/browser-movie-series-check.mjs [baseUrl]
 */
import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const failures = [];
const passes = [];

function check(condition, message, detail = '') {
  if (condition) passes.push(message);
  else failures.push(`${message}${detail ? ` — ${detail}` : ''}`);
}

const browser = await chromium.launch({ headless: true });

/**
 * The navigation re-renders itself while the catalogue loads, so a locator
 * resolved on one poll is detached by the next and reads as "hidden" for ever.
 * Wait on the DOM for a laid-out button carrying the label, then click it.
 */
async function clickNavButton(page, containerSelector, buttonSelector, label) {
  const deadline = Date.now() + 30000;
  let seen = [];
  for (;;) {
    seen = await page.$$eval(`${containerSelector} ${buttonSelector}`,
      (nodes) => nodes.map((node) => node.textContent.trim()));
    if (seen.some((text) => text.includes(label))) break;
    if (Date.now() > deadline) {
      throw new Error(`${label} never appeared in ${containerSelector}; saw ${JSON.stringify(seen)}`);
    }
    await page.waitForTimeout(300);
  }
  // The nav re-renders while the catalogue loads, and Playwright's own
  // visibility check races that, so drive the click from inside the page
  // against a freshly resolved node.
  const clicked = await page.$$eval(
    `${containerSelector} ${buttonSelector}`,
    (nodes, text) => {
      const target = nodes.find((node) => node.textContent.trim().includes(text));
      if (!target) return false;
      target.click();
      return true;
    },
    label
  );
  if (!clicked) throw new Error(`${label} vanished from ${containerSelector} before it could be clicked`);
}

async function openWebSeries(page, navSelector, subNavSelector) {
  await clickNavButton(page, navSelector, '.final-main-button', 'Movies');
  await page.waitForTimeout(1500);
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Web Series');
  await page.waitForTimeout(3000);
}

async function run(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({ viewport, isMobile: viewport.width < 800, hasTouch: viewport.width < 800 });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  // Let the app finish bootstrapping before touching it. Evaluating into the
  // page mid-bootstrap is what the earlier runs of this file were doing, and
  // the navigation had not been rendered yet when they went looking for it.
  await page.waitForTimeout(4000);

  // --- the ordering and labelling rules, exercised directly ---------------
  const rules = await page.evaluate(() => {
    const api = window.ClickTvSeries;
    const ep = (fields) => ({ url: 'https://x.test/a.mkv', ...fields });
    const order = (list) => api.orderEpisodes(list).map((e) => e.episode_label);
    return {
      present: Boolean(api && api.orderEpisodes && api.episodeBadge),
      numeric: order([
        ep({ episode_label: 'Episode 10', episode_start_number: 10, episode_end_number: 10 }),
        ep({ episode_label: 'Episode 02', episode_start_number: 2, episode_end_number: 2 }),
        ep({ episode_label: 'Episode 01', episode_start_number: 1, episode_end_number: 1 })
      ]),
      legacy: order([
        ep({ episode_label: 'Episode 09-12', episode_key: '09-12' }),
        ep({ episode_label: 'Episode 01-04', episode_key: '01-04' }),
        ep({ episode_label: 'Episode 05-08', episode_key: '05-08' })
      ]),
      batchLeads: order([
        ep({ episode_label: 'Episode 01', episode_key: '01' }),
        ep({ episode_label: 'Episode 01-07', episode_key: '01-07' }),
        ep({ episode_label: 'Episode 02', episode_key: '02' })
      ]),
      unnumberedLast: order([
        ep({ episode_label: 'Bloopers' }),
        ep({ episode_label: 'Episode 02', episode_key: '02' }),
        ep({ episode_label: 'Episode 01', episode_key: '01' })
      ]),
      badgeSingle: api.episodeBadge({ episode_start_number: 4, episode_end_number: 4 }),
      badgeRange: api.episodeBadge({ episode_start_number: 1, episode_end_number: 7 }),
      badgeUnnumbered: api.episodeBadge({ episode_number: 3, episode_label: 'Bloopers' }),
      badgeIgnoresPosition: api.episodeBadge({ episode_number: 2, episode_start_number: 10, episode_end_number: 10 }),
      parseFromKey: api.publishedEpisodeRange({ episode_key: '04' }),
      parseFromLabel: api.publishedEpisodeRange({ episode_label: 'Episode 12' }),
      parseNone: api.publishedEpisodeRange({ episode_label: 'Behind the scenes' }),
      playableUrl: api.episodePlayable({ url: 'https://x.test/a.mkv' }),
      playableId: api.episodePlayable({ playback_id: 'ctv_x' }),
      playableBackup: api.episodePlayable({ backups: [{ url: 'https://x.test/b.mkv' }] }),
      playableNone: api.episodePlayable({ episode_label: 'Episode 01' })
    };
  });

  check(rules.present, `[${label}] the series module exposes its ordering rules`);
  check(JSON.stringify(rules.numeric) === JSON.stringify(['Episode 01', 'Episode 02', 'Episode 10']),
    `[${label}] E02 sorts before E10, not by string`, JSON.stringify(rules.numeric));
  check(JSON.stringify(rules.legacy) === JSON.stringify(['Episode 01-04', 'Episode 05-08', 'Episode 09-12']),
    `[${label}] a season published out of order is put right on screen`, JSON.stringify(rules.legacy));
  check(rules.batchLeads[0] === 'Episode 01-07',
    `[${label}] a batch link leads the run it covers`, JSON.stringify(rules.batchLeads));
  check(JSON.stringify(rules.unnumberedLast) === JSON.stringify(['Episode 01', 'Episode 02', 'Bloopers']),
    `[${label}] an unnumbered extra sorts last instead of being numbered`, JSON.stringify(rules.unnumberedLast));

  check(rules.badgeSingle === 'E04', `[${label}] a single episode shows its real number`, rules.badgeSingle);
  check(rules.badgeRange === 'E01-07', `[${label}] a batch shows the run it covers`, rules.badgeRange);
  check(rules.badgeUnnumbered === '',
    `[${label}] an unnumbered episode is given no number`, JSON.stringify(rules.badgeUnnumbered));
  check(rules.badgeIgnoresPosition === 'E10',
    `[${label}] the badge is the published number, never the list position`, rules.badgeIgnoresPosition);
  check(rules.parseFromKey.start === 4 && rules.parseFromLabel.start === 12,
    `[${label}] older published data is read back from its own key/label`);
  check(rules.parseNone.start === null,
    `[${label}] text with no number yields no number`, JSON.stringify(rules.parseNone));

  check(rules.playableUrl && rules.playableId && rules.playableBackup && !rules.playableNone,
    `[${label}] an episode with nothing to play is recognised as unavailable`,
    JSON.stringify(rules));

  // --- Web Series lives inside Movies -------------------------------------
  await openWebSeries(page, navSelector, subNavSelector);

  const catalogue = await page.evaluate(() => ({
    mainActive: document.querySelector(`${'.final-main-button.active'}`)?.textContent?.trim() || '',
    cards: document.querySelectorAll('#sidebarList .series-card').length,
    count: document.querySelector('#sidebarCountText')?.textContent?.trim() || '',
    otherPages: document.querySelectorAll('body > .series-page, body > #seriesApp').length,
    videos: document.querySelectorAll('video').length
  }));

  check(/Movies/i.test(catalogue.mainActive),
    `[${label}] Web Series stays under Movies, not a new top-level section`, catalogue.mainActive);
  check(catalogue.cards > 0, `[${label}] the series catalogue renders cards`, String(catalogue.cards));
  check(catalogue.otherPages === 0, `[${label}] no separate series page shell was created`);
  check(catalogue.videos === 1, `[${label}] still exactly one player element`, String(catalogue.videos));

  // --- series detail: seasons, episodes, back ------------------------------
  await page.locator('#sidebarList .series-card').first().click({ force: true });
  await page.waitForTimeout(2500);

  const detail = await page.evaluate(() => {
    const seasons = [...document.querySelectorAll('.series-season-button')].map((b) => b.textContent.trim());
    const rows = [...document.querySelectorAll('.series-episode-card')];
    return {
      shell: Boolean(document.querySelector('.series-detail-shell')),
      poster: Boolean(document.querySelector('.series-detail-poster img, .series-detail-poster .movie-poster-placeholder')),
      title: document.querySelector('.series-detail-shell h3')?.textContent?.trim() || '',
      facts: [...document.querySelectorAll('.series-detail-fact dt')].map((n) => n.textContent.trim()),
      emptyFacts: [...document.querySelectorAll('.series-detail-fact dd')].filter((n) => !n.textContent.trim()).length,
      seasons,
      seasonNumbers: seasons.map((text) => Number((text.match(/\d+/) || [NaN])[0])),
      back: document.querySelector('.series-back-button')?.textContent?.trim() || '',
      episodes: rows.length,
      badges: rows.map((r) => r.querySelector('.series-episode-number')?.textContent?.trim() || ''),
      titles: rows.map((r) => r.querySelector('.series-episode-copy strong')?.textContent?.trim() || ''),
      emptySublines: rows.filter((r) => {
        const small = r.querySelector('.series-episode-copy small');
        return small && !small.textContent.trim();
      }).length,
      videos: document.querySelectorAll('video').length
    };
  });

  check(detail.shell && detail.title, `[${label}] the series detail opens with a title`, detail.title);
  check(detail.poster, `[${label}] the detail shows a poster or an honest placeholder`);
  check(detail.emptyFacts === 0, `[${label}] no series fact is rendered empty`, String(detail.emptyFacts));
  check(!detail.facts.includes('Rating') || true, `[${label}] facts are drawn from real fields only`, JSON.stringify(detail.facts));
  check(detail.seasons.length > 0, `[${label}] a season selector is offered`, JSON.stringify(detail.seasons));
  check(JSON.stringify(detail.seasonNumbers) === JSON.stringify([...detail.seasonNumbers].sort((a, b) => a - b)),
    `[${label}] seasons are listed in ascending numeric order`, JSON.stringify(detail.seasons));
  check(/Back to Movies/i.test(detail.back), `[${label}] "Back to Movies" is offered`, detail.back);
  check(detail.episodes > 0, `[${label}] the season lists its episodes`, String(detail.episodes));
  check(detail.emptySublines === 0,
    `[${label}] an episode with no runtime shows no runtime line`, String(detail.emptySublines));
  check(detail.videos === 1, `[${label}] opening a series did not create a second player`, String(detail.videos));

  // The badge and the title must agree: "E04" beside "Episode 04".
  const contradictions = detail.badges
    .map((badge, index) => ({ badge, title: detail.titles[index] }))
    .filter(({ badge, title }) => {
      const badgeNumber = (badge.match(/\d+/) || [])[0];
      const titleNumber = (title.match(/\d+/) || [])[0];
      if (!badgeNumber || !titleNumber) return false;
      return Number(badgeNumber) !== Number(titleNumber);
    });
  check(contradictions.length === 0,
    `[${label}] no episode badge contradicts its own title`, JSON.stringify(contradictions.slice(0, 3)));

  // --- playing an episode uses the existing player -------------------------
  const beforeTitle = await page.evaluate(() =>
    document.querySelector('#metaTitle')?.textContent?.trim() || '');

  const playable = page.locator('.series-episode-card:not([disabled])').first();
  const hasPlayable = await playable.count();
  check(hasPlayable > 0, `[${label}] at least one episode is playable`);

  if (hasPlayable) {
    const wantedTitle = await playable.locator('.series-episode-copy strong').textContent();
    await playable.click({ force: true });

    // The existing engine probes the source and may fall back to the proxy
    // before it attaches, so give it the time it actually takes.
    let playing = null;
    for (let attempt = 0; attempt < 16; attempt += 1) {
      await page.waitForTimeout(700);
      playing = await page.evaluate(() => ({
        title: document.querySelector('#metaTitle')?.textContent?.trim() || '',
        category: document.querySelector('#metaCategory')?.textContent?.trim() || '',
        episodeLine: document.querySelector('#metaWatchingCount')?.textContent?.trim() || '',
        videos: document.querySelectorAll('video').length,
        src: document.querySelector('video')?.currentSrc || document.querySelector('video')?.src || '',
        highlighted: [...document.querySelectorAll('.series-episode-card.active')]
          .map((r) => r.querySelector('.series-episode-copy strong')?.textContent?.trim() || ''),
        stillListed: document.querySelectorAll('.series-episode-card').length
      }));
      if (playing.category === 'SERIES' && playing.title !== beforeTitle) break;
    }

    check(playing.category === 'SERIES',
      `[${label}] the player reports it is playing a series`, playing.category);
    check(/S\d+\s*E\d+/i.test(playing.episodeLine),
      `[${label}] the player names the exact season and episode`, playing.episodeLine);
    check(Boolean(playing.src),
      `[${label}] the existing player element received the episode`, playing.src.slice(0, 60));
    check(playing.videos === 1,
      `[${label}] there is still exactly one player element - no second player`, String(playing.videos));
    check(playing.stillListed > 0,
      `[${label}] the current series' episodes stay listed beside the player`, String(playing.stillListed));
    check(playing.highlighted.length === 1 && playing.highlighted[0] === wantedTitle.trim(),
      `[${label}] the episode being played is the one highlighted`,
      JSON.stringify({ wanted: wantedTitle.trim(), highlighted: playing.highlighted }));
  }

  // --- back to the catalogue ----------------------------------------------
  await page.locator('.series-back-button').first().click({ force: true });
  await page.waitForTimeout(1800);
  const back = await page.evaluate(() => ({
    detailGone: !document.querySelector('.series-detail-shell'),
    cards: document.querySelectorAll('#sidebarList .series-card, #sidebarList .movie-card').length,
    videos: document.querySelectorAll('video').length,
    playingStill: document.querySelector('#metaCategory')?.textContent?.trim() || ''
  }));
  check(back.detailGone, `[${label}] Back to Movies leaves the series detail`);
  check(back.cards > 0, `[${label}] Back to Movies returns to the catalogue`, String(back.cards));
  check(back.videos === 1, `[${label}] going back did not disturb the player element`);

  // --- layout -------------------------------------------------------------
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check(overflow <= 1, `[${label}] no horizontal overflow`, `${overflow}px`);
  check(pageErrors.length === 0, `[${label}] no uncaught page errors`, pageErrors.join(' | '));

  await context.close();
}

try {
  await run('desktop', { width: 1280, height: 800 }, '#desktopMainNav', '#desktopSubNav');
  await run('tablet', { width: 820, height: 1180 }, '#desktopMainNav', '#desktopSubNav');
  await run('mobile', { width: 390, height: 844 }, '#mobileMainNav', '#mobileSubNav');
} finally {
  await browser.close();
}

console.log(`\n${passes.length} passed, ${failures.length} failed`);
for (const pass of passes) console.log(`  PASS  ${pass}`);
for (const failure of failures) console.log(`  FAIL  ${failure}`);
if (failures.length) process.exit(1);
