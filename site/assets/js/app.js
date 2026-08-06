'use strict';
// CLICKTV_RUNTIME_STABILITY_20260806_USER_LIST_FINAL_V1

// CLICKTV_FINAL_FIX_20260806_V2

const $ = (id) => document.getElementById(id);
const qs = (selector, root = document) => root.querySelector(selector);
const qsa = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const STORAGE_KEYS = Object.freeze({
  networkMode: 'clicktv_network_mode',
  liveNetworkMode: 'clicktv_live_network_mode_v2',
  movieNetworkMode: 'clicktv_movie_network_mode_v2',
  proxyHealth: 'clicktv_proxy_health_v1',
  playbackHistory: 'clicktv_playback_history_v1',
  recentItems: 'clicktv_recent_items_v1',
  favorites: 'clicktv_favorites_v1',
  positions: 'clicktv_positions_v1',
  favoriteItems: 'clicktv_favorite_items_v1',
  noticeDismissed: 'clicktv_notice_dismissed_v1',
  liteMode: 'clicktv_lite_mode',
  maxHeight: 'clicktv_max_height',
  telemetrySession: 'clicktv_telemetry_session_v1'
});

const VIEW = Object.freeze({
  CHANNEL: 'channel',
  MOVIE: 'movie',
  EVENT: 'event',
  UPCOMING: 'upcoming',
  RECENT: 'recent',
  FAVORITE: 'favorite'
});

const NETWORK_MODE = Object.freeze({
  AUTO: 'auto',
  BALANCED: 'balanced',
  STABLE: 'stable',
  LOW: 'low'
});

const MOVIE_ORDER = Object.freeze([
  ['Bangla', 'bangla'],
  ['Hindi', 'hindi'],
  ['English', 'english'],
  ['Dubbed', 'dubbed'],
  ['South Indian', 'south-indian'],
  ['Premium', 'premium'],
  ['Mix', 'mix']
]);

const CHANNEL_INITIAL_CHUNK = 30;
const CHANNEL_NEXT_CHUNK = 20;
const MOVIE_CHUNK_SIZE = 20;
const CHANNEL_ATTEMPT_BUDGET_MS = 16000;
const MOVIE_ATTEMPT_BUDGET_MS = 110000;
const EVENT_ATTEMPT_BUDGET_MS = 38000;
const MIDPLAY_RECOVERY_BUDGET_MS = 16000;
const QUALITY_LOCK_MAX_MS = 6500;
const AUTO_NEXT_LIMIT = 3;
const AUTO_NEXT_SECONDS = 5;
const MPEGTS_CDN = 'https://cdn.jsdelivr.net/npm/mpegts.js@1.7.3/dist/mpegts.min.js';
const SHAKA_CDN = 'https://cdnjs.cloudflare.com/ajax/libs/shaka-player/4.10.9/shaka-player.compiled.js';
const DATA_FETCH_TIMEOUT_MS = 9000;
const POSITION_SAVE_INTERVAL_MS = 10000;
const POSITION_HISTORY_LIMIT = 200;
const MOVIE_PROMPT_TEXT = 'মুভি দেখতে একটি বিভাগ নির্বাচন করুন';
const MOVIE_PREVIEW_LIMIT = 18;
const MOBILE_SEARCH_AUTO_CLOSE_MS = 5000;
const LIVE_FAST_START_RAMP_MS = 6000;
const LIVE_CHANNEL_STALL_FAILOVER_MS = 14000;
const LIVE_EVENT_STALL_FAILOVER_MS = 20000;
const LIVE_FULLSCREEN_GRACE_MS = 14000;
const MOVIE_STALL_FAILOVER_MS = 30000;
const MOVIE_4K_STALL_FAILOVER_MS = 42000;
const FULLSCREEN_DRAWER_RENDER_LIMIT = 80;
const PLAYER_SELECTION_OSD_MS = 7000;

const state = {
  runtime: null,
  manifest: null,
  deviceClass: 'normal',
  telemetrySessionId: '',
  manifestVersion: '',
  view: VIEW.CHANNEL,
  selectedCategory: null,
  selectedMovieCategory: null,
  activeMainGroup: 'sports',
  activeFinalSub: 'today-match',
  currentItems: [],
  filteredItems: [],
  renderedCount: 0,
  currentSortMode: 'default',
  seriesDetailMode: false,
  dataSessionId: 0,
  dataAbortController: null,
  movieIndex: null,
  moviePageCursor: 0,
  moviePageLoading: false,
  movieCategorySessionId: 0,
  moviePreviewMode: false,
  mobileSearchHideTimer: null,
  currentItem: null,
  playbackSession: null,
  activeLoadId: 0,
  hls: null,
  shaka: null,
  mpegts: null,
  playerType: null,
  selectedManualQuality: -1,
  manualQualityChangePending: false,
  pendingQualityResume: null,
  qualityNoticeTimer: null,
  qualityNoticeHideTimer: null,
  qualityNoticeInterval: null,
  movieAudioCheckTimer: null,
  movieAudioCompanionActive: false,
  movieAudioCompanionPrepared: false,
  movieAudioCompanionPreparedUrl: '',
  movieAudioCompanionSourceUrl: '',
  movieAudioCompanionSyncTimer: null,
  movieAudioCompanionPreparePromise: null,
  movieAudioOperationId: 0,
  movie4kAudioBlockedQualityKeys: new Set(),
  movie4kAudioBlockedSourceTokens: new Set(),
  liveStartupRampTimer: null,
  liveStartupStartedAt: 0,
  liveStartupRamped: false,
  liveAdaptiveQualityTimer: null,
  liveAdaptiveQualityStartedAt: 0,
  liveAdaptiveQualityLastStepAt: 0,
  liveAdaptiveQualityStage: 0,
  liveStartupQualityCapHeight: 0,
  userPaused: false,
  qualitySwitchLockUntil: 0,
  qualityUnlockTimer: null,
  recoveryLockUntil: 0,
  stallInterval: null,
  autoNextTimer: null,
  autoNextCount: 0,
  autoNextFailedUids: [],
  currentQuery: '',
  lastFocusedUid: null,
  lastFocusedSelector: null,
  drawerRenderedForSession: -1,
  drawerScrollPositions: Object.create(null),
  hideControlsTimer: null,
  deferredInstallPrompt: null,
  autoplayUnlockPending: false,
  lastNonZeroVolume: 1,
  fitIndex: 0,
  mediaOperationGraceUntil: 0,
  seekPointerActive: false,
  seekPendingTime: null,
  seekWasPlaying: false,
  lastTouchSeekAt: 0,
  movieAudioResumeTimer: null,
  fullscreenAudioSyncTimer: null,
  fullscreenLiveRecoveryTimer: null,
  fullscreenLiveQualityGuardTimer: null,
  fullscreenLiveQualityPreviousCap: 0,
  currentBrightness: 1,
  touchStartX: 0,
  touchStartY: 0,
  touchInitialVolume: 1,
  touchInitialBrightness: 1,
  lastTapTime: 0,
  gestureTimer: null,
  positionSavedAt: 0,
  userWantsSound: localStorage.getItem('clicktv_sound_on') === '1',
  searchQuery: '',
  osdTimer: null,
  resumeBadgeTimer: null,
  playbackPositions: readJsonStorage(STORAGE_KEYS.positions, {}),
  proxyHealth: readJsonStorage(STORAGE_KEYS.proxyHealth, {}),
  mobileNativeFullscreen: false,
  performanceMonitorTimer: null,
  performanceSample: null,
  performanceStressStreak: 0,
  performanceStableStreak: 0,
  adaptiveDecodeCapHeight: 0,
  performanceNoticeShown: false,
  playbackHistory: readJsonStorage(STORAGE_KEYS.playbackHistory, {
    lastBandwidth: 0,
    successfulStarts: 0,
    stalls: 0,
    updatedAt: 0
  })
};

const video = $('videoPlayer');
let movieAudioCompanion = $('movieAudioCompanion');
// The companion source is used only for audio. Replacing the hidden <video>
// with <audio> prevents a second 4K video decoder from running in parallel.
if (movieAudioCompanion && movieAudioCompanion.tagName !== 'AUDIO') {
  const audioOnlyCompanion = document.createElement('audio');
  audioOnlyCompanion.id = movieAudioCompanion.id;
  audioOnlyCompanion.className = movieAudioCompanion.className;
  audioOnlyCompanion.preload = movieAudioCompanion.preload || 'auto';
  audioOnlyCompanion.setAttribute('aria-hidden', 'true');
  audioOnlyCompanion.tabIndex = -1;
  movieAudioCompanion.replaceWith(audioOnlyCompanion);
  movieAudioCompanion = audioOnlyCompanion;
}
const sidebarList = $('sidebarList');
const sidebarSection = qs('.sidebar-section');
const sidebarScrollArea = $('sidebarScrollArea');
const searchInput = $('searchInput');
const mobileSearchInput = $('mobileSearchInput');
const playerMessage = $('playerMsg');
const playerMessageText = $('playerMsgText');
const errorCountdownBox = $('errorCountdownBox');
const movieSubcategoryBar = $('movieSubcategoryBar');
const chipsContainer = $('chipsContainer');
const videoContainer = $('videoContainer');
const playerControls = $('playerControls');
const desktopMainNav = $('desktopMainNav');
const mobileMainNav = $('mobileMainNav');
const desktopSubNav = $('desktopSubNav');
const mobileSubNav = $('mobileSubNav');
const desktopMainNavigation = $('desktopMainNavigation');
const desktopSubNavigation = $('desktopSubNavigation');
const mobileMainNavigation = $('mobileMainNavigation');
const mobileSubNavigation = $('mobileSubNavigation');
const seriesModule = window.ClickTvSeries || null;

function readJsonStorage(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : fallback;
  } catch (_) {
    return fallback;
  }
}

function writeJsonStorage(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    console.warn('Storage write failed, trimming cache:', key, error?.name);
    try {
      localStorage.removeItem(STORAGE_KEYS.positions);
      localStorage.removeItem(STORAGE_KEYS.proxyHealth);
      localStorage.setItem(key, JSON.stringify(value));
    } catch (_) {}
  }
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function cssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(String(value));
  return String(value).replace(/[^a-zA-Z0-9_-]/g, (character) => `\\${character}`);
}

function slugify(value) {
  return String(value ?? 'item')
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'item';
}

function resolvePublicUrl(path) {
  if (!path) return '';
  try {
    return new URL(path, `${location.origin}/`).toString();
  } catch (_) {
    return String(path);
  }
}

function withVersion(path) {
  const url = new URL(resolvePublicUrl(path));
  if (state.manifestVersion) url.searchParams.set('v', state.manifestVersion);
  return url.toString();
}

function withTimeoutSignal(signal, timeoutMs = DATA_FETCH_TIMEOUT_MS) {
  const timeoutSignal = AbortSignal.timeout ? AbortSignal.timeout(timeoutMs) : null;
  if (!timeoutSignal) return signal;
  if (!signal) return timeoutSignal;
  if (AbortSignal.any) return AbortSignal.any([signal, timeoutSignal]);
  return signal;
}

async function fetchJson(path, options = {}) {
  const response = await fetch(withVersion(path), {
    cache: options.cache || 'no-store',
    signal: withTimeoutSignal(options.signal, options.timeoutMs),
    headers: { Accept: 'application/json' }
  });
  if (!response.ok) throw new Error(`HTTP ${response.status} for ${path}`);
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.toLowerCase().includes('json')) {
    const sample = (await response.text()).slice(0, 80);
    throw new Error(`Expected JSON but received ${sample || contentType}`);
  }
  return response.json();
}

function showPlayerMessage(text, showLoader = true) {
  playerMessageText.textContent = text;
  const loader = qs('.premium-loader', playerMessage);
  if (loader) loader.style.display = showLoader ? 'flex' : 'none';
  playerMessage.classList.add('show');
}

function hidePlayerMessage() {
  playerMessage.classList.remove('show');
  hideFailureActions();
}

function showListMessage(text, icon = 'fa-info-circle', loading = false) {
  cancelPendingImages(sidebarList);
  sidebarList.classList.remove('movie-grid');
  sidebarList.innerHTML = `
    <div class="movie-prompt-msg">
      ${loading ? '<div class="premium-loader" style="margin-bottom:12px"><div class="loader-ring"></div></div>' : `<i class="fas ${escapeHtml(icon)}"></i>`}
      <span>${escapeHtml(text)}</span>
    </div>`;
}

function showToast(message, duration = 2200, variant = '') {
  const toast = $('osdToast');
  toast.textContent = message;
  toast.classList.toggle('glass-toast', variant === 'glass');
  toast.classList.add('show');
  clearTimeout(toast._hideTimer);
  toast._hideTimer = setTimeout(() => {
    toast.classList.remove('show');
    toast.classList.remove('glass-toast');
  }, duration);
}

function setSidebarCount(text) {
  const count = $('sidebarCountText');
  if (count) count.textContent = text;
}

function setSearchEnabled(enabled) {
  searchInput.disabled = !enabled;
  mobileSearchInput.disabled = !enabled;
  if (!enabled) {
    searchInput.value = '';
    mobileSearchInput.value = '';
    state.searchQuery = '';
    state.currentQuery = '';
  }
}

function normalizeMovieKey(label) {
  const found = MOVIE_ORDER.find(([name, slug]) => name.toLowerCase() === String(label).toLowerCase() || slug === label);
  return found ? found[1] : slugify(label);
}

function manifestMovieEntry(slug) {
  if (!state.manifest?.movies) return null;
  const pair = Object.entries(state.manifest.movies).find(([label]) => normalizeMovieKey(label) === slug);
  return pair ? { label: pair[0], ...pair[1] } : null;
}


const BANGLA_CHANNEL_PRIORITY = Object.freeze([
  ['btv', 'bangladesh television'],
  ['btv news'],
  ['somoy tv', 'somoy television', 'somoy'],
  ['jamuna tv', 'jamuna television', 'jamuna'],
  ['channel 24', 'channel24'],
  ['gazi tv', 'gazi television', 'gtv'],
  ['ekattor hd', 'ekattor tv hd', 'ekattor'],
  ['independent tv', 'independent television', 'independent'],
  ['dbc news', 'dbc'],
  ['news 24', 'news24'],
  ['atn news'],
  ['gaan bangla', 'gan bangla'],
  ['rtv'],
  ['banglavision', 'bangla vision'],
  ['channel i'],
  ['me tv', 'metv'],
  ['deepto tv', 'deepto'],
  ['maasranga tv', 'maasranga', 'masranga tv', 'masranga'],
  ['srk tv', 'srk'],
  ['boishakhi tv', 'boishakhi'],
  ['green tv', 'green television'],
  ['asian tv', 'asian television'],
  ['atn bangla'],
  ['ekhon tv', 'ekhon'],
  ['nexus tv hd', 'nexus tv', 'nexus'],
  ['global tv', 'global television'],
  ['channel s'],
  ['mohona tv', 'mohona'],
  ['my tv', 'mytv'],
  ['ekattor tv'],
  ['ntv'],
  ['satv', 'sa tv'],
  ['ekushey tv', 'etv'],
  ['nagorik tv hd', 'nagorik tv', 'nagorik'],
  ['desh tv', 'desh television'],
  ['ep tv', 'eptv'],
  ['music bangla'],
  ['g series', 'g-serise', 'g serise'],
  ['movie bangla tv', 'movie bangla'],
  ['rajdhani tv', 'rajdhani'],
  ['khusbo bangla', 'khushbu bangla', 'khushbo bangla'],
  ['ruposhi bangla']
]);

const FAILED_PUBLISH_STATUSES = new Set([
  'failed',
  'failed_bd',
  'rejected_low_quality',
  'quarantine',
  '404_quarantined',
  'dead'
]);

function cleanDisplayName(value) {
  let text = String(value || '').replace(/\\"/g, '"').replace(/\s+/g, ' ').trim();
  if (!text) return '';

  if (/\bq[\s_-]*85\b/i.test(text) && /\bposters?\b/i.test(text)) {
    const commaParts = text.split(',');
    const tail = commaParts[commaParts.length - 1].replace(/^["'\s]+|["'\s]+$/g, '').trim();
    if (tail) text = tail;
  }

  if (/\b(?:jpe?g|png|webp|gif)\b/i.test(text) && /\bgroup\s*title\b/i.test(text) && text.includes(',')) {
    const tail = text.split(',').pop().replace(/^["'\s]+|["'\s]+$/g, '').trim();
    if (tail) text = tail;
  }

  text = text
    .replace(/^["'\s]+|["'\s]+$/g, '')
    .replace(/\s*,\s*$/, '')
    .replace(/\s{2,}/g, ' ')
    .trim();

  return text || 'Untitled';
}

function canonicalDisplayKey(value) {
  return cleanDisplayName(value)
    .toLowerCase()
    .normalize('NFKD')
    .replace(/&/g, ' and ')
    .replace(/[^a-z0-9\u0980-\u09ff]+/g, ' ')
    .replace(/\b(?:hd|full hd|fhd|sd)\b/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function banglaPriorityIndex(name) {
  const key = canonicalDisplayKey(name);
  for (let index = 0; index < BANGLA_CHANNEL_PRIORITY.length; index += 1) {
    if (BANGLA_CHANNEL_PRIORITY[index].some((alias) => canonicalDisplayKey(alias) === key)) {
      return index;
    }
  }
  return Number.MAX_SAFE_INTEGER;
}

function isFailedPublishedItem(item) {
  const status = String(item?.verification_status || item?.status || '').trim().toLowerCase();
  return FAILED_PUBLISH_STATUSES.has(status) || item?.publish_allowed === false;
}

function isLikelyVodLeak(item) {
  const pipeline = String(item?.source_pipeline || '').toLowerCase();
  const kind = String(item?.content_kind || item?.content_type || '').toLowerCase();
  const category = String(item?.category || '').toLowerCase();
  const url = String(item?.url || item?.link || item?.stream_url || '').split('|', 1)[0].toLowerCase();
  const name = cleanDisplayName(item?.name || item?.title || '');
  const hasMovieYear = /\((?:19|20)\d{2}\)|\b(?:19|20)\d{2}\b/.test(name);
  const hasMovieWords = /\b(?:dubbed|movie|film|web\s*series|series|natok|telefilm|episode|s\d{1,2}e\d{1,3})\b/i.test(name);
  const directVod = /\.(?:mp4|m4v|mkv|avi|mov|webm|flv)(?:$|\?)/i.test(url);
  const moviePath = /\/(?:movie|movies|film|films|vod|series|webseries|web-series|natok|telefilm)\//i.test(url);

  return (
    pipeline === 'movies' ||
    pipeline === 'movie' ||
    ['movie', 'movies', 'vod', 'film', 'series'].includes(kind) ||
    category === 'vod' ||
    category === 'movies' ||
    directVod ||
    moviePath ||
    (hasMovieYear && hasMovieWords)
  );
}

function isLikelyLiveLeak(item) {
  const pipeline = String(item?.source_pipeline || '').toLowerCase();
  const kind = String(item?.content_kind || item?.content_type || '').toLowerCase();
  const url = String(item?.url || item?.link || item?.stream_url || '').split('|', 1)[0].toLowerCase();
  const name = cleanDisplayName(item?.name || item?.title || '');
  const hasYear = /\((?:19|20)\d{2}\)|\b(?:19|20)\d{2}\b/.test(name);
  const clearlyLivePipeline = ['tv', 'live', 'livetv', 'live_tv', 'channel'].includes(pipeline) ||
    ['tv', 'live', 'livetv', 'live_tv', 'channel'].includes(kind);
  const clearlyLiveUrl = /\/cdn\/live\/|\/live\/|\.stream\/playlist\.m3u8|\/playlist\.m3u8(?:$|\?)/i.test(url);
  const liveBrandName = /\b(?:tv|television|channel|news|sports|radio|cinema|movies)\b/i.test(name);

  return clearlyLivePipeline || (!hasYear && clearlyLiveUrl && liveBrandName);
}

function sourceConfidenceScore(item) {
  const status = String(item?.verification_status || '').toLowerCase();
  const statusScores = {
    verified_global: 80,
    verified_bd: 78,
    verified: 76,
    verified_proxy: 72,
    stale_last_good: 55,
    bd_protected_pending: 48,
    geo_pending: 44,
    retryable_pending: 32,
    host_deferred: 20
  };
  let score = statusScores[status] || 0;
  if (item?.verified === true) score += 12;
  if (String(item?.url || '').startsWith('https://')) score += 5;
  if (item?.logo || item?.poster || item?.image) score += 3;
  return score;
}

function mergeDuplicateNormalizedItems(items, sourceKind) {
  const byKey = new Map();

  items.forEach((item) => {
    const priority = sourceKind === VIEW.CHANNEL ? banglaPriorityIndex(item.name) : Number.MAX_SAFE_INTEGER;
    const identity = priority !== Number.MAX_SAFE_INTEGER
      ? `bangla-priority:${priority}`
      : canonicalDisplayKey(item.name) || item.id || item.url;
    const key = `${sourceKind}:${identity}`;
    const existing = byKey.get(key);

    if (!existing) {
      byKey.set(key, item);
      return;
    }

    const winner = sourceConfidenceScore(item) > sourceConfidenceScore(existing) ? item : existing;
    const loser = winner === item ? existing : item;
    const mergedSources = [];
    const seenUrls = new Set();

    [...(winner._sources || []), ...(loser._sources || [])].forEach((source) => {
      const url = String(source?.url || '').trim();
      if (!url || seenUrls.has(url) || mergedSources.length >= 6) return;
      seenUrls.add(url);
      mergedSources.push(source);
    });

    winner._sources = mergedSources;
    winner.backups = mergedSources.slice(1, 6);
    if (!winner.logo && loser.logo) winner.logo = loser.logo;
    byKey.set(key, winner);
  });

  return [...byKey.values()].map((item, index) => ({
    ...item,
    seqNumber: index + 1,
    _uid: `${sourceKind}:${item.id || slugify(item.name)}:${index}`
  }));
}

function isPlayable(item) {
  if (!item || item.metadata_only) return false;
  if (item.url || item.link || item.stream_url) return true;
  if (Array.isArray(item._sources) && item._sources.some((source) => source?.url)) return true;
  if (Array.isArray(item.backups) && item.backups.some((source) => {
    if (typeof source === 'string') return Boolean(source.trim());
    return Boolean(source?.url || source?.link || source?.stream_url);
  })) return true;
  return false;
}

function inferSafeHeaderProfile(raw = {}) {
  const explicit = String(raw.header_profile || raw.profile || '').trim();
  if (explicit) return explicit;

  const headers = raw && typeof raw.headers === 'object' && raw.headers ? raw.headers : {};
  const referer = String(headers.Referer || headers.referer || '').toLowerCase();
  const origin = String(headers.Origin || headers.origin || '').toLowerCase();
  const url = String(raw.url || raw.link || raw.stream_url || '').toLowerCase();
  const hint = `${referer} ${origin} ${url}`;

  if (hint.includes('streame.center')) return 'streame_center';
  if (hint.includes('fibwatch.art')) return 'fibwatch';
  if (hint.includes('executeandship.com')) return 'crichd';
  if (hint.includes('toffee') || hint.includes('toffeelive')) return 'toffee_okhttp';
  if (hint.includes('aiv-cdn.net') || hint.includes('akamaized.net')) return 'android_chrome';
  return '';
}

function inferStreamType(raw = {}) {
  const explicit = String(raw.type || raw.stream_type || raw.format || '').toLowerCase();
  if (['hls', 'dash', 'media', 'mpegts', 'key', 'subtitle'].includes(explicit)) return explicit;
  const url = String(raw.url || raw.link || raw.stream_url || '').toLowerCase();
  if (url.includes('.mpd')) return 'dash';
  if (url.includes('.m3u8')) return 'hls';
  if (/\.(ts|mpegts|flv)(?:$|\?)/.test(url)) return 'mpegts';
  return 'media';
}

function inferProxyMode(raw = {}, profile = '') {
  const explicit = String(raw.proxy_mode || '').toLowerCase();
  if (['direct_first', 'proxy_first', 'proxy_only', 'direct_only', 'auto'].includes(explicit)) return explicit;

  if (raw.force_proxy || raw.proxy_required) return 'proxy_only';

  const url = String(raw.url || raw.link || raw.stream_url || '').toLowerCase();
  const status = String(raw.verification_status || '').toLowerCase();

  if (status === 'verified_proxy') return 'proxy_first';
  if (url.startsWith('http://')) return 'proxy_first';

  // These sources genuinely depend on Referer/Origin profiles.
  if (['streame_center', 'fibwatch', 'crichd'].includes(profile)) return 'proxy_first';

  // Many Toffee/Bangladesh CDN links play directly for local users. Keep the
  // old reliable direct-first behaviour and use the profile only as fallback.
  if (profile === 'toffee_okhttp') return 'direct_first';

  if (profile && !['android_tv', 'android_chrome', 'default'].includes(profile)) {
    return 'proxy_first';
  }

  return 'direct_first';
}

function shouldInheritManifestQuery(raw = {}) {
  if (typeof raw.inherit_manifest_query === 'boolean') return raw.inherit_manifest_query;
  const url = String(raw.url || raw.link || raw.stream_url || '');
  try {
    const parsed = new URL(url, location.href);
    if (!parsed.search) return false;
    const names = [...parsed.searchParams.keys()].map((key) => key.toLowerCase());
    return names.some((key) => /token|auth|signature|sig|expires|exp|key|session|hdnea|policy/.test(key));
  } catch (_) {
    return /[?&](?:token|auth|signature|sig|expires|exp|key|session|hdnea|policy)=/i.test(url);
  }
}

function normalizeBackup(backup, parent) {
  if (!backup) return null;
  const source = typeof backup === 'string' ? { url: backup } : backup;
  const url = source.url || source.link || source.stream_url;
  if (!url) return null;
  const profile = inferSafeHeaderProfile(source) || inferSafeHeaderProfile(parent);
  const proxyMode = inferProxyMode(source, profile || inferSafeHeaderProfile(parent));
  return {
    url,
    name: source.name || 'Backup',
    verification_mode: source.verification_mode || parent.verification_mode || '',
    verification_status: source.verification_status || parent.verification_status || '',
    header_profile: profile,
    proxy_mode: proxyMode,
    force_proxy: Boolean(source.force_proxy || source.proxy_required || proxyMode === 'proxy_only'),
    stream_type: inferStreamType(source),
    resolution: source.resolution || source.label || '',
    resolution_height: Number(source.resolution_height || source.height || 0),
    label: source.label || source.resolution || '',
    codec: source.codec || '',
    audio_codec: source.audio_codec || source.audioCodec || source.audio || '',
    edition: source.edition || '',
    language: source.language || '',
    width: Number(source.width || 0),
    height: Number(source.height || source.resolution_height || 0),
    bitrate: Number(source.bitrate || source.bandwidth || source.average_bitrate || 0),
    source_id: source.source_id || '',
    inherit_manifest_query: shouldInheritManifestQuery(source) || shouldInheritManifestQuery(parent)
  };
}

function rankSources(raw) {
  const primaryUrl = raw.url || raw.stream_url || raw.link || '';
  const primaryProfile = inferSafeHeaderProfile(raw);
  const primaryProxyMode = inferProxyMode(raw, primaryProfile);
  const primary = primaryUrl ? [{
    url: primaryUrl,
    name: 'Primary',
    verification_mode: raw.verification_mode || '',
    verification_status: raw.verification_status || '',
    header_profile: primaryProfile,
    proxy_mode: primaryProxyMode,
    force_proxy: Boolean(raw.force_proxy || raw.proxy_required || primaryProxyMode === 'proxy_only'),
    stream_type: inferStreamType(raw),
    resolution: raw.resolution || raw.label || '',
    resolution_height: Number(raw.resolution_height || raw.height || 0),
    label: raw.label || raw.resolution || '',
    codec: raw.codec || '',
    audio_codec: raw.audio_codec || raw.audioCodec || raw.audio || '',
    edition: raw.edition || '',
    language: raw.language || '',
    width: Number(raw.width || 0),
    height: Number(raw.height || raw.resolution_height || 0),
    bitrate: Number(raw.bitrate || raw.bandwidth || raw.average_bitrate || 0),
    inherit_manifest_query: shouldInheritManifestQuery(raw)
  }] : [];

  const backups = Array.isArray(raw.backups)
    ? raw.backups.slice(0, 5).map((entry) => normalizeBackup(entry, raw)).filter(Boolean)
    : [];

  if (!backups.length && Array.isArray(raw.links)) {
    raw.links.slice(primaryUrl ? 1 : 0, 6).forEach((entry) => {
      const normalized = normalizeBackup(entry, raw);
      if (normalized) backups.push(normalized);
    });
  }

  const seen = new Set();
  const all = [...primary, ...backups].filter((source) => {
    const key = source.url.trim();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 6);

  return all
    .map((source, originalIndex) => ({ ...source, originalIndex }))
    .sort((a, b) => {
      const aHttps = a.url.toLowerCase().startsWith('https://') ? 0 : 1;
      const bHttps = b.url.toLowerCase().startsWith('https://') ? 0 : 1;
      return aHttps - bHttps || a.originalIndex - b.originalIndex;
    });
}

function normalizeItem(raw, index, sourceKind = VIEW.CHANNEL) {
  const {
    headers: _ignoredHeaders,
    cookie: _ignoredCookie,
    authorization: _ignoredAuthorization,
    ...safeRaw
  } = raw;

  const rawName = safeRaw.name || safeRaw.title || `Item ${index + 1}`;
  const name = cleanDisplayName(rawName) || `Item ${index + 1}`;
  const id = safeRaw.id || slugify(name);
  const uid = `${sourceKind}:${id}:${index}`;
  const sources = rankSources(raw);

  return {
    ...safeRaw,
    id,
    _uid: uid,
    seqNumber: index + 1,
    name,
    title: name,
    logo: safeRaw.logo || safeRaw.poster || safeRaw.image || '',
    url: safeRaw.url || safeRaw.stream_url || safeRaw.link || '',
    category: safeRaw.category || safeRaw.group_title || state.selectedCategory || '',
    backups: Array.isArray(safeRaw.backups)
      ? safeRaw.backups.slice(0, 5).map((entry) => normalizeBackup(entry, raw)).filter(Boolean)
      : [],
    header_profile: inferSafeHeaderProfile(raw),
    proxy_mode: inferProxyMode(raw, inferSafeHeaderProfile(raw)),
    stream_type: inferStreamType(raw),
    width: Number(safeRaw.width || 0),
    height: Number(safeRaw.height || 0),
    bitrate: Number(safeRaw.bitrate || safeRaw.bandwidth || safeRaw.average_bitrate || 0),
    inherit_manifest_query: shouldInheritManifestQuery(raw),
    _sources: sources,
    _sourceKind: sourceKind
  };
}

function normalizeList(rawList, sourceKind) {
  if (!Array.isArray(rawList)) return [];

  const normalized = rawList
    .filter((item) => item && item.publish_allowed !== false)
    .filter((item) => !isFailedPublishedItem(item))
    .map((item, index) => normalizeItem(item, index, sourceKind))
    .filter((item) => {
      if (sourceKind === VIEW.CHANNEL) return !isLikelyVodLeak(item);
      if (sourceKind === VIEW.MOVIE) return !isLikelyLiveLeak(item) && isPlayable(item);
      return isPlayable(item);
    });

  return mergeDuplicateNormalizedItems(normalized, sourceKind);
}

async function loadRuntimeAndManifest() {
  showPlayerMessage('Click TV data প্রস্তুত করা হচ্ছে…');
  state.runtime = await fetchJson('/runtime-config.json', { cache: 'no-store' });
  const manifestPath = state.runtime.data_manifest || '/data/manifest.json';
  state.manifest = await fetchJson(manifestPath, { cache: 'no-store' });
  state.manifestVersion = String(state.manifest.updated_at || Date.now());
  buildNavigation();
  applyNetworkMode(readNetworkMode(), false);

  const todayVisible = Boolean(state.manifest.today_match?.visible && Number(state.manifest.today_match?.count || 0) > 0);
  if (todayVisible) {
    await selectMainView('today-match', null, { initial: true });
    return;
  }

  const firstChannel = Object.entries(state.manifest.channels || {}).find(([, entry]) => entry?.visible !== false && Number(entry?.count || 0) > 0);
  if (firstChannel) {
    await selectMainView('channel', firstChannel[0], { initial: true });
    return;
  }

  hidePlayerMessage();
  showListMessage('বর্তমানে কোনো চ্যানেল পাওয়া যায়নি', 'fa-exclamation-triangle');
}

function createChip(label, iconClass, onClick, dataset = {}) {
  const button = document.createElement('button');
  button.className = 'chip';
  button.type = 'button';
  button.innerHTML = `${iconClass ? `<i class="fas ${escapeHtml(iconClass)}"></i> ` : ''}${escapeHtml(label)}`;
  Object.entries(dataset).forEach(([key, value]) => { button.dataset[key] = value; });
  button.addEventListener('click', onClick);
  return button;
}

function buildNavigation() {
  chipsContainer.replaceChildren();

  if (state.manifest.today_match?.visible && Number(state.manifest.today_match.count || 0) > 0) {
    chipsContainer.appendChild(createChip('Today Match', 'fa-trophy', (event) => selectMainView('today-match', null, { chip: event.currentTarget }), { view: 'today-match' }));
  }

  if (state.manifest.upcoming?.visible && Number(state.manifest.upcoming.count || 0) > 0) {
    chipsContainer.appendChild(createChip('Upcoming', 'fa-calendar-alt', (event) => selectMainView('upcoming', null, { chip: event.currentTarget }), { view: 'upcoming' }));
  }

  const channelEntries = Object.entries(state.manifest.channels || {}).filter(([, entry]) => entry?.visible !== false);
  let movieInserted = false;
  channelEntries.forEach(([label, entry], index) => {
    chipsContainer.appendChild(createChip(label, '', (event) => selectMainView('channel', label, { chip: event.currentTarget }), { view: 'channel', category: label }));
    if (!movieInserted && (label.toLowerCase() === 'sports' || index === 1)) {
      chipsContainer.appendChild(createChip('Movie', 'fa-film', (event) => openMovieParentMode(event.currentTarget), { view: 'movie' }));
      movieInserted = true;
    }
  });

  if (!movieInserted) {
    chipsContainer.appendChild(createChip('Movie', 'fa-film', (event) => openMovieParentMode(event.currentTarget), { view: 'movie' }));
  }

  chipsContainer.appendChild(createChip('Favorites', 'fa-star', (event) => selectMainView('favorites', null, { chip: event.currentTarget }), { view: 'favorite' }));
  chipsContainer.appendChild(createChip('Recent', 'fa-history', (event) => selectMainView('recent', null, { chip: event.currentTarget }), { view: 'recent' }));
  buildMovieSubcategories();
  renderFinalNavigation();
}

function buildMovieSubcategories() {
  movieSubcategoryBar.replaceChildren();
  MOVIE_ORDER.forEach(([label, slug]) => {
    const button = document.createElement('button');
    button.className = 'sub-chip';
    button.type = 'button';
    button.dataset.movieCat = slug;
    button.textContent = label;
    button.addEventListener('click', () => selectMovieSubcategory(slug, button));
    movieSubcategoryBar.appendChild(button);
  });
}


const FINAL_MAIN_GROUPS = Object.freeze([
  ['sports', 'Live Sports'],
  ['live-tv', 'Live TV'],
  ['movies', 'Movies'],
  ['drama', 'Drama'],
  ['favorites', 'Favorites']
]);

const FINAL_LIVE_TV_CATEGORIES = Object.freeze([
  ['bangla', 'Bangla', ['Bangla']],
  ['indian', 'Indian', ['Indian']],
  ['cartoon', 'Cartoon', ['Cartoon']],
  ['islamic', 'Islamic', ['Islamic']],
  ['infotainments', 'Infotainments', ['Infotainments', 'Infotainment']],
  ['foreign-news', 'Foreign News', ['Foreign News', 'Foreign']],
  ['others', 'Others', ['Others', 'Other']]
]);

function finalButton(label, className, active, handler, key) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `${className}${active ? ' active' : ''}`;
  button.textContent = label;
  if (key) button.dataset.finalKey = key;
  button.addEventListener('click', handler);
  return button;
}

function finalChannelLabel(candidates = []) {
  const entries = Object.keys(state.manifest?.channels || {});
  const wanted = candidates.map((value) => canonicalDisplayKey(value));
  return entries.find((label) => wanted.includes(canonicalDisplayKey(label))) || '';
}

function finalSubItems(group = state.activeMainGroup) {
  if (group === 'sports') {
    return [
      ['today-match', 'Today Match'],
      ['upcoming', 'Upcoming Match'],
      ['sports-channel', 'Sports']
    ];
  }
  if (group === 'live-tv') {
    return FINAL_LIVE_TV_CATEGORIES.map(([key, label]) => [key, label]);
  }
  if (group === 'movies') return MOVIE_ORDER.map(([label, slug]) => [`movie:${slug}`, label]);
  return [];
}

function renderFinalMainNavigation() {
  [desktopMainNav, mobileMainNav].forEach((root) => {
    if (!root) return;
    root.replaceChildren();
    FINAL_MAIN_GROUPS.forEach(([key, label]) => {
      root.appendChild(finalButton(
        label,
        'final-main-button tv-focusable',
        state.activeMainGroup === key,
        () => selectFinalMainGroup(key),
        key
      ));
    });
  });
}

function renderFinalSubNavigation() {
  const items = finalSubItems();
  [desktopSubNav, mobileSubNav].forEach((root) => {
    if (!root) return;
    root.replaceChildren();
    items.forEach(([key, label]) => {
      root.appendChild(finalButton(
        label,
        'final-sub-button tv-focusable',
        state.activeFinalSub === key,
        () => selectFinalSubcategory(key),
        key
      ));
    });
  });
  const visible = items.length > 0;
  if (desktopSubNavigation) desktopSubNavigation.style.display = visible ? '' : 'none';
  if (mobileSubNavigation) mobileSubNavigation.style.display = visible ? '' : 'none';
}

function renderFinalNavigation() {
  renderFinalMainNavigation();
  renderFinalSubNavigation();
}

function showFinalEmpty(label, kind = 'Items') {
  cancelDataLoading();
  clearCurrentListState();
  closeMobileSearch(true);
  setSearchQuery('');
  state.selectedCategory = label;
  state.selectedMovieCategory = null;
  state.currentItems = [];
  state.filteredItems = [];
  setSidebarCount(`0 ${kind}`);
  showListMessage(`${label} content এখনো যোগ করা হয়নি`, 'fa-info-circle');
  hidePlayerMessage();
  renderFinalNavigation();
}

async function selectFinalMainGroup(group) {
  state.activeMainGroup = group;
  if (group === 'sports') state.activeFinalSub = 'today-match';
  else if (group === 'live-tv') state.activeFinalSub = 'bangla';
  else if (group === 'movies') state.activeFinalSub = 'movie:bangla';
  else state.activeFinalSub = '';
  renderFinalNavigation();

  if (group === 'favorites') {
    await selectMainView('favorites', null, { chip: activateChipByView('favorite'), preserveFinalGroup: true });
    return;
  }
  if (group === 'drama') {
    showFinalEmpty('Drama', 'Items');
    return;
  }
  await selectFinalSubcategory(state.activeFinalSub);
}

async function selectFinalSubcategory(key) {
  state.activeFinalSub = key;
  renderFinalNavigation();

  if (key === 'today-match') {
    await selectMainView('today-match', null, { chip: activateChipByView('today-match'), preserveFinalGroup: true });
    return;
  }
  if (key === 'upcoming') {
    await selectMainView('upcoming', null, { chip: activateChipByView('upcoming'), preserveFinalGroup: true });
    return;
  }
  if (key === 'sports-channel') {
    const category = finalChannelLabel(['Sports']);
    if (!category) return showFinalEmpty('Sports', 'Channels');
    await selectMainView('channel', category, { chip: activateChipByView('channel', category), preserveFinalGroup: true });
    return;
  }
  if (key.startsWith('movie:')) {
    const slug = key.slice(6);
    const legacy = qs(`.sub-chip[data-movie-cat="${cssEscape(slug)}"]`, movieSubcategoryBar);
    await selectMovieSubcategory(slug, legacy, { preserveFinalGroup: true });
    return;
  }

  const config = FINAL_LIVE_TV_CATEGORIES.find(([entryKey]) => entryKey === key);
  if (config) {
    const category = finalChannelLabel(config[2]);
    if (!category) return showFinalEmpty(config[1], 'Channels');
    await selectMainView('channel', category, { chip: activateChipByView('channel', category), preserveFinalGroup: true });
  }
}

function adoptFinalNavigationFromLegacy(view, category = '') {
  if (view === 'today-match') {
    state.activeMainGroup = 'sports';
    state.activeFinalSub = 'today-match';
  } else if (view === 'upcoming') {
    state.activeMainGroup = 'sports';
    state.activeFinalSub = 'upcoming';
  } else if (view === 'favorites') {
    state.activeMainGroup = 'favorites';
    state.activeFinalSub = '';
  } else if (view === 'movie') {
    state.activeMainGroup = 'movies';
    state.activeFinalSub = `movie:${state.selectedMovieCategory || 'bangla'}`;
  } else if (view === 'channel') {
    const key = canonicalDisplayKey(category);
    if (key.includes('sports')) {
      state.activeMainGroup = 'sports';
      state.activeFinalSub = 'sports-channel';
    } else {
      state.activeMainGroup = 'live-tv';
      const match = FINAL_LIVE_TV_CATEGORIES.find(([, , candidates]) =>
        candidates.some((candidate) => canonicalDisplayKey(candidate) === key)
      );
      state.activeFinalSub = match?.[0] || 'others';
    }
  }
  renderFinalNavigation();
}

function setupFinalNavigationControls() {
  const scroll = (root, direction) => {
    if (!root) return;
    root.scrollBy({ left: direction * Math.max(150, root.clientWidth * .72), behavior: 'smooth' });
  };
  $('desktopMainPrevBtn')?.addEventListener('click', () => scroll(desktopMainNav, -1));
  $('desktopMainNextBtn')?.addEventListener('click', () => scroll(desktopMainNav, 1));
  $('desktopSubPrevBtn')?.addEventListener('click', () => scroll(desktopSubNav, -1));
  $('desktopSubNextBtn')?.addEventListener('click', () => scroll(desktopSubNav, 1));
  $('mobileMainPrevBtn')?.addEventListener('click', () => scroll(mobileMainNav, -1));
  $('mobileMainNextBtn')?.addEventListener('click', () => scroll(mobileMainNav, 1));
  $('mobileSubPrevBtn')?.addEventListener('click', () => scroll(mobileSubNav, -1));
  $('mobileSubNextBtn')?.addEventListener('click', () => scroll(mobileSubNav, 1));
}

function setActiveMainChip(chip) {
  qsa('.chip', chipsContainer).forEach((item) => item.classList.toggle('active', item === chip));
}

function activateChipByView(view, category = null) {
  const selector = category
    ? `.chip[data-view="${cssEscape(view)}"][data-category="${cssEscape(category)}"]`
    : `.chip[data-view="${cssEscape(view)}"]`;
  const chip = qs(selector, chipsContainer);
  setActiveMainChip(chip);
  return chip;
}

function cancelDataLoading() {
  state.dataSessionId += 1;
  state.movieCategorySessionId += 1;
  if (state.dataAbortController) state.dataAbortController.abort();
  state.dataAbortController = null;
  state.moviePageLoading = false;
}

function cancelPendingImages(root) {
  qsa('img', root).forEach((image) => {
    image.removeAttribute('src');
    image.removeAttribute('srcset');
  });
}

function scrollSidebarToTop() {
  sidebarList.scrollTop = 0;
  if (sidebarScrollArea) sidebarScrollArea.scrollTop = 0;
  if (window.matchMedia('(max-width: 1000px)').matches) {
    sidebarSection.scrollTop = 0;
  }
}

function getSidebarScrollTop() {
  if (sidebarScrollArea && sidebarScrollArea.scrollHeight > sidebarScrollArea.clientHeight) {
    return Number(sidebarScrollArea.scrollTop || 0);
  }
  if (sidebarList && sidebarList.scrollHeight > sidebarList.clientHeight) {
    return Number(sidebarList.scrollTop || 0);
  }
  return Number(sidebarSection?.scrollTop || 0);
}

function restoreSidebarScroll(top = 0) {
  const value = Math.max(0, Number(top || 0));
  requestAnimationFrame(() => {
    if (sidebarScrollArea) sidebarScrollArea.scrollTop = value;
    if (sidebarList) sidebarList.scrollTop = value;
    if (window.matchMedia('(max-width: 1000px)').matches && sidebarSection) {
      sidebarSection.scrollTop = value;
    }
  });
}

function setSeriesDetailMode(active) {
  state.seriesDetailMode = Boolean(active);
  sidebarSection?.classList.toggle('series-mode', state.seriesDetailMode);
  sidebarList?.classList.toggle('series-detail-list', state.seriesDetailMode);
}

function clearCurrentListState() {
  seriesModule?.resetDetail?.({ preservePlaybackContext: true, preserveCatalogSnapshot: false });
  setSeriesDetailMode(false);
  state.currentItems = [];
  state.filteredItems = [];
  state.renderedCount = 0;
  state.movieIndex = null;
  state.moviePageCursor = 0;
  state.moviePreviewMode = false;
  cancelPendingImages(sidebarList);
  sidebarList.replaceChildren();
  sidebarList.classList.remove('movie-grid', 'upcoming-grid', 'series-detail-list');
  state.drawerRenderedForSession = -1;
}

async function selectMainView(view, category, options = {}) {
  cancelDataLoading();
  clearCurrentListState();
  closeMobileSearch(true);
  setSearchQuery('');
  state.currentQuery = '';
  state.selectedMovieCategory = null;
  qsa('.sub-chip', movieSubcategoryBar).forEach((item) => item.classList.remove('active'));
  movieSubcategoryBar.style.display = 'none';
  setSearchEnabled(true);
  if (!options.preserveFinalGroup) adoptFinalNavigationFromLegacy(view, category || '');
  else renderFinalNavigation();

  const chip = options.chip || activateChipByView(view === 'today-match' ? 'today-match' : view, category);
  if (chip) setActiveMainChip(chip);

  if (view === 'recent') {
    state.view = VIEW.RECENT;
    state.selectedCategory = 'Recent';
    state.currentItems = getRecentItems();
    renderCurrentList(true);
    hidePlayerMessage();
    if (options.initial && !state.currentItem && state.currentItems.length) startPlayback(state.currentItems[0], false);
    return;
  }

  if (view === 'favorites') {
    state.view = VIEW.FAVORITE;
    state.selectedCategory = 'Favorites';
    state.currentItems = getFavoriteItems();
    renderCurrentList(true);
    hidePlayerMessage();
    return;
  }

  let path = '';
  let kind = VIEW.CHANNEL;
  let label = category || '';

  if (view === 'today-match') {
    kind = VIEW.EVENT;
    label = 'Today Match';
    path = state.manifest.today_match?.url;
  } else if (view === 'upcoming') {
    kind = VIEW.UPCOMING;
    label = 'Upcoming';
    path = state.manifest.upcoming?.url;
  } else {
    kind = VIEW.CHANNEL;
    path = state.manifest.channels?.[category]?.url;
  }

  state.view = kind;
  state.selectedCategory = label;
  showListMessage(`${label} তালিকা লোড হচ্ছে…`, 'fa-spinner', true);
  setSidebarCount('Loading...');

  if (!path) {
    showListMessage('এই বিভাগের JSON path পাওয়া যায়নি', 'fa-exclamation-triangle');
    setSidebarCount('0 Items');
    hidePlayerMessage();
    return;
  }

  const sessionId = ++state.dataSessionId;
  const controller = new AbortController();
  state.dataAbortController = controller;

  try {
    const data = await fetchJson(path, { signal: controller.signal, cache: 'no-store' });
    if (sessionId !== state.dataSessionId) return;
    const raw = Array.isArray(data)
      ? data
      : (data.channels || data.items || data.events || []);
    state.currentItems = normalizeList(raw, kind);
    renderCurrentList(true);
    hidePlayerMessage();

    if (options.initial && !state.currentItem && state.currentItems.length && kind !== VIEW.UPCOMING) {
      const firstPlayable = state.currentItems.find(isPlayable);
      if (firstPlayable) startPlayback(firstPlayable, false);
    }
  } catch (error) {
    if (error.name === 'AbortError' || sessionId !== state.dataSessionId) return;
    console.error(error);
    showListMessage(`${label} তালিকা লোড করা যায়নি। আবার চেষ্টা করুন।`, 'fa-exclamation-triangle');
    setSidebarCount('0 Items');
    hidePlayerMessage();
  }
}

async function openMovieParentMode(chip) {
  state.activeMainGroup = 'movies';
  state.activeFinalSub = 'movie:bangla';
  renderFinalNavigation();
  state.currentSortMode = 'default';
  $('sortSelect').value = 'default';
  setActiveMainChip(chip || activateChipByView('movie'));
  movieSubcategoryBar.style.display = 'flex';
  const banglaButton = qs('.sub-chip[data-movie-cat="bangla"]', movieSubcategoryBar);
  if (!banglaButton) {
    showListMessage('Bangla movie বিভাগ পাওয়া যায়নি', 'fa-exclamation-triangle');
    setSidebarCount('0 Movies');
    return;
  }
  scrollSidebarToTop();
  await selectMovieSubcategory('bangla', banglaButton);
}

async function loadMovieParentPreview() {
  const controller = new AbortController();
  state.dataAbortController = controller;
  const sessionId = ++state.dataSessionId;

  try {
    const perCategoryLimit = Math.max(2, Math.ceil(MOVIE_PREVIEW_LIMIT / MOVIE_ORDER.length));
    const categoryBuckets = [];
    const overflowPool = [];

    for (const [label, slug] of MOVIE_ORDER) {
      const entry = manifestMovieEntry(slug);
      if (!entry?.index) continue;

      try {
        const indexData = await fetchJson(entry.index, {
          signal: controller.signal,
          cache: 'no-store'
        });
        if (sessionId !== state.dataSessionId || !state.moviePreviewMode) return;

        const pages = Array.isArray(indexData.pages) ? indexData.pages : [];
        if (!pages.length) continue;

        const pagePath = pages[0].path ||
          (pages[0].file ? `data/movies/${indexData.slug || slug}/${pages[0].file}` : '');
        if (!pagePath) continue;

        const pageData = await fetchJson(pagePath, {
          signal: controller.signal,
          cache: 'no-store'
        });
        if (sessionId !== state.dataSessionId || !state.moviePreviewMode) return;

        const rawItems = pageData.items || pageData.movies || [];
        const normalized = normalizeList(rawItems, VIEW.MOVIE);
        if (!normalized.length) continue;

        const bucket = normalized.slice(0, perCategoryLimit).map((item) => ({
          ...item,
          _previewCategory: label,
          _previewSlug: slug
        }));
        categoryBuckets.push(...bucket);

        normalized.slice(perCategoryLimit).forEach((item) => {
          overflowPool.push({
            ...item,
            _previewCategory: label,
            _previewSlug: slug
          });
        });
      } catch (categoryError) {
        if (categoryError?.name === 'AbortError') throw categoryError;
        console.warn(`Movie preview category skipped: ${slug}`, categoryError);
      }
    }

    const preview = categoryBuckets.slice(0, MOVIE_PREVIEW_LIMIT);
    if (preview.length < MOVIE_PREVIEW_LIMIT) {
      const used = new Set(preview.map((item) => `${canonicalDisplayKey(item.name)}:${item.url}`));
      for (const item of overflowPool) {
        const key = `${canonicalDisplayKey(item.name)}:${item.url}`;
        if (used.has(key)) continue;
        used.add(key);
        preview.push(item);
        if (preview.length >= MOVIE_PREVIEW_LIMIT) break;
      }
    }

    if (!preview.length) {
      showListMessage('মুভি দেখতে একটি বিভাগ নির্বাচন করুন', 'fa-film');
      setSidebarCount('Movie Preview');
      return;
    }

    preview.forEach((item, index) => {
      item.seqNumber = index + 1;
      item._uid = `movie-preview:${item._previewSlug || 'mix'}:${item.id}:${index}`;
    });

    state.currentItems = preview;
    state.filteredItems = preview.slice();
    renderCurrentList(true);
    setSidebarCount(`${preview.length} Movie Preview · বিভাগ নির্বাচন করে আরও দেখুন`);
  } catch (error) {
    if (error.name === 'AbortError' || sessionId !== state.dataSessionId) return;
    console.warn('Movie preview load failed:', error);
    showListMessage('মুভি দেখতে একটি বিভাগ নির্বাচন করুন', 'fa-film');
    setSidebarCount('Movie Preview');
  }
}

async function selectMovieSubcategory(slug, button, options = {}) {
  cancelDataLoading();
  clearCurrentListState();
  state.view = VIEW.MOVIE;
  state.selectedCategory = 'Movie';
  state.selectedMovieCategory = slug;
  state.activeMainGroup = 'movies';
  state.activeFinalSub = `movie:${slug}`;
  renderFinalNavigation();
  state.moviePreviewMode = false;
  setSearchQuery('');
  state.currentQuery = '';
  setActiveMainChip(activateChipByView('movie'));
  movieSubcategoryBar.style.display = 'flex';
  qsa('.sub-chip', movieSubcategoryBar).forEach((item) => item.classList.toggle('active', item === button));
  scrollSidebarToTop();
  button?.scrollIntoView({ behavior: 'auto', block: 'nearest', inline: 'nearest' });
  setSearchEnabled(true);
  showListMessage('মুভির তালিকা লোড হচ্ছে…', 'fa-spinner', true);
  setSidebarCount('Loading...');

  const movieEntry = manifestMovieEntry(slug);
  if (!movieEntry?.index) {
    if (seriesModule) {
      const seriesItems = await seriesModule.loadCategory(slug);
      state.currentItems = [];
      seriesModule.mergeCategoryItems(seriesItems);
      if (state.currentItems.length) {
        renderCurrentList(true);
        return;
      }
    }
    showListMessage('এই বিভাগে বর্তমানে কোনো মুভি বা Series পাওয়া যায়নি', 'fa-info-circle');
    setSidebarCount('0 Titles');
    return;
  }

  const sessionId = ++state.movieCategorySessionId;
  const dataSessionId = ++state.dataSessionId;
  const controller = new AbortController();
  state.dataAbortController = controller;

  try {
    const indexData = await fetchJson(movieEntry.index, { signal: controller.signal, cache: 'no-store' });
    if (sessionId !== state.movieCategorySessionId || dataSessionId !== state.dataSessionId) return;
    state.movieIndex = indexData;
    state.moviePageCursor = 0;

    if (!Array.isArray(indexData.pages) || !indexData.pages.length || Number(indexData.count ?? indexData.pages.length) === 0) {
      showListMessage('এই বিভাগে বর্তমানে কোনো মুভি পাওয়া যায়নি', 'fa-info-circle');
      setSidebarCount('0 Movies');
      return;
    }

    await loadNextMoviePage({ initial: true, sessionId, dataSessionId, signal: controller.signal });
    if (seriesModule) {
      const seriesItems = await seriesModule.loadCategory(slug);
      if (sessionId !== state.movieCategorySessionId || dataSessionId !== state.dataSessionId) return;
      seriesModule.mergeCategoryItems(seriesItems);
      renderCurrentList(true);
    }
  } catch (error) {
    if (error.name === 'AbortError' || sessionId !== state.movieCategorySessionId || dataSessionId !== state.dataSessionId) return;
    console.error(error);
    showListMessage('মুভির তালিকা লোড করা যায়নি। আবার চেষ্টা করুন।', 'fa-exclamation-triangle');
    setSidebarCount('0 Movies');
  }
}

function moviePagePath(pageEntry) {
  if (pageEntry.path) return pageEntry.path;
  if (pageEntry.file && state.movieIndex?.slug) return `data/movies/${state.movieIndex.slug}/${pageEntry.file}`;
  return '';
}

async function loadNextMoviePage(options = {}) {
  if (state.seriesDetailMode || seriesModule?.detailActive) return false;
  if (state.moviePageLoading || !state.movieIndex?.pages) return false;
  if (state.moviePageCursor >= state.movieIndex.pages.length) return false;

  const sessionId = options.sessionId ?? state.movieCategorySessionId;
  const dataSessionId = options.dataSessionId ?? state.dataSessionId;
  const pageEntry = state.movieIndex.pages[state.moviePageCursor];
  const path = moviePagePath(pageEntry);
  if (!path) return false;

  state.moviePageLoading = true;
  try {
    const pageData = await fetchJson(path, {
      signal: options.signal || state.dataAbortController?.signal,
      cache: 'no-store'
    });
    if (
      sessionId !== state.movieCategorySessionId ||
      dataSessionId !== state.dataSessionId ||
      state.seriesDetailMode ||
      seriesModule?.detailActive
    ) return false;
    const items = normalizeList(pageData.items || pageData.movies || pageData.channels || [], VIEW.MOVIE);
    const startIndex = state.currentItems.length;
    items.forEach((item, offset) => {
      item.seqNumber = startIndex + offset + 1;
      item._uid = `movie:${item.id}:${startIndex + offset}`;
    });
    state.currentItems.push(...items);
    state.moviePageCursor += 1;

    if (options.initial) {
      showListMessage('মুভির তালিকা প্রস্তুত করা হচ্ছে…', 'fa-film');
      renderCurrentList(true);
    } else if (!options.deferRender) {
      applyFilterAndSort();
      appendNextChunk();
    }
    return items.length > 0;
  } finally {
    state.moviePageLoading = false;
  }
}

function manualTrustedItemCount(items = state.currentItems) {
  return items.reduce((count, item) => {
    const manual = item?.manual_source === true ||
      String(item?.verification_status || '').toLowerCase() === 'manual_trusted';
    return count + (manual ? 1 : 0);
  }, 0);
}

async function preloadRemainingManualMoviePages(options = {}) {
  if (state.view !== VIEW.MOVIE || state.moviePreviewMode || !state.movieIndex) return;
  const expectedManual = Number(
    state.movieIndex.manual_trusted_count ||
    state.movieIndex.status_counts?.manual_trusted ||
    0
  );
  if (expectedManual <= 0 || manualTrustedItemCount() >= expectedManual) return;

  let loadedAnotherPage = false;
  while (
    manualTrustedItemCount() < expectedManual &&
    state.moviePageCursor < (state.movieIndex.pages?.length || 0)
  ) {
    const loaded = await loadNextMoviePage({
      sessionId: options.sessionId,
      dataSessionId: options.dataSessionId,
      signal: options.signal,
      deferRender: true
    });
    if (!loaded) break;
    loadedAnotherPage = true;
  }

  if (loadedAnotherPage) renderCurrentList(true);
}


function getRecentItems() {
  const stored = readJsonStorage(STORAGE_KEYS.recentItems, []);
  return Array.isArray(stored)
    ? stored.map((item, index) => normalizeItem(item, index, item._sourceKind || VIEW.CHANNEL))
    : [];
}

function compactItem(item) {
  return {
    id: item.id,
    name: item.name,
    logo: item.logo,
    category: item.category,
    url: item.url,
    backups: item.backups || [],
    verification_mode: item.verification_mode || '',
    header_profile: item.header_profile || item.profile || '',
    source_pipeline: item.source_pipeline || '',
    metadata_only: Boolean(item.metadata_only),
    drm: item.drm || null,
    resolution: item.resolution || '',
    resolution_height: Number(item.resolution_height || item.height || 0),
    label: item.label || item.resolution || '',
    codec: item.codec || '',
    audio_codec: item.audio_codec || item.audioCodec || item.audio || '',
    year: item.year || '',
    rating: item.rating || '',
    _sourceKind: item._sourceKind || state.view
  };
}

function saveRecentItem(item) {
  if (!item || !isPlayable(item)) return;
  const compact = compactItem(item);
  const old = readJsonStorage(STORAGE_KEYS.recentItems, []);
  const next = [compact, ...old.filter((entry) => entry.url !== compact.url && entry.id !== compact.id)].slice(0, 15);
  writeJsonStorage(STORAGE_KEYS.recentItems, next);
}

function favoriteIds() {
  const list = readJsonStorage(STORAGE_KEYS.favorites, []);
  return Array.isArray(list) ? list : [];
}

function getFavoriteItems() {
  const stored = readJsonStorage(STORAGE_KEYS.favoriteItems, []);
  if (!Array.isArray(stored)) return [];
  const allowed = new Set(favoriteIds());
  return stored
    .filter((item) => allowed.has(item.id || item.url))
    .map((item, index) => normalizeItem(item, index, item._sourceKind || VIEW.CHANNEL));
}

function toggleFavorite(uid, event) {
  event?.stopPropagation();
  const item = state.currentItems.find((entry) => entry._uid === uid) || (state.currentItem?._uid === uid ? state.currentItem : null);
  if (!item) return;
  if (seriesModule?.handleFavorite(uid, event)) return;
  const key = item.id || item.url;
  const favorites = favoriteIds();
  const isFavorite = favorites.includes(key);
  writeJsonStorage(STORAGE_KEYS.favorites, isFavorite ? favorites.filter((id) => id !== key) : [...favorites, key]);

  const snapshots = readJsonStorage(STORAGE_KEYS.favoriteItems, []);
  const cleaned = (Array.isArray(snapshots) ? snapshots : []).filter((entry) => (entry.id || entry.url) !== key);
  writeJsonStorage(STORAGE_KEYS.favoriteItems, isFavorite ? cleaned : [compactItem(item), ...cleaned].slice(0, 300));

  updateFavoriteUi();
  showToast(isFavorite ? 'Bookmark সরানো হয়েছে' : 'Bookmark যোগ করা হয়েছে');
  if (state.view === VIEW.FAVORITE) {
    state.currentItems = getFavoriteItems();
    renderCurrentList(true);
  }
}

function updateFavoriteUi() {
  const favorites = favoriteIds();
  qsa('[data-favorite-id]', sidebarList).forEach((button) => {
    const active = favorites.includes(button.dataset.favoriteId);
    button.classList.toggle('active', active);
    const icon = qs('i', button);
    if (icon) icon.className = active ? 'fas fa-star' : 'far fa-star';
  });
  const active = state.currentItem && favorites.includes(state.currentItem.id || state.currentItem.url);
  $('favActionBtn').classList.toggle('active', Boolean(active));
  seriesModule?.updateActiveCards?.();
}

function setSearchQuery(value, sourceInput = null) {
  state.searchQuery = String(value || '').trim();
  if (searchInput !== sourceInput) searchInput.value = state.searchQuery;
  if (mobileSearchInput !== sourceInput) mobileSearchInput.value = state.searchQuery;
}

function currentSearchValue() {
  return state.searchQuery.toLowerCase();
}

function movieYearValue(item) {
  const direct = Number.parseInt(String(item?.year || '').match(/(?:19|20)\d{2}/)?.[0] || '', 10);
  if (Number.isFinite(direct)) return direct;
  const fromDate = Number.parseInt(String(item?.release_date || item?.released || '').match(/(?:19|20)\d{2}/)?.[0] || '', 10);
  if (Number.isFinite(fromDate)) return fromDate;
  const fromName = Number.parseInt(String(item?.name || item?.title || '').match(/(?:19|20)\d{2}/)?.[0] || '', 10);
  return Number.isFinite(fromName) ? fromName : 0;
}

function applyFilterAndSort() {
  const query = currentSearchValue();
  state.currentQuery = query;
  let items = state.currentItems.slice();

  if (query) {
    items = items.filter((item) => {
      const haystack = `${item.name || ''} ${item.category || ''} ${item.competition || ''}`.toLowerCase();
      return haystack.includes(query);
    });
  }

  if (state.currentSortMode === 'az') {
    items.sort((a, b) => a.name.localeCompare(b.name));
  } else if (state.currentSortMode === 'za') {
    items.sort((a, b) => b.name.localeCompare(a.name));
  } else if (state.view === VIEW.MOVIE) {
    items.sort((a, b) => {
      const yearDifference = movieYearValue(b) - movieYearValue(a);
      if (yearDifference) return yearDifference;
      return (a.seqNumber || 0) - (b.seqNumber || 0);
    });
  } else if (
    state.view === VIEW.CHANNEL &&
    canonicalDisplayKey(state.selectedCategory).includes('bangla')
  ) {
    items.sort((a, b) => {
      const aPriority = banglaPriorityIndex(a.name);
      const bPriority = banglaPriorityIndex(b.name);
      if (aPriority !== bPriority) return aPriority - bPriority;
      return (a.seqNumber || 0) - (b.seqNumber || 0);
    });
  }

  state.filteredItems = items;
}

function renderCurrentList(reset = true, options = {}) {
  if (state.seriesDetailMode || seriesModule?.detailActive) return;
  if (state.view === VIEW.MOVIE && !state.selectedMovieCategory && !state.moviePreviewMode) {
    showListMessage(MOVIE_PROMPT_TEXT, 'fa-film');
    setSidebarCount('0 Movies');
    return;
  }
  applyFilterAndSort();
  sidebarList.classList.toggle(
    'upcoming-grid',
    state.view === VIEW.UPCOMING || state.view === VIEW.EVENT
  );
  if (reset) {
    cancelPendingImages(sidebarList);
    sidebarList.replaceChildren();
    state.renderedCount = 0;
    if (!options.preserveScroll) scrollSidebarToTop();
  }

  const totalKnown = state.view === VIEW.MOVIE && state.movieIndex
    ? Number(state.movieIndex.count || state.currentItems.length)
    : state.filteredItems.length;

  if (state.view === VIEW.MOVIE) {
    sidebarList.classList.add('movie-grid');
    if (state.moviePreviewMode) {
      setSidebarCount(`${state.currentItems.length} Movie Preview · বিভাগ নির্বাচন করে আরও দেখুন`);
    } else {
      const manualTotal = Number(
        state.movieIndex?.manual_trusted_count ||
        state.movieIndex?.status_counts?.manual_trusted ||
        manualTrustedItemCount()
      );
      const manualText = manualTotal > 0 ? `${manualTotal} Manual · ` : '';
      setSidebarCount(`${manualText}${state.currentItems.length}/${totalKnown} Movies loaded`);
    }
  } else if (state.view === VIEW.UPCOMING || state.view === VIEW.EVENT) {
    sidebarList.classList.remove('movie-grid');
    setSidebarCount(`${state.filteredItems.length} Events`);
  } else if (state.view === VIEW.FAVORITE) {
    sidebarList.classList.remove('movie-grid');
    setSidebarCount(`${state.filteredItems.length} Bookmarks`);
  } else if (state.view === VIEW.RECENT) {
    sidebarList.classList.remove('movie-grid');
    setSidebarCount(`${state.filteredItems.length} Recent`);
  } else {
    sidebarList.classList.remove('movie-grid');
    setSidebarCount(`${state.filteredItems.length} Channels`);
  }

  if (!state.filteredItems.length) {
    let message = 'কোনো আইটেম পাওয়া যায়নি';
    if (state.currentQuery) message = 'কোনো ফলাফল পাওয়া যায়নি';
    else if (state.view === VIEW.MOVIE) message = 'এই বিভাগে বর্তমানে কোনো মুভি পাওয়া যায়নি';
    else if (state.view === VIEW.FAVORITE) message = 'কোনো Bookmark সংরক্ষিত নেই — পছন্দের চ্যানেল/মুভিতে ☆ চাপুন';
    else if (state.view === VIEW.RECENT) message = 'সম্প্রতি দেখা কোনো আইটেম নেই';
    showListMessage(message, 'fa-info-circle');
    return;
  }

  let initialLimit = Number(options.initialLimit || 0) || (state.view === VIEW.MOVIE ? MOVIE_CHUNK_SIZE : CHANNEL_INITIAL_CHUNK);
  if (state.view === VIEW.MOVIE && !state.moviePreviewMode) {
    const lastManualIndex = state.filteredItems.reduce((lastIndex, item, index) => {
      const manual = item?.manual_source === true ||
        String(item?.verification_status || '').toLowerCase() === 'manual_trusted';
      return manual ? index : lastIndex;
    }, -1);
    if (lastManualIndex >= 0) initialLimit = Math.max(initialLimit, lastManualIndex + 1);
  }
  appendNextChunk(initialLimit);
  requestAnimationFrame(() => {
    restoreListFocus();
    if (window.matchMedia('(max-width: 1000px)').matches) handleSidebarScroll();
  });
}

function appendNextChunk(limit = null) {
  if (state.seriesDetailMode || seriesModule?.detailActive) return;
  if (!state.filteredItems.length) return;
  const chunkSize = limit ?? (state.view === VIEW.MOVIE ? MOVIE_CHUNK_SIZE : CHANNEL_NEXT_CHUNK);
  const start = state.renderedCount;
  const chunk = state.filteredItems.slice(start, start + chunkSize);
  const fragment = document.createDocumentFragment();
  chunk.forEach((item, offset) => {
    const card = state.view === VIEW.MOVIE
      ? (seriesModule?.isSeriesItem(item)
        ? seriesModule.createSeriesCard(item, start + offset)
        : createMovieCard(item, start + offset))
      : createChannelCard(item, start + offset);
    fragment.appendChild(card);
  });
  sidebarList.appendChild(fragment);
  state.renderedCount += chunk.length;
  updateFavoriteUi();
  updateActiveCards();
}

function createImageHtml(item, className) {
  const logo = String(item.logo || '').trim();
  const isMovie = item?._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE;

  if (!logo) {
    if (isMovie) {
      return `<div class="movie-poster-placeholder" role="img" aria-label="${escapeHtml(item.name)} poster unavailable"><i class="fas fa-film"></i><span>Poster নেই</span></div>`;
    }
    return `<div class="logo-placeholder">${escapeHtml(item.name.slice(0, 2).toUpperCase())}</div>`;
  }

  return `<img class="${className}" src="${escapeHtml(logo)}" alt="${escapeHtml(item.name)}" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-name="${escapeHtml(item.name)}">`;
}

function createChannelCard(item, visualIndex) {
  const card = document.createElement('div');
  card.className = 'sidebar-item tv-focusable';
  card.tabIndex = 0;
  card.setAttribute('role', 'button');
  card.setAttribute('aria-label', item.name);
  card.dataset.uid = item._uid;
  card.dataset.itemIndex = String(visualIndex);
  card.addEventListener('focus', () => {
    state.lastFocusedUid = item._uid;
    state.lastFocusedSelector = 'card';
    maybePreconnect(item.url);
  });

  const status = state.view === VIEW.UPCOMING
    ? 'UPCOMING'
    : (item.status || item.original_status || (state.view === VIEW.EVENT ? 'LIVE' : 'LIVE'));
  const statusClass = String(status).toUpperCase() === 'UPCOMING' ? 'upcoming-badge' : 'card-live-badge';
  const eventText = [item.start_time, item.competition].filter(Boolean).join(' • ');
  const favoriteKey = item.id || item.url;

  card.innerHTML = `
    <span class="sidebar-channel-num">${visualIndex + 1}</span>
    <div class="sidebar-logo-wrap">${createImageHtml(item, '')}</div>
    <div class="sidebar-details">
      <div class="sidebar-name">${escapeHtml(item.name)}</div>
      <div class="sidebar-sub-info">
        <span class="${statusClass}">${statusClass === 'card-live-badge' ? '<span class="pulse-dot"></span>' : '<i class="fas fa-clock"></i>'} ${escapeHtml(status)}</span>
        <span class="sidebar-meta-row">${escapeHtml(eventText || item.category || state.selectedCategory || '')}</span>
      </div>
    </div>
    ${state.view !== VIEW.UPCOMING ? `<button class="card-fav-btn" data-favorite-id="${escapeHtml(favoriteKey)}" type="button" title="Bookmark"><i class="far fa-star"></i></button>` : ''}`;

  const image = qs('img', card);
  image?.addEventListener('error', () => replaceBrokenImage(image));
  const favoriteButton = qs('.card-fav-btn', card);
  favoriteButton?.addEventListener('click', (event) => toggleFavorite(item._uid, event));
  return card;
}

function createMovieCard(item, visualIndex) {
  const card = document.createElement('div');
  card.className = 'movie-card tv-focusable';
  card.tabIndex = 0;
  card.setAttribute('role', 'button');
  card.setAttribute('aria-label', item.name);
  card.dataset.uid = item._uid;
  card.dataset.itemIndex = String(visualIndex);
  card.addEventListener('focus', () => {
    state.lastFocusedUid = item._uid;
    state.lastFocusedSelector = 'card';
    maybePreconnect(item.url);
  });

  const year = item.year || item.name.match(/\((\d{4})\)/)?.[1] || 'Movie';
  const rating = item.rating ? `<span class="movie-rating-badge"><i class="fas fa-star"></i> ${escapeHtml(item.rating)}</span>` : '';
  card.innerHTML = `
    <span class="movie-rank-badge">#${visualIndex + 1}</span>
    ${rating}
    ${createImageHtml(item, 'movie-poster')}
    <div class="movie-hover-play"><i class="fas fa-play"></i></div>
    <div class="movie-card-overlay">
      <div class="movie-card-title">${escapeHtml(item.name)}</div>
      <div class="movie-card-year">${escapeHtml(year)}</div>
    </div>`;
  const image = qs('img', card);
  image?.addEventListener('error', () => replaceBrokenMovieImage(image));
  return card;
}

function replaceBrokenImage(image) {
  const wrap = image?.parentElement;
  if (!wrap) return;
  const name = image.dataset.name || 'TV';
  const initials = name.split(/[\s|-]+/).filter(Boolean).map((word) => word[0]).join('').slice(0, 2).toUpperCase();
  wrap.innerHTML = `<div class="logo-placeholder">${escapeHtml(initials || 'TV')}</div>`;
}

function replaceBrokenMovieImage(image) {
  if (!image || !image.parentNode) return;
  const placeholder = document.createElement('div');
  placeholder.className = 'movie-poster-placeholder';
  placeholder.setAttribute('role', 'img');
  placeholder.setAttribute('aria-label', `${image.dataset.name || 'Movie'} poster unavailable`);
  placeholder.innerHTML = '<i class="fas fa-film"></i><span>Poster নেই</span>';
  image.replaceWith(placeholder);
}

let preconnectTimer = null;
function maybePreconnect(url) {
  if (!url) return;
  clearTimeout(preconnectTimer);
  preconnectTimer = setTimeout(() => {
    try {
      const origin = new URL(url).origin;
      if (qs(`link[data-preconnect="${cssEscape(origin)}"]`)) return;
      const link = document.createElement('link');
      link.rel = 'preconnect';
      link.href = origin;
      link.dataset.preconnect = origin;
      document.head.appendChild(link);
      setTimeout(() => link.remove(), 15000);
    } catch (_) {}
  }, 400);
}

sidebarList.addEventListener('click', (event) => {
  const card = event.target.closest('[data-uid]');
  if (!card || event.target.closest('.card-fav-btn')) return;
  const item = state.currentItems.find((entry) => entry._uid === card.dataset.uid);
  if (!item) return;
  if (seriesModule?.handleCatalogClick(item)) return;
  if (!isPlayable(item)) {
    showToast(item.start_time ? `শুরু হবে: ${item.start_time}` : 'এই ইভেন্ট এখনো শুরু হয়নি');
    return;
  }
  startPlayback(item, true);
});

let sidebarScrollScheduled = false;
function scheduleSidebarScrollCheck() {
  if (sidebarScrollScheduled) return;
  sidebarScrollScheduled = true;
  requestAnimationFrame(() => {
    sidebarScrollScheduled = false;
    handleSidebarScroll();
  });
}
sidebarList.addEventListener('scroll', scheduleSidebarScrollCheck, { passive: true });
sidebarSection.addEventListener('scroll', scheduleSidebarScrollCheck, { passive: true });
sidebarScrollArea?.addEventListener('scroll', scheduleSidebarScrollCheck, { passive: true });

async function handleSidebarScroll() {
  if (state.seriesDetailMode || seriesModule?.detailActive) return;
  const mobileFlow = window.matchMedia('(max-width: 1000px)').matches;
  const scrollHost = sidebarScrollArea || sidebarList;
  const nearBottom = scrollHost.scrollTop + scrollHost.clientHeight >= scrollHost.scrollHeight - (mobileFlow ? 360 : 260);

  if (!nearBottom) return;

  if (state.renderedCount < state.filteredItems.length) {
    appendNextChunk();
    if (mobileFlow) requestAnimationFrame(handleSidebarScroll);
    return;
  }

  if (
    state.view === VIEW.MOVIE &&
    !state.moviePreviewMode &&
    state.movieIndex &&
    state.moviePageCursor < state.movieIndex.pages.length
  ) {
    const loaded = await loadNextMoviePage();
    if (loaded && state.renderedCount < state.filteredItems.length) {
      appendNextChunk();
      if (mobileFlow) requestAnimationFrame(handleSidebarScroll);
    }
  }
}

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

const handleSearch = debounce(() => {
  if (state.view === VIEW.MOVIE && !state.selectedMovieCategory) return;
  renderCurrentList(true);
}, 220);

searchInput.addEventListener('input', (event) => {
  setSearchQuery(event.target.value, searchInput);
  handleSearch();
});
mobileSearchInput.addEventListener('input', (event) => {
  setSearchQuery(event.target.value, mobileSearchInput);
  handleSearch();
});
$('searchBtnSubmit').addEventListener('click', () => {
  setSearchQuery(searchInput.value, searchInput);
  renderCurrentList(true);
});
$('sortSelect').addEventListener('change', (event) => {
  state.currentSortMode = event.target.value;
  renderCurrentList(true);
});

function clearMobileSearch() {
  setSearchQuery('');
  renderCurrentList(true);
  scheduleMobileSearchAutoClose();
}

function detectFormat(url, item = state.currentItem) {
  const declared = String(item?.stream_type || item?.type || '').toLowerCase();
  if (declared === 'dash') return 'dash';
  if (declared === 'hls') return 'hls';
  if (declared === 'mpegts') return 'mpegts';
  if (declared === 'media') return 'direct';
  const raw = String(url || '').toLowerCase();
  let pathname = raw;
  try { pathname = new URL(raw, location.href).pathname.toLowerCase(); } catch (_) {}
  const mime = String(item?.mime_type || item?.content_type || '').toLowerCase();
  if (pathname.endsWith('.mpd') || mime.includes('dash') || item?.drm) return 'dash';
  if (pathname.endsWith('.m3u8') || mime.includes('mpegurl')) return 'hls';
  if (pathname.endsWith('.ts') || pathname.endsWith('.mpegts') || pathname.endsWith('.flv') || mime.includes('mp2t')) return 'mpegts';
  if (/\.(mp4|m4v|webm|mov|mkv|avi)$/.test(pathname) || mime.startsWith('video/')) return 'direct';
  return item?._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE ? 'direct' : 'hls';
}

function isSafariNativeHls() {
  const canNative = Boolean(video.canPlayType('application/vnd.apple.mpegurl'));
  const ua = navigator.userAgent;
  const safari = /Safari/i.test(ua) && !/Chrome|Chromium|CriOS|Android/i.test(ua);
  return canNative && safari;
}

function playbackProxyList() {
  const list = state.runtime?.play_proxies;
  return Array.isArray(list) ? list.filter((value) => /^https:\/\//i.test(value)) : [];
}

function proxyHealthKey(proxy, targetUrl) {
  let host = 'unknown';
  try { host = new URL(targetUrl).host; } catch (_) {}
  return `${proxy}|${host}`;
}

function getProxyHealth(proxy, targetUrl) {
  return state.proxyHealth[proxyHealthKey(proxy, targetUrl)] || {
    successes: 0,
    failures: 0,
    avgResponseMs: 0,
    consecutiveFailures: 0,
    lastSuccess: 0,
    lastFailure: 0,
    cooldownUntil: 0
  };
}

function persistProxyHealth() {
  const entries = Object.entries(state.proxyHealth)
    .sort((a, b) => Math.max(b[1].lastSuccess || 0, b[1].lastFailure || 0) - Math.max(a[1].lastSuccess || 0, a[1].lastFailure || 0))
    .slice(0, 120);
  state.proxyHealth = Object.fromEntries(entries);
  writeJsonStorage(STORAGE_KEYS.proxyHealth, state.proxyHealth);
}

function rankHealthyProxies(targetUrl, allowCooling = false) {
  const now = Date.now();
  const ranked = playbackProxyList().map((proxy, order) => {
    const health = getProxyHealth(proxy, targetUrl);
    const cooling = Number(health.cooldownUntil || 0) > now;
    const recentSuccessBoost = health.lastSuccess ? Math.max(0, 300000 - (now - health.lastSuccess)) / 300000 : 0;
    const speedPenalty = health.avgResponseMs ? health.avgResponseMs / 10000 : 0.5;
    const score = (cooling ? 1000 : 0) + health.consecutiveFailures * 10 + speedPenalty - recentSuccessBoost + order / 100;
    return { proxy, health, score, cooling };
  }).sort((a, b) => a.score - b.score);

  const available = ranked.filter((entry) => !entry.cooling);
  const selected = available.length ? available : (allowCooling ? ranked : []);
  return selected.slice(0, 2).map((entry) => entry.proxy);
}

function markProxyResult(proxy, targetUrl, success, elapsedMs) {
  if (!proxy) return;
  const key = proxyHealthKey(proxy, targetUrl);
  const health = getProxyHealth(proxy, targetUrl);
  if (success) {
    health.successes += 1;
    health.consecutiveFailures = 0;
    health.lastSuccess = Date.now();
    health.cooldownUntil = 0;
    health.avgResponseMs = health.avgResponseMs
      ? Math.round(health.avgResponseMs * 0.7 + elapsedMs * 0.3)
      : Math.round(elapsedMs);
  } else {
    health.failures += 1;
    health.consecutiveFailures += 1;
    health.lastFailure = Date.now();
    const cooldown = health.consecutiveFailures === 1 ? 60000 : (health.consecutiveFailures === 2 ? 120000 : 300000);
    health.cooldownUntil = Date.now() + cooldown;
  }
  state.proxyHealth[key] = health;
  persistProxyHealth();
}

function buildProxyUrl(proxy, source) {
  const route = state.runtime?.playback_proxy_route || '/hls?url=';
  let output = `${String(proxy).replace(/\/$/, '')}${route}${encodeURIComponent(source.url)}`;
  const profile = source.header_profile || '';
  const type = source.stream_type || inferStreamType(source);
  if (type) output += `&type=${encodeURIComponent(type)}`;
  if (profile) output += `&profile=${encodeURIComponent(profile)}`;
  if (source.inherit_manifest_query) output += '&inherit=1';
  return output;
}

function buildAttemptPlan(item) {
  const plan = [];
  const sources = item._sources?.length ? item._sources : rankSources(item);

  sources.slice(0, 6).forEach((source, sourceIndex) => {
    const sourceUrl = String(source.url || '').trim();
    if (!sourceUrl) return;

    const isHttp = sourceUrl.toLowerCase().startsWith('http://');
    const mixedContent = location.protocol === 'https:' && isHttp;
    const configuredMode = source.proxy_mode || inferProxyMode(source, source.header_profile || '');
    const sourceType = source.stream_type || inferStreamType(source);
    const isEvent = item?._sourceKind === VIEW.EVENT || state.view === VIEW.EVENT;
    const hasDrm = Boolean(item?.drm || source?.drm);
    const protectedSource = Boolean(source.requires_headers || item?.requires_headers);

    let mode = configuredMode;
    if (isEvent && sourceType === 'dash' && hasDrm && mode !== 'direct_only') {
      // ClearKey/DRM DASH direct mode অনেক device-এ manifest খুললেও segment-এ আটকে যায়.
      mode = 'proxy_only';
    } else if (protectedSource && !['direct_only', 'proxy_only'].includes(mode)) {
      mode = 'proxy_first';
    }

    let proxies = rankHealthyProxies(sourceUrl, false).slice(0, 2);
    if (!proxies.length && mode !== 'direct_only') {
      proxies = rankHealthyProxies(sourceUrl, true).slice(0, 2);
    }

    const canDirect = !mixedContent && mode !== 'proxy_only';
    const canProxy = mode !== 'direct_only' && proxies.length > 0;

    const addDirect = () => {
      if (canDirect) plan.push({ source, sourceIndex, route: 'direct', proxy: null });
    };
    const addProxies = () => {
      if (canProxy) proxies.forEach((proxy) => plan.push({ source, sourceIndex, route: 'proxy', proxy }));
    };

    if (mode === 'direct_only') addDirect();
    else if (mode === 'proxy_only') addProxies();
    else if (mode === 'proxy_first' || source.force_proxy) { addProxies(); addDirect(); }
    else { addDirect(); addProxies(); }
  });

  const seen = new Set();
  return plan.filter((attempt) => {
    const key = `${attempt.route}:${attempt.proxy || ''}:${attempt.source.url}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function devicePerformanceClass() {
  const ua = String(navigator.userAgent || '');
  const memory = typeof navigator.deviceMemory === 'number' ? navigator.deviceMemory : 0;
  const cores = typeof navigator.hardwareConcurrency === 'number' ? navigator.hardwareConcurrency : 0;
  const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  const slowNetwork = Boolean(connection && ['slow-2g', '2g', '3g'].includes(connection.effectiveType));
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const androidMatch = ua.match(/Android\s+(\d+)/i);
  const androidMajor = androidMatch ? Number(androidMatch[1]) : 99;
  const oldAndroidTv = /TV|AFT|SmartTV|BRAVIA|MiBOX|Android TV|TV Bro/i.test(ua) && androidMajor <= 7;

  if ((memory > 0 && memory <= 1) || (cores > 0 && cores <= 2) || oldAndroidTv) return 'ultra-lite';
  if ((memory > 0 && memory <= 2) || (cores > 0 && cores <= 4) || slowNetwork || reducedMotion) return 'lite';
  return 'normal';
}

function liteModePreference() {
  const saved = localStorage.getItem(STORAGE_KEYS.liteMode);
  return ['auto', 'on', 'off'].includes(saved) ? saved : 'auto';
}

function isLiteModeForced() {
  return liteModePreference() === 'on';
}

function effectivePerformanceClass() {
  const preference = liteModePreference();
  if (preference === 'on') return 'lite';
  if (preference === 'off') return 'normal';
  return devicePerformanceClass();
}

function qualityCapHeight() {
  const forced = Number(localStorage.getItem(STORAGE_KEYS.maxHeight) || 0);
  if ([240, 360, 480, 720, 1080, 1440, 2160].includes(forced)) return forced;
  const deviceClass = effectivePerformanceClass();
  if (deviceClass === 'ultra-lite') return 480;
  if (deviceClass === 'lite') return 720;
  return 0;
}

function updatePerformanceClasses() {
  const deviceClass = effectivePerformanceClass();
  state.deviceClass = deviceClass;
  document.documentElement.classList.toggle('performance-mode', deviceClass !== 'normal');
  document.documentElement.classList.toggle('lite-performance', deviceClass === 'lite');
  document.documentElement.classList.toggle('ultra-lite-performance', deviceClass === 'ultra-lite');
  const label = $('liteModeState');
  if (label) label.textContent = liteModePreference() === 'auto' ? `Auto · ${deviceClass}` : (liteModePreference() === 'on' ? 'On' : 'Off');
}

function setLiteMode(value) {
  const preference = value === true ? 'on' : value === false ? 'off' : 'auto';
  localStorage.setItem(STORAGE_KEYS.liteMode, preference);
  updatePerformanceClasses();
  applyAdaptiveDecodeCap(qualityCapHeight());
  showToast(`Lite Mode: ${preference === 'auto' ? 'Auto' : preference === 'on' ? 'On' : 'Off'}`);
}

function cycleLiteMode() {
  const current = liteModePreference();
  setLiteMode(current === 'auto' ? true : current === 'on' ? false : 'auto');
}

function isMoviePlaybackContext(item = state.currentItem) {
  return Boolean(item?._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE);
}

function isLiveEventContext(item = state.currentItem) {
  const kind = item?._sourceKind || state.view;
  return kind === VIEW.EVENT || kind === VIEW.UPCOMING || state.view === VIEW.EVENT || state.view === VIEW.UPCOMING;
}

function validNetworkModes(isMovie) {
  return isMovie
    ? [NETWORK_MODE.AUTO, NETWORK_MODE.STABLE, NETWORK_MODE.LOW]
    : [NETWORK_MODE.AUTO, NETWORK_MODE.BALANCED, NETWORK_MODE.STABLE];
}

function readNetworkMode(item = state.currentItem) {
  const isMovie = isMoviePlaybackContext(item);
  if (isMovie) return NETWORK_MODE.AUTO;
  const storageKey = STORAGE_KEYS.liveNetworkMode;
  const allowed = validNetworkModes(isMovie);
  const saved = localStorage.getItem(storageKey);

  if (!isMovie && saved === NETWORK_MODE.LOW) {
    // Migration: the old Low Delay profile is now the live Auto profile.
    localStorage.setItem(storageKey, NETWORK_MODE.AUTO);
    return NETWORK_MODE.AUTO;
  }
  if (allowed.includes(saved)) return saved;
  return NETWORK_MODE.AUTO;
}

function resolveAutoProfile() {
  const deviceClass = effectivePerformanceClass();
  const weakHistory = Number(state.playbackHistory.lastBandwidth || 0) > 0 && Number(state.playbackHistory.lastBandwidth) < 1500000;
  return deviceClass === 'normal' && !weakHistory ? 'normal' : 'lite';
}

function liveFastStartProfile(mode) {
  // Compatibility wrapper: live playback now uses one fixed reference profile
  // per selected mode and never performs a second-stage buffer promotion.
  return networkProfile(mode, false);
}

function networkProfile(mode, isMovie, _fastStart = false) {
  const autoProfile = resolveAutoProfile();
  const isEvent = !isMovie && isLiveEventContext();

  // Movie/VOD profiles from the latest updated player stay unchanged.
  if (isMovie) {
    if (mode === NETWORK_MODE.LOW) {
      return {
        label: 'Low Delay', lowLatencyMode: false,
        maxBufferLength: 12, maxMaxBufferLength: 24,
        maxBufferSize: 18 * 1024 * 1024, backBufferLength: 16,
        liveSyncDurationCount: 2, liveMaxLatencyDurationCount: 5
      };
    }
    if (mode === NETWORK_MODE.STABLE) {
      return {
        label: 'Stable', lowLatencyMode: false,
        maxBufferLength: 36, maxMaxBufferLength: 60,
        maxBufferSize: 34 * 1024 * 1024, backBufferLength: 36,
        liveSyncDurationCount: 3, liveMaxLatencyDurationCount: 8
      };
    }
    if (autoProfile === 'lite') {
      return {
        label: 'Auto · Balanced', lowLatencyMode: false,
        maxBufferLength: 18, maxMaxBufferLength: 34,
        maxBufferSize: 19 * 1024 * 1024, backBufferLength: 22,
        liveSyncDurationCount: 3, liveMaxLatencyDurationCount: 7
      };
    }
    return {
      label: 'Auto · Balanced', lowLatencyMode: false,
      maxBufferLength: 26, maxMaxBufferLength: 48,
      maxBufferSize: 28 * 1024 * 1024, backBufferLength: 32,
      liveSyncDurationCount: 3, liveMaxLatencyDurationCount: 8
    };
  }

  // LIVE Auto keeps fast startup but holds a slightly safer reserve.
  if (mode === NETWORK_MODE.AUTO) {
    return {
      label: 'Auto · Smooth Start', lowLatencyMode: true,
      maxBufferLength: isEvent ? 8 : 10,
      maxMaxBufferLength: isEvent ? 18 : 22,
      maxBufferSize: 20 * 1024 * 1024,
      backBufferLength: 10,
      liveSyncDurationCount: 2,
      liveMaxLatencyDurationCount: 7
    };
  }

  // LIVE Fast Start is the exact old Auto profile from index(17).html.
  if (mode === NETWORK_MODE.BALANCED) {
    if (autoProfile === 'lite') {
      return {
        label: 'Fast Start', lowLatencyMode: isEvent,
        maxBufferLength: isEvent ? 5 : 8,
        maxMaxBufferLength: isEvent ? 12 : 18,
        maxBufferSize: 14 * 1024 * 1024,
        backBufferLength: 8,
        liveSyncDurationCount: 2,
        liveMaxLatencyDurationCount: 5
      };
    }
    return {
      label: 'Fast Start', lowLatencyMode: isEvent,
      maxBufferLength: isEvent ? 5 : 10,
      maxMaxBufferLength: isEvent ? 12 : 24,
      maxBufferSize: 18 * 1024 * 1024,
      backBufferLength: 10,
      liveSyncDurationCount: 2,
      liveMaxLatencyDurationCount: 6
    };
  }

  // LIVE Stable is the exact old Stable profile from index(17).html.
  return {
    label: 'Stable · More Buffer', lowLatencyMode: false,
    maxBufferLength: isEvent ? 12 : 16,
    maxMaxBufferLength: isEvent ? 24 : 32,
    maxBufferSize: 24 * 1024 * 1024,
    backBufferLength: isEvent ? 10 : 12,
    liveSyncDurationCount: 3,
    liveMaxLatencyDurationCount: 7
  };
}

function currentNetworkMode() {
  return readNetworkMode();
}

function isLiveFastStartPhase() {
  return false;
}

function startLiveFastStartPhase() {
  clearTimeout(state.liveStartupRampTimer);
  state.liveStartupStartedAt = 0;
  state.liveStartupRamped = true;
  state.liveStartupRampTimer = null;
}

function hlsConfigFor(mode, isMovie, _fastStart = false) {
  const profile = networkProfile(mode, isMovie);
  const bandwidthEstimate = Number(state.playbackHistory.lastBandwidth || 0);
  const deviceDefaultEstimate = effectivePerformanceClass() === 'normal' ? 2500000 : 1200000;
  const liveDefaultEstimate = effectivePerformanceClass() === 'normal' ? 1900000 : 1000000;
  const startupEstimate = bandwidthEstimate > 0
    ? (isMovie ? bandwidthEstimate : Math.min(bandwidthEstimate, 2200000))
    : (isMovie ? deviceDefaultEstimate : liveDefaultEstimate);
  return {
    enableWorker: effectivePerformanceClass() !== 'ultra-lite',
    lowLatencyMode: profile.lowLatencyMode,
    initialLiveManifestSize: 1,
    maxBufferLength: profile.maxBufferLength,
    maxMaxBufferLength: profile.maxMaxBufferLength,
    maxBufferSize: profile.maxBufferSize,
    backBufferLength: profile.backBufferLength,
    maxBufferHole: 0.1,
    liveSyncDurationCount: profile.liveSyncDurationCount,
    liveMaxLatencyDurationCount: profile.liveMaxLatencyDurationCount,
    highBufferWatchdogPeriod: 2,
    nudgeOffset: 0.1,
    nudgeMaxRetry: 3,
    startFragPrefetch: true,
    fragLoadingTimeOut: 8000,
    manifestLoadingTimeOut: 8000,
    levelLoadingTimeOut: 8000,
    startLevel: -1,
    capLevelToPlayerSize: true,
    capLevelOnFPSDrop: true,
    maxStarvationDelay: 2.5,
    maxLoadingDelay: 3.5,
    testBandwidth: true,
    abrEwmaDefaultEstimate: startupEstimate,
    abrBandwidthFactor: isMovie ? 0.86 : 0.80,
    abrBandwidthUpFactor: isMovie ? 0.65 : 0.55
  };
}

function shakaConfigFor(mode, isMovie, _fastStart = false) {
  const profile = networkProfile(mode, isMovie);
  const isEvent = isLiveEventContext();
  const retryParameters = {
    maxAttempts: isEvent ? 4 : 3,
    baseDelay: 350,
    backoffFactor: 1.45,
    fuzzFactor: 0.2,
    timeout: isEvent ? 9000 : 8000
  };
  const rebufferingGoal = isMovie
    ? (mode === NETWORK_MODE.LOW ? 1.2 : 2)
    : isEvent
      ? (mode === NETWORK_MODE.STABLE ? 1.8 : mode === NETWORK_MODE.AUTO ? 1.2 : 0.9)
      : (mode === NETWORK_MODE.STABLE ? 2 : mode === NETWORK_MODE.AUTO ? 1.4 : 1);

  return {
    manifest: { retryParameters: { ...retryParameters } },
    streaming: {
      rebufferingGoal,
      bufferingGoal: profile.maxBufferLength,
      bufferBehind: profile.backBufferLength,
      lowLatencyMode: profile.lowLatencyMode || (isEvent && mode === NETWORK_MODE.BALANCED),
      stallEnabled: true,
      stallThreshold: isEvent ? 1.25 : 1.5,
      stallSkip: 0.1,
      segmentPrefetchLimit: effectivePerformanceClass() === 'ultra-lite' ? 1 : 2,
      retryParameters: { ...retryParameters }
    },
    abr: {
      enabled: state.selectedManualQuality === -1,
      defaultBandwidthEstimate: Number(state.playbackHistory.lastBandwidth || (effectivePerformanceClass() === 'normal' ? 2500000 : 1200000)),
      switchInterval: 4,
      restrictions: qualityCapHeight() > 0 ? { maxHeight: qualityCapHeight() } : {}
    }
  };
}

function promoteLivePlaybackProfile() {
  state.liveStartupRamped = true;
  state.liveStartupRampTimer = null;
}

function scheduleLiveStartupRamp() {
  clearTimeout(state.liveStartupRampTimer);
  state.liveStartupRampTimer = null;
  state.liveStartupRamped = true;
}

function networkProfileDetail(mode, isMovie) {
  const profile = networkProfile(mode, isMovie);
  return `${profile.maxBufferLength}s Buffer`;
}

function networkMenuRows(isMovie) {
  const modes = isMovie
    ? [NETWORK_MODE.AUTO, NETWORK_MODE.STABLE, NETWORK_MODE.LOW]
    : [NETWORK_MODE.AUTO, NETWORK_MODE.BALANCED, NETWORK_MODE.STABLE];
  const liveTitles = {
    [NETWORK_MODE.AUTO]: 'Auto',
    [NETWORK_MODE.BALANCED]: 'Fast Start',
    [NETWORK_MODE.STABLE]: 'Stable'
  };
  const movieTitles = {
    [NETWORK_MODE.AUTO]: 'Auto',
    [NETWORK_MODE.STABLE]: 'Stable',
    [NETWORK_MODE.LOW]: 'Low Delay'
  };
  const titles = isMovie ? movieTitles : liveTitles;
  return modes.map((mode) => ({
    mode,
    title: titles[mode],
    detail: networkProfileDetail(mode, isMovie)
  }));
}

function renderNetworkMenu(isMovie = isMoviePlaybackContext()) {
  const menu = $('networkMenu');
  if (!menu) return;
  const activeMode = readNetworkMode();
  menu.replaceChildren();
  networkMenuRows(isMovie).forEach((row) => {
    const item = document.createElement('div');
    item.className = `popup-menu-item${row.mode === activeMode ? ' active' : ''}`;
    item.dataset.networkMode = row.mode;
    item.innerHTML = `<span>${escapeHtml(row.title)}</span><span class="bitrate-val">${escapeHtml(row.detail)}</span>`;
    item.addEventListener('click', () => applyNetworkMode(row.mode));
    menu.appendChild(item);
  });
}

function updateNetworkMenuState(mode = readNetworkMode()) {
  const isMovie = isMoviePlaybackContext();
  const allowed = validNetworkModes(isMovie);
  const safeMode = allowed.includes(mode) ? mode : NETWORK_MODE.AUTO;
  renderNetworkMenu(isMovie);
  qsa('[data-network-mode]', $('networkMenu')).forEach((item) => {
    item.classList.toggle('active', item.dataset.networkMode === safeMode);
  });

  const profile = networkProfile(safeMode, isMovie);
  const button = $('networkBtn');
  const badge = $('networkModeBadge');
  const icon = qs('i', button);
  const visual = isMovie
    ? safeMode === NETWORK_MODE.STABLE
      ? { badge: 'S', icon: 'fas fa-shield-alt' }
      : safeMode === NETWORK_MODE.LOW
        ? { badge: 'L', icon: 'fas fa-bolt' }
        : { badge: 'A', icon: 'fas fa-wifi' }
    : safeMode === NETWORK_MODE.STABLE
      ? { badge: 'S', icon: 'fas fa-shield-alt' }
      : safeMode === NETWORK_MODE.BALANCED
        ? { badge: 'F', icon: 'fas fa-bolt' }
        : { badge: 'A', icon: 'fas fa-wifi' };

  if (badge) badge.textContent = visual.badge;
  if (icon) icon.className = visual.icon;
  if (button) {
    button.title = `Network Mode: ${profile.label}`;
    button.setAttribute('aria-label', `Network Mode: ${profile.label}`);
    button.dataset.networkMode = safeMode;
  }
}

function applyNetworkMode(mode, notify = true) {
  const isMovie = isMoviePlaybackContext();
  const allowed = validNetworkModes(isMovie);
  const safeMode = allowed.includes(mode) ? mode : NETWORK_MODE.AUTO;
  const storageKey = isMovie ? STORAGE_KEYS.movieNetworkMode : STORAGE_KEYS.liveNetworkMode;
  localStorage.setItem(storageKey, safeMode);
  localStorage.setItem(STORAGE_KEYS.networkMode, safeMode);

  updatePerformanceClasses();

  if (state.hls) {
    const nextConfig = hlsConfigFor(safeMode, isMovie);
    Object.assign(state.hls.config, {
      lowLatencyMode: nextConfig.lowLatencyMode,
      maxBufferLength: nextConfig.maxBufferLength,
      maxMaxBufferLength: nextConfig.maxMaxBufferLength,
      maxBufferSize: nextConfig.maxBufferSize,
      backBufferLength: nextConfig.backBufferLength,
      liveSyncDurationCount: nextConfig.liveSyncDurationCount,
      liveMaxLatencyDurationCount: nextConfig.liveMaxLatencyDurationCount
    });

    if (!isMovie) {
      try {
        state.hls.startLoad(-1);
        if (
          safeMode === NETWORK_MODE.AUTO &&
          Number.isFinite(state.hls.liveSyncPosition) &&
          Math.abs(state.hls.liveSyncPosition - video.currentTime) > 2
        ) {
          video.currentTime = Math.max(0, state.hls.liveSyncPosition - 1.0);
        }
      } catch (_) {}
    }
  }

  if (state.shaka) {
    try { state.shaka.configure(shakaConfigFor(safeMode, isMovie)); } catch (_) {}
  }

  updateNetworkMenuState(safeMode);
  applyAdaptiveDecodeCap(qualityCapHeight());

  if (notify) {
    const message = isMovie
      ? safeMode === NETWORK_MODE.LOW ? 'Movie Network: Low Delay'
        : safeMode === NETWORK_MODE.STABLE ? 'Movie Network: Stable'
          : 'Movie Network: Auto · Balanced'
      : safeMode === NETWORK_MODE.STABLE ? 'Live Network: Stable · More Buffer'
        : safeMode === NETWORK_MODE.BALANCED ? 'Live Network: Fast Start'
          : 'Live Network: Auto · Lowest Buffer';
    showToast(message, 2400);
  }
}
window.setNetworkMode = applyNetworkMode;

function clearPlaybackTimers() {
  const session = state.playbackSession;
  if (session?.attemptTimer) clearTimeout(session.attemptTimer);
  if (session?.progressExtensionTimer) clearTimeout(session.progressExtensionTimer);
  if (session?.startupBufferGateTimer) clearInterval(session.startupBufferGateTimer);
  if (session?.nativeErrorTimer) clearTimeout(session.nativeErrorTimer);
  if (session) {
    session.attemptTimer = null;
    session.progressExtensionTimer = null;
    session.startupBufferGateTimer = null;
    session.nativeErrorTimer = null;
    session.startupBufferGateActive = false;
  }
}

async function cleanupPlayerEngine() {
  clearPlaybackTimers();
  stopStallDetector();
  clearTimeout(state.liveStartupRampTimer);
  state.liveStartupRampTimer = null;
  stopLiveAdaptiveQualityRamp(false);
  state.liveStartupQualityCapHeight = 0;
  clearMovieAudioCompatibilityCheck();
  stopMovieAudioCompanion();
  stopPlaybackPerformanceMonitor(true);
  state.qualitySwitchLockUntil = 0;
  state.manualQualityChangePending = false;
  clearTimeout(state.qualityUnlockTimer);

  if (state.hls) {
    try {
      state.hls.stopLoad();
      state.hls.detachMedia();
      state.hls.destroy();
    } catch (_) {}
    state.hls = null;
  }
  if (state.shaka) {
    try { await state.shaka.destroy(); } catch (_) {}
    state.shaka = null;
  }
  if (state.mpegts) {
    try {
      state.mpegts.pause();
      state.mpegts.unload();
      state.mpegts.detachMediaElement();
      state.mpegts.destroy();
    } catch (_) {}
    state.mpegts = null;
  }
  state.playerType = null;
  state.autoplayUnlockPending = false;
  video.onerror = null;
  try { video.pause(); } catch (_) {}
  delete video.dataset.attemptToken;
  video.removeAttribute('src');
  try { video.load(); } catch (_) {}
}

function sourceAttemptMessage() {
  const kind = state.currentItem?._sourceKind || state.view;
  const isMovie = kind === VIEW.MOVIE;
  const session = state.playbackSession;
  const retrying = Number(session?.attemptsRun || 0) > 1;

  if (isMovie) return retrying ? 'মুভিটি চালুর আরেকটি উপায় চেষ্টা করা হচ্ছে…' : 'মুভিটি চালু হচ্ছে…';
  return 'লাইভ চ্যানেল প্লে হচ্ছে…';
}

function resetManualRetryState(item) {
  clearAutoNextTimer();
  hideFailureActions();
  state.autoNextCount = 0;
  state.autoNextFailedUids = (state.autoNextFailedUids || []).filter((uid) => uid !== item?._uid);

  const urls = new Set((item?._sources?.length ? item._sources : rankSources(item || {})).map((source) => String(source?.url || '')).filter(Boolean));
  Object.entries(state.proxyHealth || {}).forEach(([key, health]) => {
    if (![...urls].some((url) => {
      try { return key.endsWith(`|${new URL(url).host}`); } catch (_) { return false; }
    })) return;
    state.proxyHealth[key] = { ...health, cooldownUntil: 0, consecutiveFailures: 0 };
  });
  persistProxyHealth();
}

function playbackAttemptBudgetMs(item) {
  const kind = item?._sourceKind || state.view;
  if (kind === VIEW.EVENT) return EVENT_ATTEMPT_BUDGET_MS;
  if (kind === VIEW.MOVIE) return MOVIE_ATTEMPT_BUDGET_MS;
  return CHANNEL_ATTEMPT_BUDGET_MS;
}

async function startPlayback(item, userInitiated = true) {
  if (!item || !isPlayable(item)) return;
  seriesModule?.handlePlaybackSelection?.(item);

  clearAutoNextTimer();
  if (userInitiated) resetManualRetryState(item);

  state.activeLoadId += 1;
  const id = state.activeLoadId;
  await cleanupPlayerEngine();
  if (id !== state.activeLoadId) return;

  clearMovieQualityGuidance();
  clearMovieAudioCompatibilityCheck();
  state.liveStartupStartedAt = 0;
  state.liveStartupRamped = true;
  state.mediaOperationGraceUntil = 0;
  state.seekPointerActive = false;
  state.seekPendingTime = null;
  state.seekWasPlaying = false;
  state.userPaused = false;
  state.currentItem = item;
  state.selectedManualQuality = item._selectedDirectQualityKey || -1;
  saveRecentItem(item);
  updateMetadata(item);
  updateActiveCards();
  setupPlayerUi(item);

  const plan = buildAttemptPlan(item);
  state.playbackSession = {
    id,
    item,
    plan,
    attemptIndex: 0,
    attemptsRun: 0,
    currentAttempt: null,
    startedAt: Date.now(),
    budgetDeadline: Date.now() + playbackAttemptBudgetMs(item),
    attemptStartedAt: 0,
    attemptTimer: null,
    success: false,
    progressSeen: false,
    mediaRecoveryCount: 0,
    networkRecoveryCount: 0,
    stallStartedAt: 0,
    stallStep: 0,
    lastTime: 0,
    lastProgressAt: Date.now(),
    userInitiated,
    attemptToken: 0,
    mediaInfoProbeDone: false,
    startupBufferGateActive: false,
    startupBufferGateReleased: false,
    startupBufferGateStartedAt: 0,
    startupBufferGateTimer: null,
    routeAccepted: false,
    routeAcceptedAt: 0,
    playbackFinalized: false,
    allowRouteFailover: false,
    nativeErrorTimer: null
  };

  if (!plan.length) {
    handlePlaybackPlanExhausted('no_playable_route');
    return;
  }

  await runCurrentAttempt();
}

async function runCurrentAttempt() {
  const session = state.playbackSession;
  if (!session || session.id !== state.activeLoadId) return;

  if (Date.now() >= session.budgetDeadline || session.attemptIndex >= session.plan.length) {
    handlePlaybackPlanExhausted('attempts_exhausted');
    return;
  }

  const attemptToken = Number(session.attemptToken || 0) + 1;
  session.attemptToken = attemptToken;
  const needsEngineReset = Boolean(
    session.attemptsRun > 0 ||
    state.hls ||
    state.shaka ||
    state.mpegts ||
    video.currentSrc ||
    video.getAttribute('src')
  );
  if (needsEngineReset) await cleanupPlayerEngine();
  if (!isActiveAttempt(session, attemptToken)) return;

  const attempt = session.plan[session.attemptIndex];
  session.currentAttempt = attempt;
  session.attemptsRun = Number(session.attemptsRun || 0) + 1;
  video.dataset.attemptToken = String(attemptToken);
  session.attemptStartedAt = Date.now();
  session.progressSeen = false;
  session.mediaRecoveryCount = 0;
  session.networkRecoveryCount = 0;
  session.success = false;
  session.lastTime = 0;
  session.lastProgressAt = Date.now();
  session.startupBufferGateActive = false;
  session.startupBufferGateReleased = false;
  session.startupBufferGateStartedAt = 0;
  session.routeAccepted = false;
  session.routeAcceptedAt = 0;
  session.playbackFinalized = false;
  session.allowRouteFailover = false;
  if (session.startupBufferGateTimer) clearInterval(session.startupBufferGateTimer);
  session.startupBufferGateTimer = null;
  showPlayerMessage(sourceAttemptMessage(), true);

  const finalUrl = attempt.route === 'proxy'
    ? buildProxyUrl(attempt.proxy, attempt.source)
    : attempt.source.url;
  const format = detectFormat(attempt.source.url, { ...session.item, ...attempt.source });
  armAttemptTimeout(attemptTimeoutFor(attempt, format, session.item));

  try {
    if (format === 'dash') {
      await initShaka(finalUrl, session, attemptToken);
    } else if (format === 'mpegts') {
      await initMpegTs(finalUrl, session, attemptToken);
    } else if (format === 'hls') {
      const nativeHls = Boolean(video.canPlayType('application/vnd.apple.mpegurl'));
      const nativeFirst = isSafariNativeHls() || (effectivePerformanceClass() === 'ultra-lite' && nativeHls);
      if (nativeFirst) await initNative(finalUrl, session, attemptToken, 'hls');
      else if (window.Hls?.isSupported()) initHls(finalUrl, session, attemptToken);
      else if (nativeHls) await initNative(finalUrl, session, attemptToken, 'hls');
      else throw new Error('HLS is not supported on this device');
    } else {
      await initNative(finalUrl, session, attemptToken, 'direct');
    }
  } catch (error) {
    if (!isActiveAttempt(session, attemptToken)) return;
    console.warn('Playback attempt failed', {
      reason: error?.message || String(error),
      route: attempt.route,
      sourceIndex: attempt.sourceIndex
    });
    failCurrentAttempt(error?.message || 'Player initialization failed', attemptToken);
  }
}

function isActiveAttempt(session, attemptToken) {
  return Boolean(session && state.playbackSession === session && session.id === state.activeLoadId && session.attemptToken === attemptToken);
}

function armAttemptTimeout(baseMs) {
  const session = state.playbackSession;
  if (!session) return;
  clearPlaybackTimers();
  const remaining = Math.max(250, session.budgetDeadline - Date.now());
  const timeout = Math.min(baseMs, remaining);
  const attemptToken = session.attemptToken;
  session.attemptTimer = setTimeout(() => {
    if (!session.success && isActiveAttempt(session, attemptToken)) failCurrentAttempt('Startup timeout', attemptToken);
  }, timeout);
}

function markAttemptProgress(reason = '', attemptToken = state.playbackSession?.attemptToken) {
  const session = state.playbackSession;
  if (!session || session.success || !isActiveAttempt(session, attemptToken)) return;
  session.progressSeen = true;
  const elapsed = Date.now() - session.attemptStartedAt;
  const format = detectFormat(session.currentAttempt?.source?.url || '', { ...session.item, ...session.currentAttempt?.source });
  const isEvent = isLiveEventContext(session.item);
  const isMovie = isMoviePlaybackContext(session.item);
  const maxTotal = isMovie
    ? (format === 'direct'
      ? (session.currentAttempt?.route === 'direct' ? 42000 : 30000)
      : (session.currentAttempt?.route === 'direct' ? 30000 : 24000))
    : isEvent && format === 'dash'
      ? 15000
      : format === 'dash'
        ? 11500
        : session.currentAttempt?.route === 'direct' ? 8500 : 7500;
  const extension = Math.max(900, maxTotal - elapsed);
  const remaining = Math.max(250, session.budgetDeadline - Date.now());
  clearTimeout(session.attemptTimer);
  session.attemptTimer = setTimeout(() => {
    if (!session.success && isActiveAttempt(session, attemptToken)) failCurrentAttempt(`Progress stopped: ${reason}`, attemptToken);
  }, Math.min(extension, remaining));
}

async function waitForNativeStartupSignal(session, attemptToken, waitMs = 1400) {
  if (!isActiveAttempt(session, attemptToken)) return;
  await new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      ['loadedmetadata', 'loadeddata', 'canplay', 'progress', 'error'].forEach((name) => video.removeEventListener(name, finish));
      clearTimeout(timer);
      resolve();
    };
    ['loadedmetadata', 'loadeddata', 'canplay', 'progress', 'error'].forEach((name) => video.addEventListener(name, finish, { once: true }));
    const timer = setTimeout(finish, waitMs);
  });
}

async function initNative(url, session, attemptToken, type) {
  video.removeAttribute('crossorigin');
  state.playerType = type === 'hls' ? 'native-hls' : 'native';
  video.preload = 'auto';
  video.src = url;
  markAttemptProgress('native source assigned', attemptToken);

  video.onerror = () => {
    if (!isActiveAttempt(session, attemptToken) || isQualityLocked()) return;
    clearTimeout(session.nativeErrorTimer);
    const isMovie = session.item?._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE;
    const graceMs = isMovie ? 1800 : 600;
    session.nativeErrorTimer = setTimeout(() => {
      session.nativeErrorTimer = null;
      if (!isActiveAttempt(session, attemptToken) || session.success || video.readyState >= 2) return;
      const code = Number(video.error?.code || 0);
      failCurrentAttempt(code ? `Native media error ${code}` : 'Native media error', attemptToken);
    }, graceMs);
  };

  try { video.load(); } catch (_) {}
  await waitForNativeStartupSignal(session, attemptToken, type === 'direct' ? 1600 : 900);
  if (!isActiveAttempt(session, attemptToken)) return;
  await safePlay(session, attemptToken);
  buildQualityMenu();
  updateStreamInfoBadge();
}





function initHls(url, session, attemptToken) {
  state.playerType = 'hls';
  const isMovie = session.item._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE;
  const hls = new Hls(hlsConfigFor(currentNetworkMode(), isMovie));
  state.hls = hls;

  hls.on(Hls.Events.MEDIA_ATTACHED, () => {
    if (!isActiveAttempt(session, attemptToken)) return;
    hls.loadSource(url);
    safePlay(session, attemptToken).catch(() => {});
  });
  hls.on(Hls.Events.MANIFEST_PARSED, (_, data) => {
    if (!isActiveAttempt(session, attemptToken)) return;
    markAttemptProgress('manifest parsed', attemptToken);
    if (state.selectedManualQuality === -1) {
      const startupStages = !isMovie ? liveStartupQualityStages(session.item) : [];
      if (startupStages.length) {
        applyLiveAdaptiveQualityCap(startupStages[0], true);
      } else {
        const cap = qualityCapHeight();
        hls.autoLevelCapping = cap > 0 ? findHlsCapLevel(cap) : -1;
      }
    }
    if (!isMovie) {
      requestAnimationFrame(() => {
        try {
          const livePosition = Number(hls.liveSyncPosition || 0);
          const targetLag = currentNetworkMode() === NETWORK_MODE.AUTO ? 1.0 : currentNetworkMode() === NETWORK_MODE.STABLE ? 2.5 : 1.2;
          if (livePosition > 0 && (!Number.isFinite(video.currentTime) || Math.abs(livePosition - video.currentTime) > 3)) {
            video.currentTime = Math.max(0, livePosition - targetLag);
          }
        } catch (_) {}
      });
    }
    buildQualityMenu(data.levels || hls.levels);
  });
  hls.on(Hls.Events.FRAG_LOADED, () => {
    if (isActiveAttempt(session, attemptToken) && !session.success) markAttemptProgress('fragment loaded', attemptToken);
  });
  hls.on(Hls.Events.LEVEL_SWITCHING, () => { if (state.manualQualityChangePending) lockQualitySwitch(); });
  hls.on(Hls.Events.LEVEL_SWITCHED, () => {
    if (!isActiveAttempt(session, attemptToken)) return;
    if (state.manualQualityChangePending) unlockQualitySwitchSoon();
    buildQualityMenu(hls.levels);
    updateStreamInfoBadge();
  });
  hls.on(Hls.Events.ERROR, (_, data) => handleHlsError(data, session, attemptToken));
  hls.attachMedia(video);
}

function handleHlsError(data, session, attemptToken) {
  if (!data || !isActiveAttempt(session, attemptToken)) return;
  if (isQualityLocked()) return;
  const responseCode = Number(data.response?.code || data.networkDetails?.status || 0);

  if (!data.fatal) return;

  if (
    session.routeAccepted &&
    !session.allowRouteFailover &&
    (session.startupBufferGateActive || Date.now() < Number(state.mediaOperationGraceUntil || 0))
  ) {
    try {
      if (data.type === Hls.ErrorTypes.MEDIA_ERROR) state.hls?.recoverMediaError();
      else state.hls?.startLoad(-1);
    } catch (_) {}
    return;
  }
  if (responseCode === 403 || responseCode === 404) {
    failCurrentAttempt(`HTTP ${responseCode}`, attemptToken);
    return;
  }

  if (data.type === Hls.ErrorTypes.MEDIA_ERROR && state.hls && session.mediaRecoveryCount < 1) {
    session.mediaRecoveryCount += 1;
    state.recoveryLockUntil = Date.now() + 3000;
    try { state.hls.recoverMediaError(); } catch (_) { failCurrentAttempt('HLS media error', attemptToken); }
    return;
  }

  if (data.type === Hls.ErrorTypes.NETWORK_ERROR && state.hls && session.networkRecoveryCount < 1) {
    session.networkRecoveryCount += 1;
    state.recoveryLockUntil = Date.now() + 2500;
    try { state.hls.startLoad(); } catch (_) { failCurrentAttempt('HLS network error', attemptToken); }
    return;
  }

  failCurrentAttempt(data.details || 'Fatal HLS error', attemptToken);
}

let shakaLoaderPromise = null;
function ensureShakaLibrary() {
  if (window.shaka?.Player) return Promise.resolve(window.shaka);
  if (shakaLoaderPromise) return shakaLoaderPromise;
  shakaLoaderPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = SHAKA_CDN;
    script.async = true;
    script.onload = () => window.shaka?.Player ? resolve(window.shaka) : reject(new Error('Shaka Player unavailable'));
    script.onerror = () => reject(new Error('Shaka Player load failed'));
    document.head.appendChild(script);
  });
  return shakaLoaderPromise;
}

async function initShaka(url, session, attemptToken) {
  await ensureShakaLibrary();
  if (!window.shaka?.Player) throw new Error('Shaka Player load হয়নি');
  state.playerType = 'shaka';
  shaka.polyfill.installAll();
  if (!shaka.Player.isBrowserSupported()) throw new Error('DASH playback supported নয়');

  const player = new shaka.Player();
  state.shaka = player;
  await player.attach(video);
  if (!isActiveAttempt(session, attemptToken)) {
    try { await player.destroy(); } catch (_) {}
    if (state.shaka === player) state.shaka = null;
    return;
  }
  const isMovie = session.item._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE;
  player.configure(shakaConfigFor(currentNetworkMode(), isMovie));

  ['loading', 'trackschanged', 'adaptation', 'streaming'].forEach((eventName) => {
    player.addEventListener(eventName, () => markAttemptProgress(`DASH ${eventName}`, attemptToken));
  });
  player.addEventListener('buffering', (event) => {
    if (event.buffering) markAttemptProgress('DASH buffering', attemptToken);
  });

  const clearKeys = parseClearKeys(session.item.drm?.license_key);
  if (clearKeys) player.configure({ drm: { clearKeys } });
  player.addEventListener('error', (event) => {
    if (!isActiveAttempt(session, attemptToken) || isQualityLocked() || state.userPaused) return;
    if (
      session.routeAccepted &&
      !session.allowRouteFailover &&
      (session.startupBufferGateActive || Date.now() < Number(state.mediaOperationGraceUntil || 0))
    ) {
      try { player.retryStreaming?.(); } catch (_) {}
      return;
    }
    failCurrentAttempt(event.detail?.message || 'Shaka playback error', attemptToken);
  });
  await player.load(url);
  if (!isActiveAttempt(session, attemptToken)) return;
  markAttemptProgress('DASH manifest loaded', attemptToken);

  if (!isMovie && state.selectedManualQuality === -1) {
    applyInitialShakaLiveTrack(player, session.item);
  }

  try {
    if (player.isLive?.()) {
      const range = player.seekRange();
      const end = Number(range?.end || 0);
      const start = Number(range?.start || 0);
      const mode = currentNetworkMode();
      const targetLatency = mode === NETWORK_MODE.AUTO ? 1.8 : mode === NETWORK_MODE.STABLE ? 5 : 2.5;
      if (end > start) video.currentTime = Math.max(start, end - targetLatency);
    }
  } catch (_) {}

  await safePlay(session, attemptToken);
  buildQualityMenu();
}

function parseClearKeys(value) {
  if (!value) return null;
  if (typeof value === 'object' && !Array.isArray(value)) return value;
  const output = {};
  String(value).split(',').forEach((pair) => {
    const [keyId, key] = pair.trim().split(':');
    if (keyId && key) output[keyId.trim()] = key.trim();
  });
  return Object.keys(output).length ? output : null;
}

let mpegtsLoaderPromise = null;
function ensureMpegTsLibrary() {
  if (window.mpegts) return Promise.resolve(window.mpegts);
  if (mpegtsLoaderPromise) return mpegtsLoaderPromise;
  mpegtsLoaderPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = MPEGTS_CDN;
    script.async = true;
    script.onload = () => window.mpegts ? resolve(window.mpegts) : reject(new Error('MPEGTS library unavailable'));
    script.onerror = () => reject(new Error('MPEGTS Player load হয়নি'));
    document.head.appendChild(script);
  });
  return mpegtsLoaderPromise;
}

async function initMpegTs(url, session, attemptToken) {
  const mpegts = await ensureMpegTsLibrary();
  if (!mpegts.isSupported()) throw new Error('MPEGTS playback supported নয়');
  state.playerType = 'mpegts';
  const player = mpegts.createPlayer({
    type: 'mpegts',
    isLive: session.item._sourceKind !== VIEW.MOVIE,
    url
  }, {
    enableWorker: true,
    lazyLoad: true,
    liveBufferLatencyChasing: true,
    stashInitialSize: resolveAutoProfile() === 'lite' ? 128 * 1024 : 384 * 1024
  });
  state.mpegts = player;
  player.attachMediaElement(video);
  player.load();
  player.on(mpegts.Events.ERROR, (_, detail) => {
    if (isActiveAttempt(session, attemptToken) && !isQualityLocked()) failCurrentAttempt(detail || 'MPEGTS error', attemptToken);
  });
  markAttemptProgress('MPEGTS loader attached', attemptToken);
  await safePlay(session, attemptToken);
  buildQualityMenu();
}

async function safePlay(session, attemptToken) {
  state.userPaused = false;
  const playOnce = async () => {
    await video.play();
    if (!isActiveAttempt(session, attemptToken)) throw new DOMException('Stale attempt', 'AbortError');
  };

  try {
    await playOnce();
  } catch (error) {
    if (!isActiveAttempt(session, attemptToken)) throw new DOMException('Stale attempt', 'AbortError');

    if (error?.name === 'NotAllowedError') {
      if (video.volume > 0) state.lastNonZeroVolume = video.volume;
      video.muted = true;
      state.autoplayUnlockPending = state.userWantsSound;
      updateMuteUi();
      await playOnce();
      return;
    }

    // Slow progressive files (including trusted manual media) can reject play while
    // the browser is still attaching the source. Give the active route one real retry.
    const movieAttempt = session.item?._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE;
    if (error?.name === 'AbortError' || (movieAttempt && error?.name === 'NotSupportedError')) {
      await new Promise((resolve) => setTimeout(resolve, movieAttempt ? 900 : 260));
      if (!isActiveAttempt(session, attemptToken)) throw new DOMException('Stale attempt', 'AbortError');
      try { video.load(); } catch (_) {}
      await waitForNativeStartupSignal(session, attemptToken, movieAttempt ? 1300 : 400);
      if (!isActiveAttempt(session, attemptToken)) throw new DOMException('Stale attempt', 'AbortError');
      await playOnce();
      return;
    }

    throw error;
  }
}

async function resumeVideoSafely(reason = 'resume', notifyUser = false) {
  if (state.userPaused || video.ended) return false;
  try {
    await video.play();
    return true;
  } catch (error) {
    console.info(`Video ${reason} deferred:`, error?.name || error?.message || error);
    if (notifyUser && error?.name !== 'AbortError') showToast('Play button চাপুন');
    return false;
  }
}

function failCurrentAttempt(reason, attemptToken = state.playbackSession?.attemptToken) {
  const session = state.playbackSession;
  if (!session || !isActiveAttempt(session, attemptToken) || isQualityLocked()) return;

  const acceptedLiveRoute = Boolean(
    session.routeAccepted &&
    !isMoviePlaybackContext(session.item) &&
    !session.allowRouteFailover
  );
  if (acceptedLiveRoute) {
    if (!session.stallStartedAt) session.stallStartedAt = Date.now();
    session.success = true;
    tryLiveNetworkRecovery(true);
    return;
  }

  const protectedPlaybackWindow = Boolean(
    session.routeAccepted &&
    !session.allowRouteFailover &&
    (session.startupBufferGateActive || Date.now() < Number(state.mediaOperationGraceUntil || 0))
  );
  if (protectedPlaybackWindow) {
    tryLiveNetworkRecovery();
    return;
  }

  clearPlaybackTimers();
  const attempt = session.currentAttempt;
  if (attempt?.route === 'proxy') {
    markProxyResult(attempt.proxy, attempt.source.url, false, Date.now() - session.attemptStartedAt);
  }
  session.success = false;
  sendPlaybackTelemetry('failure', reason);
  session.attemptIndex += 1;
  console.warn('Trying next playback attempt:', reason);
  runCurrentAttempt();
}

function isMobilePlaybackDevice() {
  return window.matchMedia('(max-width: 1000px), (pointer: coarse)').matches;
}

function updateMobilePlaybackPerformance() {
  const active = isMobilePlaybackDevice() && !video.paused && !video.ended;
  document.documentElement.classList.toggle('mobile-playback-performance', active);
  if (active) startPlaybackPerformanceMonitor();
  else stopPlaybackPerformanceMonitor(false);
}

function readVideoFrameStats() {
  try {
    if (typeof video.getVideoPlaybackQuality === 'function') {
      const quality = video.getVideoPlaybackQuality();
      return {
        total: Number(quality.totalVideoFrames || 0),
        dropped: Number(quality.droppedVideoFrames || 0)
      };
    }
  } catch (_) {}

  const total = Number(video.webkitDecodedFrameCount || video.mozDecodedFrames || 0);
  const dropped = Number(video.webkitDroppedFrameCount || video.mozDroppedFrames || 0);
  return { total, dropped };
}


function bufferedAheadSeconds() {
  try {
    const currentTime = Number(video.currentTime || 0);
    for (let index = 0; index < video.buffered.length; index += 1) {
      const start = Number(video.buffered.start(index));
      const end = Number(video.buffered.end(index));
      if (currentTime >= start - 0.15 && currentTime <= end + 0.15) {
        return Math.max(0, end - currentTime);
      }
    }
  } catch (_) {}
  return 0;
}

function shouldUseLiveStartupBufferGate(session = state.playbackSession) {
  if (!session || session.startupBufferGateReleased || session.startupBufferGateActive) return false;
  if (isMoviePlaybackContext(session.item)) return false;
  const format = detectFormat(
    session.currentAttempt?.source?.url || '',
    { ...session.item, ...session.currentAttempt?.source }
  );
  return format === 'hls' || format === 'dash';
}

function liveStartupBufferTargetSeconds(item = state.currentItem) {
  const mode = currentNetworkMode();
  const isEvent = isLiveEventContext(item);
  if (isEvent) {
    if (mode === NETWORK_MODE.BALANCED) return 1.8;
    if (mode === NETWORK_MODE.STABLE) return 3.4;
    return 2.8;
  }
  if (mode === NETWORK_MODE.BALANCED) return 2.2;
  if (mode === NETWORK_MODE.STABLE) return 4.0;
  return 3.2;
}

function liveStartupBufferMinimumSeconds(item = state.currentItem) {
  const mode = currentNetworkMode();
  const isEvent = isLiveEventContext(item);
  if (mode === NETWORK_MODE.BALANCED) return isEvent ? 0.8 : 1.0;
  if (mode === NETWORK_MODE.STABLE) return isEvent ? 1.9 : 2.2;
  return isEvent ? 1.5 : 1.8;
}

function liveStartupBufferMaximumWaitMs(item = state.currentItem) {
  const mode = currentNetworkMode();
  const isEvent = isLiveEventContext(item);
  if (isEvent) {
    if (mode === NETWORK_MODE.BALANCED) return 2000;
    if (mode === NETWORK_MODE.STABLE) return 4000;
    return 3000;
  }
  if (mode === NETWORK_MODE.BALANCED) return 2400;
  if (mode === NETWORK_MODE.STABLE) return 4400;
  return 3300;
}

function releaseLiveStartupBufferGate(session, attemptToken) {
  if (!session || !isActiveAttempt(session, attemptToken) || session.startupBufferGateReleased) return;
  if (session.startupBufferGateTimer) clearInterval(session.startupBufferGateTimer);
  session.startupBufferGateTimer = null;
  session.startupBufferGateActive = false;
  session.startupBufferGateReleased = true;
  state.mediaOperationGraceUntil = Math.max(Number(state.mediaOperationGraceUntil || 0), Date.now() + 7000);
  video.play().catch((error) => {
    if (isActiveAttempt(session, attemptToken)) {
      failCurrentAttempt(error?.message || 'Unable to resume after startup buffering', attemptToken);
    }
  });
}

function startLiveStartupBufferGate(session, attemptToken) {
  if (!shouldUseLiveStartupBufferGate(session) || !isActiveAttempt(session, attemptToken)) return false;

  if (session.attemptTimer) clearTimeout(session.attemptTimer);
  if (session.progressExtensionTimer) clearTimeout(session.progressExtensionTimer);
  session.attemptTimer = null;
  session.progressExtensionTimer = null;
  session.startupBufferGateActive = true;
  session.startupBufferGateStartedAt = Date.now();
  showPlayerMessage(sourceAttemptMessage(), true);

  try { video.pause(); } catch (_) {}

  const targetSeconds = liveStartupBufferTargetSeconds(session.item);
  const minimumSeconds = liveStartupBufferMinimumSeconds(session.item);
  const maximumWaitMs = liveStartupBufferMaximumWaitMs(session.item);
  const hardMaximumWaitMs = maximumWaitMs + 1400;
  const check = () => {
    if (!isActiveAttempt(session, attemptToken)) {
      if (session.startupBufferGateTimer) clearInterval(session.startupBufferGateTimer);
      session.startupBufferGateTimer = null;
      return;
    }
    const elapsed = Date.now() - session.startupBufferGateStartedAt;
    const buffered = bufferedAheadSeconds();

    if (buffered < targetSeconds) {
      try { state.hls?.startLoad(-1); } catch (_) {}
      try { state.shaka?.retryStreaming?.(); } catch (_) {}
    }

    const targetReady = buffered >= targetSeconds;
    const normalDeadlineReady = elapsed >= maximumWaitMs && buffered >= minimumSeconds;
    const hardDeadlineReached = elapsed >= hardMaximumWaitMs;
    if (targetReady || normalDeadlineReady || hardDeadlineReached) {
      releaseLiveStartupBufferGate(session, attemptToken);
    }
  };

  session.startupBufferGateTimer = setInterval(check, 180);
  check();
  return true;
}

function liveStartupQualityStages(item = state.currentItem) {
  if (!item || isMoviePlaybackContext(item)) return [];

  const userAgent = String(navigator.userAgent || '');
  const isTvDevice = /TV|Android TV|AFT|SmartTV|BRAVIA|MiBOX|TV Bro/i.test(userAgent);
  const isPhoneOrTablet = window.matchMedia('(max-width: 1000px)').matches && !isTvDevice;
  const deviceClass = effectivePerformanceClass();

  const isEvent = isLiveEventContext(item);
  let stages;
  if (isEvent) {
    if (deviceClass === 'ultra-lite') stages = [360, 480];
    else if (deviceClass === 'lite' || isPhoneOrTablet) stages = [360, 480, 720];
    else stages = [480, 720, 1080];
  } else if (deviceClass === 'ultra-lite') stages = [360, 480];
  else if (deviceClass === 'lite' || isPhoneOrTablet) stages = [480, 720];
  else stages = [480, 720, 1080];

  const permanentCap = Number(qualityCapHeight() || 0);
  return [...new Set(
    stages
      .map((height) => permanentCap > 0 ? Math.min(height, permanentCap) : height)
      .filter((height) => Number.isFinite(height) && height > 0)
  )];
}

function effectiveLiveQualityCap(requestedHeight = 0) {
  const requested = Number(requestedHeight || 0);
  const deviceCap = Number(qualityCapHeight() || 0);
  const performanceCap = Number(state.adaptiveDecodeCapHeight || 0);
  const caps = [requested, deviceCap, performanceCap]
    .filter((height) => Number.isFinite(height) && height > 0);
  return caps.length ? Math.min(...caps) : 0;
}

function shakaAvailableCapHeight(requestedHeight = 0) {
  const requested = Number(requestedHeight || 0);
  if (!state.shaka || requested <= 0) return 0;

  const heights = state.shaka.getVariantTracks()
    .filter((track) => track.type === 'variant' && Number(track.height || 0) > 0)
    .map((track) => Number(track.height || 0))
    .filter((height) => height <= requested)
    .sort((a, b) => b - a);

  return heights[0] || 0;
}

function applyLiveAdaptiveQualityCap(requestedHeight = 0, startupHint = false) {
  if (isMoviePlaybackContext()) return;
  if (state.selectedManualQuality !== -1) return;

  state.liveStartupQualityCapHeight = Number(requestedHeight || 0);
  const maxHeight = effectiveLiveQualityCap(requestedHeight);

  if (state.hls) {
    const capLevel = maxHeight > 0 ? findHlsCapLevel(maxHeight) : -1;
    state.hls.autoLevelCapping = capLevel;

    if (startupHint && capLevel >= 0 && !state.playbackSession?.success) {
      try {
        state.hls.startLevel = capLevel;
        state.hls.nextAutoLevel = capLevel;
      } catch (_) {}
    }
  }

  if (state.shaka) {
    const baseConfig = shakaConfigFor(currentNetworkMode(), false);
    const availableCap = maxHeight > 0 ? shakaAvailableCapHeight(maxHeight) : 0;

    try {
      state.shaka.configure({
        abr: {
          ...baseConfig.abr,
          enabled: true,
          restrictions: availableCap > 0 ? { maxHeight: availableCap } : {}
        }
      });
    } catch (_) {}
  }
}

function applyInitialShakaLiveTrack(player, item = state.currentItem) {
  if (!player || isMoviePlaybackContext(item) || state.selectedManualQuality !== -1) return;

  const stages = liveStartupQualityStages(item);
  const requestedHeight = stages[0] || 0;
  if (requestedHeight <= 0) return;

  const maxHeight = effectiveLiveQualityCap(requestedHeight);
  const tracks = player.getVariantTracks()
    .filter((track) => track.type === 'variant' && Number(track.height || 0) > 0)
    .sort((a, b) => Number(a.height || 0) - Number(b.height || 0));

  if (!tracks.length) return;

  const eligible = tracks.filter((track) => Number(track.height || 0) <= maxHeight);
  const selectedTrack = eligible.length ? eligible[eligible.length - 1] : tracks[0];
  if (!selectedTrack) return;

  state.liveStartupQualityCapHeight = requestedHeight;

  try {
    player.configure({ abr: { enabled: false } });
    player.selectVariantTrack(selectedTrack, true, 0);
    player.configure({
      abr: {
        enabled: true,
        restrictions: { maxHeight: Number(selectedTrack.height || maxHeight) }
      }
    });
  } catch (_) {}
}

function stopLiveAdaptiveQualityRamp(releaseTemporaryCap = false) {
  if (state.liveAdaptiveQualityTimer) {
    clearInterval(state.liveAdaptiveQualityTimer);
  }

  state.liveAdaptiveQualityTimer = null;
  state.liveAdaptiveQualityStartedAt = 0;
  state.liveAdaptiveQualityLastStepAt = 0;
  state.liveAdaptiveQualityStage = 0;

  if (
    releaseTemporaryCap &&
    !isMoviePlaybackContext() &&
    state.selectedManualQuality === -1
  ) {
    state.liveStartupQualityCapHeight = 0;
    applyLiveAdaptiveQualityCap(0);
  }
}

function startLiveAdaptiveQualityRamp(
  session = state.playbackSession,
  attemptToken = session?.attemptToken
) {
  stopLiveAdaptiveQualityRamp(false);

  if (!session || !isActiveAttempt(session, attemptToken)) return;
  if (isMoviePlaybackContext(session.item)) return;
  if (state.selectedManualQuality !== -1) return;
  if (!state.hls && !state.shaka) return;

  const stages = liveStartupQualityStages(session.item);
  if (!stages.length) return;

  const now = Date.now();
  state.liveAdaptiveQualityStartedAt = now;
  state.liveAdaptiveQualityLastStepAt = now;
  state.liveAdaptiveQualityStage = 0;
  applyLiveAdaptiveQualityCap(stages[0], false);

  state.liveAdaptiveQualityTimer = setInterval(() => {
    if (!isActiveAttempt(session, attemptToken)) {
      stopLiveAdaptiveQualityRamp(false);
      return;
    }

    if (isMoviePlaybackContext(session.item) || state.selectedManualQuality !== -1) {
      stopLiveAdaptiveQualityRamp(false);
      return;
    }

    if (video.paused || video.ended || video.readyState < 3) return;
    if (state.fullscreenLiveQualityGuardTimer) return;

    const currentTime = Date.now();
    const totalElapsed = currentTime - state.liveAdaptiveQualityStartedAt;
    const sinceLastStep = currentTime - state.liveAdaptiveQualityLastStepAt;
    const progressIsFresh = currentTime - Number(session.lastProgressAt || 0) < 1800;
    const currentlyStalling = Boolean(session.stallStartedAt);

    if (!progressIsFresh || currentlyStalling) return;
    if (totalElapsed < 10000 || sinceLastStep < 10000) return;

    const profile = networkProfile(currentNetworkMode(), false);
    const requiredBuffer = Math.max(
      3,
      Math.min(7, Number(profile.maxBufferLength || 0) * 0.8)
    );
    const currentBuffer = bufferedAheadSeconds();
    const enoughBuffer = currentBuffer >= requiredBuffer;
    const maximumWaitReached = totalElapsed >= 36000;

    if (!enoughBuffer && (!maximumWaitReached || currentBuffer < 3.5)) return;

    if (state.liveAdaptiveQualityStage < stages.length - 1) {
      state.liveAdaptiveQualityStage += 1;
      state.liveAdaptiveQualityLastStepAt = currentTime;
      applyLiveAdaptiveQualityCap(stages[state.liveAdaptiveQualityStage], false);
      return;
    }

    state.liveStartupQualityCapHeight = 0;
    applyLiveAdaptiveQualityCap(0);
    stopLiveAdaptiveQualityRamp(false);
  }, 1000);
}

function findHlsCapLevel(maxHeight) {
  const levels = Array.isArray(state.hls?.levels) ? state.hls.levels : [];
  let bestIndex = -1;
  let bestHeight = -1;
  levels.forEach((level, index) => {
    const height = Number(level?.height || 0);
    if (height > 0 && height <= maxHeight && height > bestHeight) {
      bestHeight = height;
      bestIndex = index;
    }
  });
  return bestIndex;
}

function applyAdaptiveDecodeCap(maxHeight) {
  if (state.selectedManualQuality !== -1) return;
  const height = Number(maxHeight || 0);
  state.adaptiveDecodeCapHeight = height;

  if (!isMoviePlaybackContext()) {
    applyLiveAdaptiveQualityCap(state.liveStartupQualityCapHeight || 0);
    return;
  }

  if (state.hls) {
    state.hls.autoLevelCapping = height > 0 ? findHlsCapLevel(height) : -1;
  }

  if (state.shaka) {
    const baseConfig = shakaConfigFor(currentNetworkMode(), true);
    state.shaka.configure({
      ...baseConfig,
      abr: {
        ...baseConfig.abr,
        restrictions: height > 0 ? { maxHeight: height } : {}
      }
    });
  }
}

function stopPlaybackPerformanceMonitor(resetCap = false) {
  if (state.performanceMonitorTimer) clearInterval(state.performanceMonitorTimer);
  state.performanceMonitorTimer = null;
  state.performanceSample = null;
  state.performanceStressStreak = 0;
  state.performanceStableStreak = 0;
  if (resetCap && state.adaptiveDecodeCapHeight > 0) applyAdaptiveDecodeCap(0);
  if (resetCap) {
    state.adaptiveDecodeCapHeight = 0;
    state.performanceNoticeShown = false;
  }
}

function startPlaybackPerformanceMonitor() {
  if (!isMobilePlaybackDevice() || state.performanceMonitorTimer || video.paused || video.ended) return;
  state.performanceSample = readVideoFrameStats();

  state.performanceMonitorTimer = setInterval(() => {
    if (document.hidden || video.paused || video.ended || !state.playbackSession?.success) return;
    const next = readVideoFrameStats();
    const previous = state.performanceSample;
    state.performanceSample = next;
    if (!previous || next.total <= previous.total) return;

    const decodedDelta = next.total - previous.total;
    const droppedDelta = Math.max(0, next.dropped - previous.dropped);
    const dropRatio = decodedDelta > 0 ? droppedDelta / decodedDelta : 0;
    const stressed = decodedDelta >= 24 && (droppedDelta >= 6 || dropRatio >= 0.055);

    if (stressed) {
      state.performanceStressStreak += 1;
      state.performanceStableStreak = 0;
    } else {
      state.performanceStressStreak = 0;
      state.performanceStableStreak += 1;
    }

    if (state.performanceStressStreak >= 2 && state.selectedManualQuality === -1) {
      const nextCap = state.adaptiveDecodeCapHeight > 720 ? 720
        : state.adaptiveDecodeCapHeight === 720 ? 480
          : state.adaptiveDecodeCapHeight === 480 ? 360
            : 720;
      if (nextCap !== state.adaptiveDecodeCapHeight) {
        applyAdaptiveDecodeCap(nextCap);
        if (!state.performanceNoticeShown) {
          state.performanceNoticeShown = true;
          showToast('Smooth playback-এর জন্য quality সাময়িকভাবে কমানো হয়েছে', 3200);
        }
      }
      state.performanceStressStreak = 0;
    }

    if (state.adaptiveDecodeCapHeight > 0 && state.performanceStableStreak >= 10) {
      applyAdaptiveDecodeCap(0);
      state.performanceStableStreak = 0;
      state.performanceNoticeShown = false;
    }
  }, 4000);
}

function attemptTimeoutFor(attempt, format, item) {
  const kind = item?._sourceKind || state.view;
  const isEvent = kind === VIEW.EVENT || kind === VIEW.UPCOMING;
  const isMovie = kind === VIEW.MOVIE;
  const isDrmDash = format === 'dash' && Boolean(item?.drm || attempt?.source?.drm);

  if (isMovie) {
    const trustedManual = Boolean(item?.manual_source || item?.skip_verification || item?.verification_status === 'manual_trusted');
    if (format === 'direct') {
      if (attempt?.route === 'direct') {
        if (trustedManual) return attempt?.sourceIndex === 0 ? 42000 : 34000;
        return attempt?.sourceIndex === 0 ? 30000 : 26000;
      }
      return trustedManual ? 30000 : 24000;
    }
    if (format === 'dash' || format === 'hls') return attempt?.route === 'proxy' ? 24000 : 21000;
    return attempt?.route === 'proxy' ? 24000 : 28000;
  }

  if (isEvent && isDrmDash) return attempt?.route === 'proxy' ? 12000 : 9500;
  if (isEvent && format === 'dash') return attempt?.route === 'proxy' ? 10500 : 8500;
  if (format === 'dash') return attempt?.route === 'proxy' ? 9000 : 8000;
  if (attempt?.route === 'direct') return attempt?.sourceIndex === 0 ? 5200 : 5800;
  return 6000;
}

function acceptPlaybackRoute(session) {
  if (!session || session.routeAccepted) return;

  session.routeAccepted = true;
  session.routeAcceptedAt = Date.now();
  session.success = true;
  session.playingStartedAt = Date.now();
  clearPlaybackTimers();

  const elapsed = Date.now() - session.attemptStartedAt;
  if (session.currentAttempt?.route === 'proxy') {
    markProxyResult(session.currentAttempt.proxy, session.currentAttempt.source.url, true, elapsed);
  }

  if (state.currentItem && session.currentAttempt?.source?.url) {
    state.currentItem._activeSourceUrl = session.currentAttempt.source.url;
  }

  recordPlaybackSuccess();
  sendPlaybackTelemetry('success');
}

function finalizePlaybackSuccess(session) {
  if (!session || session.playbackFinalized || !session.routeAccepted) return;
  session.playbackFinalized = true;
  hidePlayerMessage();
  state.autoNextCount = 0;
  state.autoNextFailedUids = [];
  updatePlayPauseUi();
  startLiveAdaptiveQualityRamp(session, session.attemptToken);
  startStallDetector();
  updateMobilePlaybackPerformance();
  buildQualityMenu();
}

function handlePlaybackSuccess() {
  const session = state.playbackSession;
  if (!session || session.id !== state.activeLoadId || String(session.attemptToken) !== video.dataset.attemptToken) return;

  acceptPlaybackRoute(session);
  session.startupBufferGateActive = false;
  session.startupBufferGateReleased = true;
  finalizePlaybackSuccess(session);
}

function telemetryEndpoint() {
  return String(state.runtime?.telemetry_url || '').trim();
}

function telemetrySessionId() {
  if (state.telemetrySessionId) return state.telemetrySessionId;
  let saved = sessionStorage.getItem(STORAGE_KEYS.telemetrySession);
  if (!saved) {
    saved = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    try { sessionStorage.setItem(STORAGE_KEYS.telemetrySession, saved); } catch (_) {}
  }
  state.telemetrySessionId = saved;
  return saved;
}

function classifyPlaybackFailure(reason = '') {
  const text = String(reason || '').toLowerCase();
  if (/http\s*403|forbidden/.test(text)) return 'manifest_or_segment_403';
  if (/http\s*404|not found/.test(text)) return 'manifest_or_segment_404';
  if (/530/.test(text)) return 'origin_530';
  if (/drm|clearkey|license/.test(text)) return 'drm_error';
  if (/codec|not supported|mse|media source/.test(text)) return 'codec_or_mse_unsupported';
  if (/timeout|progress stopped/.test(text)) return 'startup_timeout';
  if (/stall/.test(text)) return 'network_stall';
  if (/autoplay|notallowed/.test(text)) return 'autoplay_blocked';
  return 'unknown';
}

function sendPlaybackTelemetry(result, reason = '') {
  const endpoint = telemetryEndpoint();
  const session = state.playbackSession;
  const attempt = session?.currentAttempt;
  const item = session?.item || state.currentItem;
  if (!endpoint || !item) return;

  const payload = {
    item_id: String(item.id || item._uid || '').slice(0, 160),
    source_id: String(attempt?.source?.source_id || item.source_id || '').slice(0, 120),
    source_index: Number(attempt?.sourceIndex || 0),
    route: String(attempt?.route || ''),
    proxy_name: attempt?.proxy ? String(attempt.proxy).replace(/^https?:\/\//, '').split('.')[0] : '',
    stream_type: String(attempt?.source?.stream_type || item.stream_type || detectFormat(attempt?.source?.url || item.url, item)),
    result: result === 'success' ? 'success' : 'failure',
    failure_class: result === 'success' ? '' : classifyPlaybackFailure(reason),
    startup_ms: Math.max(0, Date.now() - Number(session?.attemptStartedAt || Date.now())),
    device_class: effectivePerformanceClass(),
    network_mode: currentNetworkMode(),
    session_id: telemetrySessionId(),
    ts: Date.now()
  };

  try {
    const body = JSON.stringify(payload);
    const contentType = 'text/plain;charset=UTF-8';
    if (navigator.sendBeacon) {
      const queued = navigator.sendBeacon(
        endpoint,
        new Blob([body], { type: contentType })
      );
      if (queued) return;
    }
    fetch(endpoint, {
      method: 'POST',
      mode: 'no-cors',
      keepalive: true,
      headers: { 'Content-Type': contentType },
      body
    }).catch(() => {});
  } catch (_) {}
}

function recordPlaybackSuccess() {
  const bandwidth = Number(state.hls?.bandwidthEstimate || 0);
  if (bandwidth > 0) state.playbackHistory.lastBandwidth = bandwidth;
  state.playbackHistory.successfulStarts = Number(state.playbackHistory.successfulStarts || 0) + 1;
  state.playbackHistory.updatedAt = Date.now();
  writeJsonStorage(STORAGE_KEYS.playbackHistory, state.playbackHistory);
}

function handlePlaybackPlanExhausted(reason) {
  const session = state.playbackSession;
  if (session) {
    session.attemptToken += 1;
    const uid = session.item?._uid;
    if (uid && !state.autoNextFailedUids.includes(uid)) {
      state.autoNextFailedUids.push(uid);
    }
  }

  clearPlaybackTimers();
  stopStallDetector();
  cleanupPlayerEngine().catch(() => {});

  console.warn('Playback plan exhausted', {
    reason,
    item: session?.item?.name || '',
    attempts: session?.attemptsRun || 0
  });

  const kind = session?.item?._sourceKind || state.view;
  const message = kind === VIEW.MOVIE
    ? 'মুভিটি চালানো যায়নি।'
    : kind === VIEW.EVENT
      ? 'লাইভ ম্যাচটি চালানো যায়নি।'
      : 'চ্যানেলটি চালানো যায়নি।';

  showPlayerMessage(message, false);
  showFailureActions();
}

function showFailureActions() {
  clearAutoNextTimer();

  const session = state.playbackSession;
  const allItems = getNavigationItems(false);
  const remainingItems = getNavigationItems(true);
  const genuinelyTried = Number(session?.attemptsRun || 0) > 0;
  const canAutoNext =
    genuinelyTried &&
    !session?.userInitiated &&
    state.autoNextCount < AUTO_NEXT_LIMIT &&
    allItems.length > 1 &&
    remainingItems.length > 0;

  errorCountdownBox.style.display = 'flex';
  $('retryCurrentBtn').style.display = 'inline-flex';
  $('nextNowBtn').style.display = allItems.length > 1 ? 'inline-flex' : 'none';

  if (!canAutoNext) {
    $('errorCountdownLabel').textContent = session?.userInitiated
      ? 'এই চ্যানেলের সব উপলভ্য link চেষ্টা করা হয়েছে। Retry অথবা Next নির্বাচন করুন।'
      : state.autoNextCount >= AUTO_NEXT_LIMIT
        ? '৩টি বিকল্প চেষ্টা করা হয়েছে। তালিকা থেকে অন্যটি নির্বাচন করুন।'
        : remainingItems.length === 0 && allItems.length > 1
          ? 'এই তালিকার উপলভ্য বিকল্পগুলো চেষ্টা করা হয়েছে।'
          : 'অন্য কোনো চালানো যায় এমন আইটেম পাওয়া যায়নি।';
    return;
  }

  let seconds = AUTO_NEXT_SECONDS;
  $('errorCountdownLabel').replaceChildren(
    document.createTextNode('পরবর্তীটি চেষ্টা হবে '),
    Object.assign(document.createElement('strong'), {
      id: 'errorCountdown',
      textContent: String(seconds)
    }),
    document.createTextNode(' সেকেন্ডে')
  );

  state.autoNextTimer = setInterval(() => {
    seconds -= 1;
    const counter = $('errorCountdown');
    if (counter) counter.textContent = String(Math.max(0, seconds));

    if (seconds <= 0) {
      clearAutoNextTimer();
      state.autoNextCount += 1;
      playRelativeItem(1, false);
    }
  }, 1000);
}

function hideFailureActions() {
  errorCountdownBox.style.display = 'none';
}

function clearAutoNextTimer() {
  if (state.autoNextTimer) clearInterval(state.autoNextTimer);
  state.autoNextTimer = null;
}

function retryCurrentItem() {
  clearAutoNextTimer();
  if (state.currentItem) startPlayback(state.currentItem, true);
}

function getNavigationItems(excludeFailed = false) {
  const playable = state.filteredItems.filter(isPlayable);
  if (!excludeFailed) return playable;

  const failed = new Set(state.autoNextFailedUids || []);
  return playable.filter((item) => !failed.has(item._uid));
}

function playRelativeItem(direction, userInitiated = true) {
  if (userInitiated && seriesModule?.isEpisodeItem(state.currentItem)) {
    seriesModule.playRelativeEpisode(direction);
    return;
  }
  const allItems = getNavigationItems(false);
  if (!allItems.length) return;

  if (userInitiated) {
    const currentIndex = allItems.findIndex(
      (item) => item._uid === state.currentItem?._uid || item.url === state.currentItem?.url
    );
    const nextIndex = currentIndex < 0
      ? 0
      : (currentIndex + direction + allItems.length) % allItems.length;
    startPlayback(allItems[nextIndex], true);
    return;
  }

  const failed = new Set(state.autoNextFailedUids || []);
  const currentIndex = allItems.findIndex(
    (item) => item._uid === state.currentItem?._uid || item.url === state.currentItem?.url
  );

  for (let step = 1; step <= allItems.length; step += 1) {
    const index = currentIndex < 0
      ? step - 1
      : (currentIndex + direction * step + allItems.length * 2) % allItems.length;
    const candidate = allItems[index];
    if (!candidate || failed.has(candidate._uid)) continue;
    startPlayback(candidate, false);
    return;
  }

  showFailureActions();
}

function bufferedSecondsAhead() {
  try {
    const current = Number(video.currentTime || 0);
    for (let index = 0; index < video.buffered.length; index += 1) {
      const start = Number(video.buffered.start(index));
      const end = Number(video.buffered.end(index));
      if (current >= start - 0.08 && current <= end + 0.08) return Math.max(0, end - current);
    }
  } catch (_) {}
  return 0;
}

function tryLiveNetworkRecovery(force = false) {
  try {
    if (state.hls && (force || Number(state.playbackSession?.stallStep || 0) >= 2)) {
      state.hls.startLoad(-1);
      if (force && bufferedAheadSeconds() < 0.8) {
        const livePosition = Number(state.hls.liveSyncPosition || 0);
        if (livePosition > 0 && Math.abs(livePosition - Number(video.currentTime || 0)) > 1.5) {
          video.currentTime = Math.max(0, livePosition - 1.0);
        }
      }
    }
  } catch (_) {}
  try {
    if (state.shaka && typeof state.shaka.retryStreaming === 'function') {
      state.shaka.retryStreaming();
    }
  } catch (_) {}
  try {
    if (state.mpegts) {
      state.mpegts.load();
      state.mpegts.play();
    }
  } catch (_) {}
  void resumeVideoSafely('network recovery');
}

function startStallDetector() {
  stopStallDetector();
  const session = state.playbackSession;
  if (!session) return;

  session.lastTime = video.currentTime || 0;
  session.lastProgressAt = Date.now();
  session.stallStartedAt = 0;
  session.stallStep = 0;

  state.stallInterval = setInterval(() => {
    const active = state.playbackSession;
    if (!active || active.id !== state.activeLoadId || !active.success || document.hidden || video.paused) return;
    if (
      active.startupBufferGateActive ||
      isQualityLocked() ||
      Date.now() < state.recoveryLockUntil ||
      Date.now() < Number(state.mediaOperationGraceUntil || 0)
    ) {
      active.lastTime = Number(video.currentTime || active.lastTime || 0);
      active.lastProgressAt = Date.now();
      active.stallStartedAt = 0;
      active.stallStep = 0;
      return;
    }

    const current = video.currentTime || 0;
    const progressed = current > active.lastTime + 0.04;
    if (progressed) {
      active.lastTime = current;
      active.lastProgressAt = Date.now();
      active.stallStartedAt = 0;
      active.stallStep = 0;
      return;
    }

    const stalled = video.readyState < 3 || Date.now() - active.lastProgressAt > 2200;
    if (!stalled) return;

    if (!active.stallStartedAt) active.stallStartedAt = Date.now();
    const elapsed = Date.now() - active.stallStartedAt;
    const kind = active.item?._sourceKind || state.view;
    const activeMovieHeight = kind === VIEW.MOVIE ? Number(activeDirectMovieQualityGroup(active.item)?.height || 0) : 0;
    const finalRecoveryAt = (kind === VIEW.EVENT || kind === VIEW.UPCOMING)
      ? LIVE_EVENT_STALL_FAILOVER_MS
      : kind === VIEW.MOVIE
        ? (activeMovieHeight >= 2160 ? MOVIE_4K_STALL_FAILOVER_MS : MOVIE_STALL_FAILOVER_MS)
        : LIVE_CHANNEL_STALL_FAILOVER_MS;

    if (elapsed >= 3000 && active.stallStep === 0) {
      active.stallStep = 1;
      tryGapRecovery();
    }

    if (elapsed >= 6000 && active.stallStep === 1) {
      active.stallStep = 2;
      tryLiveEdgeRecovery();
    }

    const isLivePlayback = kind !== VIEW.MOVIE;
    if (isLivePlayback && elapsed >= 9000 && active.stallStep === 2) {
      active.stallStep = 3;
      tryLiveNetworkRecovery(true);
    }

    const readyForFailover = isLivePlayback ? active.stallStep === 3 : active.stallStep === 2;
    if (elapsed >= finalRecoveryAt && readyForFailover) {
      active.stallStep = 4;
      state.playbackHistory.stalls = Number(state.playbackHistory.stalls || 0) + 1;
      writeJsonStorage(STORAGE_KEYS.playbackHistory, state.playbackHistory);
      state.recoveryLockUntil = Date.now() + 8000;
      active.allowRouteFailover = true;
      active.success = false;
      active.budgetDeadline = Date.now() + MIDPLAY_RECOVERY_BUDGET_MS;
      failCurrentAttempt('Playback stalled');
    }
  }, isMobilePlaybackDevice() ? 1000 : 500);
}

function stopStallDetector() {
  if (state.stallInterval) clearInterval(state.stallInterval);
  state.stallInterval = null;
}

function tryGapRecovery() {
  void resumeVideoSafely('gap recovery');
  try {
    if (video.buffered.length) {
      const current = video.currentTime;
      for (let i = 0; i < video.buffered.length; i += 1) {
        const start = video.buffered.start(i);
        if (start > current && start - current < 1.5) {
          video.currentTime = start + 0.05;
          break;
        }
      }
    }
  } catch (_) {}
}

function tryLiveEdgeRecovery() {
  if (state.currentItem?._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE) return;
  try {
    const livePosition = state.hls?.liveSyncPosition;
    if (Number.isFinite(livePosition) && Math.abs(livePosition - video.currentTime) > 2) {
      video.currentTime = Math.max(0, livePosition - 0.8);
    }
    state.hls?.startLoad(-1);
  } catch (_) {}

  try {
    if (state.shaka?.isLive?.()) {
      const range = state.shaka.seekRange();
      const end = Number(range?.end || 0);
      if (end > 0 && Math.abs(end - video.currentTime) > 2.5) {
        video.currentTime = Math.max(0, end - 1.2);
      }
      void resumeVideoSafely('live-edge recovery');
    }
  } catch (_) {}
}

function isQualityLocked() {
  return Date.now() < state.qualitySwitchLockUntil;
}

function lockQualitySwitch() {
  state.manualQualityChangePending = true;
  state.qualitySwitchLockUntil = Date.now() + QUALITY_LOCK_MAX_MS;
  state.recoveryLockUntil = Math.max(state.recoveryLockUntil, Date.now() + QUALITY_LOCK_MAX_MS);
  clearTimeout(state.qualityUnlockTimer);
  state.qualityUnlockTimer = setTimeout(() => {
    state.qualitySwitchLockUntil = 0;
    state.manualQualityChangePending = false;
  }, QUALITY_LOCK_MAX_MS);
}

function unlockQualitySwitchSoon() {
  clearTimeout(state.qualityUnlockTimer);
  state.qualityUnlockTimer = setTimeout(() => {
    state.qualitySwitchLockUntil = 0;
    state.manualQualityChangePending = false;
  }, 900);
}

function numericBitrate(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

function hlsLevelBitrate(level) {
  return numericBitrate(
    level?.averageBitrate ||
    level?.bitrate ||
    level?.maxBitrate ||
    level?.attrs?.AVERAGE_BANDWIDTH ||
    level?.attrs?.BANDWIDTH
  );
}

function formatBitrate(value) {
  const bitrate = numericBitrate(value);
  if (!bitrate) return '';
  if (bitrate >= 1000000) return `${(bitrate / 1000000).toFixed(bitrate >= 10000000 ? 1 : 2)} Mbps`;
  return `${Math.round(bitrate / 1000)} Kbps`;
}

function resolutionLabel(width, height) {
  const h = Number(height || 0);
  if (!h) return '';
  return `${Math.round(h)}p`;
}

function qualityLabelForHlsLevel(level, index, totalLevels) {
  const height = Number(level?.height || 0);
  if (height > 0) return `${Math.round(height)}p`;

  const attrsResolution = String(level?.attrs?.RESOLUTION || '').trim();
  const attrsMatch = attrsResolution.match(/(\d+)x(\d+)/i);
  if (attrsMatch) return `${Number(attrsMatch[2])}p`;

  const name = String(level?.name || level?.attrs?.NAME || '').trim();
  const namedHeight = name.match(/(\d{3,4})\s*p?/i);
  if (namedHeight) return `${Number(namedHeight[1])}p`;

  const measuredHeight = Number(video.videoHeight || 0);
  if (measuredHeight > 0) return `${Math.round(measuredHeight)}p`;

  const itemResolution = String(state.currentItem?.resolution || '').trim();
  const itemHeight = itemResolution.match(/(\d{3,4})\s*p?|[x×](\d{3,4})/i);
  if (itemHeight) return `${Number(itemHeight[1] || itemHeight[2])}p`;

  if (Number(totalLevels || 0) <= 1) return 'Original';
  return `Stream ${index + 1}`;
}

function currentVariantInfo() {
  if (state.hls) {
    const levels = state.hls.levels || [];
    const index = state.hls.currentLevel >= 0 ? state.hls.currentLevel : state.hls.loadLevel;
    const level = levels[index] || levels[0];
    if (level) {
      return {
        label: qualityLabelForHlsLevel(level, Math.max(0, index), levels.length),
        bitrate: hlsLevelBitrate(level) || numericBitrate(state.hls.bandwidthEstimate)
      };
    }
  }

  if (state.shaka) {
    const active = state.shaka.getVariantTracks().find((track) => track.active);
    if (active) {
      return {
        label: active.height ? `${Math.round(active.height)}p` : 'Original',
        bitrate: numericBitrate(active.bandwidth)
      };
    }
  }

  const measuredHeight = Number(video.videoHeight || 0);
  const rawResolution = String(state.currentItem?.resolution || '');
  const match = rawResolution.match(/(\d{3,4})\s*p?|[x×](\d{3,4})/i);
  const label = measuredHeight > 0
    ? `${Math.round(measuredHeight)}p`
    : match
      ? `${Number(match[1] || match[2])}p`
      : 'Original';

  return {
    label,
    bitrate: numericBitrate(
      state.currentItem?.bitrate ||
      state.currentItem?.bandwidth ||
      state.currentItem?.average_bitrate
    )
  };
}

function updateStreamInfoBadge() {
  const badge = $('streamInfoBadge');
  if (!badge) return;
  badge.style.display = 'none';
  badge.replaceChildren();
}

function sourceResolutionHeight(source = {}) {
  const explicit = Number(source.resolution_height || source.height || 0);
  if (explicit > 0) return explicit;
  const text = `${source.resolution || ''} ${source.label || ''} ${source.url || ''}`;
  if (/\b(?:4k|uhd)\b/i.test(text)) return 2160;
  const match = text.match(/(?:^|[^\d])(2160|1440|1080|720|576|540|480|360|240)\s*p?\b/i);
  return match ? Number(match[1]) : 0;
}

function sourceCodecName(source = {}) {
  const explicit = String(source.codec || '').trim().toLowerCase();
  const text = `${explicit} ${source.label || ''} ${source.resolution || ''} ${source.url || ''}`.toLowerCase();
  if (/\bav1\b/.test(text)) return 'AV1';
  if (/\b(?:hevc|h\.?265|x265)\b/.test(text)) return 'HEVC';
  if (/\b(?:avc|h\.?264|x264)\b/.test(text)) return 'H.264';
  return '';
}

function sourceAudioCodecName(source = {}) {
  const value = String(
    source.audio_codec || source.audioCodec || source.audio || ''
  ).trim().toLowerCase();
  if (!value) return '';
  if (/\b(?:aac|mp4a)\b/.test(value)) return 'AAC';
  if (/\bopus\b/.test(value)) return 'Opus';
  if (/\bvorbis\b/.test(value)) return 'Vorbis';
  if (/\b(?:mp3|mpeg audio)\b/.test(value)) return 'MP3';
  if (/\bflac\b/.test(value)) return 'FLAC';
  if (/\b(?:e-?ac-?3|eac3|ec-3|ddp|dolby digital plus)\b/.test(value)) return 'E-AC-3';
  if (/\b(?:ac-?3|ac3|dolby digital)\b/.test(value)) return 'AC-3';
  if (/\b(?:dts|truehd|mlp)\b/.test(value)) return 'DTS/TrueHD';
  return value.toUpperCase();
}

function browserAudioCompatibilityRank(source = {}) {
  const codec = sourceAudioCodecName(source);
  if (['AAC', 'Opus', 'Vorbis', 'MP3', 'FLAC'].includes(codec)) return 0;
  if (!codec) return 1;
  if (['AC-3', 'E-AC-3', 'DTS/TrueHD'].includes(codec)) return 3;
  return 2;
}

function movieQualityTitle(height) {
  const value = Number(height || 0);
  if (value >= 2160) return '4K';
  if (value >= 1080) return 'FHD';
  if (value >= 720) return 'HD';
  return value > 0 ? `${value}p` : 'Original';
}

function directMovieQualityPresentation(source = {}) {
  const height = sourceResolutionHeight(source);
  const codec = sourceCodecName(source);
  const title = movieQualityTitle(height);
  const detailParts = [];
  if (height > 0) detailParts.push(`${height}p`);
  if (codec && codec !== 'H.264') detailParts.push(codec);
  const detail = detailParts.join(' · ');
  return { title, detail, summary: detail ? `${title} ${detail}` : title };
}

function directMovieQualityLabel(source = {}) {
  return directMovieQualityPresentation(source).summary;
}

function directMovieQualityGroups(item = state.currentItem) {
  if (!isMoviePlaybackContext(item)) return [];
  const pool = Array.isArray(item?._qualitySourcePool) && item._qualitySourcePool.length
    ? item._qualitySourcePool
    : (Array.isArray(item?._sources) ? item._sources : rankSources(item || {}));
  const groups = new Map();
  pool.forEach((source, sourceIndex) => {
    const url = String(source?.url || '').trim();
    if (!url) return;
    const height = sourceResolutionHeight(source);
    const codec = sourceCodecName(source);
    const key = `direct:${height || 0}:${codec || 'default'}`;
    const normalized = {
      ...source,
      sourceIndex,
      proxy_mode: 'direct_first',
      force_proxy: false,
      proxy_required: false
    };
    const presentation = directMovieQualityPresentation(source);
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        height,
        codec,
        label: presentation.summary,
        title: presentation.title,
        detail: presentation.detail,
        bitrate: Number(source.bitrate || 0),
        sources: []
      });
    }
    groups.get(key).sources.push(normalized);
  });
  groups.forEach((group) => {
    group.sources.sort((a, b) => {
      const audioDifference = browserAudioCompatibilityRank(a) - browserAudioCompatibilityRank(b);
      if (audioDifference) return audioDifference;
      return Number(a.sourceIndex || 0) - Number(b.sourceIndex || 0);
    });
  });

  return [...groups.values()].sort((a, b) => {
    const heightDifference = Number(b.height || 0) - Number(a.height || 0);
    if (heightDifference) return heightDifference;
    const codecOrder = { '': 0, 'H.264': 0, HEVC: 1, AV1: 2 };
    return (codecOrder[a.codec] ?? 9) - (codecOrder[b.codec] ?? 9);
  });
}

function activeDirectMovieQualityGroup(item = state.currentItem) {
  const groups = directMovieQualityGroups(item);
  if (!groups.length) return null;
  const activeUrl = String(
    item?._activeSourceUrl ||
    state.playbackSession?.currentAttempt?.source?.url ||
    item?.url ||
    ''
  );
  return groups.find((group) => group.sources.some((source) => source.url === activeUrl)) ||
    groups.find((group) => group.key === item?._selectedDirectQualityKey) ||
    groups[0];
}

function qualityAudioBlockToken(itemId, qualityKey) {
  return `${String(itemId || '')}|${String(qualityKey || '')}`;
}

function isMovieQualityAudioBlocked(itemId, qualityKey) {
  return state.movie4kAudioBlockedQualityKeys.has(qualityAudioBlockToken(itemId, qualityKey));
}

function clearMovieQualityGuidance() {
  clearTimeout(state.qualityNoticeTimer);
  clearTimeout(state.qualityNoticeHideTimer);
  clearInterval(state.qualityNoticeInterval);
  state.qualityNoticeTimer = null;
  state.qualityNoticeHideTimer = null;
  state.qualityNoticeInterval = null;
  const note = $('qualityAvailabilityBadge');
  if (note) {
    note.classList.remove('show');
    note.setAttribute('aria-hidden', 'true');
  }
}

function usable4KGroups(item = state.currentItem) {
  const itemId = String(item?.id || '');
  return directMovieQualityGroups(item).filter((group) =>
    Number(group.height || 0) >= 2160 && !isMovieQualityAudioBlocked(itemId, group.key)
  );
}

function hide4KAvailabilityReminder() {
  const note = $('qualityAvailabilityBadge');
  if (!note) return;
  note.classList.remove('show');
  note.setAttribute('aria-hidden', 'true');
}

function show4KAvailabilityReminder(item) {
  if (String(state.currentItem?.id || '') !== String(item?.id || '')) return;
  if (!isMoviePlaybackContext(item) || video.paused || video.ended || document.hidden) return;
  const active = activeDirectMovieQualityGroup(item);
  if (Number(active?.height || 0) >= 2160 || !usable4KGroups(item).length) return;
  const note = $('qualityAvailabilityBadge');
  if (!note) return;
  note.classList.add('show');
  note.setAttribute('aria-hidden', 'false');
  clearTimeout(state.qualityNoticeHideTimer);
  state.qualityNoticeHideTimer = setTimeout(hide4KAvailabilityReminder, 18000);
}

function schedule4KAvailabilityNotice(item) {
  clearMovieQualityGuidance();
  if (!isMoviePlaybackContext(item) || !usable4KGroups(item).length) return;
  if (Number(activeDirectMovieQualityGroup(item)?.height || 0) >= 2160) return;
  state.qualityNoticeTimer = setTimeout(() => show4KAvailabilityReminder(item), 8000);
  state.qualityNoticeInterval = setInterval(() => show4KAvailabilityReminder(item), 5 * 60 * 1000);
}

function clearMovieAudioCompatibilityCheck() {
  clearTimeout(state.movieAudioCheckTimer);
  state.movieAudioCheckTimer = null;
}

function browserReportsMissingAudioTrack() {
  try {
    if (typeof video.mozHasAudio === 'boolean') return video.mozHasAudio === false;
  } catch (_) {}
  try {
    if (video.audioTracks && typeof video.audioTracks.length === 'number') {
      return video.audioTracks.length === 0;
    }
  } catch (_) {}
  try {
    const capture = typeof video.captureStream === 'function'
      ? video.captureStream()
      : typeof video.mozCaptureStream === 'function' ? video.mozCaptureStream() : null;
    if (capture && typeof capture.getAudioTracks === 'function') {
      const audioTracks = capture.getAudioTracks();
      // A positive track report is useful. A zero-track capture report alone is
      // not treated as final because some Chromium builds omit the track here.
      if (Number(audioTracks?.length || 0) > 0) return false;
    }
  } catch (_) {}
  return null;
}

function movieAudioSourceToken(itemId, sourceUrl) {
  return `${String(itemId || '')}|${String(sourceUrl || '')}`;
}

function isMovieAudioSourceBlocked(itemId, sourceUrl) {
  return state.movie4kAudioBlockedSourceTokens.has(movieAudioSourceToken(itemId, sourceUrl));
}

function activeDirectMovieSource(item = state.currentItem, group = activeDirectMovieQualityGroup(item)) {
  const activeUrl = String(
    item?._activeSourceUrl ||
    state.playbackSession?.currentAttempt?.source?.url ||
    item?.url ||
    ''
  );
  return group?.sources?.find((source) => String(source?.url || '') === activeUrl) || group?.sources?.[0] || null;
}


function stopMovieAudioCompanion() {
  state.movieAudioOperationId = Number(state.movieAudioOperationId || 0) + 1;
  clearInterval(state.movieAudioCompanionSyncTimer);
  clearTimeout(state.movieAudioResumeTimer);
  clearTimeout(state.fullscreenAudioSyncTimer);
  state.movieAudioCompanionSyncTimer = null;
  state.movieAudioResumeTimer = null;
  state.fullscreenAudioSyncTimer = null;
  state.movieAudioCompanionActive = false;
  state.movieAudioCompanionPrepared = false;
  state.movieAudioCompanionPreparedUrl = '';
  state.movieAudioCompanionSourceUrl = '';
  state.movieAudioCompanionPreparePromise = null;

  if (!movieAudioCompanion) return;
  try { movieAudioCompanion.pause(); } catch (_) {}
  movieAudioCompanion.onerror = null;
  movieAudioCompanion.onloadedmetadata = null;
  movieAudioCompanion.oncanplay = null;
  movieAudioCompanion.removeAttribute('src');
  try { movieAudioCompanion.load(); } catch (_) {}
}

function movieAudioCompanionSyncIntervalMs() {
  const deviceClass = effectivePerformanceClass();
  if (deviceClass === 'ultra-lite') return 600;
  return isMobilePlaybackDevice() ? 450 : 400;
}

function movieAudioLeadSeconds() {
  return isMobilePlaybackDevice() ? 0.14 : 0.08;
}

function movieAudioTargetTime() {
  return Math.max(0, Number(video.currentTime || 0) + movieAudioLeadSeconds());
}

function holdMovieAudioForVideoBuffering() {
  if (!state.movieAudioCompanionActive || !movieAudioCompanion) return;
  try { movieAudioCompanion.pause(); } catch (_) {}
}

function scheduleMovieAudioResync(delayMs = 160, force = true) {
  clearTimeout(state.movieAudioResumeTimer);
  state.movieAudioResumeTimer = setTimeout(() => {
    state.movieAudioResumeTimer = null;
    syncMovieAudioCompanion(force);
  }, Math.max(0, Number(delayMs || 0)));
}

function syncMovieAudioCompanion(force = false) {
  if ((!state.movieAudioCompanionActive && !state.movieAudioCompanionPrepared) || !movieAudioCompanion) return;

  const preparedOnly = state.movieAudioCompanionPrepared && !state.movieAudioCompanionActive;
  movieAudioCompanion.muted = preparedOnly ? true : video.muted;
  movieAudioCompanion.volume = preparedOnly ? 0 : Math.max(0, Math.min(1, Number(video.volume || 0)));

  if (!preparedOnly && (video.readyState < 3 || video.seeking)) {
    holdMovieAudioForVideoBuffering();
    return;
  }

  const baseRate = Number(video.playbackRate || 1);
  const target = movieAudioTargetTime();
  const current = Number(movieAudioCompanion.currentTime || 0);
  const drift = Number.isFinite(target) && Number.isFinite(current) ? target - current : 0;
  const hardSyncThreshold = preparedOnly ? 0.75 : (isMobilePlaybackDevice() ? 0.32 : 0.50);

  if (force || Math.abs(drift) > hardSyncThreshold) {
    try { movieAudioCompanion.currentTime = target; } catch (_) {}
    movieAudioCompanion.playbackRate = baseRate;
  } else if (!preparedOnly && Math.abs(drift) > 0.055) {
    const correction = Math.max(-0.04, Math.min(0.04, drift * 0.08));
    movieAudioCompanion.playbackRate = Math.max(0.25, baseRate + correction);
  } else {
    movieAudioCompanion.playbackRate = baseRate;
  }

  if (video.paused || video.ended) {
    if (!movieAudioCompanion.paused) {
      try { movieAudioCompanion.pause(); } catch (_) {}
    }
    return;
  }

  if (movieAudioCompanion.paused) {
    movieAudioCompanion.play().catch((error) => {
      console.info('Companion audio resume deferred:', error?.name || error?.message || error);
      if (!video.paused && !video.ended) scheduleMovieAudioResync(320, true);
    });
  }
}

function movieAudioCompanionCandidate(item, activeGroup) {
  const activeSource = activeDirectMovieSource(item, activeGroup) || {};
  const activeEdition = String(activeSource.edition || '').trim().toLowerCase();
  const activeLanguage = String(activeSource.language || '').trim().toLowerCase();
  const activeHeight = Number(activeGroup?.height || 2160);
  const candidates = [];

  directMovieQualityGroups(item).forEach((group) => {
    const groupHeight = Number(group.height || 0);
    if (groupHeight <= 0 || groupHeight >= activeHeight) return;

    (group.sources || []).forEach((source) => {
      const url = String(source?.url || '').trim();
      if (!url) return;

      const codecRank = browserAudioCompatibilityRank(source);
      const edition = String(source.edition || '').trim().toLowerCase();
      const language = String(source.language || '').trim().toLowerCase();
      let score = codecRank * 10000;

      if (activeEdition && edition && activeEdition !== edition) score += 2500;
      if (activeLanguage && language && activeLanguage !== language) score += 1500;

      score += Math.max(0, groupHeight);
      candidates.push({ source, group, score });
    });
  });

  candidates.sort((a, b) => a.score - b.score);
  return candidates[0] || null;
}

function companionAttemptUrls(source) {
  const sourceUrl = String(source?.url || '').trim();
  if (!sourceUrl) return [];

  const isHttp = sourceUrl.toLowerCase().startsWith('http://');
  const mixedContent = location.protocol === 'https:' && isHttp;
  const attempts = [];

  if (!mixedContent) attempts.push({ url: sourceUrl, route: 'direct', proxy: null });
  rankHealthyProxies(sourceUrl, true).slice(0, 2).forEach((proxy) => {
    attempts.push({
      url: buildProxyUrl(proxy, { ...source, proxy_mode: 'direct_first' }),
      route: 'proxy',
      proxy
    });
  });

  return attempts;
}

function loadMovieAudioCompanionAttempt(attempt, source, position, options = {}) {
  const operationId = Number(state.movieAudioOperationId || 0);
  const playbackId = Number(state.activeLoadId || 0);
  return new Promise((resolve, reject) => {
    if (!movieAudioCompanion) {
      reject(new Error('Audio companion element missing'));
      return;
    }

    const silent = options.silent === true;
    let settled = false;
    const startedAt = Date.now();
    const timeout = setTimeout(() => finish(false, new Error('Audio companion timeout')), 8500);

    const cleanup = () => {
      clearTimeout(timeout);
      movieAudioCompanion.onloadedmetadata = null;
      movieAudioCompanion.oncanplay = null;
      movieAudioCompanion.onerror = null;
    };

    const finish = (success, error = null) => {
      if (settled) return;
      settled = true;
      cleanup();
      if (operationId !== Number(state.movieAudioOperationId || 0) || playbackId !== Number(state.activeLoadId || 0)) {
        reject(new DOMException('Stale audio companion operation', 'AbortError'));
        return;
      }

      if (attempt.route === 'proxy') {
        markProxyResult(
          attempt.proxy,
          source.url,
          success,
          Math.max(1, Date.now() - startedAt)
        );
      }

      if (success) resolve(true);
      else reject(error || new Error('Audio companion failed'));
    };

    movieAudioCompanion.onloadedmetadata = () => {
      try { movieAudioCompanion.currentTime = Math.max(0, Number(position || 0) + movieAudioLeadSeconds()); } catch (_) {}
    };
    movieAudioCompanion.oncanplay = async () => {
      try {
        movieAudioCompanion.muted = silent ? true : video.muted;
        movieAudioCompanion.volume = silent ? 0 : Math.max(0, Math.min(1, Number(video.volume || 0)));
        movieAudioCompanion.playbackRate = Number(video.playbackRate || 1);
        try { movieAudioCompanion.currentTime = Math.max(0, Number(position || 0) + movieAudioLeadSeconds()); } catch (_) {}
        await movieAudioCompanion.play();
        finish(true);
      } catch (error) {
        finish(false, error);
      }
    };
    movieAudioCompanion.onerror = () => finish(false, new Error('Audio companion media error'));

    movieAudioCompanion.src = attempt.url;
    try { movieAudioCompanion.load(); } catch (error) { finish(false, error); }
  });
}

async function prepareMovieAudioCompanion(item, candidate) {
  stopMovieAudioCompanion();
  if (!candidate?.source?.url || String(state.currentItem?.id || '') !== String(item?.id || '')) return false;

  const position = Number(video.currentTime || 0);
  const attempts = companionAttemptUrls(candidate.source);

  for (const attempt of attempts) {
    try {
      await loadMovieAudioCompanionAttempt(attempt, candidate.source, position, { silent: true });
      if (String(state.currentItem?.id || '') !== String(item?.id || '')) {
        stopMovieAudioCompanion();
        return false;
      }

      state.movieAudioCompanionPrepared = true;
      state.movieAudioCompanionPreparedUrl = candidate.source.url;
      state.movieAudioCompanionSourceUrl = candidate.source.url;
      syncMovieAudioCompanion(true);
      state.movieAudioCompanionSyncTimer = setInterval(() => syncMovieAudioCompanion(false), movieAudioCompanionSyncIntervalMs());
      return true;
    } catch (_) {
      try {
        movieAudioCompanion.removeAttribute('src');
        movieAudioCompanion.load();
      } catch (_) {}
    }
  }

  return false;
}

function activatePreparedMovieAudioCompanion() {
  if (!state.movieAudioCompanionPrepared || !movieAudioCompanion) return false;
  state.movieAudioCompanionPrepared = false;
  state.movieAudioCompanionActive = true;
  movieAudioCompanion.muted = video.muted;
  movieAudioCompanion.volume = Math.max(0, Math.min(1, Number(video.volume || 0)));
  syncMovieAudioCompanion(true);
  return true;
}

async function startMovieAudioCompanion(item, candidate) {
  if (
    state.movieAudioCompanionPrepared &&
    state.movieAudioCompanionPreparedUrl === String(candidate?.source?.url || '') &&
    String(state.currentItem?.id || '') === String(item?.id || '')
  ) {
    return activatePreparedMovieAudioCompanion();
  }

  stopMovieAudioCompanion();
  if (!candidate?.source?.url || String(state.currentItem?.id || '') !== String(item?.id || '')) return false;

  const position = Number(video.currentTime || 0);
  const attempts = companionAttemptUrls(candidate.source);

  for (const attempt of attempts) {
    try {
      await loadMovieAudioCompanionAttempt(attempt, candidate.source, position, { silent: false });
      if (String(state.currentItem?.id || '') !== String(item?.id || '')) {
        stopMovieAudioCompanion();
        return false;
      }

      state.movieAudioCompanionActive = true;
      state.movieAudioCompanionSourceUrl = candidate.source.url;
      syncMovieAudioCompanion(true);
      state.movieAudioCompanionSyncTimer = setInterval(() => syncMovieAudioCompanion(false), movieAudioCompanionSyncIntervalMs());
          return true;
    } catch (_) {
      try {
        movieAudioCompanion.removeAttribute('src');
        movieAudioCompanion.load();
      } catch (_) {}
    }
  }

  showToast('Compatible 4K audio source পাওয়া যায়নি', 5200, 'glass');
  return false;
}

function bestMovieAudioFallback(groups, activeGroup, item = state.currentItem) {
  const itemId = String(item?.id || '');
  const activeSource = activeDirectMovieSource(item, activeGroup);
  const activeUrl = String(activeSource?.url || '');

  const sameGroupSource = (activeGroup?.sources || []).find((source) => {
    const url = String(source?.url || '');
    return url && url !== activeUrl && !isMovieAudioSourceBlocked(itemId, url);
  });
  if (sameGroupSource) {
    return {
      group: activeGroup,
      preferredSourceUrl: sameGroupSource.url,
      sameTier: true,
      audioCompanion: false
    };
  }

  const alternate4K = groups.find((group) =>
    group.key !== activeGroup?.key &&
    Number(group.height || 0) >= 2160 &&
    !isMovieQualityAudioBlocked(itemId, group.key)
  );
  if (alternate4K) {
    const preferred = alternate4K.sources.find((source) =>
      !isMovieAudioSourceBlocked(itemId, source.url)
    );
    return {
      group: alternate4K,
      preferredSourceUrl: preferred?.url || '',
      sameTier: true,
      audioCompanion: false
    };
  }

  const companion = movieAudioCompanionCandidate(item, activeGroup);
  if (companion) {
    return {
      group: activeGroup,
      preferredSourceUrl: '',
      sameTier: false,
      audioCompanion: true,
      companion
    };
  }

  return null;
}

async function fallbackFromUnsupported4KAudio(item, currentGroup) {
  const currentSource = activeDirectMovieSource(item, currentGroup);
  if (currentSource?.url) {
    state.movie4kAudioBlockedSourceTokens.add(movieAudioSourceToken(item.id, currentSource.url));
  }

  const fallback = bestMovieAudioFallback(
    directMovieQualityGroups(state.currentItem),
    currentGroup,
    state.currentItem
  );

  if (!fallback) {
    showToast('এই 4K source-এর audio codec browser-compatible নয় · 4K video চালু রাখা হয়েছে', 5200, 'glass');
    return;
  }

  if (fallback.audioCompanion) {
    await startMovieAudioCompanion(item, fallback.companion);
    return;
  }

  if (fallback.group.key !== currentGroup.key) {
    state.movie4kAudioBlockedQualityKeys.add(
      qualityAudioBlockToken(item.id, currentGroup.key)
    );
  }

  showToast(`4K sound-এর জন্য ${fallback.group.label} alternate source চেষ্টা করা হচ্ছে`, 4200, 'glass');
  await selectDirectMovieQuality(fallback.group.key, {
    audioFallback: true,
    preferredSourceUrl: fallback.preferredSourceUrl
  });
}

function ensureMovieAudioCompanionPrepared(item, candidate) {
  const candidateUrl = String(candidate?.source?.url || '');
  if (!candidateUrl || String(state.currentItem?.id || '') !== String(item?.id || '')) {
    return Promise.resolve(false);
  }

  if (
    state.movieAudioCompanionPrepared &&
    state.movieAudioCompanionPreparedUrl === candidateUrl
  ) {
    return Promise.resolve(true);
  }

  if (
    state.movieAudioCompanionActive &&
    state.movieAudioCompanionSourceUrl === candidateUrl
  ) {
    return Promise.resolve(true);
  }

  if (state.movieAudioCompanionPreparePromise) {
    return state.movieAudioCompanionPreparePromise;
  }

  const preparation = prepareMovieAudioCompanion(item, candidate);
  state.movieAudioCompanionPreparePromise = preparation;

  preparation.finally(() => {
    if (
      state.movieAudioCompanionPreparePromise === preparation &&
      !state.movieAudioCompanionPrepared &&
      !state.movieAudioCompanionActive
    ) {
      state.movieAudioCompanionPreparePromise = null;
    }
  });

  return preparation;
}

function scheduleMovieAudioCompatibilityCheck(item = state.currentItem) {
  clearMovieAudioCompatibilityCheck();
  if (!isMoviePlaybackContext(item)) return;

  const active = activeDirectMovieQualityGroup(item);
  if (Number(active?.height || 0) < 2160) {
    stopMovieAudioCompanion();
    return;
  }

  if (state.movieAudioCompanionActive) {
    syncMovieAudioCompanion(true);
    return;
  }

  const soundExpected = state.userWantsSound || (!video.muted && Number(video.volume || 0) > 0);
  if (!soundExpected) return;

  const sourceAtStart = activeDirectMovieSource(item, active);
  const sourceCodecRank = browserAudioCompatibilityRank(sourceAtStart || {});
  const companionCandidate = movieAudioCompanionCandidate(item, active);
  const preparation = companionCandidate
    ? ensureMovieAudioCompanionPrepared(item, companionCandidate)
    : Promise.resolve(false);

  /*
   * 4K companion audio is prepared silently even while autoplay is muted.
   * This removes most of the extra wait after the viewer unmutes.
   */
  if (video.muted || Number(video.volume || 0) <= 0) return;

  const startTime = Number(video.currentTime || 0);
  const supportsDecodedCounter = 'webkitAudioDecodedByteCount' in video;
  const startDecodedBytes = supportsDecodedCounter
    ? Number(video.webkitAudioDecodedByteCount || 0)
    : 0;
  const explicitMissingAtStart = browserReportsMissingAudioTrack() === true;
  const metadataWarnsUnsupported = sourceCodecRank >= 3;

  if (explicitMissingAtStart || metadataWarnsUnsupported) {
    preparation.then((prepared) => {
      if (String(state.currentItem?.id || '') !== String(item?.id || '')) return;
      if (prepared && !state.movieAudioCompanionActive) {
        activatePreparedMovieAudioCompanion();
      }
    }).catch(() => {});
    return;
  }

  state.movieAudioCheckTimer = setTimeout(async () => {
    if (String(state.currentItem?.id || '') !== String(item?.id || '')) return;

    const currentGroup = activeDirectMovieQualityGroup(state.currentItem);
    if (Number(currentGroup?.height || 0) < 2160 || video.muted || Number(video.volume || 0) <= 0) return;
    if (state.movieAudioCompanionActive) return;

    const explicitMissing = browserReportsMissingAudioTrack() === true;
    const progressed = Number(video.currentTime || 0) - startTime >= 0.65;
    const decodedBytes = supportsDecodedCounter
      ? Number(video.webkitAudioDecodedByteCount || 0)
      : 0;
    const decodedNoAudio = supportsDecodedCounter && progressed && decodedBytes <= startDecodedBytes;

    if (!explicitMissing && !decodedNoAudio) {
      stopMovieAudioCompanion();
      return;
    }

    let prepared = false;
    try { prepared = await preparation; } catch (_) {}
    if (prepared && activatePreparedMovieAudioCompanion()) return;
    fallbackFromUnsupported4KAudio(item, currentGroup).catch(() => {});
  }, 1000);
}

async function selectDirectMovieQuality(groupKey, options = {}) {
  hideAllPopups();
  hide4KAvailabilityReminder();
  const current = state.currentItem;
  const groups = directMovieQualityGroups(current);
  const selectedGroup = groups.find((group) => group.key === groupKey);
  if (!current || !selectedGroup?.sources?.length) {
    showToast('এই quality source পাওয়া যায়নি');
    return;
  }

  const originalPool = Array.isArray(current._qualitySourcePool) && current._qualitySourcePool.length
    ? current._qualitySourcePool.map((source) => ({ ...source }))
    : (current._sources || rankSources(current)).map((source) => ({ ...source }));
  const selectedUrls = new Set(selectedGroup.sources.map((source) => source.url));
  const preferredSourceUrl = String(options.preferredSourceUrl || '');
  const orderedSources = originalPool
    .filter((source) => selectedUrls.has(source.url))
    .filter((source) => !options.audioFallback || !isMovieAudioSourceBlocked(current.id, source.url) || source.url === preferredSourceUrl)
    .map((source) => ({
      ...source,
      proxy_mode: 'direct_first',
      force_proxy: false,
      proxy_required: false
    }))
    .sort((a, b) => {
      if (preferredSourceUrl) {
        if (a.url === preferredSourceUrl) return -1;
        if (b.url === preferredSourceUrl) return 1;
      }
      const audioDifference = browserAudioCompatibilityRank(a) - browserAudioCompatibilityRank(b);
      if (audioDifference) return audioDifference;
      return 0;
    })
    .slice(0, 6);
  const primary = orderedSources[0];
  if (!primary?.url) {
    showToast('এই quality source পাওয়া যায়নি');
    return;
  }

  const position = Number(video.currentTime || 0);
  if (Number.isFinite(position) && position > 0.5) {
    state.pendingQualityResume = {
      itemId: current.id,
      position,
      expiresAt: Date.now() + 30000
    };
  } else {
    state.pendingQualityResume = null;
  }

  const switchedItem = {
    ...current,
    url: primary.url,
    resolution: primary.resolution || primary.label || selectedGroup.label,
    resolution_height: sourceResolutionHeight(primary),
    label: primary.label || primary.resolution || selectedGroup.label,
    codec: primary.codec || selectedGroup.codec || '',
    stream_type: primary.stream_type || current.stream_type,
    header_profile: primary.header_profile || current.header_profile,
    proxy_mode: 'direct_first',
    force_proxy: false,
    proxy_required: false,
    inherit_manifest_query: Boolean(primary.inherit_manifest_query),
    backups: orderedSources.slice(1, 6).map((source) => ({
      ...source,
      proxy_mode: 'direct_first',
      force_proxy: false,
      proxy_required: false
    })),
    _sources: orderedSources,
    _qualitySourcePool: originalPool,
    _selectedDirectQualityKey: selectedGroup.key,
    _selectedDirectQualityLabel: selectedGroup.label,
    _activeSourceUrl: primary.url
  };

  lockQualitySwitch();
  if (!options.audioFallback) showToast(`Quality: ${selectedGroup.label}`);
  await startPlayback(switchedItem, false);
  unlockQualitySwitchSoon();
}

function applyPendingQualityResume() {
  const pending = state.pendingQualityResume;
  if (!pending) return;
  if (Date.now() > Number(pending.expiresAt || 0)) {
    state.pendingQualityResume = null;
    return;
  }
  if (String(state.currentItem?.id || '') !== String(pending.itemId || '')) return;
  const duration = Number(video.duration || 0);
  const requested = Number(pending.position || 0);
  if (!Number.isFinite(requested) || requested <= 0) {
    state.pendingQualityResume = null;
    return;
  }
  const target = duration > 1 ? Math.min(requested, Math.max(0, duration - 0.5)) : requested;
  try {
    video.currentTime = target;
    state.pendingQualityResume = null;
  } catch (_) {}
}


function buildQualityMenu(levels = null) {
  const menu = $('qualityMenu');
  menu.replaceChildren();

  const addItem = (label, bitrate, active, handler, disabled = false) => {
    const item = document.createElement('div');
    item.className = `popup-menu-item${active ? ' active' : ''}${disabled ? ' disabled' : ''}`;
    item.innerHTML = `<span>${escapeHtml(label)}</span>`;
    if (!disabled) item.addEventListener('click', handler);
    menu.appendChild(item);
  };

  if (state.hls) {
    const hlsLevels = levels || state.hls.levels || [];
    const currentInfo = currentVariantInfo();
    addItem(
      'Auto',
      currentInfo.bitrate,
      state.selectedManualQuality === -1,
      () => selectQuality(-1)
    );

    const uniqueLevels = new Map();
    hlsLevels.forEach((level, index) => {
      const label = qualityLabelForHlsLevel(level, index, hlsLevels.length);
      const height = Number(level?.height || String(label).match(/(\d{3,4})/)?.[1] || 0);
      const key = height > 0 ? `h:${height}` : `label:${label}`;
      const candidate = { level, index, label, bitrate: hlsLevelBitrate(level) };
      const existing = uniqueLevels.get(key);
      if (!existing || candidate.bitrate > existing.bitrate) uniqueLevels.set(key, candidate);
    });

    [...uniqueLevels.values()]
      .sort((a, b) => (Number(b.level.height || 0) - Number(a.level.height || 0)) || (b.bitrate - a.bitrate))
      .forEach(({ level, index, label }) => {
        addItem(
          label,
          hlsLevelBitrate(level),
          state.selectedManualQuality === index,
          () => selectQuality(index)
        );
      });

    updateStreamInfoBadge();
    return;
  }

  if (state.shaka) {
    const tracks = state.shaka.getVariantTracks().filter((track) => track.type === 'variant' && track.height);
    const activeTrack = tracks.find((track) => track.active);
    addItem(
      'Auto',
      activeTrack?.bandwidth || 0,
      state.selectedManualQuality === -1,
      () => selectQuality(-1)
    );

    const unique = new Map();
    tracks.forEach((track) => {
      const key = Number(track.height || 0);
      if (!key) return;
      const existing = unique.get(key);
      if (!existing || track.active || (!existing.active && Number(track.bandwidth || 0) < Number(existing.bandwidth || Infinity))) {
        unique.set(key, track);
      }
    });

    [...unique.values()]
      .sort((a, b) => Number(b.height || 0) - Number(a.height || 0))
      .forEach((track) => {
        addItem(
          `${Math.round(track.height)}p`,
          track.bandwidth,
          state.selectedManualQuality === track.id,
          () => selectQuality(track.id)
        );
      });

    updateStreamInfoBadge();
    return;
  }

  const directGroups = directMovieQualityGroups();
  if (directGroups.length > 1) {
    const activeUrl = String(
      state.currentItem?._activeSourceUrl ||
      state.playbackSession?.currentAttempt?.source?.url ||
      state.currentItem?.url ||
      ''
    );
    directGroups.forEach((group) => {
      const active = group.sources.some((source) => source.url === activeUrl) ||
        state.currentItem?._selectedDirectQualityKey === group.key;
      addItem(
        group.label,
        group.bitrate,
        active,
        () => selectDirectMovieQuality(group.key)
      );
    });
    updateStreamInfoBadge();
    return;
  }

  const info = currentVariantInfo();
  addItem(info.label || 'Original', info.bitrate, true, () => {}, true);
  updateStreamInfoBadge();
}

function selectQuality(value) {
  hideAllPopups();
  stopLiveAdaptiveQualityRamp(false);
  if (!isMoviePlaybackContext()) {
    state.liveStartupQualityCapHeight = 0;
    applyLiveAdaptiveQualityCap(0);
  }
  state.manualQualityChangePending = true;
  lockQualitySwitch();

  if (state.hls) {
    state.selectedManualQuality = value;
    if (value === -1) {
      state.hls.currentLevel = -1;
      state.hls.loadLevel = -1;
      state.hls.nextLevel = -1;
      if (!isMoviePlaybackContext()) applyLiveAdaptiveQualityCap(0);
      showToast('Quality: Auto');
    } else {
      state.hls.loadLevel = value;
      state.hls.nextLevel = value;
      const label = qualityLabelForHlsLevel(
        state.hls.levels[value],
        value,
        state.hls.levels.length
      );
      showToast(`Quality: ${label}`);
    }
    buildQualityMenu(state.hls.levels);
    if (video.paused) void resumeVideoSafely('quality change');
    return;
  }

  if (state.shaka) {
    if (value === -1) {
      state.selectedManualQuality = -1;
      state.shaka.configure({ abr: { enabled: true } });
      if (!isMoviePlaybackContext()) applyLiveAdaptiveQualityCap(0);
      showToast('Quality: Auto');
    } else {
      const track = state.shaka.getVariantTracks().find((item) => item.id === value);
      if (track) {
        state.selectedManualQuality = value;
        state.shaka.configure({ abr: { enabled: false } });
        state.shaka.selectVariantTrack(track, true, 4);
        showToast(`Quality: ${track.height || ''}p`);
      }
    }
    unlockQualitySwitchSoon();
    buildQualityMenu();
    return;
  }

  unlockQualitySwitchSoon();
  showToast('এই source-এ আলাদা quality নেই');
}
window.selectQualityLevel = selectQuality;

function isPhoneSizedPlayer() {
  const userAgent = String(navigator.userAgent || '');
  const tvDevice = /TV|Android TV|AFT|SmartTV|BRAVIA|MiBOX|TV Bro/i.test(userAgent);
  return !tvDevice && window.matchMedia('(max-width: 1000px)').matches;
}

function applyDefaultPlayerFit() {
  state.fitIndex = 0;
  const fit = isMoviePlaybackContext() ? 'contain' : 'cover';
  video.style.setProperty('object-fit', fit, 'important');
  video.style.setProperty('object-position', 'center center', 'important');
}

function setPlayerControlVisible(id, visible, display = 'inline-flex') {
  const control = $(id);
  if (!control) return;
  control.hidden = !visible;
  control.setAttribute('aria-hidden', visible ? 'false' : 'true');
  control.style.setProperty('display', visible ? display : 'none', 'important');
}

function updateContextualPlayerButtons() {
  const isMovie = isMoviePlaybackContext();
  const mobileFullscreen = wrapperFullscreenElement() === videoContainer && isPhoneSizedPlayer();
  document.documentElement.classList.toggle('movie-playback-context', isMovie);

  setPlayerControlVisible('skipBackBtn', isMovie);
  setPlayerControlVisible('skipFwdBtn', isMovie);
  setPlayerControlVisible('speedBtn', isMovie);
  setPlayerControlVisible('networkBtn', !isMovie);
  setPlayerControlVisible('aspectBtn', mobileFullscreen);

  if (isMovie) $('networkMenu')?.classList.remove('show');
  else $('speedMenu')?.classList.remove('show');
}

function setupPlayerUi(item) {
  const isMovie = item._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE;
  renderNetworkMenu(isMovie);
  updateNetworkMenuState(isMovie ? NETWORK_MODE.AUTO : readNetworkMode(item));
  $('liveIndicator').style.display = isMovie ? 'none' : 'inline-flex';
  $('progressContainer').classList.add('show');
  $('progressContainer').classList.toggle('live-progress', !isMovie);
  $('progressFill').style.width = '0%';
  $('currentTime').textContent = '00:00';
  $('durationTime').textContent = isMovie ? '00:00' : 'LIVE';
  $('metaWatchingCount').style.display = isMovie ? 'none' : 'inline';
  applyDefaultPlayerFit();
  updateContextualPlayerButtons();
  video.poster = isMovie ? (item.logo || '') : '';
}

function updateMetadata(item) {
  $('metaTitle').textContent = item.name;
  $('metaCategory').textContent = item.category || state.selectedCategory || '';
  $('metaWatchingCount').textContent = state.view === VIEW.EVENT
    ? (item.competition || 'Live Event')
    : 'Watching Now';

  const extras = $('movieExtraBadges');
  if (extras) {
    extras.style.display = 'none';
    extras.setAttribute('aria-hidden', 'true');
  }

  ['streamInfoBadge', 'movieRealResBadge', 'movieRatingMetaBadge'].forEach((id) => {
    const badge = $(id);
    if (badge) {
      badge.style.display = 'none';
      badge.replaceChildren();
    }
  });

  const displayIndex = state.filteredItems.findIndex((entry) => entry._uid === item._uid);
  $('osdNumber').textContent = `#${displayIndex >= 0 ? displayIndex + 1 : (item.seqNumber || 1)}`;
  $('osdName').textContent = item.name;

  const osd = $('channelOsd');
  osd.classList.remove('show');
  clearTimeout(state.osdTimer);
  void osd.offsetWidth;
  osd.classList.add('show');
  state.osdTimer = setTimeout(() => osd.classList.remove('show'), PLAYER_SELECTION_OSD_MS);
  seriesModule?.decorateMetadata?.(item);
  updateFavoriteUi();
}

function updateActiveCards() {
  qsa('[data-uid]', sidebarList).forEach((card) => {
    const active = card.dataset.uid === state.currentItem?._uid;
    card.classList.toggle('active', active);
    const existing = qs('.playing-equalizer, .movie-eq-overlay', card);
    if (existing && !active) existing.remove();
    if (active && card.classList.contains('sidebar-item') && !qs('.playing-equalizer', card)) {
      const name = qs('.sidebar-name', card);
      if (name) {
        const eq = document.createElement('span');
        eq.className = 'playing-equalizer';
        eq.innerHTML = '<span></span><span></span><span></span>';
        name.appendChild(eq);
      }
    }
    if (active && card.classList.contains('movie-card') && !qs('.movie-eq-overlay', card)) {
      const eq = document.createElement('div');
      eq.className = 'movie-eq-overlay';
      eq.innerHTML = '<div class="movie-playing-eq"><span></span><span></span><span></span><span></span><span></span><span></span></div>';
      card.appendChild(eq);
    }
  });
  seriesModule?.updateActiveCards?.();
}

function updatePlayPauseUi() {
  const paused = video.paused;
  $('playIcon').style.display = paused ? 'inline-block' : 'none';
  $('pauseIcon').style.display = paused ? 'none' : 'inline-block';
}

function updateMuteUi() {
  const muted = video.muted || video.volume === 0;
  $('muteNoticeText').style.display = muted ? 'flex' : 'none';
  $('muteBtn').classList.toggle('muted-red', muted);
  $('volumeIcon').className = muted ? 'fas fa-volume-mute' : 'fas fa-volume-up';
  $('volumeSlider').value = muted ? '0' : String(video.volume);
}

async function handleMuteButtonClick(event) {
  event?.preventDefault();
  event?.stopPropagation();

  const needsUserUnlock = state.autoplayUnlockPending || video.muted || video.volume === 0;

  if (needsUserUnlock) {
    video.muted = false;
    video.volume = Math.max(0.05, Number(state.lastNonZeroVolume || 1));
    state.autoplayUnlockPending = false;
    state.userWantsSound = true;
    localStorage.setItem('clicktv_sound_on', '1');
    updateMuteUi();

    if (state.movieAudioCompanionActive || state.movieAudioCompanionPrepared) {
      syncMovieAudioCompanion(true);
      scheduleMovieAudioResync(220, true);
    }
    if (video.paused && !state.userPaused) {
      try {
        await video.play();
      } catch (_) {
        showToast('Play button চাপুন');
      }
    }
    return;
  }

  if (video.volume > 0) state.lastNonZeroVolume = video.volume;
  video.muted = true;
  state.userWantsSound = false;
  localStorage.setItem('clicktv_sound_on', '0');
  updateMuteUi();
}

video.addEventListener('playing', handlePlaybackSuccess);
video.addEventListener('playing', updateMobilePlaybackPerformance);
video.addEventListener('playing', () => {
  state.userPaused = false;
  schedule4KAvailabilityNotice(state.currentItem);
  scheduleMovieAudioCompatibilityCheck(state.currentItem);
  updateFullscreen4KPerformanceClass();
  if (state.movieAudioCompanionActive || state.movieAudioCompanionPrepared) {
    syncMovieAudioCompanion(true);
    scheduleMovieAudioResync(220, false);
  }
});
video.addEventListener('volumechange', () => {
  if (state.movieAudioCompanionActive) syncMovieAudioCompanion(false);
  updateMuteUi();
  if (!video.muted && Number(video.volume || 0) > 0) {
    scheduleMovieAudioCompatibilityCheck(state.currentItem);
  } else {
    clearMovieAudioCompatibilityCheck();
  }
});
video.addEventListener('play', () => syncMovieAudioCompanion(false));
video.addEventListener('pause', () => syncMovieAudioCompanion(false));
video.addEventListener('seeking', () => {
  state.mediaOperationGraceUntil = Date.now() + 8000;
  holdMovieAudioForVideoBuffering();
});
video.addEventListener('seeked', () => {
  state.mediaOperationGraceUntil = Date.now() + 6000;
  scheduleMovieAudioResync(140, true);
});
video.addEventListener('ratechange', () => syncMovieAudioCompanion(false));
video.addEventListener('ended', stopMovieAudioCompanion);

video.addEventListener('loadedmetadata', () => {
  markAttemptProgress('metadata loaded');
  applyPendingQualityResume();
  updateResolutionBadge();
  probeDirectMediaInfo().catch(() => {});
});
video.addEventListener('loadeddata', () => markAttemptProgress('data loaded'));
video.addEventListener('canplay', () => markAttemptProgress('can play'));
video.addEventListener('pause', () => {
  updatePlayPauseUi();
  updateMobilePlaybackPerformance();
  hide4KAvailabilityReminder();
  clearMovieAudioCompatibilityCheck();
});
video.addEventListener('play', () => {
  updatePlayPauseUi();
  updateMobilePlaybackPerformance();
  if (!state.userPaused) {
    try { state.hls?.startLoad(-1); } catch (_) {}
  }
});
video.addEventListener('ended', () => {
  if (seriesModule?.handleEnded?.()) return;
  updateMobilePlaybackPerformance();
  clearMovieQualityGuidance();
  clearMovieAudioCompatibilityCheck();
});
video.addEventListener('waiting', () => {
  holdMovieAudioForVideoBuffering();
  const session = state.playbackSession;
  if (session?.success && !session.stallStartedAt) session.stallStartedAt = Date.now();
});
video.addEventListener('stalled', () => {
  holdMovieAudioForVideoBuffering();
  const session = state.playbackSession;
  if (session?.success && !session.stallStartedAt) session.stallStartedAt = Date.now();
});
video.addEventListener('timeupdate', () => {
  const session = state.playbackSession;
  if (session?.success && video.currentTime > session.lastTime + 0.04) {
    session.lastTime = video.currentTime;
    session.lastProgressAt = Date.now();
    session.stallStartedAt = 0;
    session.stallStep = 0;
  }
  updatePlaybackProgress();
  if (seriesModule?.isEpisodeItem(state.currentItem)) {
    seriesModule.updateProgress(state.currentItem, video.currentTime, video.duration);
  }
});

async function probeDirectMediaInfo() {
  const session = state.playbackSession;
  if (!session || session.mediaInfoProbeDone || !state.currentItem) return;
  session.mediaInfoProbeDone = true;

  const attempt = session.currentAttempt;
  if (!attempt || detectFormat(attempt.source.url, { ...session.item, ...attempt.source }) !== 'direct') return;
  if (!Number.isFinite(video.duration) || video.duration <= 0) return;
  if (numericBitrate(state.currentItem.bitrate)) return;

  let probeUrl = '';
  if (attempt.route === 'proxy' && attempt.proxy) {
    probeUrl = buildProxyUrl(attempt.proxy, { ...attempt.source, stream_type: 'media' });
  } else {
    const proxy = rankHealthyProxies(attempt.source.url)[0];
    if (proxy) probeUrl = buildProxyUrl(proxy, { ...attempt.source, stream_type: 'media' });
  }
  if (!probeUrl) return;

  try {
    const response = await fetch(probeUrl, {
      method: 'HEAD',
      cache: 'no-store',
      signal: withTimeoutSignal(undefined, 5000)
    });
    if (!response.ok) return;
    const bytes = Number(response.headers.get('content-length') || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return;
    const bitrate = Math.round((bytes * 8) / video.duration);
    if (bitrate > 0) {
      state.currentItem.bitrate = bitrate;
      if (attempt.source) attempt.source.bitrate = bitrate;
      updateStreamInfoBadge();
      buildQualityMenu();
    }
  } catch (_) {}
}

function updateResolutionBadge() {
  updateStreamInfoBadge();
}

function updatePlaybackProgress() {
  const isMovie = state.currentItem?._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE;

  if (!isMovie) {
    const session = state.playbackSession;
    const startedAt = Number(session?.playingStartedAt || 0);
    const elapsedSeconds = startedAt > 0 ? Math.max(0, (Date.now() - startedAt) / 1000) : 0;
    $('currentTime').textContent = formatTime(elapsedSeconds, Math.max(3600, elapsedSeconds));
    $('durationTime').textContent = 'LIVE';

    let bufferAhead = 0;
    try {
      if (video.buffered?.length) {
        const end = video.buffered.end(video.buffered.length - 1);
        bufferAhead = Math.max(0, end - Number(video.currentTime || 0));
      }
    } catch (_) {}
    const target = Math.max(4, networkProfile(currentNetworkMode(), false).maxBufferLength);
    $('progressFill').style.width = `${Math.max(3, Math.min(100, (bufferAhead / target) * 100))}%`;
    return;
  }

  if (!Number.isFinite(video.duration) || video.duration <= 0) return;
  const percent = (video.currentTime / video.duration) * 100;
  $('progressFill').style.width = `${Math.max(0, Math.min(100, percent))}%`;
  $('currentTime').textContent = formatTime(video.currentTime);
  $('durationTime').textContent = formatTime(video.duration);
  const now = Date.now();
  if (state.currentItem?.url && now - state.positionSavedAt >= POSITION_SAVE_INTERVAL_MS) {
    state.positionSavedAt = now;
    state.playbackPositions[state.currentItem.url] = {
      position: video.currentTime,
      duration: video.duration,
      savedAt: now
    };
    const entries = Object.entries(state.playbackPositions);
    if (entries.length > POSITION_HISTORY_LIMIT) {
      state.playbackPositions = Object.fromEntries(
        entries
          .sort((a, b) => Number(b[1]?.savedAt || 0) - Number(a[1]?.savedAt || 0))
          .slice(0, POSITION_HISTORY_LIMIT)
      );
    }
    writeJsonStorage(STORAGE_KEYS.positions, state.playbackPositions);
  }
}

function formatTime(seconds, referenceDuration = video.duration) {
  const safeSeconds = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  const reference = Number.isFinite(referenceDuration) && referenceDuration > 0 ? referenceDuration : safeSeconds;
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const secs = Math.floor(safeSeconds % 60);
  const pad = (value) => String(value).padStart(2, '0');
  return reference >= 3600
    ? `${pad(hours)}:${pad(minutes)}:${pad(secs)}`
    : `${pad(minutes)}:${pad(secs)}`;
}

$('progressWrapper').addEventListener('mousemove', (event) => {
  const isMovie = state.currentItem?._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE;
  if (!isMovie || !Number.isFinite(video.duration) || video.duration <= 0) return;
  const rect = event.currentTarget.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  $('seekPreview').textContent = formatTime(ratio * video.duration);
  $('seekPreview').style.left = `${ratio * 100}%`;
  $('seekPreview').style.display = 'block';
});
$('progressWrapper').addEventListener('mouseleave', () => { $('seekPreview').style.display = 'none'; });
$('progressWrapper').addEventListener('click', (event) => {
  if (Date.now() - Number(state.lastTouchSeekAt || 0) < 800) return;
  if (!isMoviePlaybackContext() || !Number.isFinite(video.duration) || video.duration <= 0) return;
  const targetTime = seekTimeFromPointer(event.clientX);
  commitMovieSeek(targetTime);
});

function fullscreenDrawerContextKey(mode = '') {
  if (mode) return mode;
  if (seriesModule?.detailActive || seriesModule?.isEpisodeItem?.(state.currentItem)) {
    const seriesId = seriesModule?.activeSeriesItem?.id || state.currentItem?.series_id || 'series';
    return `series:${seriesId}`;
  }
  return `${state.view}:${state.selectedMovieCategory || state.selectedCategory || state.activeFinalSub || 'all'}`;
}

function rememberFullscreenDrawerScroll() {
  const list = $('fsDrawerList');
  if (!list) return;
  const key = list.dataset.contextKey;
  if (key) state.drawerScrollPositions[key] = Number(list.scrollTop || 0);
}

function restoreFullscreenDrawerScroll(key) {
  const list = $('fsDrawerList');
  if (!list || !key) return;
  requestAnimationFrame(() => {
    list.scrollTop = Math.max(0, Number(state.drawerScrollPositions[key] || 0));
  });
}

function populateFullscreenDrawer(query = '') {
  if (seriesModule?.populateFullscreenDrawer?.(query)) return;
  const list = $('fsDrawerList');
  rememberFullscreenDrawerScroll();
  list.replaceChildren();
  list.classList.remove('series-drawer-detail', 'movie-drawer-grid', 'channel-drawer-grid');

  const normalized = String(query || '').trim().toLowerCase();
  const allMatches = state.filteredItems.filter((item) => String(item.name || '').toLowerCase().includes(normalized));
  const items = allMatches.slice(0, FULLSCREEN_DRAWER_RENDER_LIMIT);
  const movieMode = state.view === VIEW.MOVIE || state.currentItem?._sourceKind === VIEW.MOVIE;
  const contextKey = fullscreenDrawerContextKey();
  list.dataset.contextKey = contextKey;
  list.classList.add(movieMode ? 'movie-drawer-grid' : 'channel-drawer-grid');

  const fragment = document.createDocumentFragment();
  items.forEach((item, index) => {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = `fs-drawer-item tv-focusable${item._uid === state.currentItem?._uid ? ' active' : ''}`;
    row.dataset.uid = item._uid;
    row.setAttribute('aria-label', item.name);
    const meta = movieMode
      ? String(item.year || item.release_year || item.category || '').trim()
      : String(item.category || item.competition || 'Live').trim();
    row.innerHTML = `
      <span class="fs-drawer-rank">${index + 1}</span>
      <span class="fs-drawer-logo-wrap">${createImageHtml(item, '')}</span>
      <span class="fs-drawer-card-copy">
        <strong class="fs-drawer-title">${escapeHtml(item.name)}</strong>
        <small class="fs-drawer-meta">${escapeHtml(meta)}</small>
      </span>`;
    fragment.appendChild(row);
  });

  if (allMatches.length > items.length) {
    const hint = document.createElement('div');
    hint.className = 'fs-drawer-limit-note';
    hint.textContent = `প্রথম ${items.length}টি দেখানো হচ্ছে · বাকিগুলো Search করুন`;
    fragment.appendChild(hint);
  }

  list.appendChild(fragment);
  restoreFullscreenDrawerScroll(contextKey);
  state.drawerRenderedForSession = state.dataSessionId;
}

// একটি delegated listener — প্রতি row-এ আলাদা listener লাগে না
$('fsDrawerList').addEventListener('click', (event) => {
  const row = event.target.closest('.fs-drawer-item');
  if (!row) return;
  rememberFullscreenDrawerScroll();
  let item = state.filteredItems.find((entry) => entry._uid === row.dataset.uid);
  if (!item && seriesModule) item = seriesModule.episodeByUid?.(row.dataset.uid);
  if (item && seriesModule?.handleDrawerClick?.(item)) {
    // Series master/episode selections stay inside the approved Series drawer.
    showControlsTemporarily();
    return;
  }
  if (item && isPlayable(item)) startPlayback(item, true);
  $('fsDrawer').classList.remove('open');
});
$('fsDrawerList').addEventListener('error', (event) => {
  const image = event.target;
  if (image?.tagName === 'IMG') replaceBrokenImage(image);
}, true);

let popupAutoCloseTimer = null;
function togglePopupMenu(menuId) {
  const menu = $(menuId);
  const open = menu.classList.contains('show');
  hideAllPopups();
  if (!open) {
    menu.classList.add('show');
    clearTimeout(popupAutoCloseTimer);
    popupAutoCloseTimer = setTimeout(hideAllPopups, 3000);
  }
}

function hideAllPopups() {
  clearTimeout(popupAutoCloseTimer);
  popupAutoCloseTimer = null;
  ['qualityMenu', 'networkMenu', 'speedMenu'].forEach((id) => $(id)?.classList.remove('show'));
}

function setSpeed(speed) {
  const rate = Number(speed) || 1;
  video.playbackRate = rate;
  localStorage.setItem('clicktv_speed', String(rate));
  qsa('.popup-menu-item', $('speedMenu')).forEach((item) => {
    item.classList.toggle('active', Number(item.dataset.speed) === rate);
  });
  hideAllPopups();
  showToast(`Speed: ${rate}x`);
}

function focusCard(uid = state.currentItem?._uid || state.lastFocusedUid) {
  const cards = qsa('.tv-focusable', sidebarList);
  if (!cards.length) return false;
  const card = cards.find((item) => item.dataset.uid === uid) || cards[0];
  card.focus({ preventScroll: false });
  card.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  return true;
}

function restoreListFocus() {
  if (state.lastFocusedSelector !== 'card') return;
  focusCard(state.lastFocusedUid);
}

function nearestCardInDirection(current, direction) {
  const cards = qsa('.tv-focusable', sidebarList);
  const currentRect = current.getBoundingClientRect();
  const currentCenter = { x: currentRect.left + currentRect.width / 2, y: currentRect.top + currentRect.height / 2 };
  let best = null;
  let bestScore = Infinity;

  cards.forEach((card) => {
    if (card === current) return;
    const rect = card.getBoundingClientRect();
    const center = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    const dx = center.x - currentCenter.x;
    const dy = center.y - currentCenter.y;
    const valid = direction === 'left' ? dx < -2 : direction === 'right' ? dx > 2 : direction === 'up' ? dy < -2 : dy > 2;
    if (!valid) return;
    const primary = direction === 'left' || direction === 'right' ? Math.abs(dx) : Math.abs(dy);
    const secondary = direction === 'left' || direction === 'right' ? Math.abs(dy) : Math.abs(dx);
    const score = primary + secondary * 2.5;
    if (score < bestScore) {
      bestScore = score;
      best = card;
    }
  });
  return best;
}

function handleRemoteNavigation(event) {
  const key = event.key || '';
  const code = event.keyCode || event.which || 0;
  const active = document.activeElement;
  const isInput = active && ['INPUT', 'SELECT', 'TEXTAREA'].includes(active.tagName);

  const isChannelUp = key === 'ChannelUp' || key === 'MediaTrackNext' || code === 427 || code === 33;
  const isChannelDown = key === 'ChannelDown' || key === 'MediaTrackPrevious' || code === 428 || code === 34;
  const isBack = ['BrowserBack', 'GoBack', 'Backspace', 'Escape'].includes(key) || [4, 461, 10009].includes(code);

  if (isChannelUp || isChannelDown) {
    event.preventDefault();
    playRelativeItem(isChannelUp ? 1 : -1, true);
    return;
  }

  if (isBack) {
    if (isInput) {
      active.blur();
      event.preventDefault();
      return;
    }
    if ($('fsDrawer').classList.contains('open')) {
      $('fsDrawer').classList.remove('open');
      event.preventDefault();
      return;
    }
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
      event.preventDefault();
      return;
    }
    if (focusCard()) event.preventDefault();
    return;
  }

  if (isInput) return;

  const card = active?.closest?.('.tv-focusable');
  if (card && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(key)) {
    event.preventDefault();
    const direction = key.replace('Arrow', '').toLowerCase();
    let next = nearestCardInDirection(card, direction);
    if (!next && (direction === 'down' || direction === 'right') && state.renderedCount < state.filteredItems.length) {
      appendNextChunk();
      next = nearestCardInDirection(card, direction);
    }
    next?.focus();
    next?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    return;
  }

  if ((key === 'Enter' || code === 13) && card) {
    event.preventDefault();
    card.click();
    return;
  }

  if (!document.fullscreenElement && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(key)) {
    if (!card && focusCard()) event.preventDefault();
    return;
  }

  if (document.fullscreenElement) {
    const isMovie = state.currentItem?._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE;
    if (key === 'ArrowRight') {
      event.preventDefault();
      if (isMovie) video.currentTime = Math.min(video.duration || Infinity, video.currentTime + 10);
      else playRelativeItem(1, true);
    } else if (key === 'ArrowLeft') {
      event.preventDefault();
      if (isMovie) video.currentTime = Math.max(0, video.currentTime - 10);
      else playRelativeItem(-1, true);
    } else if (key === 'ArrowUp') {
      event.preventDefault();
      video.muted = false;
      video.volume = Math.min(1, video.volume + 0.1);
      updateMuteUi();
      showToast(`Volume: ${Math.round(video.volume * 100)}%`);
    } else if (key === 'ArrowDown') {
      event.preventDefault();
      video.volume = Math.max(0, video.volume - 0.1);
      video.muted = video.volume === 0;
      updateMuteUi();
      showToast(`Volume: ${Math.round(video.volume * 100)}%`);
    }
  }

  const focusedButton = active?.closest?.('button, [role="button"], .popup-menu-item');
  if ((key === ' ' && !focusedButton) || key === 'MediaPlayPause') {
    event.preventDefault();
    video.paused ? void resumeVideoSafely('remote play', true) : video.pause();
  }
}

document.addEventListener('keydown', handleRemoteNavigation);

function showControlsTemporarily() {
  playerControls.classList.remove('hide');
  $('fsDrawerToggle').classList.remove('hide');
  videoContainer.classList.remove('hide-cursor');
  clearTimeout(state.hideControlsTimer);
  if ((document.fullscreenElement || document.webkitFullscreenElement || innerWidth <= 1000) && !video.paused) {
    state.hideControlsTimer = setTimeout(() => {
      if ($('fsDrawer')?.classList.contains('open')) return;
      playerControls.classList.add('hide');
      $('fsDrawerToggle').classList.add('hide');
      videoContainer.classList.add('hide-cursor');
    }, 4000);
  }
}

videoContainer.addEventListener('mousemove', showControlsTemporarily);
videoContainer.addEventListener('touchstart', showControlsTemporarily, { passive: true });
videoContainer.addEventListener('touchmove', showControlsTemporarily, { passive: true });

function showGesture(type, value) {
  clearTimeout(state.gestureTimer);
  const indicator = type === 'volume' ? $('volIndicator') : $('brightIndicator');
  const progress = type === 'volume' ? $('volProg') : $('brightProg');
  indicator.classList.add('show');
  progress.style.height = `${value}%`;
  state.gestureTimer = setTimeout(() => indicator.classList.remove('show'), 1000);
}

videoContainer.addEventListener('touchstart', (event) => {
  const touch = event.touches[0];
  state.touchStartX = touch.clientX;
  state.touchStartY = touch.clientY;
  state.touchInitialVolume = video.volume;
  state.touchInitialBrightness = state.currentBrightness;
}, { passive: true });

videoContainer.addEventListener('touchmove', (event) => {
  if (event.target.closest('#playerControls, .fs-drawer')) return;
  const touch = event.touches[0];
  const dx = touch.clientX - state.touchStartX;
  const dy = touch.clientY - state.touchStartY;
  if (Math.abs(dy) <= Math.abs(dx) || Math.abs(dy) < 15) return;
  const rect = videoContainer.getBoundingClientRect();
  const ratio = -dy / rect.height;
  const relativeX = state.touchStartX - rect.left;

  if (relativeX > rect.width * 0.7) {
    const volume = Math.max(0, Math.min(1, state.touchInitialVolume + ratio));
    video.volume = volume;
    video.muted = volume === 0;
    updateMuteUi();
    showGesture('volume', Math.round(volume * 100));
  } else if (relativeX < rect.width * 0.3) {
    state.currentBrightness = Math.max(0.1, Math.min(1, state.touchInitialBrightness + ratio));
    $('brightOverlay').style.backgroundColor = `rgba(0,0,0,${1 - state.currentBrightness})`;
    showGesture('brightness', Math.round(state.currentBrightness * 100));
  }
}, { passive: true });

videoContainer.addEventListener('touchend', (event) => {
  if (event.target.closest('#playerControls, .fs-drawer, #fsDrawerToggle')) return;
  const touch = event.changedTouches[0];
  const dx = touch.clientX - state.touchStartX;
  const dy = touch.clientY - state.touchStartY;
  if (Math.abs(dx) > 90 && Math.abs(dy) < 40) {
    playRelativeItem(dx < 0 ? 1 : -1, true);
    return;
  }

  const now = Date.now();
  if (now - state.lastTapTime < 300) {
    const rect = videoContainer.getBoundingClientRect();
    const x = touch.clientX - rect.left;
    const isMovie = state.currentItem?._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE;
    if (isMovie && x < rect.width / 3) {
      video.currentTime = Math.max(0, video.currentTime - 10);
      showSkip('left');
    } else if (isMovie && x > rect.width * 2 / 3) {
      video.currentTime = Math.min(video.duration || Infinity, video.currentTime + 10);
      showSkip('right');
    }
  }
  state.lastTapTime = now;
});

function showSkip(side) {
  const element = side === 'left' ? $('skipLeft') : $('skipRight');
  element.classList.add('show');
  setTimeout(() => element.classList.remove('show'), 800);
}

async function cleanupLegacyServiceWorker() {
  const legacyServiceWorkerPath = '/sw.js';
  const cleanupKey = 'clicktv_legacy_sw_cleanup_v1';
  void legacyServiceWorkerPath;
  try {
    if (localStorage.getItem(cleanupKey) === '1') return;
  } catch (_) {}
  if ('serviceWorker' in navigator) {
    try {
      const registrations = await navigator.serviceWorker.getRegistrations();
      await Promise.all(registrations.map((registration) => registration.unregister()));
    } catch (error) {
      console.warn('Legacy service worker cleanup failed', error);
    }
  }
  if ('caches' in window) {
    try {
      const cacheNames = await caches.keys();
      await Promise.all(cacheNames.map((cacheName) => caches.delete(cacheName)));
    } catch (error) {
      console.warn('Legacy cache cleanup failed', error);
    }
  }
  try { localStorage.setItem(cleanupKey, '1'); } catch (_) {}
}

function setupPwaInstall() {
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    state.deferredInstallPrompt = event;
    $('pwaInstallBtn').style.display = 'inline-flex';
  });
  $('pwaInstallBtn').addEventListener('click', async () => {
    if (!state.deferredInstallPrompt) {
      showToast('Browser menu থেকে Install app নির্বাচন করুন');
      return;
    }
    state.deferredInstallPrompt.prompt();
    await state.deferredInstallPrompt.userChoice;
    state.deferredInstallPrompt = null;
  });
}

function updateClock() {
  const now = new Date();
  let hours = now.getHours();
  const minutes = String(now.getMinutes()).padStart(2, '0');
  const period = hours >= 12 ? 'PM' : 'AM';
  hours = hours % 12 || 12;
  $('clockTime').textContent = `${String(hours).padStart(2, '0')}:${minutes} ${period}`;
}

function dismissNotice() {
  $('sticky-header-notice').style.display = 'none';
  syncNoticeHeight();
  try { localStorage.setItem(STORAGE_KEYS.noticeDismissed, '1'); } catch (_) {}
}

function syncNoticeHeight() {
  const notice = $('sticky-header-notice');
  const height = notice.style.display === 'none' ? 0 : notice.offsetHeight;
  document.documentElement.style.setProperty('--notice-height', `${height}px`);
}

function restoreNoticeState() {
  if (localStorage.getItem(STORAGE_KEYS.noticeDismissed) === '1') {
    $('sticky-header-notice').style.display = 'none';
  }
  syncNoticeHeight();
}

window.addEventListener('resize', syncNoticeHeight);
window.addEventListener('orientationchange', syncNoticeHeight);

$('noticeCloseBtn').addEventListener('click', dismissNotice);

qsa('.popup-menu-item[data-speed]', $('speedMenu')).forEach((item) => {
  item.addEventListener('click', () => setSpeed(Number(item.dataset.speed)));
});
renderNetworkMenu();
$('mobileSearchClearBtn').addEventListener('click', clearMobileSearch);

$('chipPrevBtn').addEventListener('click', () => $('chipsContainerWrap').scrollBy({ left: -220, behavior: 'smooth' }));
$('chipNextBtn').addEventListener('click', () => $('chipsContainerWrap').scrollBy({ left: 220, behavior: 'smooth' }));
$('playPauseBtn').addEventListener('click', () => {
  if (video.paused) {
    state.userPaused = false;
    try { state.hls?.startLoad(-1); } catch (_) {}
    try { state.mpegts?.load(); } catch (_) {}
    void resumeVideoSafely('play button', true);
  } else {
    state.userPaused = true;
    try { state.hls?.stopLoad(); } catch (_) {}
    try { state.mpegts?.pause(); } catch (_) {}
    video.pause();
  }
});
$('nextChBtn').addEventListener('click', () => playRelativeItem(1, true));
$('prevChBtn').addEventListener('click', () => playRelativeItem(-1, true));
$('skipBackBtn').addEventListener('click', () => { video.currentTime = Math.max(0, video.currentTime - 10); showSkip('left'); });
$('skipFwdBtn').addEventListener('click', () => { video.currentTime = Math.min(video.duration || Infinity, video.currentTime + 10); showSkip('right'); });
$('muteBtn').addEventListener('click', handleMuteButtonClick);
$('volumeSlider').addEventListener('input', (event) => {
  const nextVolume = Number(event.target.value);
  video.volume = nextVolume;
  video.muted = nextVolume === 0;
  if (nextVolume > 0) {
    state.lastNonZeroVolume = nextVolume;
    state.autoplayUnlockPending = false;
  }
  updateMuteUi();
  if (!video.muted && video.paused) void resumeVideoSafely('volume unlock', true);
});
$('favActionBtn').addEventListener('click', (event) => {
  if (state.currentItem) toggleFavorite(state.currentItem._uid, event);
});
$('qualityBtn').addEventListener('click', (event) => { event.stopPropagation(); buildQualityMenu(); togglePopupMenu('qualityMenu'); });
$('networkBtn').addEventListener('click', (event) => {
  event.stopPropagation();
  if (isMoviePlaybackContext()) return;
  togglePopupMenu('networkMenu');
});
$('speedBtn').addEventListener('click', (event) => { event.stopPropagation(); togglePopupMenu('speedMenu'); });
$('aspectBtn').addEventListener('click', () => {
  const modes = ['cover', 'contain', 'fill'];
  const labels = ['Auto Fit', 'Original', 'Stretch'];
  state.fitIndex = (state.fitIndex + 1) % modes.length;
  video.style.setProperty('object-fit', modes[state.fitIndex], 'important');
  showToast(`Screen Fit: ${labels[state.fitIndex]}`, 1600, 'glass');
});
function wrapperFullscreenElement() {
  return document.fullscreenElement || document.webkitFullscreenElement || null;
}

function clearLiveFullscreenRecovery() {
  clearTimeout(state.fullscreenLiveRecoveryTimer);
  clearInterval(state.fullscreenLiveQualityGuardTimer);
  state.fullscreenLiveRecoveryTimer = null;
  state.fullscreenLiveQualityGuardTimer = null;
}

function protectLivePlaybackDuringFullscreenTransition() {
  if (isMoviePlaybackContext()) return;
  const session = state.playbackSession;
  if (!session || !session.routeAccepted || !isActiveAttempt(session, session.attemptToken)) return;

  clearLiveFullscreenRecovery();
  state.mediaOperationGraceUntil = Math.max(
    Number(state.mediaOperationGraceUntil || 0),
    Date.now() + 8000
  );
  session.success = true;
  session.allowRouteFailover = false;
  session.lastProgressAt = Date.now();
  session.stallStartedAt = 0;
  session.stallStep = 0;

  if (video.paused && !state.userPaused) {
    requestAnimationFrame(() => {
      video.play().catch((error) => {
        console.info('Fullscreen resume deferred:', error?.name || error?.message || error);
      });
    });
  }
}

function updateFullscreen4KPerformanceClass() {
  const wrapperActive = wrapperFullscreenElement() === videoContainer;
  const active4K = isMoviePlaybackContext() && Number(activeDirectMovieQualityGroup()?.height || 0) >= 2160;
  document.documentElement.classList.toggle('fullscreen-4k-playback', wrapperActive && active4K);
}

async function toggleFullscreen() {
  const fullscreenElement = wrapperFullscreenElement();
  if (!fullscreenElement) {
    state.mobileNativeFullscreen = false;
    video.controls = false;

    if (videoContainer.requestFullscreen) {
      await videoContainer.requestFullscreen().catch(() => {});
    } else if (videoContainer.webkitRequestFullscreen) {
      videoContainer.webkitRequestFullscreen();
    } else if (video.webkitEnterFullscreen) {
      // iPhone Safari fallback: native fullscreen cannot show HTML controls.
      state.mobileNativeFullscreen = true;
      video.controls = true;
      video.webkitEnterFullscreen();
    } else if (video.requestFullscreen) {
      state.mobileNativeFullscreen = true;
      video.controls = true;
      await video.requestFullscreen().catch(() => {});
    }
    screen.orientation?.lock?.('landscape').catch(() => {});
  } else if (document.exitFullscreen) {
    await document.exitFullscreen().catch(() => {});
    screen.orientation?.unlock?.();
  } else if (document.webkitExitFullscreen) {
    document.webkitExitFullscreen();
  }
}

function restoreCustomControlsAfterFullscreen() {
  const fullscreenElement = wrapperFullscreenElement();
  const wrapperActive = fullscreenElement === videoContainer;
  document.documentElement.classList.toggle('custom-player-fullscreen', wrapperActive);

  if (wrapperActive) {
    state.mobileNativeFullscreen = false;
    video.controls = false;
    showControlsTemporarily();
  } else if (!fullscreenElement && state.mobileNativeFullscreen) {
    state.mobileNativeFullscreen = false;
    video.controls = false;
    screen.orientation?.unlock?.();
  }

  if (isMoviePlaybackContext()) {
    state.mediaOperationGraceUntil = Date.now() + 6500;
  } else {
    protectLivePlaybackDuringFullscreenTransition();
  }
  updateFullscreen4KPerformanceClass();
  updateContextualPlayerButtons();

  if (state.movieAudioCompanionActive || state.movieAudioCompanionPrepared) {
    holdMovieAudioForVideoBuffering();
    clearTimeout(state.fullscreenAudioSyncTimer);
    state.fullscreenAudioSyncTimer = setTimeout(() => {
      state.fullscreenAudioSyncTimer = null;
      syncMovieAudioCompanion(true);
      scheduleMovieAudioResync(420, false);
    }, 320);
  }

  if (!wrapperActive) applyDefaultPlayerFit();
  updateMobilePlaybackPerformance();
}
document.addEventListener('fullscreenchange', restoreCustomControlsAfterFullscreen);
document.addEventListener('webkitfullscreenchange', restoreCustomControlsAfterFullscreen);
video.addEventListener('webkitendfullscreen', restoreCustomControlsAfterFullscreen);
$('fullscreenBtn').addEventListener('click', toggleFullscreen);
$('fsDrawerToggle').addEventListener('click', (event) => {
  event.stopPropagation();
  const drawer = $('fsDrawer');
  const opening = !drawer.classList.contains('open');
  drawer.classList.toggle('open', opening);
  showControlsTemporarily();
  if (opening) populateFullscreenDrawer($('fsDrawerSearch').value.trim());
});
$('fsDrawerClose').addEventListener('click', () => {
  rememberFullscreenDrawerScroll();
  $('fsDrawer').classList.remove('open');
  showControlsTemporarily();
});
$('fsDrawerSearch').addEventListener('input', (event) => populateFullscreenDrawer(event.target.value.trim()));
function clearMobileSearchAutoClose() {
  clearTimeout(state.mobileSearchHideTimer);
  state.mobileSearchHideTimer = null;
}

function closeMobileSearch(force = false) {
  if (!force && state.searchQuery) return;
  clearMobileSearchAutoClose();
  $('mobileSearchBox').style.display = 'none';
  mobileSearchInput.blur();
}

function scheduleMobileSearchAutoClose() {
  clearMobileSearchAutoClose();
  if (state.searchQuery) return;
  state.mobileSearchHideTimer = setTimeout(() => closeMobileSearch(false), MOBILE_SEARCH_AUTO_CLOSE_MS);
}

function openMobileSearch() {
  $('mobileSearchBox').style.display = 'flex';
  mobileSearchInput.focus();
  scheduleMobileSearchAutoClose();
}

$('mobileSearchToggleBtn').addEventListener('click', () => {
  const visible = getComputedStyle($('mobileSearchBox')).display !== 'none';
  if (visible) closeMobileSearch(true);
  else openMobileSearch();
});
mobileSearchInput.addEventListener('focus', scheduleMobileSearchAutoClose);
mobileSearchInput.addEventListener('input', () => {
  if (state.searchQuery) clearMobileSearchAutoClose();
  else scheduleMobileSearchAutoClose();
});
$('subscribeBtn')?.addEventListener('click', () => {
  const button = $('subscribeBtn');
  const active = localStorage.getItem('clicktv_subscribed_v1') === '1';
  localStorage.setItem('clicktv_subscribed_v1', active ? '0' : '1');
  button.innerHTML = active
    ? '<i class="fas fa-star"></i> Subscribe'
    : '<i class="fas fa-check"></i> Subscribed';
  showToast(active ? 'Subscription সরানো হয়েছে' : 'Click TV subscription চালু হয়েছে');
});

$('retryCurrentBtn').addEventListener('click', retryCurrentItem);
$('nextNowBtn').addEventListener('click', () => {
  clearAutoNextTimer();
  state.autoNextCount = 0;
  playRelativeItem(1, true);
});
$('centerPlayBtn').addEventListener('click', () => { state.userPaused = false; void resumeVideoSafely('center play', true); });
function hideResumeBadge() {
  $('resumeBadge').classList.remove('show');
  clearTimeout(state.resumeBadgeTimer);
}

function maybeOfferResume() {
  hideResumeBadge();
  const item = state.currentItem;
  const isMovie = item?._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE;
  if (!isMovie || !item?.url) return;
  const saved = state.playbackPositions[item.url];
  const duration = Number(saved?.duration || video.duration || 0);
  const position = Number(saved?.position || 0);
  if (position < 60 || (duration && position > duration - 60)) return;
  $('resumeTime').textContent = formatTime(position, duration);
  $('resumeBadge').classList.add('show');
  state.resumeBadgeTimer = setTimeout(hideResumeBadge, 12000);
}

$('resumeBadge').addEventListener('click', () => {
  const saved = state.currentItem && state.playbackPositions[state.currentItem.url];
  if (saved?.position) video.currentTime = saved.position;
  hideResumeBadge();
});

video.addEventListener('loadedmetadata', maybeOfferResume);

video.addEventListener('pause', () => {
  const isMovie = state.currentItem?._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE;
  $('centerPlayBtn').style.display = isMovie && video.paused ? 'flex' : 'none';
});
video.addEventListener('play', () => { $('centerPlayBtn').style.display = 'none'; });

function seekTimeFromPointer(clientX) {
  if (!Number.isFinite(video.duration) || video.duration <= 0) return null;
  const rect = $('progressWrapper').getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(1, rect.width)));
  return ratio * video.duration;
}

function updateTouchSeekPreview(clientX) {
  const targetTime = seekTimeFromPointer(clientX);
  if (!Number.isFinite(targetTime)) return;
  state.seekPendingTime = targetTime;
  const ratio = Math.max(0, Math.min(1, targetTime / Math.max(1, Number(video.duration || 1))));
  $('progressFill').style.width = `${ratio * 100}%`;
  const preview = $('seekPreview');
  preview.textContent = formatTime(targetTime, video.duration);
  preview.style.display = 'block';
  preview.style.left = `${ratio * 100}%`;
}

function commitMovieSeek(targetTime) {
  if (!isMoviePlaybackContext() || !Number.isFinite(targetTime)) return;
  state.mediaOperationGraceUntil = Date.now() + 8000;
  state.seekWasPlaying = !video.paused;
  holdMovieAudioForVideoBuffering();
  try { video.currentTime = Math.max(0, Math.min(video.duration || targetTime, targetTime)); } catch (_) {}
  if (state.seekWasPlaying) void resumeVideoSafely('seek resume');
}

$('progressWrapper').addEventListener('touchstart', (event) => {
  if (!isMoviePlaybackContext()) return;
  event.preventDefault();
  state.seekPointerActive = true;
  updateTouchSeekPreview(event.touches[0].clientX);
}, { passive: false });
$('progressWrapper').addEventListener('touchmove', (event) => {
  if (!state.seekPointerActive || !isMoviePlaybackContext()) return;
  event.preventDefault();
  updateTouchSeekPreview(event.touches[0].clientX);
}, { passive: false });
$('progressWrapper').addEventListener('touchend', (event) => {
  if (!state.seekPointerActive || !isMoviePlaybackContext()) return;
  event.preventDefault();
  state.seekPointerActive = false;
  state.lastTouchSeekAt = Date.now();
  const targetTime = Number(state.seekPendingTime);
  state.seekPendingTime = null;
  $('seekPreview').style.display = 'none';
  commitMovieSeek(targetTime);
}, { passive: false });
$('progressWrapper').addEventListener('touchcancel', () => {
  state.seekPointerActive = false;
  state.seekPendingTime = null;
  $('seekPreview').style.display = 'none';
}, { passive: true });

function isPhoneBrowserLayout() {
  const userAgentDataMobile = navigator.userAgentData && typeof navigator.userAgentData.mobile === 'boolean'
    ? navigator.userAgentData.mobile
    : null;
  const mobileUserAgent = userAgentDataMobile !== null
    ? userAgentDataMobile
    : /Mobi|Android.+Mobile|iPhone|iPod/i.test(String(navigator.userAgent || ''));
  return Boolean(mobileUserAgent) && window.matchMedia('(max-width: 1000px)').matches;
}

function updateMobileZoomLockClass() {
  document.documentElement.classList.toggle('mobile-zoom-locked', isPhoneBrowserLayout());
}

function setupMobileZoomGuard() {
  updateMobileZoomLockClass();
  window.addEventListener('resize', updateMobileZoomLockClass, { passive: true });
  window.addEventListener('orientationchange', updateMobileZoomLockClass, { passive: true });

  document.addEventListener('gesturestart', (event) => {
    if (isPhoneBrowserLayout()) event.preventDefault();
  }, { passive: false });

  document.addEventListener('touchstart', (event) => {
    if (isPhoneBrowserLayout() && event.touches.length > 1) event.preventDefault();
  }, { passive: false });

  document.addEventListener('touchmove', (event) => {
    if (isPhoneBrowserLayout() && event.touches.length > 1) event.preventDefault();
  }, { passive: false });
}

window.addEventListener('error', (event) => {
  console.error('Runtime error:', event.message, event.filename, event.lineno);
});
window.addEventListener('unhandledrejection', (event) => {
  console.error('Unhandled promise rejection:', event.reason);
});

document.addEventListener('click', (event) => {
  if (!event.target.closest('.popup-menu, .ctrl-btn')) hideAllPopups();
});
document.addEventListener('touchstart', (event) => {
  if (!event.target.closest('.popup-menu, .ctrl-btn')) hideAllPopups();
}, { passive: true });

qsa('.ctrl-btn').forEach((button) => {
  button.addEventListener('click', () => {
    button.classList.add('active-click');
    setTimeout(() => button.classList.remove('active-click'), 500);
  });
});

window.addEventListener('beforeunload', () => {
  clearAutoNextTimer();
  stopStallDetector();
});

function restorePlayerPreferences() {
  const savedSpeed = Number(localStorage.getItem('clicktv_speed'));
  if (savedSpeed > 0) setSpeed(savedSpeed);
  hideAllPopups();
}

function initializeSeriesModule() {
  if (!seriesModule) return;
  seriesModule.init({
    state,
    VIEW,
    STORAGE_KEYS,
    movieOrder: MOVIE_ORDER,
    fetchJson,
    normalizeItem,
    escapeHtml,
    sidebarList,
    videoContainer,
    fsDrawerList: $('fsDrawerList'),
    showToast,
    showListMessage,
    setSidebarCount,
    scrollSidebarToTop,
    getSidebarScrollTop,
    restoreSidebarScroll,
    setSeriesDetailMode,
    renderCurrentList,
    startPlayback,
    updateFavoriteUi,
    rememberFullscreenDrawerScroll,
    restoreFullscreenDrawerScroll,
    fullscreenDrawerContextKey,
    populateDefaultFullscreenDrawer: (query = '') => {
      const temporarily = window.ClickTvSeries;
      if (temporarily?.detailActive) temporarily.resetDetail({ preservePlaybackContext: true });
      populateFullscreenDrawer(query);
    }
  });
}

async function bootstrap() {
  setupFinalNavigationControls();
  initializeSeriesModule();
  setupMobileZoomGuard();
  updatePerformanceClasses();
  restoreNoticeState();
  restorePlayerPreferences();
  updateMuteUi();
  updateClock();
  setInterval(updateClock, effectivePerformanceClass() === 'normal' ? 1000 : 30000);
  await cleanupLegacyServiceWorker();
  setupPwaInstall();
  renderNetworkMenu();
  updateNetworkMenuState(readNetworkMode());
  try {
    await loadRuntimeAndManifest();
  } catch (error) {
    console.error(error);
    showPlayerMessage('Data manifest load হয়নি। Refresh করে আবার চেষ্টা করুন।', false);
    showListMessage('Click TV data load করা যায়নি', 'fa-exclamation-triangle');
    setSidebarCount('0 Items');
  }
}

bootstrap();

  