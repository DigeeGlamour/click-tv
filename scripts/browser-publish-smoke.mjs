import fs from 'node:fs';
import crypto from 'node:crypto';
import { chromium } from 'playwright-core';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const mode = String(process.argv[3] || 'channels').toLowerCase();
const reportPath = process.argv[4] || 'reports/browser-playback-smoke.json';
const statePath = 'state/browser-playback-cache.json';
const overallBudgetMs = Math.max(30000, Number(process.env.BROWSER_SMOKE_BUDGET_MS || 95000));
const channelLimit = Math.max(1, Number(process.env.BROWSER_SMOKE_CHANNEL_LIMIT || 4));
const movieLimit = Math.max(1, Number(process.env.BROWSER_SMOKE_MOVIE_LIMIT || 3));
const startedAt = Date.now();

const channelTabs = ['bangla', 'sports-channel', 'indian', 'cartoon', 'islamic', 'infotainments', 'foreign-news', 'others'];
const movieTabs = ['movie:bangla', 'movie:hindi', 'movie:english', 'movie:dubbed', 'movie:south-indian', 'movie:premium', 'movie:mix'];

function readJson(path, fallback) {
  try { return JSON.parse(fs.readFileSync(path, 'utf8')); } catch (_) { return fallback; }
}

function chromePath() {
  const candidates = [
    process.env.CHROME_PATH,
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
  ].filter(Boolean);
  return candidates.find((candidate) => fs.existsSync(candidate));
}

function fingerprint(value) {
  return crypto.createHash('sha256').update(String(value || '')).digest('hex').slice(0, 24);
}

function cacheKey(kind, item) {
  return `${kind}:${String(item.id || item.name || '').trim().toLowerCase()}:${fingerprint(item.playbackKey)}`;
}

function boundedCandidates(items, kind, limit, cache) {
  const now = Date.now();
  const passTtl = 7 * 24 * 60 * 60 * 1000;
  const failTtl = 12 * 60 * 60 * 1000;
  return items
    .filter((item) => item.playable && item.attemptCount > 0 && !item.navigatesToSeries)
    .map((item) => {
      const key = cacheKey(kind, item);
      const prior = cache.records?.[key];
      const ttl = prior?.status === 'passed' ? passTtl : failTtl;
      const stale = !prior || now - Number(prior.checked_at_ms || 0) >= ttl;
      const reportedPriority = kind === 'channel' && ['jamuna tv', 'channel 24'].includes(String(item.name || '').trim().toLowerCase());
      return { ...item, key, stale, reportedPriority, priorAt: Number(prior?.checked_at_ms || 0) };
    })
    .sort((left, right) => Number(right.reportedPriority) - Number(left.reportedPriority) || Number(right.stale) - Number(left.stale) || left.priorAt - right.priorAt || left.index - right.index)
    .slice(0, limit);
}

async function selectTab(page, kind, key) {
  if (kind === 'channel') {
    const mainKey = key === 'sports-channel' ? 'sports' : 'live-tv';
    await page.locator(`#desktopMainNav [data-final-key="${mainKey}"]`).click();
    await page.locator(`#desktopSubNav [data-final-key="${key}"]`).click();
  } else {
    await page.locator('#desktopMainNav [data-final-key="movies"]').click();
    await page.locator(`#desktopSubNav [data-final-key="${key}"]`).click();
  }
  await page.waitForFunction(() => window.__clickTvRuntimeTest?.currentItemsPlaybackAudit?.().length > 0, null, { timeout: 20000 });
}

async function verifyItem(page, item, kind) {
  const itemStartedAt = Date.now();
  const perItemMs = kind === 'movie' ? 22000 : 14000;
  const initial = await page.evaluate(() => window.__clickTvRuntimeTest.mediaSnapshot().currentTime);
  const started = await page.evaluate((index) => window.__clickTvRuntimeTest.startPlaybackByIndex(index), item.index);
  if (!started) return { status: 'failed', reason: 'runtime_rejected_item', elapsed_ms: Date.now() - itemStartedAt };

  try {
    await page.waitForFunction(({ initialTime }) => {
      const snapshot = window.__clickTvRuntimeTest?.mediaSnapshot?.();
      if (!snapshot || snapshot.errorCode) return false;
      return snapshot.sessionSuccess && snapshot.readyState >= 2 && snapshot.currentTime > initialTime + 0.45;
    }, { initialTime: initial }, { timeout: Math.min(perItemMs, Math.max(1000, overallBudgetMs - (Date.now() - startedAt))) });
    const media = await page.evaluate(() => window.__clickTvRuntimeTest.mediaSnapshot());
    return { status: 'passed', reason: 'media_time_progressed', elapsed_ms: Date.now() - itemStartedAt, attempts: media.attemptsRun };
  } catch (_) {
    const media = await page.evaluate(() => window.__clickTvRuntimeTest.mediaSnapshot());
    return {
      status: 'failed',
      reason: media.errorCode ? `media_error_${media.errorCode}` : 'no_media_time_progress',
      elapsed_ms: Date.now() - itemStartedAt,
      attempts: media.attemptsRun,
    };
  }
}

const cache = readJson(statePath, { schema_version: 1, channel_cursor: 0, movie_cursor: 0, records: {} });
cache.records ||= {};
const report = {
  schema_version: 1,
  generated_at: new Date().toISOString(),
  mode,
  budget_ms: overallBudgetMs,
  exhaustive: false,
  note: 'Bounded real-Chromium playback progression check; cache rotates coverage across scans.',
  checks: [],
};

const executablePath = chromePath();
if (!executablePath) throw new Error('Installed Chrome/Chromium executable was not found');
const browser = await chromium.launch({ executablePath, headless: true, args: ['--autoplay-policy=no-user-gesture-required'] });

try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 }, serviceWorkers: 'block' });
  const page = await context.newPage();
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(() => Boolean(window.__clickTvRuntimeTest), null, { timeout: 30000 });

  const jobs = [];
  if (['channels', 'all'].includes(mode)) {
    const key = channelTabs[Number(cache.channel_cursor || 0) % channelTabs.length];
    jobs.push({ kind: 'channel', key, limit: channelLimit });
    cache.channel_cursor = (Number(cache.channel_cursor || 0) + 1) % channelTabs.length;
  }
  if (['movies', 'all'].includes(mode)) {
    const key = movieTabs[Number(cache.movie_cursor || 0) % movieTabs.length];
    jobs.push({ kind: 'movie', key, limit: movieLimit });
    cache.movie_cursor = (Number(cache.movie_cursor || 0) + 1) % movieTabs.length;
  }

  for (const job of jobs) {
    await selectTab(page, job.kind, job.key);
    const items = await page.evaluate(() => window.__clickTvRuntimeTest.currentItemsPlaybackAudit());
    const selected = boundedCandidates(items, job.kind, job.limit, cache);
    for (const item of selected) {
      if (Date.now() - startedAt >= overallBudgetMs - 1000) break;
      const result = await verifyItem(page, item, job.kind);
      const checkedAt = Date.now();
      cache.records[item.key] = { status: result.status, checked_at_ms: checkedAt, reason: result.reason };
      report.checks.push({
        kind: job.kind,
        category_key: job.key,
        id: item.id,
        name: item.name,
        fingerprint: fingerprint(item.playbackKey),
        ...result,
      });
    }
  }
  await context.close();
} finally {
  await browser.close();
}

cache.generated_at = new Date().toISOString();
cache.records = Object.fromEntries(Object.entries(cache.records)
  .sort((left, right) => Number(right[1]?.checked_at_ms || 0) - Number(left[1]?.checked_at_ms || 0))
  .slice(0, 2500));
report.elapsed_ms = Date.now() - startedAt;
report.passed = report.checks.filter((item) => item.status === 'passed').length;
report.failed = report.checks.filter((item) => item.status === 'failed').length;
fs.writeFileSync(statePath, `${JSON.stringify(cache, null, 2)}\n`);
fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
console.log(`Browser smoke completed in ${report.elapsed_ms}ms: ${report.passed} passed, ${report.failed} failed`);
