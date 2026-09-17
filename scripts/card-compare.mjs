/**
 * Movie card, element by element: the demo file against the live site.
 *
 * Usage: node scripts/card-compare.mjs <demoFileUrl|siteUrl> <out.json> [isSite]
 */
import { chromium } from 'playwright';
import fs from 'node:fs';

const target = process.argv[2];
const out = process.argv[3];
const isSite = process.argv[4] === 'site';

const MEASURE = `(() => {
  const pick = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return {
      w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10,
      pad: s.padding, radius: s.borderRadius, gap: s.gap,
      font: s.fontFamily.split(',')[0].replace(/["']/g, ''),
      size: s.fontSize, weight: s.fontWeight, lh: s.lineHeight,
      color: s.color, bg: s.backgroundColor, border: s.border,
      display: s.display, cols: s.gridTemplateColumns, ratio: s.aspectRatio,
      objectFit: s.objectFit, overflow: s.overflow,
    };
  };
  const q = (root, sels) => {
    for (const sel of sels.split(',')) {
      const el = (root || document).querySelector(sel.trim());
      if (el) return el;
    }
    return null;
  };
  const cardSel = '.media-card, .movie-card';
  const card = document.querySelector(cardSel);
  if (!card) return { error: 'no card found' };
  const row = card.closest('.cards-row, .movie-row-strip, .media-grid, .sidebar-list');
  return {
    row: pick(row),
    card: pick(card),
    poster: pick(q(card, '.card-poster-box, .poster-box, .movie-card-art, .movie-card-poster, img')),
    img: pick(card.querySelector('img')),
    meta: pick(q(card, '.card-meta, .movie-card-body, .movie-card-meta')),
    title: pick(q(card, '.related-movie-title, .card-title, .movie-card-title, h3, strong')),
    sub: pick(q(card, '.related-meta-sub, .card-sub, .movie-card-sub, small')),
    rule: pick(q(card, '.related-gold-line, .movie-card-rule')),
    badge: pick(q(card, '.badge-rank, .card-rank, .movie-card-rank')),
    cardCount: document.querySelectorAll(cardSel).length,
  };
})()`;

const browser = await chromium.launch();
const report = {};
for (const vp of [{ l: '1440', w: 1440, h: 900 }, { l: '390', w: 390, h: 844 }]) {
  const ctx = await browser.newContext({
    viewport: { width: vp.w, height: vp.h },
    isMobile: vp.w < 800, hasTouch: vp.w < 800,
  });
  const page = await ctx.newPage();
  await page.goto(target, { waitUntil: 'load', timeout: 60000 });
  await page.waitForTimeout(2500);
  if (isSite) {
    const nav = page.locator('.final-main-button:visible', { hasText: 'Movies' }).first();
    await nav.waitFor({ state: 'visible', timeout: 30000 });
    await nav.click();
    await page.waitForTimeout(3000);
  }
  report[vp.l] = await page.evaluate(MEASURE);
  await ctx.close();
}
await browser.close();
fs.writeFileSync(out, JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 1).slice(0, 4000));
