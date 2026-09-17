/**
 * The other half of scripts/demo-design-audit.mjs: the same measurements,
 * taken on the built site, so demo and implementation can be compared number
 * by number instead of by eye.
 *
 * Usage: node scripts/site-design-audit.mjs [baseUrl] [outDir]
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const outDir = process.argv[3] || 'movie-ui-screenshots/site';
const VIEWPORTS = [
  { label: '1440', width: 1440, height: 900 },
  { label: '1920', width: 1920, height: 1080 },
  { label: '390', width: 390, height: 844 },
];

const MEASURE = `(() => {
  const box = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return {
      w: Math.round(r.width), h: Math.round(r.height),
      x: Math.round(r.x), y: Math.round(r.y),
      cols: s.gridTemplateColumns, gap: s.gap, pad: s.padding,
      radius: s.borderRadius,
      font: s.fontFamily.split(',')[0].replace(/["']/g, ''),
      size: s.fontSize, weight: s.fontWeight, lh: s.lineHeight,
      bg: s.backgroundColor, color: s.color, minH: s.minHeight,
    };
  };
  const out = {};
  out.header = box('.final-header, .app-header, header');
  out.shell = box('.youtube-layout.final-shell, .app-shell');
  out.rail = box('.movie-rail, .sidebar-section.side-panel, .final-sidebar');
  out.content = box('.sidebar-section.main-panel, .content-area');
  out.hero = box('.movie-hero, .hero-showcase');
  out.heroInner = box('.movie-hero-inner, .hero-inner');
  out.heroTitle = box('.movie-hero-title, .hero-title');
  out.heroKicker = box('.movie-hero-kicker, .hero-kicker');
  out.heroDesc = box('.movie-hero-desc, .hero-desc');
  out.heroPlay = box('.movie-hero-play, .btn-hero-play');
  out.heroPill = box('.movie-hero-dots, .hero-control-pill');
  out.stage = box('.exact-player-stage');
  out.playerMain = box('.player-main-column');
  out.videoBox = box('.video-stage-box, .video-container-wrap');
  out.metaStrip = box('.player-meta-strip');
  out.sidePanel = box('.player-side-panel, .movie-related-panel');
  out.panelHeader = box('.panel-top-header, .movie-related-head');
  out.detail = box('.detail-hero-card, .movie-detail-hero');
  out.detailGrid = box('.detail-inner-grid');
  out.detailPoster = box('.detail-poster-wrap img, .detail-poster-wrap');
  out.detailHeading = box('.detail-heading, .movie-detail-title');
  out.card = box('.movie-card, .final-card, .channel-card');
  out.video = (() => { const v = document.querySelector('video'); if (!v) return null;
    const r = v.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) }; })();
  out.overflow = document.documentElement.scrollWidth - document.documentElement.clientWidth;
  return out;
})()`;

async function openMovies(page) {
  const nav = page.locator('.final-main-button:visible', { hasText: 'Movies' }).first();
  await nav.waitFor({ state: 'visible', timeout: 30000 });
  await nav.click();
  await page.waitForTimeout(1800);
}

const browser = await chromium.launch();
const report = {};
fs.mkdirSync(outDir, { recursive: true });

for (const vp of VIEWPORTS) {
  const context = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    isMobile: vp.width < 800, hasTouch: vp.width < 800,
  });
  const page = await context.newPage();
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await openMovies(page);
  report[`${vp.label}-home`] = await page.evaluate(MEASURE);
  await page.screenshot({ path: path.join(outDir, `site-home-${vp.label}.png`) });

  const card = page.locator('.movie-card:visible, .final-card:visible').first();
  if (await card.count()) {
    await card.click();
    await page.waitForTimeout(1500);
    report[`${vp.label}-detail`] = await page.evaluate(MEASURE);
    await page.screenshot({ path: path.join(outDir, `site-detail-${vp.label}.png`) });

    const play = page.locator('.btn-play-white:visible, .movie-detail-play:visible').first();
    if (await play.count()) {
      await play.click();
      await page.waitForTimeout(3000);
      report[`${vp.label}-player`] = await page.evaluate(MEASURE);
      await page.screenshot({ path: path.join(outDir, `site-player-${vp.label}.png`) });
    }
  }
  await context.close();
}
await browser.close();
fs.writeFileSync(path.join(outDir, 'site-measurements.json'), JSON.stringify(report, null, 2));
for (const [key, value] of Object.entries(report)) {
  console.log(`\n--- ${key} ---`);
  for (const [name, b] of Object.entries(value)) {
    if (!b || typeof b !== 'object') { console.log(`  ${name}: ${b}`); continue; }
    if (b.w === 0 && b.h === 0) continue;
    console.log(`  ${name.padEnd(13)} ${String(b.w).padStart(5)}x${String(b.h).padEnd(5)} cols=${b.cols} gap=${b.gap} pad=${b.pad} r=${b.radius} ${b.size || ''}/${b.weight || ''}`);
  }
}
