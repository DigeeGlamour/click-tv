/* Click TV Service Worker
 * Static app files cache করবে।
 * Live stream, video segment ও playback proxy request স্পর্শ করবে না।
 */

const CACHE_VERSION = "click-tv-design-playback-20260811-v8";
const APP_CACHE = `${CACHE_VERSION}-app`;
const DATA_CACHE = `${CACHE_VERSION}-data`;

const APP_SHELL = [
  "/",
  "/index.html",
  "/app.webmanifest",
  "/runtime-config.json",
  "/assets/css/app.css",
  "/assets/css/series.css",
  "/assets/css/final-design.css",
  "/assets/css/reference-design.css",
  "/assets/js/series.js",
  "/assets/js/app.js"
];

const STREAM_EXTENSIONS = [
  ".m3u8",
  ".mpd",
  ".ts",
  ".m4s",
  ".mp4",
  ".mkv",
  ".webm",
  ".avi",
  ".aac",
  ".m4a",
  ".mp3",
  ".flac",
  ".key",
  ".vtt",
  ".srt"
];

const STREAM_PATHS = [
  "/hls",
  "/proxy",
  "/stream",
  "/segment",
  "/playlist",
  "/dash",
  "/live-stream"
];

function getRequestUrl(request) {
  try {
    return new URL(request.url);
  } catch {
    return null;
  }
}

function isHttpRequest(request) {
  const url = getRequestUrl(request);

  return Boolean(
    url &&
    (url.protocol === "http:" || url.protocol === "https:")
  );
}

function isStreamRequest(request) {
  const url = getRequestUrl(request);

  if (!url) return true;

  const pathname = url.pathname.toLowerCase();
  const fullUrl = url.href.toLowerCase();

  if (
    request.destination === "video" ||
    request.destination === "audio"
  ) {
    return true;
  }

  if (
    STREAM_EXTENSIONS.some(extension =>
      pathname.endsWith(extension) ||
      fullUrl.includes(`${extension}?`)
    )
  ) {
    return true;
  }

  if (
    STREAM_PATHS.some(path =>
      pathname.includes(path)
    )
  ) {
    return true;
  }

  if (
    url.hostname.endsWith("workers.dev") &&
    !pathname.endsWith(".json")
  ) {
    return true;
  }

  return false;
}

function isDataRequest(request) {
  const url = getRequestUrl(request);

  if (!url || url.origin !== self.location.origin) {
    return false;
  }

  return (
    (
      url.pathname.startsWith("/data/") &&
      url.pathname.endsWith(".json")
    ) ||
    url.pathname === "/runtime-config.json"
  );
}

function isStaticRequest(request) {
  const url = getRequestUrl(request);

  if (!url || url.origin !== self.location.origin) {
    return false;
  }

  return (
    request.destination === "script" ||
    request.destination === "style" ||
    request.destination === "image" ||
    request.destination === "font" ||
    url.pathname.endsWith(".webmanifest")
  );
}

async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);

  try {
    const response = await fetch(request);

    if (response && response.ok) {
      await cache.put(request, response.clone());
    }

    return response;
  } catch (error) {
    const cachedResponse = await cache.match(request);

    if (cachedResponse) {
      return cachedResponse;
    }

    throw error;
  }
}

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cachedResponse = await cache.match(request);

  if (cachedResponse) {
    return cachedResponse;
  }

  const networkResponse = await fetch(request);

  if (networkResponse && networkResponse.ok) {
    await cache.put(request, networkResponse.clone());
  }

  return networkResponse;
}

async function handleNavigation(request) {
  const cache = await caches.open(APP_CACHE);

  try {
    const response = await fetch(request);

    if (response && response.ok) {
      await cache.put("/index.html", response.clone());
    }

    return response;
  } catch {
    const cachedPage =
      await cache.match("/index.html") ||
      await cache.match("/");

    if (cachedPage) {
      return cachedPage;
    }

    return new Response(
      `<!DOCTYPE html>
      <html lang="bn">
      <head>
        <meta charset="UTF-8">
        <meta
          name="viewport"
          content="width=device-width, initial-scale=1"
        >
        <title>Click TV Offline</title>

        <style>
          * {
            box-sizing: border-box;
          }

          body {
            margin: 0;
            min-height: 100vh;
            display: grid;
            place-items: center;
            padding: 24px;
            background: #080c14;
            color: #ffffff;
            font-family: Arial, sans-serif;
            text-align: center;
          }

          .offline-box {
            width: 100%;
            max-width: 420px;
            padding: 30px 20px;
            background: #0f1522;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 14px;
          }

          h1 {
            margin: 0 0 12px;
            font-size: 24px;
          }

          p {
            margin: 0;
            color: #aaaaaa;
            font-size: 14px;
            line-height: 1.6;
          }
        </style>
      </head>

      <body>
        <div class="offline-box">
          <h1>Click TV এখন অফলাইন</h1>

          <p>
            ইন্টারনেট সংযোগ ফিরে এলে আবার চেষ্টা করুন।
            লাইভ চ্যানেল ও মুভি অফলাইনে চালানো সম্ভব নয়।
          </p>
        </div>
      </body>
      </html>`,
      {
        status: 503,
        statusText: "Offline",
        headers: {
          "Content-Type": "text/html; charset=UTF-8",
          "Cache-Control": "no-store"
        }
      }
    );
  }
}

self.addEventListener("install", event => {
  event.waitUntil(
    caches
      .open(APP_CACHE)
      .then(cache => cache.addAll(APP_SHELL))
      .catch(error => {
        console.warn(
          "Click TV app shell cache failed:",
          error
        );
      })
  );

  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(cacheNames =>
      Promise.all(
        cacheNames
          .filter(cacheName =>
            cacheName !== APP_CACHE &&
            cacheName !== DATA_CACHE
          )
          .map(cacheName =>
            caches.delete(cacheName)
          )
      )
    )
  );

  self.clients.claim();
});

self.addEventListener("fetch", event => {
  const request = event.request;

  if (
    request.method !== "GET" ||
    !isHttpRequest(request)
  ) {
    return;
  }

  /*
   * Live stream বা video request-এ respondWith ব্যবহার করা হবে না।
   * Browser সরাসরি network থেকে request করবে।
   */
  if (isStreamRequest(request)) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      handleNavigation(request)
    );

    return;
  }

  /*
   * Scanner-generated JSON এবং runtime config:
   * প্রথমে network, ব্যর্থ হলে cache।
   */
  if (isDataRequest(request)) {
    event.respondWith(
      networkFirst(request, DATA_CACHE)
    );

    return;
  }

  /*
   * Local CSS, JS, image, font ও web manifest:
   * প্রথমে cache, না থাকলে network।
   */
  if (isStaticRequest(request)) {
    event.respondWith(
      cacheFirst(request, APP_CACHE)
    );
  }
});

self.addEventListener("message", event => {
  if (
    event.data &&
    event.data.type === "SKIP_WAITING"
  ) {
    self.skipWaiting();
  }

  if (
    event.data &&
    event.data.type === "CLEAR_CLICK_TV_CACHE"
  ) {
    event.waitUntil(
      Promise.all([
        caches.delete(APP_CACHE),
        caches.delete(DATA_CACHE)
      ])
    );
  }
});
