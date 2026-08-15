/**
 * Click TV Playback Telemetry Worker
 *
 * Routes:
 *   GET  /health
 *   POST /report
 *   GET  /summary      Authorization: Bearer <EXPORT_TOKEN>
 *   GET  /export       Authorization: Bearer <EXPORT_TOKEN>
 *
 * Required bindings:
 *   PLAYBACK_REPORTS   Cloudflare KV namespace
 *
 * Required variables/secrets:
 *   ALLOWED_ORIGINS    comma-separated website origins
 *   EXPORT_TOKEN       encrypted secret
 */

const MAX_BODY_BYTES = 16 * 1024;
const REPORT_TTL_SECONDS = 7 * 24 * 60 * 60;
const MAX_EXPORT_REPORTS = 5000;
const ALLOWED_RESULTS = new Set(["success", "failure"]);
const ALLOWED_FAILURES = new Set([
  "",
  "manifest_or_segment_403",
  "manifest_or_segment_404",
  "origin_530",
  "drm_error",
  "codec_or_mse_unsupported",
  "startup_timeout",
  "network_stall",
  "autoplay_blocked",
  "unknown",
]);

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/health") {
      return json({
        ok: true,
        service: "click-tv-playback-telemetry",
        kv_bound: Boolean(env.PLAYBACK_REPORTS),
        timestamp: Date.now(),
      }, 200, corsHeaders(request, env));
    }

    if (request.method === "OPTIONS") {
      if (!originAllowed(request, env)) return text("Origin not allowed", 403, {});
      return new Response(null, { status: 204, headers: corsHeaders(request, env) });
    }

    if (url.pathname === "/report" && request.method === "POST") {
      if (!originAllowed(request, env)) return text("Origin not allowed", 403, {});
      if (!env.PLAYBACK_REPORTS) return text("KV binding missing", 503, corsHeaders(request, env));
      return handleReport(request, env);
    }

    if ((url.pathname === "/summary" || url.pathname === "/export") && request.method === "GET") {
      if (!authorized(request, env)) return text("Unauthorized", 401, {});
      if (!env.PLAYBACK_REPORTS) return text("KV binding missing", 503, {});
      const reports = await readReports(env, Number(url.searchParams.get("limit") || MAX_EXPORT_REPORTS));
      if (url.pathname === "/export") return json({ count: reports.length, reports }, 200, noStoreHeaders());
      return json(buildSummary(reports), 200, noStoreHeaders());
    }

    return text("Not found", 404, corsHeaders(request, env));
  },
};

async function handleReport(request, env) {
  const size = Number(request.headers.get("content-length") || 0);
  if (size > MAX_BODY_BYTES) return text("Payload too large", 413, corsHeaders(request, env));

  let raw;
  try {
    const body = await request.text();
    if (body.length > MAX_BODY_BYTES) return text("Payload too large", 413, corsHeaders(request, env));
    raw = JSON.parse(body);
  } catch (_) {
    return text("Invalid JSON", 400, corsHeaders(request, env));
  }

  const report = sanitizeReport(raw);
  if (!report.item_id || !ALLOWED_RESULTS.has(report.result)) {
    return text("Invalid report", 400, corsHeaders(request, env));
  }

  const minuteBucket = Math.floor(report.ts / 60000);
  const dedupeKey = [
    "report",
    minuteBucket,
    safeKey(report.session_id || "anonymous"),
    safeKey(report.item_id),
    safeKey(report.source_id || "source"),
    safeKey(report.route || "route"),
    safeKey(report.result),
  ].join(":");

  await env.PLAYBACK_REPORTS.put(dedupeKey, JSON.stringify(report), {
    expirationTtl: REPORT_TTL_SECONDS,
  });

  return json({ ok: true }, 202, corsHeaders(request, env));
}

function sanitizeReport(raw) {
  const result = stringValue(raw?.result, 16).toLowerCase();
  let failureClass = stringValue(raw?.failure_class, 64).toLowerCase();
  if (!ALLOWED_FAILURES.has(failureClass)) failureClass = "unknown";

  return {
    item_id: stringValue(raw?.item_id, 160),
    source_id: stringValue(raw?.source_id, 120),
    source_index: numberValue(raw?.source_index, 0, 5),
    route: stringValue(raw?.route, 16),
    proxy_name: stringValue(raw?.proxy_name, 64),
    stream_type: stringValue(raw?.stream_type, 16),
    result,
    failure_class: result === "success" ? "" : failureClass,
    startup_ms: numberValue(raw?.startup_ms, 0, 120000),
    device_class: stringValue(raw?.device_class, 24),
    network_mode: stringValue(raw?.network_mode, 24),
    session_id: stringValue(raw?.session_id, 64),
    ts: numberValue(raw?.ts, Date.now() - 86400000, Date.now() + 300000) || Date.now(),
  };
}

async function readReports(env, requestedLimit) {
  const limit = Math.max(1, Math.min(MAX_EXPORT_REPORTS, requestedLimit || MAX_EXPORT_REPORTS));
  const reports = [];
  let cursor;

  do {
    const listed = await env.PLAYBACK_REPORTS.list({ prefix: "report:", cursor, limit: Math.min(1000, limit - reports.length) });
    for (const key of listed.keys) {
      const value = await env.PLAYBACK_REPORTS.get(key.name, "json");
      if (value) reports.push(value);
      if (reports.length >= limit) break;
    }
    cursor = listed.list_complete ? undefined : listed.cursor;
  } while (cursor && reports.length < limit);

  return reports.sort((a, b) => Number(b.ts || 0) - Number(a.ts || 0));
}

function buildSummary(reports) {
  const byItem = new Map();

  for (const report of reports) {
    const key = report.item_id || "unknown";
    const current = byItem.get(key) || {
      item_id: key,
      reports: 0,
      successes: 0,
      failures: 0,
      sessions: new Set(),
      failure_classes: {},
      routes: {},
      last_success_at: 0,
      last_failure_at: 0,
      startup_total_ms: 0,
      startup_samples: 0,
    };

    current.reports += 1;
    if (report.session_id) current.sessions.add(report.session_id);
    current.routes[report.route || "unknown"] = (current.routes[report.route || "unknown"] || 0) + 1;
    if (Number(report.startup_ms || 0) > 0) {
      current.startup_total_ms += Number(report.startup_ms);
      current.startup_samples += 1;
    }

    if (report.result === "success") {
      current.successes += 1;
      current.last_success_at = Math.max(current.last_success_at, Number(report.ts || 0));
    } else {
      current.failures += 1;
      current.last_failure_at = Math.max(current.last_failure_at, Number(report.ts || 0));
      const failure = report.failure_class || "unknown";
      current.failure_classes[failure] = (current.failure_classes[failure] || 0) + 1;
    }

    byItem.set(key, current);
  }

  const items = [...byItem.values()].map((item) => {
    const sessionCount = item.sessions.size;
    const failureRate = item.reports ? item.failures / item.reports : 0;
    const suspectedDead = sessionCount >= 5 && failureRate >= 0.7 && item.last_success_at < Date.now() - 30 * 60 * 1000;
    return {
      item_id: item.item_id,
      reports: item.reports,
      sessions: sessionCount,
      successes: item.successes,
      failures: item.failures,
      failure_rate: Number(failureRate.toFixed(4)),
      suspected_dead: suspectedDead,
      failure_classes: item.failure_classes,
      routes: item.routes,
      average_startup_ms: item.startup_samples ? Math.round(item.startup_total_ms / item.startup_samples) : 0,
      last_success_at: item.last_success_at,
      last_failure_at: item.last_failure_at,
    };
  }).sort((a, b) => Number(b.suspected_dead) - Number(a.suspected_dead) || b.failure_rate - a.failure_rate || b.reports - a.reports);

  return {
    generated_at: new Date().toISOString(),
    total_reports: reports.length,
    suspected_dead_count: items.filter((item) => item.suspected_dead).length,
    items,
  };
}

function originAllowed(request, env) {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  const allowed = csv(env.ALLOWED_ORIGINS);
  return allowed.includes("*") || allowed.includes(origin);
}

function authorized(request, env) {
  const token = String(env.EXPORT_TOKEN || "");
  if (!token) return false;
  const authorization = request.headers.get("authorization") || "";
  return timingSafeEqual(authorization, `Bearer ${token}`);
}

function corsHeaders(request, env) {
  const headers = new Headers();
  const origin = request.headers.get("origin") || "";
  const allowed = csv(env.ALLOWED_ORIGINS);
  if (allowed.includes("*")) headers.set("Access-Control-Allow-Origin", "*");
  else if (origin && allowed.includes(origin)) {
    headers.set("Access-Control-Allow-Origin", origin);
    headers.set("Vary", "Origin");
  }
  headers.set("Access-Control-Allow-Methods", "POST, GET, OPTIONS");
  headers.set("Access-Control-Allow-Headers", "Content-Type, Authorization");
  headers.set("Access-Control-Max-Age", "86400");
  headers.set("X-Content-Type-Options", "nosniff");
  return headers;
}

function noStoreHeaders() {
  const headers = new Headers();
  headers.set("Cache-Control", "no-store");
  headers.set("X-Content-Type-Options", "nosniff");
  return headers;
}

function csv(value) {
  return String(value || "").split(",").map((entry) => entry.trim()).filter(Boolean);
}

function stringValue(value, maxLength) {
  return String(value ?? "").replace(/[\u0000-\u001f\u007f]/g, "").trim().slice(0, maxLength);
}

function numberValue(value, min, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(min, Math.min(max, number));
}

function safeKey(value) {
  return String(value || "unknown").replace(/[^a-zA-Z0-9._-]/g, "-").slice(0, 120) || "unknown";
}

function timingSafeEqual(left, right) {
  const a = String(left);
  const b = String(right);
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let index = 0; index < a.length; index += 1) difference |= a.charCodeAt(index) ^ b.charCodeAt(index);
  return difference === 0;
}

function json(payload, status, headers) {
  const next = new Headers(headers);
  next.set("Content-Type", "application/json; charset=utf-8");
  next.set("Cache-Control", "no-store");
  return new Response(JSON.stringify(payload, null, 2), { status, headers: next });
}

function text(value, status, headers) {
  const next = new Headers(headers);
  next.set("Content-Type", "text/plain; charset=utf-8");
  next.set("Cache-Control", "no-store");
  return new Response(value, { status, headers: next });
}
