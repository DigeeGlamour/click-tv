import assert from 'node:assert/strict';
import worker from '../workers/playback-proxy/src/index.js';

const playbackId = `ctv_${'a'.repeat(32)}`;
const widevinePlaybackId = `ctv_${'b'.repeat(32)}`;
const refreshPlaybackId = `ctv_${'c'.repeat(32)}`;
const profile = {
  status: 'active',
  url: 'https://media.example/live/master.m3u8?token=private-token',
  headers: {
    Cookie: 'session=public-cookie',
    Authorization: 'Bearer scanner-token',
    Referer: 'https://source.example/',
    Origin: 'https://source.example',
    'User-Agent': 'Scanner-Verified-Agent/1.0',
  },
  drm: { license_type: 'clearkey', license_key: 'kid:key' },
  stream_type: 'hls',
  inherit_manifest_query: true,
};
const env = {};
const upstreamCalls = [];
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, init = {}) => {
  const parsed = new URL(String(url));
  if (parsed.pathname === '/data/playback-sources.json') {
    const refreshedRecords = parsed.searchParams.has('refresh')
      ? { [refreshPlaybackId]: structuredClone(profile) }
      : {};
    return Response.json({
      schema_version: 1,
      records: {
        [playbackId]: structuredClone(profile),
        [widevinePlaybackId]: {
          ...structuredClone(profile),
          stream_type: 'dash',
          drm: {
            type: 'widevine',
            license_url: 'https://license.example/widevine',
            license_headers: { Authorization: 'Bearer license-token', 'X-Device': 'click-tv' },
          },
        },
        ...refreshedRecords,
      },
    });
  }
  if (parsed.href === 'https://clicktv.pages.dev/data/allowed-hosts.json') {
    return Response.json({ count: 2, hosts: ['media.example', 'license.example'] });
  }
  if (parsed.href === 'https://license.example/widevine') {
    upstreamCalls.push({ url: parsed.toString(), headers: new Headers(init.headers), method: init.method, body: new Uint8Array(init.body || []) });
    return new Response(new Uint8Array([9, 8, 7, 6]), {
      status: 200,
      headers: { 'Content-Type': 'application/octet-stream' },
    });
  }
  upstreamCalls.push({ url: parsed.toString(), headers: new Headers(init.headers) });
  if (parsed.pathname.endsWith('.m3u8')) {
    return new Response('#EXTM3U\n#EXT-X-TARGETDURATION:4\nsegment.ts\n', {
      status: 200,
      headers: { 'Content-Type': 'application/vnd.apple.mpegurl' },
    });
  }
  return new Response(new Uint8Array([1, 2, 3]), {
    status: 200,
    headers: { 'Content-Type': 'video/mp2t' },
  });
};

try {
  const originHeaders = { Origin: 'https://clicktv.pages.dev' };
  const initial = await worker.fetch(
    new Request(`https://worker.example/hls?id=${playbackId}`, { headers: originHeaders }),
    env,
    { waitUntil() {} },
  );
  assert.equal(initial.status, 200);
  assert.equal(upstreamCalls[0].headers.get('Cookie'), 'session=public-cookie');
  assert.equal(upstreamCalls[0].headers.get('Authorization'), 'Bearer scanner-token');
  assert.equal(upstreamCalls[0].headers.get('Referer'), 'https://source.example/');
  assert.equal(upstreamCalls[0].headers.get('Origin'), 'https://source.example');
  assert.equal(upstreamCalls[0].headers.get('User-Agent'), 'Scanner-Verified-Agent/1.0');
  assert.match(upstreamCalls[0].url, /private-token/);

  const manifest = await initial.text();
  const childUrl = manifest.split('\n').find((line) => line.startsWith('https://worker.example/hls?'));
  assert.ok(childUrl, 'manifest child URL was rewritten');
  assert.equal(new URL(childUrl).searchParams.get('pid'), playbackId);
  assert.ok(new URL(childUrl).searchParams.get('sig'));

  const child = await worker.fetch(
    new Request(childUrl, { headers: originHeaders }),
    env,
    { waitUntil() {} },
  );
  assert.equal(child.status, 200);
  assert.equal(upstreamCalls.at(-1).headers.get('Cookie'), 'session=public-cookie');
  assert.equal(upstreamCalls.at(-1).headers.get('Authorization'), 'Bearer scanner-token');
  assert.equal(upstreamCalls.at(-1).headers.get('Referer'), 'https://source.example/');
  assert.equal(upstreamCalls.at(-1).headers.get('Origin'), 'https://source.example');
  assert.equal(upstreamCalls.at(-1).headers.get('User-Agent'), 'Scanner-Verified-Agent/1.0');

  const drm = await worker.fetch(
    new Request(`https://worker.example/drm?id=${playbackId}`, { headers: originHeaders }),
    env,
    { waitUntil() {} },
  );
  assert.equal(drm.status, 200);
  assert.equal((await drm.json()).drm.license_key, 'kid:key');
  assert.equal(drm.headers.get('Cache-Control'), 'no-store');

  const licenseChallenge = new Uint8Array([4, 3, 2, 1]);
  const license = await worker.fetch(
    new Request(`https://worker.example/license?id=${widevinePlaybackId}`, {
      method: 'POST',
      headers: { ...originHeaders, 'Content-Type': 'application/octet-stream' },
      body: licenseChallenge,
    }),
    env,
    { waitUntil() {} },
  );
  assert.equal(license.status, 200);
  assert.deepEqual(new Uint8Array(await license.arrayBuffer()), new Uint8Array([9, 8, 7, 6]));
  const licenseCall = upstreamCalls.find((call) => call.url === 'https://license.example/widevine');
  assert.equal(licenseCall.method, 'POST');
  assert.deepEqual(licenseCall.body, licenseChallenge);
  assert.equal(licenseCall.headers.get('Authorization'), 'Bearer license-token');
  assert.equal(licenseCall.headers.get('X-Device'), 'click-tv');
  assert.equal(license.headers.get('Cache-Control'), 'no-store, no-cache, must-revalidate');

  const noOrigin = await worker.fetch(
    new Request(`https://worker.example/hls?id=${playbackId}`),
    env,
    { waitUntil() {} },
  );
  assert.equal(noOrigin.status, 403);

  const wrongOrigin = await worker.fetch(
    new Request(`https://worker.example/hls?id=${playbackId}`, {
      headers: { Origin: 'https://unauthorized.example' },
    }),
    env,
    { waitUntil() {} },
  );
  assert.equal(wrongOrigin.status, 403);

  const refreshed = await worker.fetch(
    new Request(`https://worker.example/hls?id=${refreshPlaybackId}`, { headers: originHeaders }),
    env,
    { waitUntil() {} },
  );
  assert.equal(refreshed.status, 200, 'catalogue miss must refresh once before 404');

  const health = await worker.fetch(new Request('https://stream-proxy-3.example/health'), env, {});
  const healthBody = await health.json();
  assert.equal(healthBody.version, '5.2.0');
  assert.equal(healthBody.name, 'play-proxy-2');
  assert.equal(healthBody.configuration_storage, 'git_pages_json');
  assert.equal(healthBody.dashboard_configuration_required, false);

  // A Pages preview deploy and a local copy of the site must be able to play,
  // otherwise a playback bug can only ever be reproduced on production.
  for (const allowedOrigin of [
    'https://clicktv.pages.dev',
    'https://a1b2c3d4.clicktv.pages.dev',
    'http://localhost:8791',
    'http://127.0.0.1:8080',
  ]) {
    const response = await worker.fetch(
      new Request(`https://worker.example/hls?id=${playbackId}`, { headers: { Origin: allowedOrigin } }),
      env,
      { waitUntil() {} },
    );
    assert.notEqual(response.status, 403, `origin must be allowed: ${allowedOrigin}`);
    assert.equal(response.headers.get('Access-Control-Allow-Origin'), allowedOrigin);
  }

  for (const blockedOrigin of ['https://evil.example', 'https://clicktv.pages.dev.evil.com', 'http://192.168.1.5:8080']) {
    const response = await worker.fetch(
      new Request(`https://worker.example/hls?id=${playbackId}`, { headers: { Origin: blockedOrigin } }),
      env,
      { waitUntil() {} },
    );
    assert.equal(response.status, 403, `origin must stay blocked: ${blockedOrigin}`);
  }

  console.log('Playback Worker public-catalog runtime PASS');
} finally {
  globalThis.fetch = realFetch;
}
