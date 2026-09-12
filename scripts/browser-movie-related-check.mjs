/**
 * Related / recommended content smoke test (PART 17).
 *
 * A recommendation is a claim, so the claims are what this pins down: it is
 * built from real metadata with named weights, the current item is never
 * recommended to itself, a withdrawn or wrong-type title is never offered,
 * `available_link_count` is never mistaken for popularity, and no filler is
 * added to reach a fixed count - fewer good matches means fewer cards.
 *
 * It also pins the one ordering rule that matters beside the player: while a
 * series episode plays, its own season keeps the area and related content
 * does not displace episode navigation.
 *
 * Usage: node scripts/browser-movie-related-check.mjs [baseUrl]
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

async function clickNavButton(page, containerSelector, buttonSelector, label) {
  const deadline = Date.now() + 30000;
  for (;;) {
    const seen = await page.$$eval(`${containerSelector} ${buttonSelector}`,
      (nodes) => nodes.map((node) => node.textContent.trim()));
    if (seen.some((text) => text.includes(label))) break;
    if (Date.now() > deadline) throw new Error(`${label} never appeared in ${containerSelector}; saw ${JSON.stringify(seen)}`);
    await page.waitForTimeout(300);
  }
  await page.$$eval(`${containerSelector} ${buttonSelector}`, (nodes, text) => {
    const target = nodes.find((node) => node.textContent.trim().includes(text));
    if (target) target.click();
  }, label);
}

async function run(label, viewport, navSelector, subNavSelector) {
  const context = await browser.newContext({ viewport, isMobile: viewport.width < 800, hasTouch: viewport.width < 800 });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  await clickNavButton(page, navSelector, '.final-main-button', 'Movies');
  await page.waitForTimeout(1500);

  // --- the scoring rules, exercised directly ------------------------------
  const scoring = await page.evaluate(() => {
    const subject = {
      id: 'subject', name: 'Subject', category: 'Bangla', year: 2024,
      genres: ['Action', 'Thriller'], type: 'movie'
    };
    const score = (candidate) => window.movieRelatedScore(candidate, subject);
    return {
      weights: window.movieRelatedWeights(),
      itself: score({ ...subject }),
      sameTmdb: score({ id: 'other', tmdb_id: 99, category: 'Bangla', year: 2024, type: 'movie' }),
      subjectTmdb: window.movieRelatedScore(
        { id: 'other', tmdb_id: 99, category: 'Bangla', year: 2024, type: 'movie' },
        { ...subject, tmdb_id: 99 }
      ),
      twoGenres: score({ id: 'a', category: 'Bangla', year: 2024, genres: ['Action', 'Thriller'], type: 'movie' }),
      oneGenre: score({ id: 'b', category: 'Bangla', year: 2024, genres: ['Action'], type: 'movie' }),
      noGenre: score({ id: 'c', category: 'Bangla', year: 2024, genres: [], type: 'movie' }),
      otherCategory: score({ id: 'd', category: 'Hindi', year: 2024, genres: ['Action'], type: 'movie' }),
      farYear: score({ id: 'e', category: 'Bangla', year: 1990, genres: [], type: 'movie' }),
      nearYear: score({ id: 'f', category: 'Mix', year: 2023, genres: [], type: 'movie' }),
      unrelated: score({ id: 'g', category: 'Hindi', year: 1970, genres: ['Comedy'], type: 'movie' }),
      series: score({ id: 'h', category: 'Bangla', year: 2024, genres: ['Action', 'Thriller'], type: 'series' }),
      inactive: score({ id: 'i', category: 'Bangla', year: 2024, genres: ['Action', 'Thriller'], type: 'movie', is_active: false }),
      metadataOnly: score({ id: 'j', category: 'Bangla', year: 2024, genres: ['Action'], type: 'movie', metadata_only: true }),
      // Server count must not move the needle in either direction.
      manyServers: score({ id: 'k', category: 'Bangla', year: 2024, genres: ['Action'], type: 'movie', available_link_count: 40 }),
      oneServer: score({ id: 'l', category: 'Bangla', year: 2024, genres: ['Action'], type: 'movie', available_link_count: 1 }),
      ratedHigh: score({ id: 'm', category: 'Bangla', year: 2024, genres: ['Action'], type: 'movie', rating: 9.5 }),
      ratedNone: score({ id: 'n', category: 'Bangla', year: 2024, genres: ['Action'], type: 'movie' })
    };
  });

  check(scoring.itself === -1, `[${label}] an item is never related to itself`, String(scoring.itself));
  check(scoring.subjectTmdb === -1,
    `[${label}] a duplicate external identity is excluded`, String(scoring.subjectTmdb));
  check(scoring.series === -1,
    `[${label}] a series is never offered as a related movie`, String(scoring.series));
  check(scoring.inactive === -1,
    `[${label}] a withdrawn title is never recommended`, String(scoring.inactive));
  check(scoring.metadataOnly === -1,
    `[${label}] an unplayable metadata-only record is never recommended`, String(scoring.metadataOnly));

  check(scoring.twoGenres > scoring.oneGenre && scoring.oneGenre > scoring.noGenre,
    `[${label}] shared genres are the strongest signal`,
    JSON.stringify({ two: scoring.twoGenres, one: scoring.oneGenre, none: scoring.noGenre }));
  check(scoring.oneGenre > scoring.otherCategory,
    `[${label}] same category outranks a different one`,
    JSON.stringify({ same: scoring.oneGenre, other: scoring.otherCategory }));
  check(scoring.nearYear > 0 && scoring.noGenre > scoring.farYear,
    `[${label}] a nearby release year counts, a distant one does not`,
    JSON.stringify({ near: scoring.nearYear, far: scoring.farYear }));
  check(scoring.unrelated === -1,
    `[${label}] something with nothing in common is not offered at all`, String(scoring.unrelated));

  check(scoring.manyServers === scoring.oneServer,
    `[${label}] server count is not popularity - 40 servers score exactly as 1`,
    JSON.stringify({ many: scoring.manyServers, one: scoring.oneServer }));
  check(scoring.ratedHigh > scoring.ratedNone
    && (scoring.ratedHigh - scoring.ratedNone) <= scoring.weights.ratingTieBreakMax + 1e-9,
    `[${label}] a real rating breaks ties and cannot outweigh a genre`,
    JSON.stringify({ rated: scoring.ratedHigh, unrated: scoring.ratedNone, cap: scoring.weights.ratingTieBreakMax }));
  check(scoring.weights.sharedGenre > scoring.weights.sameCategory
    && scoring.weights.sameCategory > scoring.weights.nearYear,
    `[${label}] the weights are ordered as the plan states`, JSON.stringify(scoring.weights));

  // --- against the real catalogue ------------------------------------------
  const real = await page.evaluate(async () => {
    const index = await window.loadMovieBrowseIndex();
    const subject = index.find((row) => (row.type || 'movie') === 'movie');
    if (!subject) return null;
    const related = await window.movieRelatedFor(subject);
    return {
      total: index.length,
      subject: { id: subject.id, name: subject.name, category: subject.category },
      ids: related.map((row) => row.id),
      categories: related.map((row) => row.category),
      types: related.map((row) => row.type || 'movie'),
      hasStreamFields: related.some((row) => row.url || row.backups || row.headers || row.playback_id || row.drm)
    };
  });

  check(Boolean(real) && real.total > 0, `[${label}] the browse index is available to score against`);
  if (real) {
    check(real.ids.length > 0 && real.ids.length <= 12,
      `[${label}] a real title gets a bounded related list`, JSON.stringify(real.ids.length));
    check(!real.ids.includes(real.subject.id),
      `[${label}] the subject is not in its own related list`);
    check(new Set(real.ids).size === real.ids.length,
      `[${label}] no duplicate appears in the list`, JSON.stringify(real.ids));
    check(real.types.every((type) => type === 'movie'),
      `[${label}] only movies are offered for a movie`, JSON.stringify(real.types));
    check(!real.hasStreamFields,
      `[${label}] related carries no stream URL, backup, header or playback id`);
    check(real.categories.filter((category) => category === real.subject.category).length > 0,
      `[${label}] the same language/category is actually preferred`,
      JSON.stringify({ subject: real.subject.category, got: real.categories }));
  }

  // --- on the detail panel --------------------------------------------------
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Bangla');
  await page.waitForTimeout(3000);
  const info = page.locator('#sidebarList .movie-card-info').first();
  if (await info.count()) {
    await info.click({ force: true });
    await page.waitForTimeout(2500);
    const detail = await page.evaluate(() => ({
      heading: document.querySelector('.movie-detail-panel .movie-related-title')?.textContent?.trim() || '',
      cards: document.querySelectorAll('.movie-detail-panel .movie-related-card').length,
      subject: document.querySelector('.movie-detail-title')?.textContent?.trim() || '',
      names: [...document.querySelectorAll('.movie-detail-panel .movie-related-card strong')]
        .map((node) => node.textContent.trim()),
      claimsAi: /\b(ai|personalis|personaliz|for you|because you)\b/i.test(
        document.querySelector('.movie-detail-panel')?.textContent || '')
    }));

    check(/You May Also Like|Related/i.test(detail.heading),
      `[${label}] the detail offers a related section`, detail.heading);
    check(detail.cards > 0 && detail.cards <= 12,
      `[${label}] it shows a bounded number of suggestions`, String(detail.cards));
    check(!detail.names.includes(detail.subject),
      `[${label}] the title being viewed is not suggested back`,
      JSON.stringify({ subject: detail.subject, names: detail.names.slice(0, 3) }));
    check(!detail.claimsAi,
      `[${label}] nothing claims personalisation or AI recommendation`);
  } else {
    check(false, `[${label}] a movie card with a Details control was available`);
  }

  // --- beside the player, while a movie plays -------------------------------
  // The detail hides the grid while it is open, so close it before reaching
  // for a card underneath.
  await page.locator('.movie-detail-close').first().click({ force: true }).catch(() => {});
  await page.waitForTimeout(1200);
  const playCard = page.locator('#sidebarList .movie-card').first();
  if (await playCard.count()) {
    await playCard.click({ force: true });
    let beside = { hidden: true, cards: 0, playing: '' };
    for (let attempt = 0; attempt < 15; attempt += 1) {
      await page.waitForTimeout(700);
      beside = await page.evaluate(() => ({
        hidden: Boolean(document.querySelector('#movieRelatedPanel')?.hidden),
        cards: document.querySelectorAll('#movieRelatedPanel .movie-related-card').length,
        playing: document.querySelector('#metaTitle')?.textContent?.trim() || '',
        grid: document.querySelectorAll('#sidebarList .movie-card').length
      }));
      if (!beside.hidden && beside.cards > 0) break;
    }
    check(!beside.hidden && beside.cards > 0,
      `[${label}] related movies appear beside the player once a movie starts`,
      JSON.stringify(beside));
    check(beside.grid > 0,
      `[${label}] the category grid is still there - related was added, not swapped in`,
      String(beside.grid));
  } else {
    check(false, `[${label}] a movie card was available to play`);
  }

  // --- a series episode keeps its own area ---------------------------------
  await clickNavButton(page, subNavSelector, '.final-sub-button', 'Web Series');
  await page.waitForTimeout(3000);
  const seriesCard = page.locator('#sidebarList .series-card').first();
  if (await seriesCard.count()) {
    await seriesCard.click({ force: true });
    await page.waitForTimeout(2500);
    const episode = page.locator('.series-episode-card:not([disabled])').first();
    if (await episode.count()) {
      await episode.click({ force: true });
      await page.waitForTimeout(4000);
      const duringEpisode = await page.evaluate(() => ({
        relatedHidden: Boolean(document.querySelector('#movieRelatedPanel')?.hidden),
        episodes: document.querySelectorAll('.series-episode-card').length,
        highlighted: document.querySelectorAll('.series-episode-card.active').length
      }));
      check(duringEpisode.relatedHidden,
        `[${label}] related does not displace episode navigation during a series`);
      check(duringEpisode.episodes > 0,
        `[${label}] the season's episodes still hold that area`, String(duringEpisode.episodes));
      check(duringEpisode.highlighted === 1,
        `[${label}] the current episode is still the highlighted one`, String(duringEpisode.highlighted));
    } else {
      check(false, `[${label}] a playable episode was available`);
    }
  } else {
    check(false, `[${label}] a series card was available`);
  }

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
