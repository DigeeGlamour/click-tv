/**
 * The demo and the site, side by side in a real browser, element by element.
 *
 * Reading two stylesheets and believing they agree is how the card, the
 * detail and the player panel each drifted from the approved design without
 * anyone seeing it: the values were ported from the demo's FIRST definition
 * of a rule while the demo's own final block overrode it further down, and
 * four `!important` rules in three other sheets decided the outcome anyway.
 *
 * So this opens `Movie demo design index.html` and the running site in the
 * same Chromium, drives both to the same screen - Home, then a detail, then
 * playback - and prints the computed geometry and type of each matching
 * element next to each other. What it prints is what the browser drew, not
 * what the CSS says.
 *
 * Usage: node scripts/demo-parity-check.mjs [siteUrl] [width]
 */
import { chromium } from 'playwright';
import { pathToFileURL } from 'url';

const DEMO = pathToFileURL('C:/Users/RUMAN/Downloads/Movie Final Plan implementation/Movie demo design index.html').href;
const SITE = process.argv[2] || 'http://127.0.0.1:4173/';
const WIDTH = Number(process.argv[3] || 1440);

const PROPS = ['fontFamily','fontSize','fontWeight','lineHeight','color','backgroundColor',
  'borderWidth','borderColor','borderRadius','padding','margin','gap','display',
  'gridTemplateColumns','textAlign','letterSpacing','objectFit','aspectRatio','flexDirection','justifyContent','alignItems','width','height','overflow','position'];

function measure([sel, props]) {
  const el = document.querySelector(sel);
  if (!el) return null;
  const cs = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  const out = { _w: Math.round(r.width * 10) / 10, _h: Math.round(r.height * 10) / 10 };
  for (const p of props) out[p] = cs[p];
  return out;
}


const HOME = {
  hero: ['.hero-showcase', '.movie-hero'],
  heroBg: ['.hero-bg-layer.active', '.movie-hero-bg'],
  heroOverlay: ['.hero-bg-overlay', '.movie-hero-scrim'],
  heroInner: ['.hero-inner', '.movie-hero-inner'],
  heroKicker: ['.hero-badge-live', '.movie-hero-kicker'],
  heroTitle: ['.hero-title', '.movie-hero-title'],
  heroMeta: ['.hero-meta-row', '.movie-hero-meta'],
  heroDesc: ['.hero-desc', '.movie-hero-desc'],
  heroActions: ['.hero-actions', '.movie-hero-actions'],
  heroPlay: ['.btn-hero-play', '.movie-hero-play'],
  statusStrip: ['.status-strip', '.movie-status-strip'],
  statusCard: ['.status-card', '.movie-status-card'],
  statusIcon: ['.status-icon-wrap', '.movie-status-icon'],
  statusTitle: ['.status-info h3', '.movie-status-card h3'],
  statusDesc: ['.status-info p', '.movie-status-card p'],
  secHeader: ['.sec-header', '.movie-row-head'],
  secTitle: ['.sec-title-group h2', '.movie-row-head h2'],
  secDesc: ['.sec-title-group p', '.movie-row-head p'],
  secAll: ['.btn-see-all', '.movie-row-all'],
  secArrows: ['.carousel-arrows', '.movie-row-arrows'],
  cardsRow: ['.cards-row', '.movie-row-strip'],
  rail: ['.sidebar-rail', '.desktop-category-rail'],
  railCaption: ['.group-caption', '.movie-rail-caption'],
  railItem: ['.cat-item', '#desktopSubNav.movie-rail .final-sub-button'],
  railIcon: ['.cat-icon-box', '.movie-rail-icon'],
  railLabel: ['.cat-label', '#desktopSubNav.movie-rail .final-sub-button'],
  contentArea: ['.content-area', '.sidebar-scroll-area'],
};

const DETAIL = {
  navBar: ['.detail-nav-bar', '.detail-nav-bar'],
  backPill: ['.btn-back-pill', '.btn-back-pill'],
  heroCard: ['.detail-hero-card', '.detail-hero-card'],
  backdrop: ['.detail-backdrop', '.detail-backdrop'],
  innerGrid: ['.detail-inner-grid', '.detail-inner-grid'],
  posterWrap: ['.detail-poster-wrap', '.detail-poster-wrap'],
  posterImg: ['.detail-poster-wrap img', '.detail-poster-wrap img'],
  info: ['.detail-info', '.detail-info'],
  typePill: ['.detail-type-pill', '.detail-type-pill'],
  heading: ['.detail-heading', '.detail-heading'],
  metaRow: ['.detail-meta-row', '.detail-meta-row'],
  desc: ['.detail-desc', '.detail-desc'],
  actions: ['.detail-actions-row', '.detail-actions-row'],
  playBtn: ['.btn-play-white', '.btn-play-white'],
  bookmarkBtn: ['.btn-bookmark-dark', '.btn-bookmark-dark'],
  serverBox: ['.server-card-box', '.server-card-box'],
  serverTitle: ['.server-box-title', '.server-box-title'],
  serverPill: ['.server-pill-btn', '.server-pill-btn'],
  ratingPill: ['.detail-rating-pill', '.movie-detail-rating'],
  healthBadge: ['.badge-health', '.badge-health'],
};

const PANEL = {
  panel: ['.player-side-panel', '#movieRelatedPanel'],
  head: ['.panel-top-header', '.movie-related-head'],
  panelTitle: ['#playerSidePanelTitle', '.movie-related-title'],
  countBadge: ['.panel-counter-badge', '.movie-related-count'],
  panelDesc: ['.panel-top-desc', '.movie-related-desc'],
  closeBtn: ['.btn-player-close-stage', '.movie-related-back'],
  body: ['.panel-scroll-body', '.movie-related-body'],
  grid: ['.player-related-grid', '.movie-related-grid'],
  card: ['.related-movie-card', '.movie-related-card'],
  posterBox: ['.related-poster-box', '.movie-related-poster-box'],
  posterImg: ['.related-poster-box img', '.movie-related-poster-box img'],
  rank: ['.related-movie-card .badge-rank', '.movie-related-rank'],
  rating: ['.related-movie-card .badge-rating-pill', '.movie-related-card .badge-rating-pill'],
  sub: ['.related-movie-card .related-meta-sub', '.movie-related-sub'],
  name: ['.related-movie-card .related-movie-title', '.movie-related-name'],
  rule: ['.related-movie-card .related-gold-line', '.movie-related-rule'],
  playBtn: ['.btn-play-suggest', '.movie-related-play'],
};

async function grab(page, map, side) {
  const out = {};
  for (const [role, sels] of Object.entries(map)) {
    out[role] = await page.evaluate(measure, [sels[side], PROPS]);
  }
  return out;
}

const browser = await chromium.launch();

// ---------- DEMO ----------
const demoCtx = await browser.newContext({ viewport: { width: WIDTH, height: 950 } });
const demoPage = await demoCtx.newPage();
await demoPage.goto(DEMO, { waitUntil: 'load' });
await demoPage.waitForTimeout(1200);
const demoHome = await grab(demoPage, HOME, 0);
await demoPage.locator('.media-card').first().click();
await demoPage.waitForTimeout(700);
const demoDetail = await grab(demoPage, DETAIL, 0);
await demoPage.locator('#detailPlayAction').click();
await demoPage.waitForTimeout(900);
const demoPanel = await grab(demoPage, PANEL, 0);

// ---------- SITE ----------
const siteCtx = await browser.newContext({ viewport: { width: WIDTH, height: 950 } });
const sitePage = await siteCtx.newPage();
await sitePage.goto(SITE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await sitePage.waitForTimeout(3000);
await sitePage.locator('.final-main-button:visible', { hasText: 'Movies' }).first().click();
await sitePage.waitForTimeout(6000);
const siteHome = await grab(sitePage, HOME, 1);
await sitePage.locator('.movie-card:visible').first().click();
await sitePage.waitForTimeout(3000);
const siteDetail = await grab(sitePage, DETAIL, 1);
const play = sitePage.locator('.movie-detail-play:visible').first();
let sitePanel = null;
if (await play.count()) {
  await play.click();
  await sitePage.waitForTimeout(9000);
  sitePanel = await grab(sitePage, PANEL, 1);
}

await browser.close();

function diff(label, a, b, map, side) {
  console.log(`\n================ ${label} ================`);
  for (const role of Object.keys(map)) {
    const d = a[role], s = b ? b[role] : null;
    if (!d && !s) { console.log(`  --    ${role}: absent on BOTH`); continue; }
    if (!d) { console.log(`  SITE-ONLY  ${role}`); continue; }
    if (!s) { console.log(`  MISSING ON SITE  ${role}  (demo ${d._w}x${d._h} ${d.fontSize} ${d.color})`); continue; }
    const bad = [];
    for (const k of Object.keys(d)) {
      if (String(d[k]) !== String(s[k])) bad.push(`${k}: demo=${d[k]} | site=${s[k]}`);
    }
    if (!bad.length) console.log(`  OK    ${role}`);
    else { console.log(`  DIFF  ${role}`); bad.forEach(x => console.log(`          ${x}`)); }
  }
}

diff(`HOME @${WIDTH}`, demoHome, siteHome, HOME);
diff(`DETAIL @${WIDTH}`, demoDetail, siteDetail, DETAIL);
diff(`PLAYER PANEL @${WIDTH}`, demoPanel, sitePanel, PANEL);
