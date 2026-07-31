/**
 * Click TV Playback Proxy Worker
 *
 * Deploy the same file to Playback Proxy 1-4. Only environment values change.
 * Viewer-facing routes:
 *   GET  /health
 *   GET  /hls?url=...&type=hls|dash|media|mpegts|key|subtitle&profile=...&inherit=0|1
 *   HEAD /hls?url=...&type=media&profile=...
 *   OPTIONS /hls
 *
 * Required environment variables:
 *   PROXY_NAME
 *   PROXY_VERSION
 *   ALLOWED_ORIGINS       comma separated website origins
 *   ALLOWED_HOSTS_URL     public Click TV data/allowed-hosts.json URL
 *   HEADER_SIGNING_SECRET Cloudflare secret, identical on all four workers
 * Optional encrypted secret:
 *   TOFFEE_EDGE_COOKIE    current Toffee Edge-Cache cookie, when required
 */

const DEFAULT_VERSION = "3.1.0";
const HOST_CACHE_MS = 5 * 60 * 1000;
const MAX_REDIRECTS = 5;
const MAX_TEXT_BYTES = 2 * 1024 * 1024;
const CHILD_LINK_TTL_SECONDS = 6 * 60 * 60;

let hostCache = {
  expiresAt: 0,
  hosts: new Set(),
};

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
    const proxyName = String(env.PROXY_NAME || "click-tv-playback-proxy");
    const proxyVersion = String(env.PROXY_VERSION || DEFAULT_VERSION);

    if (requestUrl.pathname === "/health") {
      return jsonResponse(
        {
          ok: true,
          service: "click-tv-playback-proxy",
          role: "playback",
          name: proxyName,
          version: proxyVersion,
          timestamp: Date.now(),
        },
        200,
        corsHeaders(request, env),
      );
    }

    if (requestUrl.pathname !== "/hls") {
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

    if (!new Set(["GET", "HEAD"]).has(request.method)) {
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
      const targetText = requestUrl.searchParams.get("url") || "";
      if (!targetText) {
        return textResponse(
          "Missing url",
          400,
          corsHeaders(request, env),
        );
      }

      const targetUrl = parseSafeHttpUrl(targetText);
      const requestedType = normalizeStreamType(
        requestUrl.searchParams.get("type"),
        targetUrl,
      );
      const profileName = normalizeProfileName(
        requestUrl.searchParams.get("profile"),
      );
      const inheritQuery = requestUrl.searchParams.get("inherit") === "1";
      const signature = requestUrl.searchParams.get("sig") || "";
      const expires = Number(requestUrl.searchParams.get("exp") || 0);
      const signedChild = await validateChildSignature({
        targetUrl,
        requestedType,
        profileName,
        inheritQuery,
        signature,
        expires,
        secret: env.HEADER_SIGNING_SECRET,
      });

      if (!signedChild) {
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
  env,
  ctx,
}) {
  const upstreamHeaders = buildUpstreamHeaders(
    request,
    profileName,
    requestedType,
    env,
  );
  const upstream = await fetchWithValidatedRedirects(
    targetUrl,
    {
      method: request.method,
      headers: upstreamHeaders,
    },
    requestedType,
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
    detectedType === "media" || detectedType === "mpegts"
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

async function rewriteHlsResponse({
  request,
  requestUrl,
  response,
  finalUrl,
  profileName,
  inheritQuery,
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
        expires,
        secret: env.HEADER_SIGNING_SECRET,
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
) {
  let currentUrl = initialUrl;
  for (let redirectCount = 0; redirectCount <= MAX_REDIRECTS; redirectCount += 1) {
    validateTargetUrl(currentUrl);
    const response = await fetch(currentUrl.toString(), {
      ...init,
      redirect: "manual",
      cf: cloudflareFetchOptions(requestedType, init.headers),
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

function cloudflareFetchOptions(type, headers) {
  const hasRange = headers instanceof Headers && headers.has("Range");
  if (type === "hls" || type === "dash" || hasRange) {
    return { cacheEverything: false, cacheTtl: 0 };
  }
  if (type === "key" || type === "subtitle") {
    return { cacheEverything: true, cacheTtl: 300 };
  }
  return { cacheEverything: true, cacheTtl: 300 };
}

function buildUpstreamHeaders(request, profileName, type, env) {
  const normalizedProfile = normalizeProfileName(profileName);
  const profile = HEADER_PROFILES[normalizedProfile] || HEADER_PROFILES.default;
  const headers = new Headers();
  for (const [name, value] of Object.entries(profile)) {
    headers.set(name, value);
  }

  // Optional protected Toffee cookie. It is never accepted from viewers and
  // never stored in public JSON. Add it only as a Cloudflare encrypted secret.
  if (
    (normalizedProfile === "toffee_okhttp" || normalizedProfile === "toffee") &&
    String(env?.TOFFEE_EDGE_COOKIE || "").trim()
  ) {
    headers.set("Cookie", String(env.TOFFEE_EDGE_COOKIE).trim());
  }

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
  const allowed = parseCsv(env.ALLOWED_ORIGINS);
  const origin = request.headers.get("Origin") || "";
  if (allowed.includes("*")) {
    headers.set("Access-Control-Allow-Origin", "*");
  } else if (origin && allowed.includes(origin)) {
    headers.set("Access-Control-Allow-Origin", origin);
    headers.set("Vary", "Origin");
  } else if (!origin && allowed.length) {
    headers.set("Access-Control-Allow-Origin", allowed[0]);
  } else {
    headers.set("Access-Control-Allow-Origin", "*");
  }
  headers.set("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS");
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
  const allowed = parseCsv(env.ALLOWED_ORIGINS);
  if (allowed.includes("*") || allowed.includes(origin)) return { ok: true };
  return { ok: false };
}

async function isInitialHostAllowed(hostname, env) {
  const normalized = normalizeHostname(hostname);
  if (!normalized || isPrivateHostname(normalized)) return false;

  const inlineHosts = parseCsv(env.ALLOWED_HOSTS).map(normalizeHostname);
  if (hostMatchesAllowlist(normalized, inlineHosts)) return true;

  const remoteHosts = await loadRemoteAllowedHosts(env.ALLOWED_HOSTS_URL);
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
  expires,
}) {
  return [
    targetUrl.toString(),
    normalizeStreamType(requestedType, targetUrl),
    normalizeProfileName(profileName),
    inheritQuery ? "1" : "0",
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
