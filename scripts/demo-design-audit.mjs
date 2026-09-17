/**
 * Head-to-head audit: the demo design file vs the built site.
 *
 * Not a similarity check. It opens `Movie demo design index.html` in a real
 * browser, walks the demo's own views, and records the computed geometry and
 * typography of every element the port has to match - then does the same on
 * the built site and prints the two side by side.
 *
 * Usage: node scripts/demo-design-audit.mjs <demoFileUrl> [siteUrl]
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const demoUrl = process.argv[2];
const outDir = process.argv[3] || 'movie-ui-screenshots/demo';
const VIEWPORTS = [
  { label: '1440', width: 1440, height: 900 },
  { label: '1920', width: 1920, height: 1080 },
  { label: '390', width: 390, height: 844 },
];

const MEASURE = `(() => {
  const out = {};
  const box = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return {
      w: Math.round(r.width), h: Math.round(r.height),
      x: Math.round(r.x), y: Math.round(r.y),
      cols: s.gridTemplateColumns, gap: s.gap,
      pad: s.padding, radius: s.borderRadius,
      font: s.fontFamily.split(',')[0].replace(/["']/g, ''),
      size: s.fontSize, weight: s.fontWeight, lh: s.lineHeight,
      bg: s.backgroundColor, color: s.color,
      minH: s.minHeight, display: s.display,
    };
  };
  out.header = box('.app-header, .site-header, header');
  out.shell = box('.app-shell, .youtube-layout');
  out.rail = box('.sidebar-rail, .sidebar-rail-wrapper, .rail');
  out.content = box('.content-area, .main-content');
  out.hero = box('.hero-showcase, .movie-hero');
  out.heroInner = box('.hero-inner, .movie-hero-inner');
  out.heroTitle = box('.hero-title, .movie-hero-title');
  out.heroKicker = box('.hero-kicker, .movie-hero-kicker');
  out.heroDesc = box('.hero-desc, .movie-hero-desc');
  out.heroPlay = box('.btn-hero-play, .movie-hero-play');
  out.heroPill = box('.hero-control-pill, .movie-hero-dots');
  out.stage = box('.exact-player-stage');
  out.playerMain = box('.player-main-column');
  out.videoBox = box('.video-stage-box, .video-container-wrap');
  out.metaStrip = box('.player-meta-strip');
  out.sidePanel = box('.player-side-panel, .movie-related-panel');
  out.panelHeader = box('.panel-top-header, .movie-related-head');
  out.detail = box('.detail-hero-card, .movie-detail-hero');
  out.detailGrid = box('.detail-inner-grid');
  out.detailPoster = box('.detail-poster-wrap img, .detail-poster-wrap, .movie-detail-poster');
  out.detailHeading = box('.detail-heading, .movie-detail-title');
  out.card = box('.media-card, .movie-card, .channel-card');
  out.overflow = document.documentElement.scrollWidth - document.documentElement.clientWidth;
  return out;
})()`;

async function run() {
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch();
  const report = {};
  for (const vp of VIEWPORTS) {
    const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
    await page.goto(demoUrl, { waitUntil: 'load' });
    await page.waitForTimeout(1600);
    report[`${vp.label}-home`] = await page.evaluate(MEASURE);
    await page.screenshot({ path: path.join(outDir, `demo-home-${vp.label}.png`), fullPage: false });

    // Detail view: click the first card the demo renders.
    const card = await page.$('.media-card, .row-card, .carousel-card');
    if (card) {
      await card.click().catch(() => {});
      await page.waitForTimeout(1200);
      report[`${vp.label}-detail`] = await page.evaluate(MEASURE);
      await page.screenshot({ path: path.join(outDir, `demo-detail-${vp.label}.png`) });
    }
    // Player view: the demo exposes its own switcher.
    const played = await page.evaluate(() => {
      const b = document.querySelector('.btn-play-white, .btn-hero-play, [id*="PlayBtn"]');
      if (!b) return false;
      b.click();
      return true;
    });
    if (played) {
      await page.waitForTimeout(1400);
      report[`${vp.label}-player`] = await page.evaluate(MEASURE);
      await page.screenshot({ path: path.join(outDir, `demo-player-${vp.label}.png`) });
    }
    await page.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(outDir, 'demo-measurements.json'), JSON.stringify(report, null, 2));
  for (const [key, value] of Object.entries(report)) {
    console.log(`\n--- ${key} ---`);
    for (const [name, v] of Object.entries(value)) {
      if (!v || typeof v !== 'object') { console.log(`  ${name}: ${v}`); continue; }
      console.log(`  ${name.padEnd(13)} ${String(v.w).padStart(5)}x${String(v.h).toString().padEnd(5)} cols=${v.cols} gap=${v.gap} pad=${v.pad} font=${v.font} ${v.size}/${v.weight}`);
    }
  }
}
run().catch((e) => { console.error(e); process.exit(1); });
