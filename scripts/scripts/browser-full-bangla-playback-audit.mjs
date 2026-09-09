import { chromium } from 'playwright';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

const baseUrl = process.argv[2] || 'http://127.0.0.1:4173';
const timeoutMs = Number(process.argv[3] || 12000);
const concurrency = Math.max(1, Math.min(12, Number(process.argv[4] || 8)));
const inputReportPath = process.argv[5] ? path.resolve(process.argv[5]) : '';
const scope = String(process.argv[6] || 'all').toLowerCase();
const root = process.cwd();
const confirmationSuffix = scope === 'channel' || scope === 'movie' ? `-${scope}` : '';
const defaultReportName = inputReportPath
  ? `browser-full-bangla-confirmation${confirmationSuffix}.json`
  : 'browser-full-bangla-playback.json';
const reportName = process.env.CLICKTV_AUDIT_REPORT || defaultReportName;
const reportPath = path.join(root, 'reports', path.basename(reportName));

async function loadJson(filePath) {
  return JSON.parse(await readFile(filePath, 'utf8'));
}

async function loadItems() {
  const channelPayload = await loadJson(path.join(root, 'data', 'channels', 'bangla.json'));
  const channels = (channelPayload.channels || []).map((item) => ({ kind: 'channel', item }));
  const index = await loadJson(path.join(root, 'data', 'movies', 'bangla', 'index.json'));
  const movies = [];
  for (const page of index.pages || []) {
    const payload = await loadJson(path.join(root, String(page.path || '')));
    for (const item of payload.items || []) movies.push({ kind: 'movie', item });
  }
  return [...channels, ...movies];
}

async function auditOne(page, record) {
  const name = String(record.item.name || record.item.title || '').trim();
  const started = Date.now();
  const plan = await page.evaluate(({ item, kind }) => window.__clickTvRuntimeTest.startAuditPlayback(item, kind), record);
  if (!plan.playable || plan.attemptCount < 1) {
    return { kind: record.kind, name, ok: false, reason: 'no_player_attempt', plan, elapsedMs: Date.now() - started };
  }
  try {
    await page.waitForFunction(() => {
      const video = document.getElementById('videoPlayer');
      return Boolean(video && video.readyState >= 2 && video.videoWidth > 0 && video.currentTime > 0.2);
    }, null, { timeout: timeoutMs });
    const media = await page.evaluate(() => {
      const video = document.getElementById('videoPlayer');
      return { readyState: video.readyState, currentTime: video.currentTime, width: video.videoWidth, height: video.videoHeight };
    });
    return { kind: record.kind, name, ok: true, elapsedMs: Date.now() - started, ...media };
  } catch (_) {
    const detail = await page.evaluate(() => {
      const video = document.getElementById('videoPlayer');
      return {
        readyState: video?.readyState || 0,
        currentTime: video?.currentTime || 0,
        mediaError: video?.error?.message || '',
        session: window.__clickTvRuntimeTest.playbackSessionSnapshot(),
      };
    });
    return { kind: record.kind, name, ok: false, reason: 'no_decoded_frame', elapsedMs: Date.now() - started, ...detail };
  }
}

let items = await loadItems();
if (inputReportPath) {
  const previous = await loadJson(inputReportPath);
  const failedKeys = new Set((previous.results || [])
    .filter((entry) => !entry.ok && (scope === 'all' || entry.kind === scope))
    .map((entry) => `${entry.kind}\u0000${entry.name}`));
  items = items.filter((record) => failedKeys.has(`${record.kind}\u0000${String(record.item.name || record.item.title || '').trim()}`));
}
const browser = await chromium.launch({
  headless: true,
  args: ['--autoplay-policy=no-user-gesture-required', '--disable-web-security'],
});
const context = await browser.newContext({
  viewport: { width: 1280, height: 720 },
  extraHTTPHeaders: { Origin: 'https://clicktv.pages.dev' },
});
let cursor = 0;
let completed = 0;
const results = [];

try {
  const workers = Array.from({ length: Math.min(concurrency, items.length) }, async (_, workerIndex) => {
    let page = null;
    if (!inputReportPath) {
      page = await context.newPage();
      await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForFunction(() => Boolean(window.__clickTvRuntimeTest?.startAuditPlayback), null, { timeout: 30000 });
    }
    while (true) {
      const index = cursor;
      cursor += 1;
      if (index >= items.length) break;
      if (inputReportPath) {
        page = await context.newPage();
        await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
        await page.waitForFunction(() => Boolean(window.__clickTvRuntimeTest?.startAuditPlayback), null, { timeout: 30000 });
      }
      const result = await auditOne(page, items[index]);
      results[index] = result;
      if (inputReportPath) {
        await page.close();
        page = null;
      }
      completed += 1;
      if (completed % 10 === 0 || completed === items.length) {
        const failed = results.filter((entry) => entry && !entry.ok).length;
        console.log(`AUDIT_PROGRESS ${completed}/${items.length} failed=${failed} worker=${workerIndex + 1}`);
      }
    }
    if (page) await page.close();
  });
  await Promise.all(workers);
} finally {
  await browser.close();
}

const report = {
  generatedAt: new Date().toISOString(),
  baseUrl,
  timeoutMs,
  concurrency,
  total: results.length,
  channels: results.filter((entry) => entry.kind === 'channel').length,
  movies: results.filter((entry) => entry.kind === 'movie').length,
  passed: results.filter((entry) => entry.ok).length,
  failed: results.filter((entry) => !entry.ok).length,
  results,
};
await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
console.log(`AUDIT_COMPLETE total=${report.total} passed=${report.passed} failed=${report.failed} report=${reportPath}`);
if (report.failed) process.exitCode = 1;
