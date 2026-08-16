/**
 * Click TV Playback Proxy Worker
 *
 * Deploy this same file unchanged to Playback Proxy 1-4.
 * Viewer-facing routes:
 *   GET  /health
 *   GET  /hls?id=ctv_... (source resolved from the public Pages catalogue)
 *   GET  /hls?url=...&type=hls|dash|media|mpegts|key|subtitle&profile=...&inherit=0|1
 *   GET  /drm?id=ctv_... (site-only normalized DRM bootstrap)
 *   POST /license?id=ctv_... (Widevine/PlayReady/FairPlay license relay)
 *   GET  /certificate?id=ctv_... (FairPlay server certificate relay)
 *   HEAD /hls?url=...&type=media&profile=...
 *   OPTIONS /hls
 *
 * No Cloudflare variables, secrets or KV bindings are required. The scanner
 * publishes the exact verified URL, request headers and DRM data to GitHub /
 * Pages in data/playback-sources.json. This is intentionally public.
 */

const DEFAULT_VERSION = "5.3.2";
const SITE_ORIGIN = "https://clicktv.pages.dev";
// Every origin the site is genuinely served from. Each Today Match card is
// proxy_only - the raw URL is deliberately absent - so an origin missing from
// this list does not degrade playback, it removes it entirely. Add a custom
// domain here the moment it starts serving the site.
const ALLOWED_ORIGINS = Object.freeze([
  SITE_ORIGIN,
  "https://www.clicktv.pages.dev",
]);

// Only the production origin used to be accepted, so a Cloudflare Pages preview
// deploy and a local `python -m http.server` copy of the site both got HTTP 403
// on every stream. That made it impossible to reproduce a playback bug anywhere
// except production. Previews live on the same pages.dev project and loopback is
// not reachable by a third party, so both are safe to allow; nothing else is.
const PAGES_PREVIEW_SUFFIX = ".clicktv.pages.dev";
const LOCAL_ORIGIN_PATTERN = /^http:\/\/(?:localhost|127\.0\.0\.1|\[::1\])(?::\d{1,5})?$/i;

function isAllowedOrigin(origin) {
  const value = String(origin || "").trim();
  if (!value) return false;
  if (ALLOWED_ORIGINS.includes("*") || ALLOWED_ORIGINS.includes(value)) return true;
  if (LOCAL_ORIGIN_PATTERN.test(value)) return true;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname.endsWith(PAGES_PREVIEW_SUFFIX);
  } catch (_) {
    return false;
  }
}
const ALLOWED_HOSTS_URL = `${SITE_ORIGIN}/data/allowed-hosts.json`;
const PLAYBACK_CATALOG_URL = `${SITE_ORIGIN}/data/playback-sources.json`;
// Deliberately public. This only detects accidental/tampered child URLs.
const CHILD_SIGNING_KEY = "click-tv-public-child-link-v5-20260809";
const HOST_CACHE_MS = 5 * 60 * 1000;
// The catalogue is now one small shard per id prefix instead of a single file
// holding every record, so a miss costs one ~70 KB fetch rather than re-parsing
// megabytes. A live HLS player re-requests its manifest every few seconds; at
// the old 60s TTL that meant reloading the whole catalogue mid-playback, which
// is what exhausted the Worker CPU budget and stalled streams.
const CATALOG_CACHE_MS = 5 * 60 * 1000;
const CATALOG_SHARD_URL = (shard) => `${SITE_ORIGIN}/data/playback/${shard}.json`;

function catalogShardFor(playbackId) {
  // Must match scanner/playback_profiles.py:catalog_shard_for exactly.
  let text = String(playbackId || "").trim().toLowerCase();
  if (text.startsWith("ctv_")) text = text.slice(4);
  const prefix = text.slice(0, 2);
  return /^[0-9a-f]{2}$/.test(prefix) ? prefix : "00";
}
const MAX_REDIRECTS = 5;
const MAX_TEXT_BYTES = 2 * 1024 * 1024;
const CHILD_LINK_TTL_SECONDS = 6 * 60 * 60;

let hostCache = {
  expiresAt: 0,
  hosts: new Set(),
};

let playbackCatalogCache = {
  expiresAt: 0,
  records: null,
};

// One entry per shard: shard name -> { expiresAt, records }.
const playbackShardCache = new Map();

const TEXT_TYPES = new Set(["hls", "dash", "subtitle"]);
const STREAM_TYPES = new Set([
  "hls",
  "dash",
  "media",
  "mpegts",
  "key",
  "subtitle",
]);

const PRIVATE_IPV4_RANGES = [
  ["0.0.0.0", 8],
  ["10.0.0.0", 8],
  ["100.64.0.0", 10],
  ["127.0.0.0", 8],
  ["169.254.0.0", 16],
  ["172.16.0.0", 12],
  ["192.0.0.0", 24],
  ["192.0.2.0", 24],
  ["192.168.0.0", 16],
  ["198.18.0.0", 15],
  ["198.51.100.0", 24],
  ["203.0.113.0", 24],
  ["224.0.0.0", 4],
  ["240.0.0.0", 4],
];

const HEADER_PROFILES = Object.freeze({
  default: {
    "User-Agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
      "AppleWebKit/537.36 (KHTML, like Gecko) " +
      "Chrome/151.0.0.0 Safari/537.36",
    Accept: "*/*",
  },
  android_tv: {
    "User-Agent":
      "Mozilla/5.0 (Linux; Android 10; BRAVIA 4K UR3) " +
      "AppleWebKit/537.36 (KHTML, like Gecko) " +
      "Chrome/122.0.0.0 Safari/537.36",
    Accept: "*/*",
  },
  android_chrome: {
    "User-Agent":
      "Mozilla/5.0 (Linux; Android 14; SM-S928B) " +
      "AppleWebKit/537.36 (KHTML, like Gecko) " +
      "Chrome/151.0.0.0 Mobile Safari/537.36",
    Accept: "*/*",
  },
  iphone_safari: {
    "User-Agent":
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) " +
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 " +
      "Mobile/15E148 Safari/604.1",
    Accept: "*/*",
  },
  windows_edge: {
    "User-Agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
      "AppleWebKit/537.36 (KHTML, like Gecko) " +
      "Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0",
    Accept: "*/*",
  },
  macos_safari: {
    "User-Agent":
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) " +
      "AppleWebKit/605.1.15 (KHTML, like Gecko) " +
      "Version/17.4 Safari/605.1.15",
    Accept: "*/*",
  },
  streame_center: {
    "User-Agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
      "AppleWebKit/537.36 (KHTML, like Gecko) " +
      "Chrome/151.0.0.0 Safari/537.36",
    Referer: "https://streame.center/",
    Origin: "https://streame.center",
    Accept: "*/*",
  },
  fibwatch: {
    "User-Agent":
      "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36",
    Referer: "https://fibwatch.art/",
    Origin: "https://fibwatch.art",
    Accept: "*/*",
  },
  crichd: {
    "User-Agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
      "AppleWebKit/537.36 (KHTML, like Gecko) " +
      "Chrome/151.0.0.0 Safari/537.36",
    Referer: "https://executeandship.com/",
    Origin: "https://executeandship.com",
    Accept: "*/*",
  },
  toffee_okhttp: {
    // The Android app profile normally sends neither browser Origin nor
    // Referer. Injecting them can make some Toffee CDN edges reject requests.
    "User-Agent": "okhttp/4.12.0",
    Accept: "*/*",
  },
  toffee: {
    "User-Agent": "Toffee (Android; 11; Mobile)",
    Referer: "https://toffeelive.com/",
    Origin: "https://toffeelive.com",
    Accept: "*/*",
  },
  fancode: {
    "User-Agent":
      "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36",
    Referer: "https://www.fancode.com/",
    Origin: "https://www.fancode.com",
    Accept: "*/*",
  },
  gpcdn: {
    "User-Agent":
      "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36",
    Referer: "https://gpcdn.net/",
    Origin: "https://gpcdn.net",
    Accept: "*/*",
  },
  akamai: {
    "User-Agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
      "AppleWebKit/537.36 (KHTML, like Gecko) " +
      "Chrome/151.0.0.0 Safari/537.36",
    Accept: "*/*",
  },
});

export default {
  async fetch(request, env, ctx) {
    const startedAt = Date.now();
    const requestUrl = new URL(request.url);
    const proxyName = proxyNameFromHostname(requestUrl.hostname);
    const proxyVersion = DEFAULT_VERSION;

    if (requestUrl.pathname === "/health") {
      return jsonResponse(
        {
          ok: true,
          service: "click-tv-playback-proxy",
          role: "playback",
          name: proxyName,
          version: proxyVersion,
          protected_playback: true,
          configuration_storage: "git_pages_json",
          playback_catalog_url: PLAYBACK_CATALOG_URL,
          dashboard_configuration_required: false,
          timestamp: Date.now(),
        },
        200,
        corsHeaders(request, env),
      );
    }

    if (!new Set(["/hls", "/drm", "/license", "/certificate"]).has(requestUrl.pathname)) {
      return textResponse("Not found", 404, corsHeaders(request, env));
    }

    if (request.method === "OPTIONS") {
      const originCheck = checkOrigin(request, env);
      if (!originCheck.ok) {
        return textResponse("Origin not allowed", 403, {});
      }
      return new Response(null, {
        status: 204,
        headers: corsHeaders(request, env),
      });
    }

    const methodAllowed = (
      (requestUrl.pathname === "/license" && request.method === "POST") ||
      (requestUrl.pathname === "/certificate" && request.method === "GET") ||
      (new Set(["/hls", "/drm"]).has(requestUrl.pathname) && new Set(["GET", "HEAD"]).has(request.method))
    );
    if (!methodAllowed) {
      return textResponse(
        "Method not allowed",
        405,
        corsHeaders(request, env),
      );
    }

    const originCheck = checkOrigin(request, env);
    if (!originCheck.ok) {
      return textResponse("Origin not allowed", 403, {});
    }

    try {
      const initialPlaybackId = normalizePlaybackId(requestUrl.searchParams.get("id"));
      const childPlaybackId = normalizePlaybackId(requestUrl.searchParams.get("pid"));
      const playbackId = initialPlaybackId || childPlaybackId;
      let playbackProfile = null;
      if (playbackId) {
        playbackProfile = await loadPlaybackProfile(playbackId, env);
        if (!playbackProfile) {
          return textResponse("Protected playback profile not found", 404, corsHeaders(request, env));
        }
      }

      if (requestUrl.pathname === "/drm") {
        if (!initialPlaybackId || childPlaybackId || !request.headers.get("Origin")) {
          return textResponse("DRM request not allowed", 403, corsHeaders(request, env));
        }
        const drm = playbackProfile?.drm;
        if (!drm || typeof drm !== "object" || !Object.keys(drm).length) {
          return textResponse("DRM profile not found", 404, corsHeaders(request, env));
        }
        const headers = corsHeaders(request, env);
        headers.set("Cache-Control", "no-store");
        return jsonResponse({ drm }, 200, headers);
      }

      if (requestUrl.pathname === "/license") {
        if (!initialPlaybackId || childPlaybackId || !request.headers.get("Origin")) {
          return textResponse("License request not allowed", 403, corsHeaders(request, env));
        }
        return proxyDrmLicense({ request, playbackProfile, env });
      }

      if (requestUrl.pathname === "/certificate") {
        if (!initialPlaybackId || childPlaybackId || !request.headers.get("Origin")) {
          return textResponse("Certificate request not allowed", 403, corsHeaders(request, env));
        }
        return proxyDrmCertificate({ request, playbackProfile, env });
      }

      // A child link carries the exact variant or segment URL, that hop's own
      // type, and a signature minted over both; pid= rides along only so the
      // Worker can recover this stream's headers and DRM. Letting the profile
      // win here meant every child request was answered with the parent
      // manifest again, re-signed as HLS - the player fetched the same
      // playlist forever instead of media, then gave up with "every available
      // link was tried". The initial id= request carries no url= of its own
      // and still resolves entirely from the profile.
      const isChildRequest = Boolean(childPlaybackId);
      const requestedUrlParam = requestUrl.searchParams.get("url");
      const targetText = String(
        (isChildRequest && requestedUrlParam) || playbackProfile?.url || requestedUrlParam || ""
      );
      if (!targetText) {
        return textResponse(
          "Missing url",
          400,
          corsHeaders(request, env),
        );
      }

      const targetUrl = parseSafeHttpUrl(targetText);
      const requestedType = normalizeStreamType(
        isChildRequest
          ? requestUrl.searchParams.get("type") || playbackProfile?.stream_type
          : playbackProfile?.stream_type || requestUrl.searchParams.get("type"),
        targetUrl,
      );
      const profileName = normalizeProfileName(
        playbackProfile?.header_profile || requestUrl.searchParams.get("profile"),
      );
      const inheritQuery = Boolean(
        playbackProfile?.inherit_manifest_query || requestUrl.searchParams.get("inherit") === "1"
      );
      const signature = requestUrl.searchParams.get("sig") || "";
      const expires = Number(requestUrl.searchParams.get("exp") || 0);
      const signedChild = await validateChildSignature({
        targetUrl,
        requestedType,
        profileName,
        inheritQuery,
        signature,
        expires,
        playbackId,
        secret: CHILD_SIGNING_KEY,
      });

      if (childPlaybackId && !signedChild) {
        const profileHost = new URL(playbackProfile.url).hostname;
        const sameTrustedHost = normalizeHostname(targetUrl.hostname) === normalizeHostname(profileHost);
        if (!sameTrustedHost || !request.headers.get("Origin")) {
          return textResponse("Invalid protected child signature", 403, corsHeaders(request, env));
        }
      }

      if (initialPlaybackId && !request.headers.get("Origin")) {
        return textResponse("Protected playback requires an allowed site origin", 403, corsHeaders(request, env));
      }

      if (!playbackId && !signedChild) {
        if (!request.headers.get("Origin")) {
          return textResponse("Playback requires the Click TV site origin", 403, corsHeaders(request, env));
        }
        const allowed = await isInitialHostAllowed(targetUrl.hostname, env);
        if (!allowed) {
          return textResponse(
            "Target host not allowed",
            403,
            corsHeaders(request, env),
          );
        }
      }

      const result = await proxyTarget({
        request,
        requestUrl,
        targetUrl,
        requestedType,
        profileName,
        inheritQuery,
        playbackId,
        credentialHeaders: playbackProfile?.headers || {},
        protectedSource: Boolean(playbackProfile),
        env,
        ctx,
        startedAt,
      });

      result.headers.set("X-Proxy-Name", proxyName);
      result.headers.set("X-Proxy-Version", proxyVersion);
      result.headers.set(
        "X-Proxy-Time-Ms",
        String(Math.max(0, Date.now() - startedAt)),
      );
      return result;
    } catch (error) {
      const message = safeErrorMessage(error);
      const headers = corsHeaders(request, env);
      headers.set("X-Proxy-Name", proxyName);
      headers.set("X-Proxy-Version", proxyVersion);
      headers.set("X-Proxy-Time-Ms", String(Date.now() - startedAt));
      return textResponse(`Playback proxy error: ${message}`, 502, headers);
    }
  },
};

async function proxyTarget({
  request,
  requestUrl,
  targetUrl,
  requestedType,
  profileName,
  inheritQuery,
  playbackId,
  credentialHeaders,
  protectedSource,
  env,
  ctx,
}) {
  const upstreamHeaders = buildUpstreamHeaders(
    request,
    profileName,
    requestedType,
    env,
    credentialHeaders,
  );
  const upstream = await fetchWithValidatedRedirects(
    targetUrl,
    {
      method: request.method,
      headers: upstreamHeaders,
    },
    requestedType,
    protectedSource,
  );

  const finalUrl = upstream.finalUrl;
  const response = upstream.response;
  const detectedType = detectResponseType(
    requestedType,
    finalUrl,
    response.headers.get("content-type") || "",
  );

  if (request.method === "HEAD") {
    const headers = buildDownstreamHeaders(
      request,
      env,
      response,
      detectedType,
      finalUrl,
    );
    return new Response(null, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  }

  if (detectedType === "hls") {
    return rewriteHlsResponse({
      request,
      requestUrl,
      response,
      finalUrl,
      profileName,
      inheritQuery,
      playbackId,
      protectedSource,
      env,
    });
  }

  if (detectedType === "dash") {
    return rewriteDashResponse({
      request,
      requestUrl,
      response,
      finalUrl,
      profileName,
      inheritQuery,
      playbackId,
      protectedSource,
      env,
    });
  }

  const headers = buildDownstreamHeaders(
    request,
    env,
    response,
    detectedType,
    finalUrl,
  );
  headers.set(
    "Cache-Control",
    protectedSource
      ? "no-store"
      : detectedType === "media" || detectedType === "mpegts"
      ? "public, max-age=60, s-maxage=300"
      : "public, max-age=300, s-maxage=1800",
  );
  headers.set("X-Cache-Status", "BYPASS");

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

function normalizedDrmType(drm) {
  const value = String(drm?.type || drm?.scheme || drm?.license_type || "").trim().toLowerCase();
  if (value.includes("widevine") || value === "com.widevine.alpha") return "widevine";
  if (value.includes("playready") || value.includes("microsoft")) return "playready";
  if (value.includes("fairplay") || value.includes("apple.fps") || value.includes("com.apple")) return "fairplay";
  if (value.includes("clearkey") || value.includes("clear_key")) return "clearkey";
  return value ? "unknown" : "";
}

function applyExactHeaders(headers, rawHeaders) {
  if (!rawHeaders || typeof rawHeaders !== "object") return;
  const blocked = new Set([
    "host", "connection", "content-length", "cf-connecting-ip", "cf-ipcountry",
    "cf-ray", "x-forwarded-for", "x-forwarded-proto", "transfer-encoding",
  ]);
  for (const [name, value] of Object.entries(rawHeaders)) {
    const cleanName = String(name || "").trim();
    const cleanValue = String(value || "").trim();
    if (!cleanName || !cleanValue || blocked.has(cleanName.toLowerCase())) continue;
    if (/\r|\n/.test(cleanName) || /\r|\n/.test(cleanValue)) continue;
    headers.set(cleanName, cleanValue);
  }
}

async function proxyDrmLicense({ request, playbackProfile, env }) {
  const drm = playbackProfile?.drm;
  const drmType = normalizedDrmType(drm);
  if (!new Set(["widevine", "playready", "fairplay"]).has(drmType)) {
    return textResponse("Unsupported DRM license type", 422, corsHeaders(request, env));
  }
  const target = parseSafeHttpUrl(drm?.license_url || drm?.license_server || "");
  if (!(await isInitialHostAllowed(target.hostname, env))) {
    return textResponse("License host not allowed", 403, corsHeaders(request, env));
  }
  const declaredLength = Number(request.headers.get("Content-Length") || 0);
  if (declaredLength > 2 * 1024 * 1024) {
    return textResponse("License request too large", 413, corsHeaders(request, env));
  }
  const body = await request.arrayBuffer();
  if (body.byteLength > 2 * 1024 * 1024) {
    return textResponse("License request too large", 413, corsHeaders(request, env));
  }
  const headers = buildUpstreamHeaders(
    request,
    playbackProfile?.header_profile || "",
    "license",
    env,
    playbackProfile?.headers || {},
  );
  applyExactHeaders(headers, drm?.license_headers || {});
  const requestContentType = request.headers.get("Content-Type");
  if (requestContentType && !headers.has("Content-Type")) headers.set("Content-Type", requestContentType);
  headers.set("Accept-Encoding", "identity");
  headers.set("Cache-Control", "no-store");

  const upstream = await fetchWithValidatedRedirects(
    target,
    { method: "POST", headers, body },
    "license",
    true,
  );
  const responseHeaders = corsHeaders(request, env);
  responseHeaders.set("Content-Type", upstream.response.headers.get("Content-Type") || "application/octet-stream");
  responseHeaders.set("Cache-Control", "no-store, no-cache, must-revalidate");
  responseHeaders.set("X-Upstream-Status", String(upstream.response.status));
  return new Response(upstream.response.body, {
    status: upstream.response.status,
    statusText: upstream.response.statusText,
    headers: responseHeaders,
  });
}

async function proxyDrmCertificate({ request, playbackProfile, env }) {
  const drm = playbackProfile?.drm;
  if (normalizedDrmType(drm) !== "fairplay") {
    return textResponse("FairPlay certificate not configured", 422, corsHeaders(request, env));
  }
  const target = parseSafeHttpUrl(drm?.certificate_url || "");
  if (!(await isInitialHostAllowed(target.hostname, env))) {
    return textResponse("Certificate host not allowed", 403, corsHeaders(request, env));
  }
  const headers = buildUpstreamHeaders(
    request,
    playbackProfile?.header_profile || "",
    "certificate",
    env,
    playbackProfile?.headers || {},
  );
  applyExactHeaders(headers, drm?.certificate_headers || {});
  const upstream = await fetchWithValidatedRedirects(
    target,
    { method: "GET", headers },
    "certificate",
    true,
  );
  const responseHeaders = corsHeaders(request, env);
  responseHeaders.set("Content-Type", upstream.response.headers.get("Content-Type") || "application/octet-stream");
  responseHeaders.set("Cache-Control", "no-store, no-cache, must-revalidate");
  responseHeaders.set("X-Upstream-Status", String(upstream.response.status));
  return new Response(upstream.response.body, {
    status: upstream.response.status,
    statusText: upstream.response.statusText,
    headers: responseHeaders,
  });
}

async function rewriteHlsResponse({
  request,
  requestUrl,
  response,
  finalUrl,
  profileName,
  inheritQuery,
  playbackId,
  protectedSource,
  env,
}) {
  if (!response.ok) {
    const headers = buildDownstreamHeaders(
      request,
      env,
      response,
      "hls",
      finalUrl,
    );
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  }

  const text = await readTextWithLimit(response, MAX_TEXT_BYTES);
  if (!/^\s*#EXTM3U/m.test(text)) {
    throw new Error("Upstream response is not a valid HLS manifest");
  }

  const lines = text.replace(/\r/g, "").split("\n");
  const rewritten = [];

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      rewritten.push(rawLine);
      continue;
    }

    if (line.startsWith("#")) {
      rewritten.push(
        await rewriteHlsTagUris({
          line: rawLine,
          baseUrl: finalUrl,
          proxyOrigin: requestUrl.origin,
          profileName,
          inheritQuery,
          playbackId,
          env,
        }),
      );
      continue;
    }

    const absolute = resolveChildUrl(line, finalUrl, inheritQuery);
    const childType = guessTypeFromUrl(absolute, "media");
    rewritten.push(
      await buildSignedProxyUrl({
        proxyOrigin: requestUrl.origin,
        targetUrl: absolute,
        type: childType,
        profileName,
        inheritQuery,
        playbackId,
        env,
      }),
    );
  }

  const headers = buildDownstreamHeaders(
    request,
    env,
    response,
    "hls",
    finalUrl,
  );
  headers.set("Content-Type", "application/vnd.apple.mpegurl; charset=utf-8");
  headers.set("Cache-Control", "no-store, no-cache, must-revalidate");
  if (protectedSource) headers.set("Cache-Control", "no-store");
  headers.delete("Content-Length");
  headers.delete("Content-Encoding");
  headers.set("X-Cache-Status", "BYPASS");

  return new Response(rewritten.join("\n"), {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

async function rewriteHlsTagUris({
  line,
  baseUrl,
  proxyOrigin,
  profileName,
  inheritQuery,
  playbackId,
  env,
}) {
  const uriPattern = /URI=("([^"]*)"|'([^']*)'|([^,\s]*))/gi;
  const matches = [...line.matchAll(uriPattern)];
  if (!matches.length) return line;

  let output = "";
  let cursor = 0;
  for (const match of matches) {
    const rawValue = match[2] ?? match[3] ?? match[4] ?? "";
    if (!rawValue) continue;
    const absolute = resolveChildUrl(rawValue, baseUrl, inheritQuery);
    const type = guessTypeFromUrl(
      absolute,
      line.toUpperCase().startsWith("#EXT-X-KEY") ? "key" : "media",
    );
    const rewrittenUrl = await buildSignedProxyUrl({
      proxyOrigin,
      targetUrl: absolute,
      type,
      profileName,
      inheritQuery,
      playbackId,
      env,
    });
    const quote = match[2] !== undefined ? '"' : match[3] !== undefined ? "'" : "";
    const replacement = `URI=${quote}${rewrittenUrl}${quote}`;
    output += line.slice(cursor, match.index) + replacement;
    cursor = match.index + match[0].length;
  }
  output += line.slice(cursor);
  return output;
}

async function rewriteDashResponse({
  request,
  requestUrl,
  response,
  finalUrl,
  profileName,
  inheritQuery,
  playbackId,
  protectedSource,
  env,
}) {
  if (!response.ok) {
    const headers = buildDownstreamHeaders(
      request,
      env,
      response,
      "dash",
      finalUrl,
    );
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  }

  let xml = await readTextWithLimit(response, MAX_TEXT_BYTES);
  if (!/<MPD[\s>]/i.test(xml)) {
    throw new Error("Upstream response is not a valid DASH MPD");
  }

  xml = await replaceAsync(
    xml,
    /<BaseURL(?:\s[^>]*)?>([\s\S]*?)<\/BaseURL>/gi,
    async (full, rawValue) => {
      const decoded = decodeXml(String(rawValue).trim());
      if (!decoded) return full;
      const absolute = resolveChildUrl(decoded, finalUrl, inheritQuery);
      const proxied = await buildSignedProxyUrl({
        proxyOrigin: requestUrl.origin,
        targetUrl: absolute,
        type: guessTypeFromUrl(absolute, "media"),
        profileName,
        inheritQuery,
        playbackId,
        env,
      });
      return `<BaseURL>${escapeXml(proxied)}</BaseURL>`;
    },
  );

  xml = await replaceAsync(
    xml,
    /\b(media|initialization|sourceURL|index|href)=("([^"]+)"|'([^']+)')/gi,
    async (full, attrName, quoted, doubleValue, singleValue) => {
      const rawValue = doubleValue ?? singleValue ?? "";
      if (!rawValue || rawValue.startsWith("urn:") || rawValue.startsWith("data:")) {
        return full;
      }
      const absolute = resolveChildUrl(decodeXml(rawValue), finalUrl, inheritQuery);
      const type = attrName.toLowerCase() === "href"
        ? guessTypeFromUrl(absolute, "dash")
        : guessTypeFromUrl(absolute, "media");
      const proxied = await buildSignedProxyUrl({
        proxyOrigin: requestUrl.origin,
        targetUrl: absolute,
        type,
        profileName,
        inheritQuery,
        playbackId,
        env,
      });
      const quote = quoted[0];
      return `${attrName}=${quote}${escapeXml(proxied)}${quote}`;
    },
  );

  const headers = buildDownstreamHeaders(
    request,
    env,
    response,
    "dash",
    finalUrl,
  );
  headers.set("Content-Type", "application/dash+xml; charset=utf-8");
  headers.set("Cache-Control", "no-store, no-cache, must-revalidate");
  if (protectedSource) headers.set("Cache-Control", "no-store");
  headers.delete("Content-Length");
  headers.delete("Content-Encoding");
  headers.set("X-Cache-Status", "BYPASS");

  return new Response(xml, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

async function buildSignedProxyUrl({
  proxyOrigin,
  targetUrl,
  type,
  profileName,
  inheritQuery,
  playbackId,
  env,
}) {
  const expires = Math.floor(Date.now() / 1000) + CHILD_LINK_TTL_SECONDS;
  const normalizedType = normalizeStreamType(type, targetUrl);
  const normalizedProfile = normalizeProfileName(profileName);
  const targetText = targetUrl.toString();
  const hasDashTemplate = containsDashTemplateToken(targetText);

  // DASH SegmentTemplate URLs change after the MPD is parsed. A signature made
  // for "$Number$" cannot validate after Shaka replaces it with an actual
  // segment number. These template children therefore use the normal dynamic
  // host allowlist instead of a stale signature.
  const signature = hasDashTemplate
    ? ""
    : await createChildSignature({
        targetUrl,
        requestedType: normalizedType,
        profileName: normalizedProfile,
        inheritQuery,
        playbackId,
        expires,
        secret: CHILD_SIGNING_KEY,
      });

  const output = new URL("/hls", proxyOrigin);
  output.searchParams.set("url", targetText);
  output.searchParams.set("type", normalizedType);
  if (normalizedProfile !== "default") {
    output.searchParams.set("profile", normalizedProfile);
  }
  if (inheritQuery) output.searchParams.set("inherit", "1");
  if (signature) {
    output.searchParams.set("exp", String(expires));
    output.searchParams.set("sig", signature);
  }
  if (playbackId) output.searchParams.set("pid", playbackId);

  return restoreDashTemplateTokens(output.toString());
}

function containsDashTemplateToken(value) {
  return /\$(?:RepresentationID|Bandwidth|Time|Number(?:%0\d+d)?)\$/i.test(
    String(value || ""),
  );
}

function restoreDashTemplateTokens(value) {
  return String(value).replace(
    /%24(RepresentationID|Bandwidth|Time|Number(?:%25\d+d)?)%24/gi,
    (_, token) => `$${String(token).replace(/%25/gi, "%")}$`,
  );
}

function resolveChildUrl(rawValue, baseUrl, inheritQuery) {
  const child = parseSafeHttpUrl(new URL(rawValue, baseUrl).toString());
  if (inheritQuery) {
    const parent = new URL(baseUrl);
    for (const [key, value] of parent.searchParams.entries()) {
      if (!child.searchParams.has(key)) child.searchParams.append(key, value);
    }
  }
  return child;
}

async function fetchWithValidatedRedirects(
  initialUrl,
  init,
  requestedType,
  protectedSource = false,
) {
  let currentUrl = initialUrl;
  for (let redirectCount = 0; redirectCount <= MAX_REDIRECTS; redirectCount += 1) {
    validateTargetUrl(currentUrl);
    const response = await fetch(currentUrl.toString(), {
      ...init,
      redirect: "manual",
      cf: cloudflareFetchOptions(requestedType, init.headers, protectedSource),
    });

    if (![301, 302, 303, 307, 308].includes(response.status)) {
      return { response, finalUrl: currentUrl };
    }

    const location = response.headers.get("location");
    if (!location) return { response, finalUrl: currentUrl };
    if (redirectCount >= MAX_REDIRECTS) {
      throw new Error("Too many upstream redirects");
    }
    currentUrl = parseSafeHttpUrl(new URL(location, currentUrl).toString());
  }
  throw new Error("Redirect loop");
}

function cloudflareFetchOptions(type, headers, protectedSource = false) {
  if (protectedSource) return { cacheEverything: false, cacheTtl: 0 };
  const hasRange = headers instanceof Headers && headers.has("Range");
  if (type === "hls" || type === "dash" || hasRange) {
    return { cacheEverything: false, cacheTtl: 0 };
  }
  if (type === "key" || type === "subtitle") {
    return { cacheEverything: true, cacheTtl: 300 };
  }
  return { cacheEverything: true, cacheTtl: 300 };
}

function buildUpstreamHeaders(request, profileName, type, env, credentialHeaders = {}) {
  const normalizedProfile = normalizeProfileName(profileName);
  const profile = HEADER_PROFILES[normalizedProfile] || HEADER_PROFILES.default;
  const headers = new Headers();
  for (const [name, value] of Object.entries(profile)) {
    headers.set(name, value);
  }

  applyExactHeaders(headers, credentialHeaders);

  const range = request.headers.get("Range");
  if (range) headers.set("Range", range);

  const acceptLanguage = request.headers.get("Accept-Language");
  if (acceptLanguage) headers.set("Accept-Language", acceptLanguage);

  headers.set("Accept-Encoding", "identity");
  headers.set("Cache-Control", type === "hls" || type === "dash" ? "no-cache" : "no-cache");
  return headers;
}

function buildDownstreamHeaders(
  request,
  env,
  upstream,
  type,
  finalUrl,
) {
  const headers = corsHeaders(request, env);
  const passthrough = [
    "Content-Type",
    "Content-Length",
    "Content-Range",
    "Accept-Ranges",
    "ETag",
    "Last-Modified",
    "Date",
  ];
  for (const name of passthrough) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  headers.set("X-Upstream-Status", String(upstream.status));
  headers.set("X-Upstream-Host", finalUrl.hostname);
  headers.set("X-Stream-Type", type);
  return headers;
}

function corsHeaders(request, env) {
  const headers = new Headers();
  const origin = request.headers.get("Origin") || "";
  if (ALLOWED_ORIGINS.includes("*")) {
    headers.set("Access-Control-Allow-Origin", "*");
  } else if (origin && isAllowedOrigin(origin)) {
    headers.set("Access-Control-Allow-Origin", origin);
    headers.set("Vary", "Origin");
  } else if (!origin) {
    headers.set("Access-Control-Allow-Origin", SITE_ORIGIN);
  } else {
    headers.set("Access-Control-Allow-Origin", SITE_ORIGIN);
    headers.set("Vary", "Origin");
  }
  headers.set("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS");
  headers.set(
    "Access-Control-Allow-Headers",
    "Range, Content-Type, Accept, Accept-Language",
  );
  headers.set(
    "Access-Control-Expose-Headers",
    "Content-Length, Content-Range, Accept-Ranges, Content-Type, " +
      "X-Proxy-Name, X-Proxy-Version, X-Upstream-Status, " +
      "X-Proxy-Time-Ms, X-Cache-Status, X-Stream-Type",
  );
  headers.set("Access-Control-Max-Age", "86400");
  headers.set("Cross-Origin-Resource-Policy", "cross-origin");
  headers.set("X-Content-Type-Options", "nosniff");
  return headers;
}

function checkOrigin(request, env) {
  const origin = request.headers.get("Origin");
  if (!origin) return { ok: true };
  if (ALLOWED_ORIGINS.includes("*") || isAllowedOrigin(origin)) return { ok: true };
  return { ok: false };
}

async function isInitialHostAllowed(hostname, env) {
  const normalized = normalizeHostname(hostname);
  if (!normalized || isPrivateHostname(normalized)) return false;

  const remoteHosts = await loadRemoteAllowedHosts(ALLOWED_HOSTS_URL);
  return hostMatchesAllowlist(normalized, [...remoteHosts]);
}

async function loadRemoteAllowedHosts(urlValue) {
  const now = Date.now();
  if (hostCache.expiresAt > now && hostCache.hosts.size) {
    return hostCache.hosts;
  }
  const url = String(urlValue || "").trim();
  if (!url) return new Set();

  try {
    const target = parseSafeHttpUrl(url);
    const response = await fetch(target.toString(), {
      headers: { Accept: "application/json" },
      cf: { cacheEverything: true, cacheTtl: 300 },
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const values = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.hosts)
        ? payload.hosts
        : [];
    const hosts = new Set(
      values
        .map(normalizeHostname)
        .filter((host) => host && !isPrivateHostname(host)),
    );
    hostCache = {
      expiresAt: now + HOST_CACHE_MS,
      hosts,
    };
    return hosts;
  } catch (error) {
    if (hostCache.hosts.size) return hostCache.hosts;
    console.warn("Allowed host list load failed:", safeErrorMessage(error));
    return new Set();
  }
}

function hostMatchesAllowlist(hostname, hosts) {
  return hosts.some((allowed) => {
    if (!allowed) return false;
    if (allowed.startsWith("*.")) {
      const suffix = allowed.slice(1);
      return hostname.endsWith(suffix) && hostname !== suffix.slice(1);
    }
    return hostname === allowed || hostname.endsWith(`.${allowed}`);
  });
}

function parseSafeHttpUrl(value) {
  let url;
  try {
    url = value instanceof URL ? new URL(value.toString()) : new URL(String(value));
  } catch (_) {
    throw new Error("Invalid target URL");
  }
  validateTargetUrl(url);
  return url;
}

function validateTargetUrl(url) {
  if (!(url instanceof URL)) throw new Error("Invalid target URL");
  if (!new Set(["http:", "https:"]).has(url.protocol)) {
    throw new Error("Unsafe target protocol");
  }
  if (url.username || url.password) {
    throw new Error("Credentials in target URL are not allowed");
  }
  const hostname = normalizeHostname(url.hostname);
  if (!hostname || isPrivateHostname(hostname)) {
    throw new Error("Private or unsafe target host");
  }
}

function isPrivateHostname(hostname) {
  const host = normalizeHostname(hostname);
  if (!host) return true;
  if (
    host === "localhost" ||
    host.endsWith(".localhost") ||
    host.endsWith(".local") ||
    host.endsWith(".internal") ||
    host === "metadata.google.internal"
  ) {
    return true;
  }
  if (host.includes(":")) {
    const value = host.toLowerCase();
    return (
      value === "::" ||
      value === "::1" ||
      value.startsWith("fc") ||
      value.startsWith("fd") ||
      value.startsWith("fe8") ||
      value.startsWith("fe9") ||
      value.startsWith("fea") ||
      value.startsWith("feb")
    );
  }
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) {
    return isPrivateIpv4(host);
  }
  return false;
}

function isPrivateIpv4(value) {
  const number = ipv4ToNumber(value);
  if (number === null) return true;
  return PRIVATE_IPV4_RANGES.some(([base, bits]) => {
    const baseNumber = ipv4ToNumber(base);
    const mask = bits === 0 ? 0 : (0xffffffff << (32 - bits)) >>> 0;
    return ((number & mask) >>> 0) === ((baseNumber & mask) >>> 0);
  });
}

function ipv4ToNumber(value) {
  const parts = value.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) {
    return null;
  }
  return (
    ((parts[0] << 24) >>> 0) +
    (parts[1] << 16) +
    (parts[2] << 8) +
    parts[3]
  ) >>> 0;
}

function normalizeHostname(value) {
  return String(value || "").trim().toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
}

function normalizePlaybackId(value) {
  const id = String(value || "").trim();
  return /^ctv_[a-f0-9]{32}$/.test(id) ? id : "";
}

async function fetchShardRecords(shard, { bypassCache } = {}) {
  const url = bypassCache
    ? `${CATALOG_SHARD_URL(shard)}?refresh=${Date.now()}`
    : CATALOG_SHARD_URL(shard);
  const response = await fetch(url, {
    headers: bypassCache
      ? { Accept: "application/json", "Cache-Control": "no-cache" }
      : { Accept: "application/json" },
    ...(bypassCache ? { cache: "no-store" } : {}),
    cf: bypassCache
      ? { cacheEverything: false, cacheTtl: 0 }
      : { cacheEverything: true, cacheTtl: 300 },
  });
  if (!response.ok) return null;
  // Cloudflare Pages answers an unknown path with the site's own HTML and
  // HTTP 200 - it does not 404. Parsing that as JSON throws, and the throw
  // used to escape all the way past the legacy-catalogue fallback below, so
  // every protected stream returned 404 on a repository whose data/ had not
  // been resharded yet. A local static server 404s instead, which is why this
  // only ever showed up in production.
  const payload = await readJsonOrNull(response);
  if (!payload || typeof payload !== "object") return null;
  return payload.records && typeof payload.records === "object" ? payload.records : null;
}

async function readJsonOrNull(response) {
  try {
    return await response.json();
  } catch (_) {
    return null;
  }
}

async function loadLegacyCatalogRecords(now) {
  // Only reached while a repository still carries the pre-shard single file.
  // Keeping this path means the Worker can be deployed before the first
  // sharded scan lands without playback going dark in between.
  if (playbackCatalogCache.records && playbackCatalogCache.expiresAt > now) {
    return playbackCatalogCache.records;
  }
  const response = await fetch(PLAYBACK_CATALOG_URL, {
    headers: { Accept: "application/json" },
    cf: { cacheEverything: true, cacheTtl: 300 },
  });
  if (!response.ok) return null;
  const payload = await readJsonOrNull(response);
  if (!payload || typeof payload !== "object") return null;
  if (!payload.records || typeof payload.records !== "object") return null;
  playbackCatalogCache = { expiresAt: now + CATALOG_CACHE_MS, records: payload.records };
  return payload.records;
}

async function loadPlaybackProfile(playbackId) {
  if (!playbackId) return null;
  const now = Date.now();
  const shard = catalogShardFor(playbackId);
  let records = null;
  try {
    const cached = playbackShardCache.get(shard);
    if (cached && cached.expiresAt > now) {
      records = cached.records;
    } else {
      records = await fetchShardRecords(shard);
      if (records) {
        playbackShardCache.set(shard, { expiresAt: now + CATALOG_CACHE_MS, records });
      }
    }

    // Pages and Workers do not update atomically. On a shard ID miss, bypass
    // both cache layers once before declaring the player route absent. This is
    // now one small shard rather than the entire catalogue.
    if (!records || !records[playbackId]) {
      const fresh = await fetchShardRecords(shard, { bypassCache: true });
      if (fresh) {
        records = fresh;
        playbackShardCache.set(shard, { expiresAt: Date.now() + CATALOG_CACHE_MS, records });
      }
    }

    if (!records || !records[playbackId]) {
      const legacy = await loadLegacyCatalogRecords(now);
      if (legacy && legacy[playbackId]) records = legacy;
    }
  } catch (_) {
    return null;
  }
  if (!records) return null;
  const profile = records[playbackId];
  if (!profile || typeof profile !== "object" || profile.status !== "active") return null;
  if (Number(profile.expires_at || 0) > 0 && Number(profile.expires_at) < Math.floor(Date.now() / 1000)) {
    return null;
  }
  try {
    parseSafeHttpUrl(profile.url);
  } catch (_) {
    return null;
  }
  return profile;
}

function proxyNameFromHostname(hostname) {
  const host = normalizeHostname(hostname);
  if (host.startsWith("raspy-meadow-9279.")) return "play-proxy-1";
  if (host.startsWith("stream-proxy-3.")) return "play-proxy-2";
  if (host.startsWith("stream-proxy-4.")) return "play-proxy-3";
  if (host.startsWith("stream-proxy-5.")) return "play-proxy-4";
  return "click-tv-playback-proxy";
}

function normalizeProfileName(value) {
  const profile = String(value || "default").trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(HEADER_PROFILES, profile)
    ? profile
    : "default";
}

function normalizeStreamType(value, url) {
  const type = String(value || "").trim().toLowerCase();
  if (STREAM_TYPES.has(type)) return type;
  return guessTypeFromUrl(url, "media");
}

function guessTypeFromUrl(value, fallback = "media") {
  const pathname = (value instanceof URL ? value.pathname : new URL(value.toString()).pathname).toLowerCase();
  if (pathname.endsWith(".m3u8")) return "hls";
  if (pathname.endsWith(".mpd")) return "dash";
  if (/\.(vtt|srt|ttml|xml)$/.test(pathname)) return "subtitle";
  if (/\.(key|bin)$/.test(pathname)) return "key";
  if (/\.(ts|m2ts|mpegts|flv)$/.test(pathname)) return "mpegts";
  return fallback;
}

function detectResponseType(requestedType, finalUrl, contentType) {
  const normalized = normalizeStreamType(requestedType, finalUrl);
  const mime = String(contentType || "").toLowerCase();
  if (mime.includes("mpegurl")) return "hls";
  if (mime.includes("dash+xml")) return "dash";
  if (mime.includes("text/vtt") || mime.includes("subrip")) return "subtitle";
  return normalized;
}

async function readTextWithLimit(response, limit) {
  const contentLength = Number(response.headers.get("content-length") || 0);
  if (contentLength > limit) throw new Error("Upstream text response is too large");
  const buffer = await response.arrayBuffer();
  if (buffer.byteLength > limit) throw new Error("Upstream text response is too large");
  return new TextDecoder("utf-8").decode(buffer);
}

async function createChildSignature({
  targetUrl,
  requestedType,
  profileName,
  inheritQuery,
  playbackId,
  expires,
  secret,
}) {
  const secretText = String(secret || "");
  if (!secretText) return "";
  const payload = signaturePayload({
    targetUrl,
    requestedType,
    profileName,
    inheritQuery,
    playbackId,
    expires,
  });
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secretText),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(payload),
  );
  return base64Url(new Uint8Array(signature));
}

async function validateChildSignature({
  targetUrl,
  requestedType,
  profileName,
  inheritQuery,
  playbackId,
  signature,
  expires,
  secret,
}) {
  if (!signature || !expires || expires < Math.floor(Date.now() / 1000)) {
    return false;
  }
  if (expires > Math.floor(Date.now() / 1000) + CHILD_LINK_TTL_SECONDS + 300) {
    return false;
  }
  const expected = await createChildSignature({
    targetUrl,
    requestedType,
    profileName,
    inheritQuery,
    playbackId,
    expires,
    secret,
  });
  return expected ? timingSafeEqual(expected, signature) : false;
}

function signaturePayload({
  targetUrl,
  requestedType,
  profileName,
  inheritQuery,
  playbackId,
  expires,
}) {
  return [
    targetUrl.toString(),
    normalizeStreamType(requestedType, targetUrl),
    normalizeProfileName(profileName),
    inheritQuery ? "1" : "0",
    String(playbackId || ""),
    String(expires),
  ].join("\n");
}

function base64Url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function timingSafeEqual(left, right) {
  const a = String(left);
  const b = String(right);
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let index = 0; index < a.length; index += 1) {
    difference |= a.charCodeAt(index) ^ b.charCodeAt(index);
  }
  return difference === 0;
}

async function replaceAsync(text, pattern, replacer) {
  const matches = [...text.matchAll(pattern)];
  if (!matches.length) return text;
  let output = "";
  let cursor = 0;
  for (const match of matches) {
    output += text.slice(cursor, match.index);
    output += await replacer(...match);
    cursor = match.index + match[0].length;
  }
  output += text.slice(cursor);
  return output;
}

function escapeXml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function decodeXml(value) {
  return String(value)
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&gt;/g, ">")
    .replace(/&lt;/g, "<")
    .replace(/&amp;/g, "&");
}

function parseCsv(value) {
  return String(value || "")
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function jsonResponse(payload, status, headers) {
  const nextHeaders = new Headers(headers);
  nextHeaders.set("Content-Type", "application/json; charset=utf-8");
  nextHeaders.set("Cache-Control", "no-store");
  return new Response(JSON.stringify(payload, null, 2), {
    status,
    headers: nextHeaders,
  });
}

function textResponse(text, status, headers) {
  const nextHeaders = new Headers(headers);
  nextHeaders.set("Content-Type", "text/plain; charset=utf-8");
  nextHeaders.set("Cache-Control", "no-store");
  return new Response(text, { status, headers: nextHeaders });
}

function safeErrorMessage(error) {
  const message = String(error?.message || error || "Unknown error");
  return message.replace(/https?:\/\/[^\s]+/gi, "[redacted-url]").slice(0, 240);
}
