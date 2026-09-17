/**
 * The header, and the category typography, across all three sections.
 *
 * The brief asks for one header on Live Sports, Live TV and Movies, and for
 * the movie rail's category type to be the reference the other two match.
 * Both are measured here rather than eyeballed, and the two sections'
 * LAYOUT is measured too, so a typography change that quietly moved a box
 * shows up as a failure.
 *
 * Usage: node scripts/header-parity-check.mjs [baseUrl]
 */
import { chromium } from 'playwright';
import fs from 'node:fs';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const SECTIONS = ['Live Sports', 'Live TV', 'Movies'];
const outDir = 'movie-ui-screenshots/site';

const MEASURE = `(() => {
  const el = document.querySelector('.app-header, .final-header, header');
  const nav = document.querySelector('.final-main-nav:not([hidden])');
  const pick = (n) => { if (!n) return null; const r = n.getBoundingClientRect(); const s = getComputedStyle(n);
    return { w: Math.round(r.width), h: Math.round(r.height), pad: s.padding, gap: s.gap, font: s.fontFamily.split(',')[0].replace(/["']/g,'') }; };
  const active = document.querySelector('.final-main-button.active');
  const cat = Array.from(document.querySelectorAll('.final-sub-button')).find((n) => n.offsetParent);
  const catStyle = cat ? getComputedStyle(cat) : null;
  return {
    header: pick(el),
    nav: pick(nav),
    activePill: active ? { text: active.textContent.trim(), radius: getComputedStyle(active).borderRadius } : null,
    navItems: Array.from(document.querySelectorAll('.final-main-button')).filter((n) => n.offsetParent).map((n) => n.textContent.trim()),
    category: catStyle ? {
      font: catStyle.fontFamily.split(',')[0].replace(/["']/g,''),
      size: catStyle.fontSize, weight: catStyle.fontWeight,
      lh: catStyle.lineHeight, color: catStyle.color,
      height: Math.round(cat.getBoundingClientRect().height),
      pad: catStyle.padding, radius: catStyle.borderRadius,
    } : null,
    shell: (() => { const n = document.querySelector('.youtube-layout.final-shell'); if (!n) return null;
      const s = getComputedStyle(n); return { cols: s.gridTemplateColumns, gap: s.gap, pad: s.padding }; })(),
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  };
})()`;

const browser = await chromium.launch();
const out = {};
for (const label of SECTIONS) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  const nav = page.locator('.final-main-button:visible', { hasText: label }).first();
  await nav.waitFor({ state: 'visible', timeout: 30000 });
  await nav.click();
  await page.waitForTimeout(2000);
  out[label] = await page.evaluate(MEASURE);
  await page.screenshot({ path: `${outDir}/header-${label.replace(/\s+/g, '-').toLowerCase()}-1440.png`, clip: { x: 0, y: 0, width: 1440, height: 120 } });
  await page.screenshot({ path: `${outDir}/section-${label.replace(/\s+/g, '-').toLowerCase()}-1440.png` });
  await page.close();
}
await browser.close();
fs.writeFileSync(`${outDir}/header-parity.json`, JSON.stringify(out, null, 2));

// The capsule's own width follows the active label - "Live Sports" is
// wider than "Movies" - so the comparison is the header box, the nav items
// and the pill shape, not the pixel width of a word.
const keyOf = (v) => JSON.stringify([v.header, v.activePill?.radius, v.navItems, v.nav?.h, v.nav?.gap]);
const keys = SECTIONS.map((s) => keyOf(out[s]));
const same = keys.every((k) => k === keys[0]);
const catKeys = SECTIONS.map((s) => JSON.stringify([out[s].category?.font, out[s].category?.size, out[s].category?.weight, out[s].category?.lh, out[s].category?.color]));
const catSame = catKeys.every((k) => k === catKeys[0]);

for (const s of SECTIONS) {
  const v = out[s];
  console.log(`\n--- ${s} ---`);
  console.log(`  header    ${v.header.w}x${v.header.h} pad=${v.header.pad} gap=${v.header.gap}`);
  console.log(`  nav       [${v.navItems.join(' | ')}]  active="${v.activePill?.text}" r=${v.activePill?.radius}`);
  console.log(`  category  ${v.category?.size}/${v.category?.weight} lh=${v.category?.lh} ${v.category?.color} h=${v.category?.height} pad=${v.category?.pad} r=${v.category?.radius}`);
  console.log(`  shell     cols=${v.shell?.cols} gap=${v.shell?.gap}`);
  console.log(`  overflow  ${v.overflow}`);
}
console.log(`\nheader identical across all three sections : ${same ? 'YES' : 'NO'}`);
console.log(`category typography identical             : ${catSame ? 'YES' : 'NO'}`);
process.exit(same && catSame ? 0 : 1);
