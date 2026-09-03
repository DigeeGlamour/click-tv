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
  routePreferences: 'clicktv_route_preferences_v1',
  channelSelection: 'clicktv_channel_selection_v1',
  playbackHistory: 'clicktv_playback_history_v1',
  recentItems: 'clicktv_recent_items_v1',
  favorites: 'clicktv_favorites_v1',
  positions: 'clicktv_positions_v1',
  lastView: 'clicktv_last_view_v1',
  favoriteItems: 'clicktv_favorite_items_v1',
  noticeDismissed: 'clicktv_notice_dismissed_v1',
  liteMode: 'clicktv_lite_mode',
  maxHeight: 'clicktv_max_height',
  telemetrySession: 'clicktv_telemetry_session_v1',
  eventReminders: 'clicktv_event_reminders_v1',
  eventRemindersFired: 'clicktv_event_reminders_fired_v1'
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
const CHANNEL_ATTEMPT_BUDGET_MS = 14000;
// A protected channel spends part of its budget before any media moves: a
// /drm?id= round-trip for the ClearKey keys, a /hls?id= proxy lookup, then CDM
// configuration and an encrypted init segment. Measured on the deployed site,
// 14 s was not enough for one attempt of that chain, let alone the two the
// plan holds - Star Jalsha exhausted its plan with readyState 0, no error and
// no segment ever requested. This is the session-wide cap; markAttemptProgress
// holds the per-attempt one.
const PROTECTED_CHANNEL_ATTEMPT_BUDGET_MS = 40000;
const MOVIE_ATTEMPT_BUDGET_MS = 110000;
const EVENT_ATTEMPT_BUDGET_MS = 26000;
const MIDPLAY_RECOVERY_BUDGET_MS = 16000;
const QUALITY_LOCK_MAX_MS = 6500;
const AUTO_NEXT_LIMIT = 3;
const AUTO_NEXT_SECONDS = 5;
const MPEGTS_CDN = 'https://cdn.jsdelivr.net/npm/mpegts.js@1.7.3/dist/mpegts.min.js';
const SHAKA_CDN = 'https://cdnjs.cloudflare.com/ajax/libs/shaka-player/4.10.9/shaka-player.compiled.js';
const DATA_FETCH_TIMEOUT_MS = 9000;
const EVENT_CATALOG_REFRESH_MS = 60000;
const POSITION_SAVE_INTERVAL_MS = 10000;
const POSITION_HISTORY_LIMIT = 200;
const MOVIE_PROMPT_TEXT = 'মুভি দেখতে একটি বিভাগ নির্বাচন করুন';
const MOVIE_PREVIEW_LIMIT = 18;
const MOBILE_SEARCH_AUTO_CLOSE_MS = 5000;
const LIVE_FAST_START_RAMP_MS = 6000;
const LIVE_CHANNEL_STALL_FAILOVER_MS = 8000;
const LIVE_EVENT_STALL_FAILOVER_MS = 11000;
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
  telemetryEnabled: false,
  manifestVersion: '',
  view: VIEW.CHANNEL,
  selectedCategory: null,
  selectedMovieCategory: null,
  activeMainGroup: 'sports',
  activeFinalSub: 'today-match',
  currentItems: [],
  filteredItems: [],
  renderedCount: 0,
  renderedUids: new Set(),
  currentSortMode: 'default',
  seriesDetailMode: false,
  dataSessionId: 0,
  dataAbortController: null,
  currentDataPath: '',
  eventCatalogRefreshActive: false,
  // Smart Filter. Purely a view over the final merged cards: it never reaches
  // the scanner, never refetches and never touches playback.
  eventSportFilter: 'all',
  // Requirement 7: the active playback session, pinned against catalogue churn.
  pinnedSession: null,
  movieIndex: null,
  moviePageCursor: 0,
  moviePageLoading: false,
  movieSearchLoading: false,
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
  drawerRenderedItems: new Map(),
  drawerGlobalCatalog: null,
  drawerGlobalCatalogPromise: null,
  drawerSearchRequestId: 0,
  drawerSearchDebounceTimer: null,
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
  movieControlsLocked: false,
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
  routePreferences: readJsonStorage(STORAGE_KEYS.routePreferences, {}),
  // Section 13. The channel a viewer picked, per event. Separate from the
  // scanner's default on purpose: a background scan may re-rank the channels
  // all it likes, and a viewer's choice still wins.
  channelSelection: readJsonStorage(STORAGE_KEYS.channelSelection, {}),
  embedSession: null,
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
  }),
  // What the player actually decided at runtime, for the stream-info panel and
  // for a test to be able to read instead of guessing.
  playbackDiagnostics: {}
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
const desktopSearchWrap = qs('.desktop-search');
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
  const requestUrl = new URL(withVersion(path));
  if (options.fresh === true) requestUrl.searchParams.set('_refresh', String(Date.now()));
  const response = await fetch(requestUrl.toString(), {
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

function setSidebarCount(text, detail = '') {
  const count = $('sidebarCountText');
  if (count) count.textContent = text;
  // The breakdown line lives beside the count and is hidden with the whole
  // meta bar below 1000px, so it never competes for room on a phone.
  const sub = $('sidebarCountDetail');
  if (sub) {
    sub.textContent = detail;
    sub.style.display = detail ? '' : 'none';
  }
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

// How long past its own kickoff a fixture may still be shown under Upcoming.
// Mirrors events.upcoming_past_grace_minutes in config/settings.json: the
// scanner's -20 minute trigger runs every few minutes, and a feed that
// publishes a link just after the whistle should still be caught.
const UPCOMING_PAST_GRACE_MS = 10 * 60 * 1000;

function hasAlreadyKickedOff(item) {
  // The scanner drops these on its next pass, and this is the same rule read
  // from the same clock - but the scanner cannot run when GitHub does not
  // schedule it, and it frequently does not. Measured on 2026-08-30 the gaps
  // between runs were 4, 11, 29, 33, 37, 56, 62, 89, 209 and 246 minutes
  // against crons asking for one every five, and in one of those gaps a 15:30
  // match was still sitting on Upcoming at 16:14 badged LINK UPDATING.
  //
  // A published file is a snapshot of when it was written. The browser knows
  // what time it is now, so it can tell that a fixture has started whatever the
  // file says, and a viewer should never meet a match on the Upcoming tab that
  // kicked off three quarters of an hour ago.
  //
  // Deliberately narrow: it needs a real start time and only ever removes a
  // fixture from the UPCOMING list. A fixture with no clock, or one already
  // live with a link, is Today Match's business and is untouched here.
  const raw = item?.start_time || item?.start_at || '';
  if (!raw) return false;
  const start = Date.parse(String(raw));
  if (!Number.isFinite(start)) return false;
  return Date.now() - start > UPCOMING_PAST_GRACE_MS;
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
    const seenSources = new Set();

    [...(winner._sources || []), ...(loser._sources || [])].forEach((source) => {
      const url = String(source?.url || '').trim();
      const playbackId = String(source?.playback_id || '').trim();
      const sourceKey = playbackId || url;
      if (!sourceKey || seenSources.has(sourceKey) || mergedSources.length >= 6) return;
      seenSources.add(sourceKey);
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
  if (item.playback_id) return true;
  if (item.url || item.link || item.stream_url) return true;
  if (Array.isArray(item._sources) && item._sources.some((source) => source?.url || source?.playback_id)) return true;
  if (Array.isArray(item.backups) && item.backups.some((source) => {
    if (typeof source === 'string') return Boolean(source.trim());
    return Boolean(source?.url || source?.link || source?.stream_url || source?.playback_id);
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
  const playbackId = String(source.playback_id || '').trim();
  if (!url && !playbackId) return null;
  const profile = inferSafeHeaderProfile(source) || inferSafeHeaderProfile(parent);
  const proxyMode = inferProxyMode(source, profile || inferSafeHeaderProfile(parent));
  return {
    url,
    playback_id: playbackId,
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
    protected_source: Boolean(source.protected_source),
    requires_credentials: Boolean(source.requires_credentials),
    requires_headers: Boolean(source.requires_headers),
    drm: source.drm || null,
    inherit_manifest_query: shouldInheritManifestQuery(source) || shouldInheritManifestQuery(parent)
  };
}

function rankSources(raw) {
  const primaryUrl = raw.url || raw.stream_url || raw.link || '';
  const primaryPlaybackId = String(raw.playback_id || '').trim();
  const primaryProfile = inferSafeHeaderProfile(raw);
  const primaryProxyMode = inferProxyMode(raw, primaryProfile);
  const primary = (primaryUrl || primaryPlaybackId) ? [{
    url: primaryUrl,
    playback_id: primaryPlaybackId,
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
    source_id: raw.source_id || '',
    protected_source: Boolean(raw.protected_source),
    requires_credentials: Boolean(raw.requires_credentials),
    requires_headers: Boolean(raw.requires_headers),
    drm: raw.drm || null,
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
    const key = String(source.playback_id || source.url || '').trim();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 6);

  return all
    .map((source, originalIndex) => ({ ...source, originalIndex }))
    .sort((a, b) => {
      const aHttps = a.playback_id || a.url.toLowerCase().startsWith('https://') ? 0 : 1;
      const bHttps = b.playback_id || b.url.toLowerCase().startsWith('https://') ? 0 : 1;
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
    // Smart Filter guide 9 and 10: the final card carries one canonical sport
    // field, resolved from the source's own category first, then the
    // competition, then the name. Unknown stays "other" — nothing is invented.
    sport_type: sourceKind === VIEW.EVENT || sourceKind === VIEW.UPCOMING
      ? eventSportType({ ...safeRaw, name })
      : '',
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
      // Upcoming schedules are useful before a stream URL exists. Keep the
      // published metadata card, then open its details preview on selection -
      // but only while it is still upcoming.
      if (sourceKind === VIEW.UPCOMING) {
        return Boolean(String(item.name || '').trim()) && !hasAlreadyKickedOff(item);
      }
      // Today Match keeps a fixture whose stream has not resolved yet. Thirty
      // minutes before kickoff the scanner moves the match here and publishes
      // it as metadata_only until it finds a link - and this filter used to
      // drop exactly those cards. The match had already left Upcoming, so it
      // appeared on neither tab and a viewer looking for a game about to start
      // could not find it anywhere.
      //
      // The renderer already handles a card with no channels: it carries the
      // event-card-no-channels class and shows the kickoff without a play
      // button, which is the honest thing to show while the hunt is on.
      if (sourceKind === VIEW.EVENT) {
        if (!String(item.name || '').trim()) return false;
        return isPlayable(item) || item.metadata_only === true;
      }
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
  renderDataFreshness();
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

const FINAL_MAIN_ICONS = Object.freeze({
  sports: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="13" cy="4.5" r="2"/><path d="m10.5 8.2 3.8 2.1 2.4-2.1M10.5 8.2 8 12l-3 .8M12.6 10.5l-1.4 4.2-3.6 4.1M11.2 14.7l4.3 1.6 2.4 3.1"/><circle cx="19" cy="17.5" r="2.2"/></svg>',
  'live-tv': '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="6.5" width="17" height="12" rx="2"/><path d="m8 3 4 3.5L16 3M9 21h6"/></svg>',
  movies: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="8" width="17" height="12" rx="2"/><path d="M3.5 8 5 4l4 4 2-5 4 4 2-5 3.5 3v3M8 13h8"/></svg>',
  drama: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 5.5h8v6.2c0 3-1.9 5-4 6.3-2.1-1.3-4-3.3-4-6.3z"/><path d="M12.5 6.5h8v6.2c0 3-1.9 5-4 6.3-1.2-.7-2.3-1.7-3-2.9"/><path d="M6 9.5h.01M9 9.5h.01M6 13c.8.7 1.8 1 2.7 1M15 10h.01M18 10h.01M15 14c.8-.7 1.8-1 2.7-1"/></svg>',
  favorites: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-2.9-5.6 2.9 1.1-6.2L3 9.6l6.2-.9z"/></svg>'
});

function finalButton(label, className, active, handler, key, withIcon = false) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `${className}${active ? ' active' : ''}`;
  if (withIcon && FINAL_MAIN_ICONS[key]) {
    button.innerHTML = `<span class="final-main-icon">${FINAL_MAIN_ICONS[key]}</span><span class="final-main-label">${escapeHtml(label)}</span>`;
  } else {
    button.textContent = label;
  }
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
        key,
        root === mobileMainNav
      ));
    });
  });
}

function renderFinalSubNavigation() {
  const items = finalSubItems();
  mobileSubNavigation?.classList.toggle('sports-subnav', state.activeMainGroup === 'sports');
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
  const categoryTitle = $('desktopCategoryTitle');
  if (categoryTitle) {
    categoryTitle.textContent = FINAL_MAIN_GROUPS.find(([key]) => key === state.activeMainGroup)?.[1] || 'Click TV';
  }
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
  state.renderedUids.clear();
  state.movieIndex = null;
  state.moviePageCursor = 0;
  state.moviePreviewMode = false;
  cancelPendingImages(sidebarList);
  sidebarList.replaceChildren();
  sidebarList.classList.remove('movie-grid', 'upcoming-grid', 'series-detail-list');
  state.drawerRenderedForSession = -1;
}

async function selectMainView(view, category, options = {}) {
  closeEventPreview();
  // Smart Filter guide 5: every section change starts from All Events, in
  // both directions between Today Match and Upcoming.
  closeEventSportFilter();
  state.eventSportFilter = 'all';
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
  state.currentDataPath = path || '';
  // Remembered so a viewer who lives on Upcoming is not put back on Today
  // Match every time they open the site.
  try {
    localStorage.setItem(STORAGE_KEYS.lastView, JSON.stringify({ kind, label }));
  } catch (error) { /* private mode, or storage full - not worth failing over */ }
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
    state.lastDataLoadedAt = Date.now();
    renderCurrentList(true);
    hidePlayerMessage();

    // The first match used to start playing on its own the moment the page
    // loaded. Nobody asked for it: it spends the viewer's data, talks over
    // whatever they were listening to, and picks the match for them. The card
    // is selected so the player shows what it would play, and the viewer
    // presses it.
    if (options.initial && !state.currentItem && state.currentItems.length && kind !== VIEW.UPCOMING) {
      const firstPlayable = state.currentItems.find(isPlayable);
      if (firstPlayable) selectWithoutPlaying(firstPlayable);
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
      // A new page re-sorts the whole list by year, so the already rendered
      // rows are rebuilt in the new order instead of appended to a stale DOM.
      const visibleTarget = state.renderedCount + MOVIE_CHUNK_SIZE;
      renderCurrentList(true, { preserveScroll: true, initialLimit: visibleTarget });
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

async function preloadAllMoviePagesForSearch() {
  if (
    state.view !== VIEW.MOVIE || state.moviePreviewMode || !state.movieIndex ||
    state.movieSearchLoading || state.moviePageCursor >= (state.movieIndex.pages?.length || 0)
  ) return;

  const sessionId = state.movieCategorySessionId;
  const dataSessionId = state.dataSessionId;
  state.movieSearchLoading = true;
  try {
    while (
      state.searchQuery &&
      sessionId === state.movieCategorySessionId &&
      dataSessionId === state.dataSessionId &&
      state.moviePageCursor < (state.movieIndex.pages?.length || 0)
    ) {
      const previousCursor = state.moviePageCursor;
      await loadNextMoviePage({
        sessionId,
        dataSessionId,
        signal: state.dataAbortController?.signal,
        deferRender: true
      });
      if (state.moviePageCursor === previousCursor) break;
    }
  } finally {
    state.movieSearchLoading = false;
  }
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
    playback_id: item.playback_id || '',
    protected_source: Boolean(item.protected_source),
    requires_credentials: Boolean(item.requires_credentials),
    proxy_mode: item.proxy_mode || '',
    stream_type: item.stream_type || '',
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
  const next = [compact, ...old.filter((entry) => (
    (entry.playback_id || entry.url) !== (compact.playback_id || compact.url) && entry.id !== compact.id
  ))].slice(0, 15);
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

// Requirement: "Recently Added" is the movie default, so the ordering needs a
// value for when a film arrived. The scanner writes first_seen_at from a store
// kept outside the cards; a card rebuilt by a scan cannot know its own age.
//
// Bucketed to whole days on purpose, matching the server-side sort key. Exact
// timestamps would scatter one scan's intake by milliseconds and bury the
// manual pinning that decides order within it.
function movieAddedDay(item) {
  const stamp = String(item?.first_seen_at || '').trim();
  if (!stamp) return 0;
  const parsed = Date.parse(stamp);
  if (!Number.isFinite(parsed)) return 0;
  return Math.floor(parsed / 86400000);
}

function movieIsNew(item) {
  if (item?.is_new === true) return true;
  if (item?.is_new === false) return false;
  const day = movieAddedDay(item);
  if (!day) return false;
  return (Math.floor(Date.now() / 86400000) - day) <= 7;
}

function applyFilterAndSort() {
  const query = currentSearchValue();
  state.currentQuery = query;
  let items = state.currentItems.slice();

  if (state.view === VIEW.UPCOMING || state.view === VIEW.EVENT) {
    items = items.filter((item) => !isEventEnded(item));
    // Smart Filter guide 8 and 24: the last stage of the chain, and it only
    // hides. The sort below is untouched, so the surviving cards keep exactly
    // the order they had under All Events.
    if (state.eventSportFilter !== 'all') {
      items = items.filter((item) => itemSportType(item) === state.eventSportFilter);
    }
  }

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
  } else if (state.view === VIEW.UPCOMING || state.view === VIEW.EVENT) {
    const statusRank = { LIVE_NOW: 0, CHANNEL_LIVE: 1, STARTING_SOON: 2, LINK_UPDATING: 3, UPCOMING: 4, TIME_UNVERIFIED: 5 };
    // Requirement 11. Cricket first, Football second, every other sport after
    // - then the existing status and kickoff order inside each sport.
    const sportRank = (item) => {
      const sport = itemSportType(item);
      return sport === 'cricket' ? 0 : sport === 'football' ? 1 : 2;
    };
    // Sport ordering is an audience preference and it belongs on Today Match,
    // where everything is on now or within half an hour and cricket is what
    // this audience opens the site for.
    //
    // On Upcoming it is simply wrong. Sport ranked above the clock, so a
    // football match kicking off in five minutes sorted below every cricket
    // fixture in the list including tomorrow's - and the one question that tab
    // answers is what is on next.
    // And on Today Match it belongs *inside* the status, not above it. Ranked
    // above, a football match already being played sorted below every cricket
    // fixture that had not started - the tab put a preference ahead of the one
    // thing that is actually happening. So urgency decides first and the sport
    // preference decides between matches in the same state, which is where a
    // preference belongs.
    const sportOrdersWithinStatus = state.view !== VIEW.UPCOMING;
    items.sort((a, b) => {
      const statusDifference = (statusRank[eventUiStatus(a)] ?? 9) - (statusRank[eventUiStatus(b)] ?? 9);
      if (statusDifference) return statusDifference;
      if (sportOrdersWithinStatus) {
        const sportDifference = sportRank(a) - sportRank(b);
        if (sportDifference) return sportDifference;
      }
      const aTime = eventStartDate(a)?.getTime() ?? Number.MAX_SAFE_INTEGER;
      const bTime = eventStartDate(b)?.getTime() ?? Number.MAX_SAFE_INTEGER;
      if (aTime !== bTime) return aTime - bTime;
      // On Upcoming the sport preference is asked last of all, only once the
      // clock has had its say, so it can never put tomorrow's cricket above a
      // football match kicking off in five minutes.
      if (!sportOrdersWithinStatus) {
        const sportDifference = sportRank(a) - sportRank(b);
        if (sportDifference) return sportDifference;
      }
      return (a.seqNumber || 0) - (b.seqNumber || 0);
    });
  } else if (state.view === VIEW.MOVIE) {
    items.sort((a, b) => {
      // Recently Added first. Year alone left the catalogue looking frozen:
      // discovered movies carried no year at all, so every one of them tied at
      // 0 and fell through to server order, which was alphabetical - a 2026
      // release sat on page 5 behind "100 percent Love (2012)".
      const addedDifference = movieAddedDay(b) - movieAddedDay(a);
      if (addedDifference) return addedDifference;
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


// Guide 23. The headline count answers "how many", the breakdown answers "of
// what" - and the breakdown is desktop-only so it never crowds a phone. Kept
// as its own function because requirement 8's diff path needs it too.
/* The two tab headers, exactly as the design writes them: the title on the
 * left and the count on the right, in Bengali digits.
 *
 *     আজকের ম্যাচ            ৭টি ম্যাচ · ৫টি লাইভ
 *     পরবর্তী ম্যাচ          ১১৯টি ম্যাচ
 *
 * The numbers come from the live data, so they move; the wording and the
 * digits do not become English when they do.
 */
function setEventListCount() {
  const events = state.filteredItems;
  if (state.view === VIEW.EVENT) {
    const live = events.filter((entry) => {
      const status = eventUiStatus(entry);
      return status === 'LIVE_NOW' || status === 'CHANNEL_LIVE';
    }).length;
    const detail = [
      `${toBanglaDigits(events.length)}টি ম্যাচ`,
      live ? `${toBanglaDigits(live)}টি লাইভ` : ''
    ].filter(Boolean).join(' · ');
    setSidebarCount('আজকের ম্যাচ', events.length ? detail : '');
    return;
  }
  setSidebarCount(
    'পরবর্তী ম্যাচ',
    events.length ? `${toBanglaDigits(events.length)}টি ম্যাচ` : ''
  );
}

/* Today Match is one grid with 1px rows; each card spans the rows its own
 * content needs, so a short card leaves no dead space and the next card packs
 * upward. Two fixed columns could not do that - a card could only ever sit
 * under the card above it, however tall that one happened to be.
 */
function ensureTodayGrid() {
  sidebarList.classList.add('today-grid');
  return sidebarList;
}

function layoutTodayMasonry() {
  if (state.view !== VIEW.EVENT) return;
  const grid = sidebarList;
  if (!grid || !grid.classList.contains('today-grid')) return;

  const style = getComputedStyle(grid);
  const rowGap = parseFloat(style.rowGap) || 0;
  const rowHeight = parseFloat(style.gridAutoRows) || 1;
  const cards = qsa(':scope > .poster-card', grid);

  // Measured in place. There used to be a pass here that set every card back
  // to `grid-row-end:auto` before measuring, and it threw the viewer's scroll
  // position away every time it ran.
  //
  // Measured at 1900x950 with the list scrolled to 600px: collapsing the
  // spans took the container's scrollHeight from 1577 to 836, the browser
  // clamped scrollTop to 0 to fit, and restoring the spans put the height
  // back with the scroll still at the top. A 600px jump - and it fired on
  // every poster that finished loading, every appended page and every
  // resize, which is why scrolling down through Today Match kept snapping
  // back.
  //
  // The pass was never needed. These cards are `align-self:start` with
  // `height:auto`, so a card's box is its own content height whether or not
  // a span is allocating grid space for it: measured across twelve cards,
  // the heights with the spans applied and with them reset are the same
  // numbers to the tenth of a pixel.
  cards.forEach((card) => {
    const height = card.getBoundingClientRect().height;
    const span = Math.max(1, Math.ceil((height + rowGap) / (rowHeight + rowGap)));
    const next = `span ${span}`;
    // Only when it actually changes: an unconditional write invalidates
    // layout for every card on every scroll tick that lands here.
    if (card.style.gridRowEnd !== next) card.style.gridRowEnd = next;
  });
}

function scheduleTodayMasonry() {
  requestAnimationFrame(() => requestAnimationFrame(layoutTodayMasonry));
}

/* Re-measure whenever a card can change height: after its poster loads, when
 * the window changes, and when anything inside it resizes. */
function watchTodayCardForMasonry(card) {
  if (!card || card.dataset.masonryWatched === '1') return;
  card.dataset.masonryWatched = '1';
  if (window.ResizeObserver) {
    if (!state.todayMasonryObserver) {
      state.todayMasonryObserver = new ResizeObserver(scheduleTodayMasonry);
    }
    state.todayMasonryObserver.observe(card);
  }
  qsa('img', card).forEach((image) => {
    if (image.complete) return;
    image.addEventListener('load', scheduleTodayMasonry, { once: true });
    image.addEventListener('error', scheduleTodayMasonry, { once: true });
  });
}

function renderCurrentList(reset = true, options = {}) {
  if (state.seriesDetailMode || seriesModule?.detailActive) return;
  if (state.view === VIEW.MOVIE && !state.selectedMovieCategory && !state.moviePreviewMode) {
    showListMessage(MOVIE_PROMPT_TEXT, 'fa-film');
    setSidebarCount('0 Movies');
    return;
  }
  applyFilterAndSort();
  renderEventSportFilter();
  sidebarSection.classList.toggle('movie-mode', state.view === VIEW.MOVIE);
  sidebarSection.classList.toggle(
    'sports-grid-mode',
    state.view === VIEW.CHANNEL && canonicalDisplayKey(state.selectedCategory).includes('sports')
  );
  sidebarSection.classList.toggle(
    'channel-grid-mode',
    state.view === VIEW.CHANNEL && !canonicalDisplayKey(state.selectedCategory).includes('sports')
  );
  sidebarSection.classList.toggle(
    'event-list-mode',
    state.view === VIEW.UPCOMING || state.view === VIEW.EVENT
  );
  sidebarList.classList.toggle(
    'upcoming-grid',
    state.view === VIEW.UPCOMING || state.view === VIEW.EVENT
  );
  // Today Match redesign: a two-column masonry list, by direct request -
  // scoped to this one class so Upcoming keeps its existing single-column
  // list exactly as it is.
  sidebarList.classList.toggle('today-grid', state.view === VIEW.EVENT);
  // The two tabs' headers differ in the approved design - Today's count is
  // 12.5px on a baseline-aligned row, Upcoming's is 14px/600 on a centred one -
  // so the section says which tab it is showing.
  sidebarSection.classList.toggle('today-mode', state.view === VIEW.EVENT);
  sidebarSection.classList.toggle('upcoming-mode', state.view === VIEW.UPCOMING);
  // The freshness stamp is per-view now, so it has to be re-decided whenever
  // the view is. Otherwise leaving Movies leaves it hidden until the next
  // thirty-second tick happens to run.
  renderDataFreshness();
  if (reset) {
    cancelPendingImages(sidebarList);
    sidebarList.replaceChildren();
    if (state.view === VIEW.EVENT) ensureTodayGrid();
    state.renderedCount = 0;
    state.renderedUids.clear();
    state.lastRenderedDayKey = '';
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
      // One line, and this was three facts crammed into it:
      // "29 Manual · 30/29 Movies loaded" wrapped onto two lines in the
      // sidebar header and ran into the freshness stamp sitting beside it.
      //
      // The count slot keeps the number a viewer acts on. The denominator is
      // only worth its width while there is more to come - once everything
      // known has loaded, "30/29" is arithmetic nobody asked for. The manual
      // figure moves to the detail slot, which exists for exactly this and
      // already hides itself on a narrow screen.
      const loaded = state.currentItems.length;
      const more = Number.isFinite(totalKnown) && totalKnown > loaded;
      setSidebarCount(
        more ? `${loaded}/${totalKnown} Movies` : `${loaded} Movies`,
        manualTotal > 0 ? `${manualTotal} Manual` : ''
      );
    }
  } else if (state.view === VIEW.UPCOMING || state.view === VIEW.EVENT) {
    sidebarList.classList.remove('movie-grid');
    setEventListCount();
    state.eventUiFingerprint = eventUiFingerprint();
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
    // Guide 23: a filter that matches nothing says so in its own words rather
    // than leaving a blank panel.
    else if (
      (state.view === VIEW.EVENT || state.view === VIEW.UPCOMING) &&
      state.eventSportFilter !== 'all'
    ) {
      message = `এই মুহূর্তে ${eventSportLabel(state.eventSportFilter)} ইভেন্ট নেই — Filter থেকে All Events বেছে নিন`;
    }
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

function eventDayKey(item) {
  const date = eventStartDate(item);
  if (!date) return '';
  // Grouped by the viewer's own day, so "today" means today where they are.
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
}

/* One day of Upcoming fixtures: the date badge, the sport filter on the first
 * day only, and the rows beneath it in a single bordered list - the shape the
 * design uses.
 *
 *     আজ · ২ সেপ্টেম্বর                              [ ▼ ALL ]
 */
function upcomingDayLabel(date) {
  const today = new Date();
  const tomorrow = new Date(today.getTime() + 86400000);
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const dayAndMonth = date.toLocaleDateString('bn-BD', { day: 'numeric', month: 'long' });
  if (sameDay(date, today)) return `আজ · ${dayAndMonth}`;
  if (sameDay(date, tomorrow)) return `আগামীকাল · ${dayAndMonth}`;
  return dayAndMonth;
}

/* Cricket and football are the only two sports this site publishes, so those
 * are the only two the filter offers beside All. There is deliberately no
 * "Other": an event that is neither never reaches this list. */
function buildUpcomingSportFilter() {
  const wrap = document.createElement('label');
  wrap.className = 'sport-filter-wrap';
  wrap.setAttribute('aria-label', 'Sport filter');
  wrap.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="2" aria-hidden="true"><path d="M4 5h16l-6 7v5l-4 2v-7L4 5z"/></svg>';

  const select = document.createElement('select');
  select.className = 'sport-filter';
  select.id = 'upcoming-sport-filter';
  [['all', 'ALL'], ['cricket', 'CRICKET'], ['football', 'FOOTBALL']].forEach(([value, label]) => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    select.appendChild(option);
  });
  select.value = ['cricket', 'football'].includes(state.eventSportFilter)
    ? state.eventSportFilter
    : 'all';
  select.addEventListener('change', () => setEventSportFilter(select.value));
  wrap.appendChild(select);
  return wrap;
}

function createDayHeading(item, options = {}) {
  const date = eventStartDate(item);
  const day = document.createElement('div');
  day.className = 'schedule-day';

  const head = document.createElement('div');
  head.className = 'day-label upcoming-day-head';
  head.setAttribute('role', 'presentation');
  const label = document.createElement('span');
  label.textContent = date ? upcomingDayLabel(date) : 'সময় নিশ্চিত নয়';
  head.appendChild(label);
  if (options.withFilter) head.appendChild(buildUpcomingSportFilter());

  const list = document.createElement('div');
  list.className = 'schedule-list';

  day.append(head, list);
  return day;
}

function appendNextChunk(limit = null) {
  if (state.seriesDetailMode || seriesModule?.detailActive) return;
  if (!state.filteredItems.length) return;
  const chunkSize = limit ?? (state.view === VIEW.MOVIE ? MOVIE_CHUNK_SIZE : CHANNEL_NEXT_CHUNK);

  // Cards are tracked by identity, never by a positional cursor. Loading the
  // next movie page re-sorts the whole catalogue by year, so an index cursor
  // would re-append rows that are already on screen (the "same movie added
  // twice" report) and silently drop the rows that moved above the cursor.
  const chunk = [];
  for (let index = 0; index < state.filteredItems.length; index += 1) {
    const item = state.filteredItems[index];
    if (!item || state.renderedUids.has(item._uid)) continue;
    chunk.push({ item, index });
    if (chunk.length >= chunkSize) break;
  }
  if (!chunk.length) return;

  if (state.view === VIEW.EVENT) {
    const grid = ensureTodayGrid();
    const fragment = document.createDocumentFragment();
    const added = [];
    chunk.forEach(({ item, index }) => {
      const card = createChannelCard(item, index);
      added.push(card);
      fragment.appendChild(card);
      state.renderedUids.add(item._uid);
    });
    grid.appendChild(fragment);
    added.forEach(watchTodayCardForMasonry);
    scheduleTodayMasonry();
  } else {
    const fragment = document.createDocumentFragment();
    chunk.forEach(({ item, index }) => {
      // Upcoming is one unbroken run of a hundred and twenty cards, and the
      // list is sorted by kickoff - so the boundary between tonight and
      // tomorrow passes without a mark and a viewer scrolling for "what is on
      // later" cannot tell which day they have reached. A heading is written
      // in when the day changes.
      const card = state.view === VIEW.MOVIE
        ? (seriesModule?.isSeriesItem(item)
          ? seriesModule.createSeriesCard(item, index)
          : createMovieCard(item, index))
        : createChannelCard(item, index);
      state.renderedUids.add(item._uid);
      if (state.view === VIEW.UPCOMING) {
        // Rows live inside their day's list, so the day is one bordered block
        // rather than a heading floating above loose cards.
        const day = eventDayKey(item);
        if (day && day !== state.lastRenderedDayKey) {
          state.lastRenderedDayKey = day;
          const firstDay = !sidebarList.querySelector('.schedule-day')
            && !fragment.querySelector('.schedule-day');
          fragment.appendChild(createDayHeading(item, { withFilter: firstDay }));
        }
        const lists = fragment.querySelectorAll('.schedule-day > .schedule-list');
        const openList = lists.length
          ? lists[lists.length - 1]
          : sidebarList.querySelector('.schedule-day:last-of-type > .schedule-list');
        (openList || fragment).appendChild(card);
        return;
      }
      fragment.appendChild(card);
    });
    sidebarList.appendChild(fragment);
  }
  state.renderedCount = state.renderedUids.size;
  updateFavoriteUi();
  updateReminderUi();
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

function playbackBadgesHtml(item) {
  const badges = [];
  const height = Number(item?.resolution_height || item?.height || 0);
  const resolutionText = String(item?.resolution || item?.label || '').trim();
  const quality = height >= 2160 ? '4K' : height >= 1440 ? '2K' : height >= 1080 ? '1080p' : height >= 720 ? '720p' : resolutionText;
  if (quality) badges.push(`<span class="source-route-badge quality">${escapeHtml(quality)}</span>`);

  const hasDrm = item?.drm && typeof item.drm === 'object' && Object.keys(item.drm).length > 0;
  const hasHeaders = Boolean(item?.requires_headers || (item?.headers && Object.keys(item.headers).length));
  const proxyMode = String(item?.proxy_mode || '').toLowerCase();
  const verificationMode = String(item?.verification_mode || '').toLowerCase();
  const route = hasHeaders
    ? 'Header'
    : (proxyMode === 'proxy_only' || verificationMode.includes('proxy'))
      ? 'Proxy'
      : 'Direct';
  badges.push(`<span class="source-route-badge route">${route}</span>`);
  if (hasDrm) badges.push('<span class="source-route-badge drm">DRM</span>');
  return badges.join('');
}

function eventDisplayParts(item) {
  const original = cleanDisplayName(item?.name || 'Live Match');
  const competition = cleanDisplayName(item?.competition || '').replace(/^Untitled$/i, '');
  const structured = original.match(/^(.+?\b(?:competition|league|cup|series)\b(?:\s+\d{4})?)(?:\s+\d+(?:st|nd|rd|th)\s+match)?\s+(.+\bvs\b.+)$/i);
  if (structured) {
    return {
      title: cleanDisplayName(structured[2]),
      competition: competition || cleanDisplayName(structured[1])
    };
  }
  const trimmed = original.replace(/^.+?\b\d+(?:st|nd|rd|th)\s+match\s+/i, '');
  return { title: trimmed || original, competition };
}

function eventStartDate(item) {
  const raw = String(item?.start_at || item?.start_time || '').trim();
  if (!raw) return null;
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function eventEndDate(item) {
  const raw = String(item?.end_at || item?.end_time || '').trim();
  if (!raw) return null;
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function isEventEnded(item, nowMs = Date.now()) {
  const configured = String(item?.schedule_status || item?.status || '').toUpperCase();
  if (configured === 'ENDED') return true;
  const end = eventEndDate(item);
  return Boolean(end && end.getTime() <= nowMs);
}

function eventDhakaDayKey(date) {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Dhaka', year: 'numeric', month: '2-digit', day: '2-digit'
  }).format(date);
}

function eventDhakaTime(date) {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Dhaka', hour: 'numeric', minute: '2-digit', hour12: true
  }).format(date);
}

// Guide 7 and 16. Every clock the user sees is Bangladesh time and says so.
// Same-day events drop the date entirely so the row stays short.
function eventScheduleText(item) {
  const start = eventStartDate(item);
  if (!start) return String(item?.start_time || 'Time verification pending');
  const time = eventDhakaTime(start);
  const now = new Date();
  const targetKey = eventDhakaDayKey(start);
  if (targetKey === eventDhakaDayKey(now)) return `Today • ${time} BDT`;
  if (targetKey === eventDhakaDayKey(new Date(now.getTime() + 86400000))) return `Tomorrow • ${time} BDT`;
  const date = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Dhaka', weekday: 'short', day: 'numeric', month: 'short'
  }).format(start);
  return `${date} • ${time} BDT`;
}

// A live card says when the match began. The elapsed chip beside the LIVE
// badge already says "started", and the date is only worth the width when the
// match did not begin today - saying "Started: Sun 16 Aug • 9:00 PM BDT" in a
// 167px column simply got clipped mid-word on the live site.
function eventStartedText(item) {
  const start = eventStartDate(item);
  if (!start) return '';
  const time = `${eventDhakaTime(start)} BDT`;
  if (eventDhakaDayKey(start) === eventDhakaDayKey(new Date())) return time;
  const date = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Dhaka', day: 'numeric', month: 'short'
  }).format(start);
  return `${date} • ${time}`;
}

// Guide 17. Minute granularity, refreshed by the 30s card clock.
function eventCountdownText(item) {
  const start = eventStartDate(item);
  if (!start) return '';
  const diff = start.getTime() - Date.now();
  if (diff <= 0) return '';
  const minutes = Math.round(diff / 60000);
  if (minutes < 60) return `Starts in ${Math.max(1, minutes)}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    const rest = minutes % 60;
    return rest ? `Starts in ${hours}h ${rest}m` : `Starts in ${hours}h`;
  }
  const days = Math.round(hours / 24);
  return days <= 1 ? 'Starts tomorrow' : `Starts in ${days} days`;
}

// Guide 6. Elapsed time is measured, never guessed. A card with no kickoff
// time on record simply keeps the plain LIVE NOW badge.
function eventLivePhaseText(item) {
  const start = eventStartDate(item);
  if (!start) return '';
  const diff = Date.now() - start.getTime();
  if (diff < 0) return '';
  // Compact on purpose: it sits immediately after the LIVE badge, which
  // already supplies the "started" sense, so "3h 52m" reads the same as
  // "Started 3h 52m ago" in a third of the width.
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

/* "Started 41m ago", off the same measured elapsed clock the live badge uses.
 *
 * The match preview showed the kickoff time and nothing else, so a fixture
 * that began forty-one minutes earlier read exactly like one that had not
 * started: "Today - 8:00 PM BDT", at 8:41 PM. Measured 2026-09-03 on
 * Kashi Rudras vs Noida Kings, kickoff 14:00 UTC, metadata_only with no
 * channels at all.
 */
function eventStartedAgoText(item) {
  const elapsed = eventLivePhaseText(item);
  if (!elapsed) return '';
  return elapsed === 'just now' ? 'Started just now' : `Started ${elapsed} ago`;
}

// Guide 18. User-facing wording for what the scanner already recorded.

// Requirement 12. The card speaks the site's language, and it says the
// countdown once. Bengali numerals throughout, because mixing "18" into a
// Bangla sentence reads as a different voice.
const BANGLA_DIGITS = ['০', '১', '২', '৩', '৪', '৫', '৬', '৭', '৮', '৯'];

function toBanglaDigits(value) {
  return String(value).replace(/[0-9]/g, (d) => BANGLA_DIGITS[Number(d)]);
}

function eventCountdownTextBn(item) {
  const start = eventStartDate(item);
  if (!start) return '';
  const diff = start.getTime() - Date.now();
  if (diff <= 0) return '';
  const minutes = Math.round(diff / 60000);
  if (minutes < 60) return `শুরু হবে ${toBanglaDigits(Math.max(1, minutes))} মিনিট পর`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    const rest = minutes % 60;
    return rest
      ? `শুরু হবে ${toBanglaDigits(hours)} ঘণ্টা ${toBanglaDigits(rest)} মিনিট পর`
      : `শুরু হবে ${toBanglaDigits(hours)} ঘণ্টা পর`;
  }
  const days = Math.round(hours / 24);
  return days <= 1 ? 'শুরু হবে আগামীকাল' : `শুরু হবে ${toBanglaDigits(days)} দিন পর`;
}

// Requirement 12's compact metadata row: day, exact BDT clock, stream state.
function eventMetaRowTextBn(item, streams) {
  const start = eventStartDate(item);
  const parts = [];
  if (start) {
    const time = eventDhakaTime(start);
    const todayKey = eventDhakaDayKey(new Date());
    const key = eventDhakaDayKey(start);
    let day = '';
    if (key === todayKey) day = 'আজ';
    else if (key === eventDhakaDayKey(new Date(Date.now() + 86400000))) day = 'আগামীকাল';
    else day = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Dhaka', day: 'numeric', month: 'short' }).format(start);
    parts.push(`${day} · ${toBanglaDigits(time)} BDT`);
  }
  if (streams) parts.push(streams.ready ? streams.shortBn : 'স্ট্রিমের অপেক্ষায়');
  return parts.join(' · ');
}

function eventLivePhaseTextBn(item) {
  const start = eventStartDate(item);
  if (!start) return '';
  const diff = Date.now() - start.getTime();
  if (diff < 0) return '';
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'এইমাত্র শুরু';
  if (minutes < 60) return `${toBanglaDigits(minutes)} মিনিট`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest
    ? `${toBanglaDigits(hours)} ঘণ্টা ${toBanglaDigits(rest)} মিনিট`
    : `${toBanglaDigits(hours)} ঘণ্টা`;
}

/* The kickoff clock the Upcoming row shows: the site's existing Bangladesh
 * conversion, written in Bengali digits as the design writes it - ৮:৩০ PM.
 * Never the raw or UTC timestamp. */
function eventClockTextBn(item) {
  const start = eventStartDate(item);
  if (!start) return 'সময় নিশ্চিত নয়';
  return toBanglaDigits(eventDhakaTime(start));
}

function eventStartedTextBn(item) {
  const start = eventStartDate(item);
  if (!start) return '';
  const time = `${toBanglaDigits(eventDhakaTime(start))} BDT`;
  if (eventDhakaDayKey(start) === eventDhakaDayKey(new Date())) return time;
  const date = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Dhaka', day: 'numeric', month: 'short' }).format(start);
  return `${date} · ${time}`;
}

function eventVerificationLabel(item) {
  const mode = String(item?.time_verification || '').toLowerCase();
  if (mode === 'official_catalogue') return 'Fixture Verified';
  if (mode === 'provider_feed') return 'Time Verified';
  if (!eventStartDate(item)) return 'Verification Pending';
  return item?.schedule_verified === true ? 'Schedule Updated' : 'Verification Pending';
}

// Guide 8, 9 and 19. A summary, never the technical detail behind it. The
// card gets guide 9's compact pill because the text column is about 175px
// wide — "Primary • +2 Backups" does not fit there and used to be cut to
// "Primary". The full wording goes to the details popup, which has the room.
function eventStreamSummary(item) {
  if (item?.metadata_only === true || !isPlayable(item)) {
    return { short: 'Waiting', shortBn: 'স্ট্রিমের অপেক্ষায়', text: 'Waiting for Stream', ready: false };
  }
  const backups = Array.isArray(item?.backups) ? item.backups.length : 0;
  const total = Math.max(Number(item?.available_link_count || 0), backups + 1);
  // "Ready" claimed playability from a URL being present. What is actually
  // known is that a link exists and the scanner reached it, which is not the
  // same promise - a route can answer 200 and decode nothing.
  if (backups <= 0) return { short: '1 Stream', shortBn: '১ লিংক', text: 'Link available', ready: true };
  return {
    short: `${total} Streams`,
    shortBn: `${toBanglaDigits(total)} স্ট্রিম`,
    text: `Primary • +${backups} Backup${backups > 1 ? 's' : ''}`,
    ready: true
  };
}

// Order is deliberate: the distinctive names are tested before the generic
// ones, so "League of Legends" reaches ESPORTS instead of being swallowed by
// the football rule that has to accept a bare "League".
const EVENT_SPORTS = [
  ['ESPORTS', 'fa-gamepad', /\b(?:esports?|e[\s-]?sports?|pubg|dota|valorant|counter[\s-]?strike|cs\s?2|league\s+of\s+legends|mobile\s+legends|free\s+fire|rocket\s+league|fifa\s+e)\b/i],
  ['CRICKET', 'fa-baseball', /\b(?:cricket|cric(?:life|hd)|t20i?|odi|test\s+match|\d{1,2}(?:st|nd|rd|th)\s+(?:test|odi|t20i?)|the\s+hundred|bbl|ipl|psl|cpl|ashes|county|vitality\s+blast)\b/i],
  ['MOTORSPORT', 'fa-flag-checkered', /\b(?:motorsport|formula\s?e?|f1|e[\s-]?prix|moto\s?gp|nascar|rally|grand\s+prix|race\s+\d|race\s+day|gt4|gt3|adac|superbike|mxgp|motocross|indycar|cycling|uci\b|tour\s+de)\b/i],
  ['GOLF', 'fa-golf-ball-tee', /\b(?:golf|pga|lpga|dp\s+world\s+tour|ryder\s+cup)\b/i],
  // "<place> Open" is a tennis tournament; "<x> Open Cup" is not.
  ['TENNIS', 'fa-table-tennis-paddle-ball', /\b(?:tennis|atp|wta|padel|badminton|squash|roland\s+garros|wimbledon)\b|\b[a-z]+\s+open\b(?!\s+cup)/i],
  ['RUGBY', 'fa-football', /\b(?:rugby|currie\s+cup|six\s+nations|super\s+rugby|nfl|american\s+football)\b/i],
  ['BASEBALL', 'fa-baseball-bat-ball', /\b(?:mlb|baseball|world\s+series|npb)\b/i],
  ['BASKETBALL', 'fa-basketball', /\b(?:basketball|nba|wnba|euroleague|basket)\b/i],
  ['VOLLEYBALL', 'fa-volleyball', /\b(?:volleyball|beach\s+volley)\b/i],
  ['HOCKEY', 'fa-hockey-puck', /\b(?:ice\s+hockey|nhl|khl|field\s+hockey)\b/i],
  ['RACING', 'fa-horse', /\b(?:horse\s+racing|racecourse|steeplechase|greyhound)\b/i],
  ['FOOTBALL', 'fa-futbol', /\b(?:football|soccer|bundesliga|eredivisie|serie\s+[ab]|la\s?liga|ligue\s?\d|s[üu]per\s+lig|lig\b|liga|uefa|fifa|afc|caf|concacaf|champions|europa|efl|efbet|championship|friendlies|frauenliga|ekstraklasa|allsvenskan|superliga|eliteserien|primeira|segunda|coppa|copa|coupe|pokal|hnl|nwsl|npl|mls|eerste\s+divisie|primera\s+(?:nacional|[a-d])\b|\d\s*deild|[uú]rvalsdeild|nb\s+i\b|jong\b|[akj][\s-]?league|cup|league|divisi[oó]n|division|fc\b|sc\b|utd\b|united)\b/i]
];

// Guide 5. The scanner's own category wins when it is specific; the generic
// "LIVE" bucket is not a sport, so the name and competition decide instead.
function eventSport(item) {
  // Requirement 11, corrected. The scanner already resolved this event's sport
  // from the source category, the competition *and* the name, and published it as
  // `sport_type`. This function only ever saw the name and the competition, so the
  // two disagreed: the Smart Filter (which reads `sport_type` through
  // itemSportType) counted a CONMEBOL Libertadores tie as football while the badge
  // on the same card read OTHER. The published value is preferred, and the local
  // patterns stay as the fallback for a card that predates the field.
  const published = String(item?.sport_type || '').trim().toLowerCase();
  if (published && published !== 'other' && published !== 'channel') {
    const known = EVENT_SPORTS.find(([label]) => label.toLowerCase() === published);
    if (known) return { label: known[0], icon: known[1] };
  }
  const declared = cleanDisplayName(item?.source_category || '').replace(/^Untitled$/i, '');
  const haystack = [declared, item?.competition, item?.name].filter(Boolean).join(' ');
  if (declared && !/^(?:live|sports?|event|other|general)$/i.test(declared)) {
    const matched = EVENT_SPORTS.find(([, , pattern]) => pattern.test(declared));
    if (matched) return { label: matched[0], icon: matched[1] };
  }
  const matched = EVENT_SPORTS.find(([, , pattern]) => pattern.test(haystack));
  if (matched) return { label: matched[0], icon: matched[1] };
  return { label: 'OTHER', icon: 'fa-medal' };
}

// Guide 10. A card only drops to the channel style when nothing identifies a
// fixture: no competition, no clock and no fixture wording in the name.
function eventHasFixtureSignal(item) {
  if (cleanDisplayName(item?.competition || '').replace(/^Untitled$/i, '')) return true;
  if (eventStartDate(item)) return true;
  const name = String(item?.name || '');
  return /\s(?:vs\.?|v\.?)\s/i.test(name) ||
    /\b(?:day|round|race|stage|session|leg|final|semi|quarter|matchday|heat|qualifying|innings|prix)\b/i.test(name) ||
    /\b\d{1,2}(?:st|nd|rd|th)\b/i.test(name);
}

function isChannelOnlyEventCard(item) {
  return !eventHasFixtureSignal(item);
}

// Smart Filter guide 9, 10 and 13. One canonical lowercase value per final
// card. A card that identifies no fixture at all is a channel, not a sport.
function eventSportType(item) {
  if (isChannelOnlyEventCard(item)) return 'channel';
  return eventSport(item).label.toLowerCase();
}

function eventSportLabel(sportType) {
  if (sportType === 'channel') return 'Channels';
  const entry = EVENT_SPORTS.find(([label]) => label.toLowerCase() === sportType);
  // A recognised sport arrives here as FOOTBALL and needs lowering; "other"
  // arrives already lowercase and needs raising. Doing both covers each case
  // — the first letter used to be left as it came, so the fallback bucket
  // showed up in the menu as "other".
  const text = entry ? entry[0] : String(sportType || 'other');
  return text.charAt(0).toUpperCase() + text.slice(1).toLowerCase();
}

function eventSportIcon(sportType) {
  if (sportType === 'channel') return 'fa-tv';
  const entry = EVENT_SPORTS.find(([label]) => label.toLowerCase() === sportType);
  return entry ? entry[1] : 'fa-medal';
}

function itemSportType(item) {
  return String(item?.sport_type || '').toLowerCase() || eventSportType(item || {});
}

// Guide 15: counts come from the final merged cards, never from raw stream
// links. Ended events are already gone by the time this runs, so the totals
// match exactly what "All Events" puts on screen.
function eventSportCounts(items) {
  const counts = new Map();
  items.forEach((item) => {
    const key = itemSportType(item);
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  // Channels last, then biggest group first, then alphabetically.
  // Requirement 11: Cricket, Football, then everything else; channels last.
  const rank = (sport) => (sport === 'cricket' ? 0 : sport === 'football' ? 1 : sport === 'channel' ? 3 : 2);
  return [...counts.entries()]
    .sort((a, b) => {
      const byRank = rank(a[0]) - rank(b[0]);
      if (byRank) return byRank;
      return b[1] - a[1] || a[0].localeCompare(b[0]);
    })
    .map(([sport, count]) => ({ sport, count, label: eventSportLabel(sport), icon: eventSportIcon(sport) }));
}

function eventFilterBaseItems() {
  if (state.view !== VIEW.UPCOMING && state.view !== VIEW.EVENT) return [];
  return state.currentItems.filter((item) => !isEventEnded(item));
}

// Guide 4. Stream-level wording belongs to the stream, not to the match title.
function stripStreamNoise(text) {
  const original = String(text || '').trim();
  let out = original;
  let previous;
  do {
    previous = out;
    out = out.replace(
      /[\s|\-–—•]*\b(?:server\s*\d*|srv\s*\d*|link\s*\d+|backup(?:\s*\d+)?|multi[\s-]?audio|fhd|uhd|hd|sd|4k|2k|1080p?|720p?|576p?|480p?)\s*$/i,
      ''
    );
  } while (out !== previous);
  return out.trim() || original;
}

// Guide 11 and 28. With one logo field per item a real two-crest layout is not
// possible, so the placeholder carries the team pair instead of bare initials.
function eventArtFallbackHtml(item, parts) {
  const pair = String(parts.title).split(/\s+(?:vs\.?|v\.?)\s+/i);
  const abbreviate = (value) => {
    const words = String(value).replace(/[^A-Za-z0-9\s]/g, ' ').split(/\s+/).filter(Boolean);
    if (!words.length) return '?';
    if (words.length === 1) return words[0].slice(0, 3).toUpperCase();
    return words.slice(0, 3).map((word) => word[0]).join('').toUpperCase();
  };
  if (pair.length === 2 && pair[0].trim() && pair[1].trim()) {
    return `<div class="event-art-versus" aria-hidden="true"><span>${escapeHtml(abbreviate(pair[0]))}</span><em>vs</em><span>${escapeHtml(abbreviate(pair[1]))}</span></div>`;
  }
  const sport = eventSport(item);
  return `<div class="event-art-fallback" aria-hidden="true"><i class="fas ${sport.icon}"></i><span>${escapeHtml(abbreviate(parts.title))}</span></div>`;
}

// Section 10/25. Every picture this fixture has, in the order to try them.
//
// The card used to read `item.logo` and nothing else, so a fixture that arrived
// with team badges and an event poster still rendered two initials - the artwork
// was published and then ignored. The chain is: the fixture's own logo, then the
// provider poster, then anything in artwork_candidates. Initials are the last
// resort only, which is what section 10 asks for.
function eventArtworkChain(item) {
  const urls = [];
  const push = (value) => {
    const url = String(value || '').trim();
    if (url && !urls.includes(url)) urls.push(url);
  };
  push(item?.logo);
  push(item?.provider_poster_url);
  const candidates = item?.artwork_candidates;
  if (Array.isArray(candidates)) candidates.forEach(push);
  return urls;
}

function eventTeamBadges(item) {
  const home = String(item?.home_badge_url || '').trim();
  const away = String(item?.away_badge_url || '').trim();
  return home && away ? { home, away } : null;
}

function eventArtHtml(item, parts) {
  // Section 10's two-crest layout. It was described as impossible because there
  // was only one logo field; both badges are published now, so it is drawn.
  const badges = eventTeamBadges(item);
  if (badges) {
    return (
      '<div class="event-art-crests" data-event-art-crests="1">'
      + `<img src="${escapeHtml(badges.home)}" alt="" loading="lazy" decoding="async"`
      + ' referrerpolicy="no-referrer" data-event-badge="home">'
      + '<em>vs</em>'
      + `<img src="${escapeHtml(badges.away)}" alt="" loading="lazy" decoding="async"`
      + ' referrerpolicy="no-referrer" data-event-badge="away">'
      + '</div>'
    );
  }
  const chain = eventArtworkChain(item);
  if (!chain.length) return eventArtFallbackHtml(item, parts);
  // The rest of the chain travels with the element, so a broken first choice
  // tries the next real picture instead of dropping straight to initials.
  const rest = chain.slice(1);
  const fallbacks = rest.length ? ` data-art-fallbacks="${escapeHtml(JSON.stringify(rest))}"` : '';
  return `<img src="${escapeHtml(chain[0])}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-event-art="1"${fallbacks}>`;
}

// Guide 20. Reminders are a local preference; nothing is sent anywhere.
function reminderIds() {
  const list = readJsonStorage(STORAGE_KEYS.eventReminders, []);
  return Array.isArray(list) ? list : [];
}

const REMINDER_LEAD_MINUTES = 5;

function reminderNotificationsAllowed() {
  return typeof Notification !== 'undefined' && Notification.permission === 'granted';
}

async function askForReminderPermission() {
  if (typeof Notification === 'undefined') return false;
  if (Notification.permission === 'granted') return true;
  if (Notification.permission === 'denied') return false;
  try {
    return (await Notification.requestPermission()) === 'granted';
  } catch (error) {
    return false;
  }
}

function fireReminderNotification(item) {
  if (!reminderNotificationsAllowed()) return false;
  try {
    const when = eventCountdownTextBn(item) || 'এখনই';
    new Notification(String(item.name || 'ম্যাচ'), {
      body: `${item.competition || 'ম্যাচ'} — ${when}`,
      icon: 'assets/img/icon-192.png',
      tag: `clicktv-reminder-${item.id || item.name}`,
    });
    return true;
  } catch (error) {
    return false;
  }
}

function checkDueReminders() {
  /* Fire the reminders whose kickoff has come round.

     Checked on the clock tick rather than with a timer per match, so a list
     that re-renders - which it does every thirty seconds - does not lose the
     pending reminders or fire them twice. */
  const ids = reminderIds();
  if (!ids.length || !reminderNotificationsAllowed()) return;
  const fired = readJsonStorage(STORAGE_KEYS.eventRemindersFired, []);
  const alreadyFired = Array.isArray(fired) ? fired : [];
  const now = Date.now();
  const stillPending = [];

  for (const item of state.currentItems || []) {
    const key = item.id || item.url || item.name;
    if (!ids.includes(key) || alreadyFired.includes(key)) continue;
    const kickoff = eventStartDate(item)?.getTime();
    if (!kickoff) continue;
    if (kickoff - now > REMINDER_LEAD_MINUTES * 60000) continue;
    if (now - kickoff > 60 * 60000) continue;  // long over; not worth a ping
    if (fireReminderNotification(item)) stillPending.push(key);
  }
  if (stillPending.length) {
    writeJsonStorage(STORAGE_KEYS.eventRemindersFired,
                     [...alreadyFired, ...stillPending].slice(-200));
  }
}

async function toggleEventReminder(uid, event) {
  event?.stopPropagation();
  const item = state.currentItems.find((entry) => entry._uid === uid);
  if (!item) return;
  const key = item.id || item.url || item.name;
  const current = reminderIds();
  const active = current.includes(key);
  writeJsonStorage(STORAGE_KEYS.eventReminders, active ? current.filter((id) => id !== key) : [...current, key]);
  updateReminderUi();

  if (active) {
    showToast('Reminder সরানো হয়েছে');
    return;
  }

  // The button used to write a note to localStorage, show a toast, and never
  // do anything else - so "Remind Me" was a promise nothing kept. Permission
  // is asked for once, and what the reminder can actually do is said plainly
  // rather than implied.
  const allowed = await askForReminderPermission();
  showToast(allowed
    ? `Reminder সেট — শুরুর ${REMINDER_LEAD_MINUTES} মিনিট আগে জানানো হবে`
    : 'Reminder সেট — নোটিফিকেশন বন্ধ, তাই সাইটে ফিরলে দেখতে পাবেন');
}

// ── Smart Filter ──────────────────────────────────────────────────────────
// A view over the final merged Today Match / Upcoming cards. It never scans,
// never refetches, never rebuilds primary/backup selection and never touches
// the player: selecting a sport re-renders the card list and nothing else.

function isEventSportFilterOpen() {
  return $('eventFilterMenu')?.classList.contains('open') === true;
}

function closeEventSportFilter(focusButton = false) {
  const menu = $('eventFilterMenu');
  const button = $('eventFilterBtn');
  if (!menu || !button) return;
  menu.classList.remove('open');
  menu.setAttribute('aria-hidden', 'true');
  button.setAttribute('aria-expanded', 'false');
  if (focusButton) button.focus();
}

function openEventSportFilter() {
  const menu = $('eventFilterMenu');
  const button = $('eventFilterBtn');
  if (!menu || !button) return;
  menu.classList.add('open');
  menu.setAttribute('aria-hidden', 'false');
  button.setAttribute('aria-expanded', 'true');
  positionEventSportFilter();
  // preventScroll matters: without it the browser scrolls the catalogue
  // column to reveal the focused row, and the menu's own scroll handler then
  // closes the menu the instant it opened.
  qs('.event-filter-option', menu)?.focus({ preventScroll: true });
  // Icons and web fonts can land after the first measurement, so measure once
  // more on the next frame rather than trusting a half-laid-out box.
  requestAnimationFrame(positionEventSportFilter);
}

// The header sits inside a scrolling column, so the menu is positioned in
// viewport coordinates instead of being clipped by that column. Its height is
// capped to the room actually available, so it can never run off screen no
// matter how many sports a day brings.
function positionEventSportFilter() {
  const menu = $('eventFilterMenu');
  const button = $('eventFilterBtn');
  if (!menu || !button || !menu.classList.contains('open')) return;

  const anchor = button.getBoundingClientRect();
  const margin = 8;
  const gap = 6;
  const spaceBelow = window.innerHeight - anchor.bottom - gap - margin;
  const spaceAbove = anchor.top - gap - margin;
  const openBelow = spaceBelow >= spaceAbove;
  const room = Math.max(120, openBelow ? spaceBelow : spaceAbove);

  menu.style.maxHeight = `${Math.round(room)}px`;
  const width = menu.offsetWidth || 210;
  const height = Math.min(menu.offsetHeight || 240, room);
  const left = Math.max(margin, Math.min(anchor.right - width, window.innerWidth - width - margin));
  const top = openBelow
    ? Math.min(anchor.bottom + gap, window.innerHeight - height - margin)
    : Math.max(margin, anchor.top - height - gap);

  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(Math.max(margin, top))}px`;
}

function setEventSportFilter(sport) {
  const next = String(sport || 'all');
  if (state.eventSportFilter === next) {
    closeEventSportFilter(true);
    return;
  }
  state.eventSportFilter = next;
  closeEventSportFilter(true);
  // Only the catalogue list is redrawn. state.currentItem, the <video> and
  // every playback timer are deliberately left alone (guide 7, 20 and 21).
  renderCurrentList(true);
}

function renderEventSportFilter() {
  const wrap = $('eventFilterWrap');
  const button = $('eventFilterBtn');
  const menu = $('eventFilterMenu');
  if (!wrap || !button || !menu) return;

  // Upcoming carries the design's own ALL / CRICKET / FOOTBALL select in its
  // first day row, so the older control would be a second filter beside it.
  if (state.view === VIEW.UPCOMING) {
    wrap.hidden = true;
    closeEventSportFilter();
    return;
  }
  const isEventView = state.view === VIEW.UPCOMING || state.view === VIEW.EVENT;
  const groups = isEventView ? eventSportCounts(eventFilterBaseItems()) : [];
  const active = state.eventSportFilter;

  // Guide 14: with a single sport on screen there is nothing to filter, so
  // the control disappears rather than sitting there doing nothing. It never
  // disappears while a filter is active, though — that would strand the user
  // on a filtered list with no way back to All Events.
  if (!isEventView || (groups.length <= 1 && active === 'all')) {
    wrap.hidden = true;
    closeEventSportFilter();
    return;
  }
  wrap.hidden = false;

  // Guide 4 says a sport with no cards is not offered, and guide 23 says a
  // filter that matches nothing must say so. Both hold: the menu lists only
  // sports that exist, plus the current selection at zero if the last of its
  // matches has just ended, so the empty-state message can explain itself and
  // All Events is still one click away.
  const total = groups.reduce((sum, group) => sum + group.count, 0);
  const menuGroups = active !== 'all' && !groups.some((group) => group.sport === active)
    ? [...groups, { sport: active, count: 0, label: eventSportLabel(active), icon: eventSportIcon(active) }]
    : groups;
  const activeGroup = menuGroups.find((group) => group.sport === active);
  const label = active === 'all' ? 'Filter' : (activeGroup?.label || 'Filter');
  qs('.event-filter-label', button).textContent = label;
  button.classList.toggle('filtered', active !== 'all');
  button.setAttribute('aria-label', active === 'all'
    ? 'Filter events by sport'
    : `Events filtered by ${label}. Change filter`);

  const option = (sport, text, icon, count) => `
    <button class="event-filter-option${active === sport ? ' selected' : ''}" type="button"
            role="menuitemradio" aria-checked="${active === sport}" data-sport="${escapeHtml(sport)}">
      <span class="event-filter-tick" aria-hidden="true"></span>
      <i class="fas ${icon}" aria-hidden="true"></i>
      <span class="event-filter-name">${escapeHtml(text)}</span>
      <span class="event-filter-count">${count}</span>
    </button>`;

  menu.innerHTML = `
    <div class="event-filter-heading">Sport</div>
    ${option('all', 'All Events', 'fa-layer-group', total)}
    ${menuGroups.map((group) => option(group.sport, group.label, group.icon, group.count)).join('')}`;

  if (menu.classList.contains('open')) positionEventSportFilter();
}

function setupEventSportFilter() {
  const button = $('eventFilterBtn');
  const menu = $('eventFilterMenu');
  if (!button || !menu) return;

  // The catalogue panel paints with backdrop-filter, and any filtered or
  // transformed ancestor becomes the containing block for position:fixed —
  // which parked the menu a full sidebar-width off screen. Re-parenting it to
  // <body> once makes its coordinates genuinely viewport-relative. The button
  // itself stays in the Events header, which is what the guide places there.
  if (menu.parentElement !== document.body) document.body.appendChild(menu);

  button.addEventListener('click', (event) => {
    event.stopPropagation();
    if (isEventSportFilterOpen()) closeEventSportFilter(true);
    else openEventSportFilter();
  });

  menu.addEventListener('click', (event) => {
    const option = event.target.closest('.event-filter-option');
    if (!option) return;
    event.stopPropagation();
    setEventSportFilter(option.dataset.sport);
  });

  menu.addEventListener('keydown', (event) => {
    const options = qsa('.event-filter-option', menu);
    const index = options.indexOf(document.activeElement);
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      options[(index + step + options.length) % options.length]?.focus();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      closeEventSportFilter(true);
    }
  });

  document.addEventListener('click', (event) => {
    if (!isEventSportFilterOpen()) return;
    if (event.target.closest('#eventFilterWrap, #eventFilterMenu')) return;
    closeEventSportFilter();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && isEventSportFilterOpen()) closeEventSportFilter(true);
  });
  window.addEventListener('resize', () => closeEventSportFilter());
  // Scrolling the catalogue keeps the menu glued to its button rather than
  // dismissing it, so a stray scroll never interrupts a choice.
  qsa('.sidebar-scroll-area').forEach((area) => area.addEventListener('scroll', positionEventSportFilter, { passive: true }));
  window.addEventListener('scroll', positionEventSportFilter, { passive: true });
}

function updateReminderUi() {
  const reminders = reminderIds();
  qsa('[data-reminder-id]', sidebarList).forEach((button) => {
    const active = reminders.includes(button.dataset.reminderId);
    button.classList.toggle('active', active);
    const icon = qs('i', button);
    if (icon) icon.className = active ? 'fas fa-bell' : 'far fa-bell';
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

function eventUiStatus(item) {
  const configured = String(item?.schedule_status || item?.status || '').toUpperCase();
  if (isEventEnded(item)) return 'ENDED';
  // Scanner-resolved LIVE_NOW is authoritative for multi-day events. Do not
  // downgrade it after the frontend's generic six-hour fallback window.
  if (item?.schedule_verified === true && configured === 'LIVE_NOW') return 'LIVE_NOW';
  if (item?.today_source_channel === true || configured === 'CHANNEL_LIVE') return 'CHANNEL_LIVE';
  const start = eventStartDate(item);
  if (!start) {
    if (['LIVE_NOW', 'CHANNEL_LIVE', 'STARTING_SOON', 'LINK_UPDATING', 'UPCOMING', 'TIME_UNVERIFIED'].includes(configured)) return configured;
    return isPlayable(item) ? 'LIVE_NOW' : 'UPCOMING';
  }
  const minutes = (start.getTime() - Date.now()) / 60000;
  // A clock plus a playable URL is not proof of an actual live match.
  // Only scanner-verified LIVE_NOW may make that claim.
  if (minutes <= 0) return isPlayable(item) ? (configured === 'LIVE_NOW' ? 'LIVE_NOW' : 'CHANNEL_LIVE') : 'LINK_UPDATING';
  if (minutes > 0 && minutes <= 60) return 'STARTING_SOON';
  return 'UPCOMING';
}

function eventStatusLabel(status) {
  /* One language on one card.

     Every other line the viewer reads is Bengali - the loading text, the
     countdown, the stream state - and the status pill was shouting English
     abbreviations beside them. Two of them did not mean anything to a viewer
     either: "CHANNEL LIVE" describes how the scanner found the match rather
     than anything about watching it, and "LINK UPDATING" is scanner
     vocabulary for "we are still looking". */
  return ({
    LIVE_NOW: 'সরাসরি',
    CHANNEL_LIVE: 'চ্যানেলে সরাসরি',
    STARTING_SOON: 'শুরু হচ্ছে',
    LINK_UPDATING: 'লিংক খোঁজা হচ্ছে',
    UPCOMING: 'আসছে',
    TIME_UNVERIFIED: 'সময় নিশ্চিত নয়',
    ENDED: 'শেষ'
  })[status] || 'আসছে';
}

function eventUiFingerprint() {
  if (state.view !== VIEW.UPCOMING && state.view !== VIEW.EVENT) return '';
  return state.currentItems.map((item) => `${item._uid}:${eventUiStatus(item)}`).join('|');
}

/* The data's own age, shown to the viewer.

   Every published file carries `updated_at` and the page read it only to
   decide whether to re-render, so a list served from the service worker
   cache after a failed fetch looked exactly as current as a fresh one. The
   scan runs every twenty minutes, so anything past forty is behind - and
   saying so is the difference between a stale card and a wrong one. */
const DATA_AGE_STALE_MINUTES = 40;

function dataAgeMinutes() {
  const stamp = state.manifest?.updated_at || state.manifestVersion;
  const at = stamp ? Date.parse(stamp) : NaN;
  if (!Number.isFinite(at)) return null;
  return Math.max(0, Math.round((Date.now() - at) / 60000));
}

function dataAgeLabel(minutes) {
  if (minutes === null) return "";
  if (minutes < 1) return "এইমাত্র আপডেট";
  if (minutes < 60) return `${minutes} মিনিট আগে আপডেট`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ঘণ্টা আগে আপডেট`;
  return `${Math.floor(hours / 24)} দিন আগে আপডেট`;
}

function renderDataFreshness() {
  const node = $("dataFreshness");
  if (!node) return;
  // Not on Movies. It shares the one header row with the movie count, which
  // is the pair that collided, and the owner does not need a movie catalogue
  // stamped with how many minutes ago it was written - unlike a live match
  // list, where the age of the data is the whole point.
  if (state.view === VIEW.MOVIE) {
    node.hidden = true;
    return;
  }
  const minutes = dataAgeMinutes();
  const label = dataAgeLabel(minutes);
  if (!label) {
    node.hidden = true;
    return;
  }
  const stale = minutes >= DATA_AGE_STALE_MINUTES;
  node.textContent = stale ? `${label} · পুরোনো` : label;
  node.classList.toggle("stale", stale);
  node.hidden = false;
}

function refreshEventCardsForClock() {
  // Requirement 10. A hidden tab keeps decoding audio but has no visible
  // clock to update, so the tick stops rather than re-laying out the list
  // every 30 seconds in the background.
  if (document.hidden) return;
  if (state.view !== VIEW.UPCOMING && state.view !== VIEW.EVENT) return;
  const nextFingerprint = eventUiFingerprint();
  if (nextFingerprint === state.eventUiFingerprint) {
    updateEventCardClocks();
    return;
  }
  state.eventUiFingerprint = nextFingerprint;
  renderCurrentList(true, { preserveScroll: true });
}


// ── Requirements 7, 8 and 14: playback survives a catalogue refresh ────────

// The session the viewer started. It is pinned the moment playback begins and
// released only when they choose something else, so no amount of background
// scanning, republishing, reordering or promotion can disturb it.
function pinPlaybackSession(item) {
  if (!item) return;
  state.pinnedSession = {
    uid: item._uid,
    id: item.id || '',
    name: item.name || '',
    url: item.url || '',
    playbackId: item.playback_id || '',
    snapshot: item
  };
}

function releasePlaybackSession() {
  state.pinnedSession = null;
}

function isPinnedSession(item) {
  const pinned = state.pinnedSession;
  if (!pinned || !item) return false;
  return item._uid === pinned.uid
    || (Boolean(pinned.id) && item.id === pinned.id)
    || (Boolean(pinned.playbackId) && item.playback_id === pinned.playbackId);
}

// Requirement 14: while the pinned stream is still the one playing, a newly
// ranked primary updates the card's backup ordering but does not replace the
// URL under an active session. Requirement 7: if the event has left the
// catalogue entirely, its card is kept so playback is not orphaned.
function preservePlayingSession(nextItems) {
  const pinned = state.pinnedSession;
  if (!pinned || !Array.isArray(nextItems)) return nextItems;
  const video = $('videoPlayer');
  const stillPlaying = Boolean(video) && !video.ended && (video.currentTime > 0 || !video.paused);
  if (!stillPlaying) return nextItems;

  const match = nextItems.find((item) => isPinnedSession(item));
  if (!match) {
    const carried = { ...pinned.snapshot, _carried_pinned_session: true };
    return [carried, ...nextItems];
  }
  if (pinned.url && match.url && match.url !== pinned.url) {
    // Keep the working URL; the fresh ranking still arrives as backups.
    const rerankedPrimary = {
      name: 'Backup-0',
      url: match.url,
      headers: match.headers || {},
      header_profile: match.header_profile || '',
      proxy_mode: match.proxy_mode || 'auto',
      stream_type: match.stream_type || '',
      verification_status: match.verification_status || '',
      publish_allowed: true
    };
    match.backups = [rerankedPrimary, ...(match.backups || [])].slice(0, 5);
    match.url = pinned.url;
    match.playback_id = pinned.playbackId || match.playback_id;
    match._pinned_primary = true;
  }
  return nextItems;
}

// Requirement 8. Keyed reconciliation: cards that are unchanged keep their DOM
// node, new ones are inserted in place, departed ones are removed - and the
// card that is playing is never re-created.
function reconcileEventCards() {
  if (state.view !== VIEW.UPCOMING && state.view !== VIEW.EVENT) {
    renderCurrentList(true, { preserveScroll: true });
    return;
  }
  applyFilterAndSort();
  renderEventSportFilter();

  // Section 18. The keyed node is the shell when there is one, because that is
  // what the list lays out - keying on the inner card would re-parent the card
  // out of its own shell and orphan its channel strip.
  const existing = new Map(
    qsa('[data-event-shell], .event-ref-card[data-uid]', sidebarList)
      .filter((node) => node.classList.contains('event-card-shell')
        || !node.closest('.event-card-shell'))
      .map((node) => [node.dataset.uid, node])
  );
  // Section 18. A selection whose channel has genuinely gone is retired here,
  // before anything is drawn, so the strip and the playback plan agree.
  pruneStaleChannelSelections(state.filteredItems);
  // Nothing on screen yet, or nothing left to show: the full render owns both
  // the first paint and the empty-state message, so hand back to it. Diffing an
  // empty list would leave a blank panel with no explanation.
  if (!existing.size || !state.filteredItems.length) {
    renderCurrentList(true, { preserveScroll: true });
    return;
  }

  const wanted = state.filteredItems.slice(0, Math.max(state.renderedCount, CHANNEL_INITIAL_CHUNK));
  const isTodayMatch = state.view === VIEW.EVENT;
  if (isTodayMatch) ensureTodayGrid();
  const fragment = document.createDocumentFragment();
  state.renderedUids.clear();

  wanted.forEach((item, index) => {
    const target = fragment;
    const previous = existing.get(item._uid);
    if (previous && isPinnedSession(item)) {
      // The playing card keeps its exact node: no innerHTML, no listeners
      // rebuilt, nothing for the player to notice. Section 18 extends that to
      // the channel strip - the chip the viewer chose keeps its DOM and its
      // selected state, so a background refresh cannot un-choose it.
      const inner = previous.classList.contains('event-ref-card')
        ? previous
        : previous.querySelector('.event-ref-card');
      const numbering = inner?.querySelector('.sidebar-channel-num, .tm-serial');
      if (numbering) numbering.textContent = String(index + 1);
      if (inner) inner.dataset.itemIndex = String(index);
      previous.dataset.itemIndex = String(index);
      updateEventChannelStrip(previous, item);
      target.appendChild(previous);
      existing.delete(item._uid);
      state.renderedUids.add(item._uid);
      return;
    }
    target.appendChild(createEventCard(item, index));
    state.renderedUids.add(item._uid);
    if (previous) existing.delete(item._uid);
  });

  existing.forEach((node) => node.remove());
  sidebarList.replaceChildren(fragment);
  if (isTodayMatch) {
    qsa(':scope > .poster-card', sidebarList).forEach(watchTodayCardForMasonry);
    scheduleTodayMasonry();
  }
  state.renderedCount = state.renderedUids.size;
  updateFavoriteUi();
  updateReminderUi();
  updateActiveCards();
  setEventListCount();
}

// Requirement 15. The scanner publishes each snapshot into its own versioned
// slot and then moves one pointer - data/manifest.json - with a single rename.
// Re-reading that pointer before every refresh is what makes this reader see
// either the whole previous snapshot or the whole new one: the event URL it
// follows always comes from one single read of one single file. Keeping the URL
// captured at page load would instead pin the tab to a snapshot that gets
// recycled a few scans later.
async function resolveEventSnapshotPath(expectedView, fallbackPath) {
  const pointerPath = state.runtime?.data_manifest || '/data/manifest.json';
  try {
    const manifest = await fetchJson(pointerPath, { cache: 'no-store', fresh: true, timeoutMs: 5000 });
    if (!manifest || typeof manifest !== 'object') return fallbackPath;
    const entry = expectedView === VIEW.UPCOMING ? manifest.upcoming : manifest.today_match;
    const next = entry && typeof entry.url === 'string' ? entry.url.trim() : '';
    if (!next) return fallbackPath;
    state.manifest = manifest;
    state.manifestVersion = String(manifest.updated_at || state.manifestVersion);
    renderDataFreshness();
    return next;
  } catch (error) {
    // An unreachable pointer must not stop the refresh; the snapshot already in
    // use stays valid until it is recycled.
    console.debug('Snapshot pointer unavailable, keeping current path', error?.message || error);
    return fallbackPath;
  }
}

async function refreshActiveEventCatalogue() {
  // Requirement 10: no catalogue fetch or re-render behind a hidden tab.
  if (document.hidden) return;
  if (state.eventCatalogRefreshActive) return;
  if (state.view !== VIEW.UPCOMING && state.view !== VIEW.EVENT) return;
  const path = state.currentDataPath;
  if (!path) return;
  const expectedView = state.view;
  state.eventCatalogRefreshActive = true;
  try {
    const snapshotPath = await resolveEventSnapshotPath(expectedView, path);
    if (state.view !== expectedView || state.currentDataPath !== path) return;
    const data = await fetchJson(snapshotPath, { cache: 'no-store', fresh: true, timeoutMs: 7000 });
    if (state.view !== expectedView || state.currentDataPath !== path) return;
    state.currentDataPath = snapshotPath;
    const raw = Array.isArray(data) ? data : (data.channels || data.items || data.events || []);
    let nextItems = normalizeList(raw, expectedView);
    // What makes this card different from the one on screen?
    //
    // The old list was _uid, url, status, start_time and end_time - and an
    // event's playable identity is in none of them. Most event cards carry an
    // empty top-level url and are played through playback_id and channels[],
    // so a scan that swapped a dead route for a working one, or attached a
    // stream to a fixture that had none, produced a byte-identical signature
    // and the refresh returned without touching the UI. The viewer sat looking
    // at Waiting, or at a route already known to be dead, until they reloaded
    // by hand. That is the exact repair this project just built, arriving in
    // the file and stopping at the browser.
    const signature = (items) => JSON.stringify(items.map((item) => [
      item._uid,
      item.url,
      item.status,
      item.start_time,
      item.end_time,
      // playable identity
      item.playback_id,
      item.metadata_only,
      item.verification_status,
      item.available_link_count,
      item.default_channel_id,
      (item.channels || []).map((channel) => [channel.id, channel.playback_id, channel.url]),
      (item.backups || []).map((backup) => backup.playback_id || backup.url)
    ]));
    if (signature(nextItems) === signature(state.currentItems)) return;

    // Requirement 7 and 14. A background refresh must never take the playing
    // event off the list or swap the stream under it. If the new catalogue no
    // longer carries what is on screen, the pinned session is kept in place;
    // if it carries it with a different primary, the URL that is actually
    // playing is preserved so hls.js is never asked to re-attach.
    nextItems = preservePlayingSession(nextItems);

    const scrollTop = getSidebarScrollTop();
    state.currentItems = nextItems;
    // Requirement 8. Diff the list against what is on screen instead of
    // rebuilding it, so the playing card's DOM - and the player - are untouched.
    reconcileEventCards();
    restoreSidebarScroll(scrollTop);
  } catch (error) {
    console.debug('Event catalogue refresh deferred', error?.message || error);
  } finally {
    state.eventCatalogRefreshActive = false;
  }
}

// Guide 30, 31 and 32. Level 1 information (title, status, time, action) is
// always on the card; level 2 appears when the data exists; level 3 stays in
// the details popup. The two card kinds share one shape so the list keeps a
// single rhythm, and differ by accent, by which clock is emphasised and by
// which action they offer.
// ── Card design sections 4-9: the compact channel selector ────────────────
// Everything below reads the published channels[] / streams[] contract and
// nothing else. No channel is invented, no count is guessed, and no raw URL,
// header, cookie, token or DRM field is ever read - section 16.

// Section 5. The chip's one-line summary, built from the stream roles the
// scanner published rather than from any assumption about how many there are.
function channelChipSummary(channel) {
  const streams = Array.isArray(channel?.streams) ? channel.streams : [];
  const primary = streams.filter((entry) => entry?.role === 'primary').length
    || (Number(channel?.stream_count) > 0 ? 1 : 0);
  const backups = Number.isFinite(Number(channel?.backup_count))
    ? Math.max(0, Number(channel.backup_count))
    : Math.max(0, streams.length - primary);
  // Only the scanner can know how many exact duplicates it removed, so the note
  // appears only when it actually reported some.
  const dupes = Math.max(0, Number(channel?.dropped_variant_count) || 0);
  // Section 5 lists both counts, and both are shown - including "0 Backups".
  // Dropping the zero read tidier and was wrong: it is the design document's
  // requirement, twenty-four viewport assertions check for it, and the reason it
  // was cramped (three chips squeezed into a 375px strip) is fixed properly by the
  // 150px column floor rather than by removing information.
  const parts = [
    { cls: 'event-channel-chip-primary', text: `${primary} Primary` },
    { cls: 'event-channel-chip-backups', text: `${backups} ${backups === 1 ? 'Backup' : 'Backups'}` }
  ];
  if (dupes > 0) {
    parts.push({ cls: 'event-channel-chip-dupes', text: `${dupes} Dupes removed` });
  }
  return parts;
}

// Section 5. A small icon: the channel's own logo when it published one,
// otherwise its initials. Never a provider name, never a renderer label.
function channelChipIconHtml(channel) {
  const logo = String(channel?.logo || '').trim();
  if (/^https?:\/\//i.test(logo)) {
    return `<span class="event-channel-chip-icon"><img src="${escapeHtml(logo)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-channel-art="1"></span>`;
  }
  const name = String(channel?.name || '').trim();
  const words = name.replace(/[^A-Za-z0-9\s]/g, ' ').split(/\s+/).filter(Boolean);
  let initials = '?';
  if (words.length === 1) {
    initials = words[0].slice(0, 2).toUpperCase();
  } else if (words.length > 1) {
    // A trailing feed number is the part that tells two feeds of one
    // broadcaster apart, so it is kept in preference to a second initial.
    const last = words[words.length - 1];
    initials = /^\d+$/.test(last)
      ? `${words[0][0]}${last}`.toUpperCase()
      : words.slice(0, 2).map((word) => word[0]).join('').toUpperCase();
  }
  return `<span class="event-channel-chip-icon">${escapeHtml(initials)}</span>`;
}

// Section 9. No reliable channel name means no selector area at all - not an
// empty box, not a placeholder bar, not a fake name. The main card stays exactly
// as it is. Section 13 says the same for an Upcoming card with nothing attached
// yet, and the same emptiness answers both.
// The quality band a single-channel button shows beside its name - FHD, HD, or
// the raw height when it is neither. Read from the channel's own primary
// stream, where the scanner records it; the card-level copy can belong to a
// different channel once there is more than one.
function channelQualityBand(channel) {
  const streams = Array.isArray(channel?.streams) ? channel.streams : [];
  const primary = streams.find((entry) => entry?.role === 'primary') || streams[0] || {};
  const height = Number(
    primary.resolution_height || primary.height || channel?.resolution_height || 0
  );
  if (height > 0) return movieQualityTitle(height);
  const declared = String(primary.resolution || channel?.resolution || '').trim();
  return /^(4K|FHD|HD|SD)$/i.test(declared) ? declared.toUpperCase() : '';
}

function eventChannelStripHtml(item, minimal = false) {
  const channels = eventChannels(item);
  // Today Match's minimal strip, by direct request: name only, no icon, no
  // Primary/Backups summary - and no grid at all for a single channel, which
  // is shown as the card's plain default rather than a one-button row.
  if (minimal) {
    // One channel used to render nothing at all, so a card with a single
    // source showed no name and no play affordance - and the sources are not
    // interchangeable: Tapmad, Sony Sports Ten 5 and Willow differ in quality
    // and in whether they work at all. It gets one full-width button now, in
    // the same chip the multi-channel strip uses, labelled with the play glyph,
    // the channel and its quality band.
    //
    // Everything about the two-or-more case is untouched, deliberately: same
    // markup, same class, same grid, same selection behaviour.
    if (channels.length < 1) return '';
    const active = activeChannelId(item);
    if (channels.length === 1) {
      const only = channels[0] || {};
      const name = String(only.name || '').trim() || 'Stream';
      const quality = channelQualityBand(only);
      const label = quality ? `${name} ${quality}` : name;
      const selected = String(only.id) === String(active);
      return `<div class="event-channel-strip tm-channels tm-channels-one" data-channel-strip="1" role="group" aria-label="Channel options">`
        + `<button type="button" class="event-channel-chip tm-channel tm-channel-solo${selected ? ' is-selected' : ''}"`
        + ` data-channel-id="${escapeHtml(String(only.id))}"`
        + ` title="${escapeHtml(label)}"`
        + ` aria-pressed="${selected ? 'true' : 'false'}"`
        + ` aria-label="${escapeHtml(label)}">▶ ${escapeHtml(label)}</button>`
        + `</div>`;
    }
    const chips = channels.map((channel) => {
      const selected = String(channel.id) === String(active);
      const label = String(channel.name || '').trim();
      return `<button type="button" class="event-channel-chip tm-channel${selected ? ' is-selected' : ''}"
        data-channel-id="${escapeHtml(String(channel.id))}"
        title="${escapeHtml(label)}"
        aria-pressed="${selected ? 'true' : 'false'}"
        aria-label="${escapeHtml(label)}">${escapeHtml(label)}</button>`;
    }).join('');
    return `<div class="event-channel-strip tm-channels" data-channel-strip="1" role="group" aria-label="Channel options">${chips}</div>`;
  }
  if (channels.length < 1) return '';
  const active = activeChannelId(item);
  const columns = Math.min(4, Math.max(1, channels.length));
  const chips = channels.map((channel) => {
    const selected = String(channel.id) === String(active);
    const summary = channelChipSummary(channel)
      .map((part) => `<span class="${part.cls}">${escapeHtml(part.text)}</span>`)
      .join('');
    const label = String(channel.name || '').trim();
    return `<button type="button" class="event-channel-chip${selected ? ' is-selected' : ''}"
      data-channel-id="${escapeHtml(String(channel.id))}"
      title="${escapeHtml(label)}"
      aria-pressed="${selected ? 'true' : 'false'}"
      aria-label="${escapeHtml(label)}">${channelChipIconHtml(channel)}<span class="event-channel-chip-name">${escapeHtml(label)}</span><span class="event-channel-chip-sub">${summary}<span class="event-channel-chip-eq" aria-hidden="true"><i></i><i></i><i></i></span></span></button>`;
  }).join('');
  return `<div class="event-channel-strip" data-channel-strip="1" data-columns="${columns}" role="group" aria-label="Channel options">${chips}</div>`;
}

// Section 6/8/18. Selection and playing state are refreshed in place. Rebuilding
// the strip on every selection would throw away the DOM the viewer just touched
// and, on the playing card, the node the player is bound to.
function updateEventChannelStrip(shell, item) {
  const strip = shell?.querySelector('[data-channel-strip]');
  if (!strip) return;
  const active = activeChannelId(item);
  const playingHere = isPinnedSession(item) || item?._uid === state.currentItem?._uid;
  qsa('.event-channel-chip', strip).forEach((chip) => {
    const selected = chip.dataset.channelId === String(active);
    chip.classList.toggle('is-selected', selected);
    chip.classList.toggle('is-playing', selected && Boolean(playingHere));
    chip.setAttribute('aria-pressed', selected ? 'true' : 'false');
  });
}

// Section 18. The channel list can change under a card between scans. An
// existing selection that still names a healthy published channel is kept; only
// a selection whose channel has genuinely gone is dropped, and the scanner's own
// default takes over from there.
function pruneStaleChannelSelections(items) {
  if (!Array.isArray(items) || !state.channelSelection) return;
  let changed = false;
  items.forEach((item) => {
    const key = eventChannelId(item);
    const chosen = state.channelSelection[key];
    if (!chosen) return;
    const channels = eventChannels(item);
    // No channels published this scan is not evidence the choice is stale - the
    // event may simply be between snapshots. Only a populated list that omits
    // the choice retires it.
    if (!channels.length) return;
    if (!channels.some((entry) => String(entry.id) === String(chosen))) {
      delete state.channelSelection[key];
      changed = true;
    }
  });
  if (changed) writeJsonStorage(STORAGE_KEYS.channelSelection, state.channelSelection);
}

// Section 7. A click selects the channel group and starts its Primary; the
// player's own plan handles Primary -> that channel's Backups. Nothing here
// refetches the catalogue or rebuilds the list, so the click costs one playback
// start and nothing else.
function bindEventChannelStrip(shell, item) {
  const strip = shell?.querySelector('[data-channel-strip]');
  if (!strip) return;
  strip.addEventListener('click', async (event) => {
    const chip = event.target?.closest?.('.event-channel-chip');
    if (!chip || !strip.contains(chip)) return;
    // The chip is its own control: the click must not also be read as "play the
    // event card", which would restart playback on the default channel.
    event.preventDefault();
    event.stopPropagation();
    const channelId = chip.dataset.channelId;
    if (!channelId) return;
    // Immediate feedback the click registered at all - without it, a switch
    // into a stream that takes a moment to decode its first frame looks
    // indistinguishable from "nothing happened, still the old channel".
    qsa('.event-channel-chip', strip).forEach((entry) => entry.classList.remove('is-switching'));
    chip.classList.add('is-switching');
    const ok = await selectEventChannel(eventChannelId(item), channelId);
    chip.classList.remove('is-switching');
    if (!ok) return;
    updateEventChannelStrip(shell, item);
  });
  qsa('img[data-channel-art]', strip).forEach((image) => {
    image.addEventListener('error', () => {
      // A broken channel logo falls back to that channel's initials rather than
      // leaving a hole in the chip.
      const chip = image.closest('.event-channel-chip');
      const holder = image.parentElement;
      const channel = eventChannels(item)
        .find((entry) => String(entry.id) === String(chip?.dataset.channelId));
      if (holder && channel) holder.replaceWith(...htmlToNodes(channelChipIconHtml(channel)));
    }, { once: true });
  });
}

// Today Match redesign, by direct request: a minimal poster-led card -
// serial badge, category badge, league name, title, channel buttons and
// nothing else. Everything the full card also carries (status pill,
// countdown, clock, stream summary, verification tick, watch/favorite
// actions) is left out on purpose, not merely hidden by CSS - the request
// was for those fields to not exist on this card at all. Scoped to the
// Today Match tab for every match state (see the call site in createEventCard),
// so the Upcoming tab keeps its existing card, unchanged, in every respect.
// What a Today Match card must say about itself now that the tab holds matches
// that have not started.
//
// The card was deliberately minimal, and that was right while everything on the
// tab was live: a LIVE badge on every card is decoration, not information. It
// stops being right the moment a fixture arrives 30 minutes before kickoff,
// because a card that looks exactly like a live one and is not is simply
// telling the viewer something untrue.
//
// Three states, and the difference between the last two matters to someone
// deciding whether to wait: a stream that is ready, versus one still being
// looked for.
function todayCardState(item) {
  const status = eventUiStatus(item);
  if (status === 'LIVE_NOW' || status === 'CHANNEL_LIVE') {
    return { tone: 'live', label: 'LIVE' };
  }
  const countdown = eventCountdownTextBn(item) || eventCountdownText(item);
  if (!countdown) {
    // Kickoff has passed but the scanner has not called it live yet.
    return isPlayable(item)
      ? { tone: 'ready', label: 'লিংক আছে' }
      : { tone: 'waiting', label: 'লিংক খোঁজা হচ্ছে' };
  }
  return isPlayable(item)
    ? { tone: 'ready', label: `লিংক আছে • ${countdown}` }
    : { tone: 'soon', label: countdown };
}

/* The finalised Today Match card, ported from the owner's design file.
 *
 * A poster-led card: artwork, status ribbon, serial, sport badge, then the
 * league, the title, a gold rule and the channel buttons - and nothing else.
 * No verification badge, resolution, source or stream count reaches the
 * viewer; those stay in the data where they belong.
 */
function todayRibbon(item) {
  const state = todayCardState(item);
  if (state.tone === 'live') return { className: 'ribbon', label: 'Live' };
  // The two non-live ribbons the design defines, and they mean different
  // things: one is a kickoff that has passed with no link yet, the other is a
  // match about to start.
  if (state.tone === 'waiting') {
    return { className: 'ribbon updating', label: 'লিংক আপডেট হচ্ছে' };
  }
  return { className: 'ribbon updating', label: 'শুরু হচ্ছে' };
}

function todayPosterHtml(item, parts) {
  const artwork = eventArtworkChain(item);
  const first = artwork[0] || '';
  if (!first) {
    return `<div class="poster"><span class="beam-bg"></span>${eventArtFallbackHtml(item, parts)}</div>`;
  }
  const rest = artwork.slice(1);
  return `<div class="poster has-img" style="background-image:url('${escapeHtml(first)}');">`
    + `<img class="poster-fit-image" src="${escapeHtml(first)}" alt="" loading="lazy"`
    + ` decoding="async" referrerpolicy="no-referrer" data-event-art`
    + ` data-art-fallbacks="${escapeHtml(JSON.stringify(rest))}">`
    + `</div>`;
}

function todayChannelPillsHtml(item) {
  const channels = eventChannels(item);
  if (!channels.length) {
    // "শীঘ্রই যোগ হবে" is a promise about the future, and after kickoff it is
    // not one this card can keep. Measured 2026-09-03: Kashi Rudras vs Noida
    // Kings started at 8:00 PM BDT with channels:[] and metadata_only, and at
    // 8:41 PM the pill still said a channel would be added soon.
    const started = Boolean(eventLivePhaseText(item));
    return started
      ? '<span class="channel-pill muted">চ্যানেল এখনো পাওয়া যায়নি</span>'
      : '<span class="channel-pill muted">চ্যানেল শীঘ্রই যোগ হবে</span>';
  }
  return channels.map((channel) => {
    const label = String(channel?.name || '').trim() || 'Server';
    return `<span class="channel-pill" role="button" tabindex="0"`
      + ` data-channel-id="${escapeHtml(String(channel?.id ?? ''))}"`
      + ` title="${escapeHtml(label)}">${escapeHtml(label)}</span>`;
  }).join('');
}

/* Only one Today channel button is green at a time, across every card on the
 * tab - the green means "this is what the player is showing", and the player
 * shows one thing.
 */
function markActiveTodayChannel(pill) {
  qsa('#sidebarList .channel-pill.active-channel').forEach((entry) => {
    entry.classList.remove('active-channel');
  });
  if (pill) pill.classList.add('active-channel');
}

function bindTodayChannelPills(card, item) {
  const lower = card.querySelector('.card-lower');
  if (!lower) return;
  const activate = async (pill) => {
    if (!pill || pill.classList.contains('muted')) return;
    const channelId = pill.dataset.channelId;
    if (!channelId) return;
    // The green lands immediately, so a stream that takes a moment to decode
    // still looks like the click registered.
    markActiveTodayChannel(pill);
    const ok = await selectEventChannel(eventChannelId(item), channelId);
    if (!ok) pill.classList.remove('active-channel');
  };
  lower.addEventListener('click', (event) => {
    const pill = event.target?.closest?.('.channel-pill');
    if (!pill || !lower.contains(pill)) return;
    // The pill is its own control: the click must not also be read as "play
    // this card", which would restart on the default channel.
    event.preventDefault();
    event.stopPropagation();
    activate(pill);
  });
  lower.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const pill = event.target?.closest?.('.channel-pill');
    if (!pill || !lower.contains(pill)) return;
    event.preventDefault();
    event.stopPropagation();
    activate(pill);
  });
}

function createTodayMatchCardV2(item, visualIndex, ctx) {
  const { card, playable, parts, channelOnly, sport } = ctx;
  card.className = ['sidebar-item event-ref-card tv-focusable poster-card',
    playable ? 'is-playable' : 'is-scheduled'].filter(Boolean).join(' ');
  card.tabIndex = 0;
  card.setAttribute('role', 'button');
  // The state is why a viewer is looking at this card at all - live, waiting
  // for a link, or ready - so a screen reader is told it alongside the name.
  card.setAttribute('aria-label', [
    parts.title,
    parts.competition,
    todayCardState(item)?.label,
    isPlayable(item) ? '' : 'স্ট্রিম এখনো আসেনি'
  ].filter(Boolean).join('. '));
  card.dataset.uid = item._uid;
  card.dataset.itemIndex = String(visualIndex);
  card.addEventListener('focus', () => {
    state.lastFocusedUid = item._uid;
    state.lastFocusedSelector = 'card';
    maybePreconnect(item.url);
  });

  const ribbon = todayRibbon(item);
  card.innerHTML = `
    ${todayPosterHtml(item, parts)}
    <div class="poster-caption">
      ${parts.competition ? `<p class="league-tag">${escapeHtml(parts.competition)}</p>` : ''}
      <h4 class="match-title">${escapeHtml(parts.title)}</h4>
      <div class="gold-rule"></div>
    </div>
    <div class="card-lower">${todayChannelPillsHtml(item)}</div>`;

  const poster = card.querySelector('.poster');
  if (poster) {
    poster.insertAdjacentHTML('beforeend',
      `<span class="${ribbon.className}"><span class="dot"></span>${escapeHtml(ribbon.label)}</span>`
      + `<span class="rank-tag">${visualIndex + 1}</span>`
      + `<span class="sport-tag">${escapeHtml(channelOnly ? 'CHANNEL' : sport.label)}</span>`);
  }

  // Section 10's fallback chain is unchanged - only the frame around it is new.
  const image = qs('img[data-event-art]', card);
  image?.addEventListener('error', () => {
    let remaining = [];
    try { remaining = JSON.parse(image.dataset.artFallbacks || '[]'); } catch (_) { remaining = []; }
    const next = Array.isArray(remaining) ? remaining.shift() : null;
    if (next) {
      image.dataset.artFallbacks = JSON.stringify(remaining);
      image.src = next;
      const frame = image.closest('.poster');
      if (frame) frame.style.backgroundImage = `url('${next}')`;
      return;
    }
    const frame = image.closest('.poster');
    if (frame) {
      frame.classList.remove('has-img');
      frame.style.backgroundImage = '';
      image.remove();
      frame.insertAdjacentHTML('afterbegin',
        `<span class="beam-bg"></span>${eventArtFallbackHtml(item, parts)}`);
    }
  });

  bindTodayChannelPills(card, item);
  if (!eventChannels(item).length) card.classList.add('event-card-no-channels');
  return card;
}

/* The finalised Upcoming Match row, ported from the owner's design file.
 *
 * One universal two-team layout for every fixture - there is no separate
 * single-logo, double-logo or old list variant:
 *
 *     CRICKET                              Womens Asia Cup
 *     [ LOGO 1 ]        ৮:৩০ PM            [ LOGO 2 ]
 *   Sri Lanka Women   ১ ঘণ্টা ১৩ মিনিট পর   Indonesia Women
 *                        VS
 *
 * The category sits above the first logo and the league above the second, so
 * each side reads as one column.
 */
function teamInitials(name) {
  const words = String(name || '').replace(/[^A-Za-z0-9 ]+/g, ' ').trim().split(/\s+/).filter(Boolean);
  if (!words.length) return 'TBD';
  if (words.length === 1) return words[0].slice(0, 3).toUpperCase();
  if (words.length === 2) return (words[0][0] + words[1][0]).toUpperCase();
  return (words[0][0] + words[1][0] + words[words.length - 1][0]).toUpperCase();
}

function splitUpcomingTeams(title) {
  const parts = String(title || '').split(/\s+(?:vs\.?|v)\s+/i);
  if (parts.length >= 2) {
    return [parts[0].trim(), parts.slice(1).join(' vs ').trim()];
  }
  return [String(title || 'Team 1').trim(), 'TBD'];
}

/* A logo box is always the same size. When there is no badge, or the badge
 * fails to load, the same box shows the team's initials rather than
 * collapsing and pulling the row out of alignment. */
function buildLogoBox(url, teamName) {
  const box = document.createElement('div');
  box.className = 'universal-logo';

  if (!url) {
    box.classList.add('initials');
    box.textContent = teamInitials(teamName);
    return box;
  }

  const image = document.createElement('img');
  image.src = url;
  image.alt = '';
  image.loading = 'lazy';
  image.decoding = 'async';
  image.referrerPolicy = 'no-referrer';
  image.addEventListener('error', () => {
    box.replaceChildren();
    box.classList.add('initials');
    box.textContent = teamInitials(teamName);
  });
  box.appendChild(image);
  return box;
}

function createUpcomingTeamRow(item, visualIndex, ctx) {
  const { card, playable, parts, channelOnly, sport } = ctx;
  const [teamOne, teamTwo] = splitUpcomingTeams(parts.title);
  // The design's own priority, in its own order: both published team badges
  // when the fixture has them, otherwise the one image the row does have for
  // the left side, and initials for whatever is still missing. Requiring both
  // badges left 58 of 60 boxes showing initials on the live site, which is not
  // the page the design draws.
  const badges = eventTeamBadges(item);
  const artwork = eventArtworkChain(item);
  const homeBadge = badges?.home || String(item?.home_badge_url || '').trim() || artwork[0] || '';
  const awayBadge = badges?.away || String(item?.away_badge_url || '').trim() || '';

  card.className = [
    'sidebar-item event-ref-card tv-focusable schedule-row universal-team-row',
    playable ? 'is-playable' : 'is-scheduled',
    channelOnly ? 'event-channel-card' : 'event-fixture-card'
  ].filter(Boolean).join(' ');
  card.tabIndex = 0;
  card.setAttribute('role', 'button');
  card.setAttribute('aria-label', [
    parts.title,
    parts.competition,
    eventStatusLabel(eventUiStatus(item)),
    eventCountdownTextBn(item)
  ].filter(Boolean).join('. '));
  card.dataset.uid = item._uid;
  card.dataset.itemIndex = String(visualIndex);
  card.addEventListener('focus', () => {
    state.lastFocusedUid = item._uid;
    state.lastFocusedSelector = 'card';
    maybePreconnect(item.url);
  });

  const left = document.createElement('div');
  left.className = 'universal-side universal-left';
  const category = document.createElement('span');
  category.className = 'universal-category';
  category.textContent = channelOnly ? 'CHANNEL' : String(sport.label || '').toUpperCase();
  left.appendChild(category);
  left.appendChild(buildLogoBox(homeBadge, teamOne));
  const nameOne = document.createElement('div');
  nameOne.className = 'universal-team-name';
  nameOne.textContent = teamOne;
  left.appendChild(nameOne);

  const center = document.createElement('div');
  center.className = 'universal-center';
  const time = document.createElement('div');
  time.className = 'time';
  time.textContent = eventClockTextBn(item);
  center.appendChild(time);
  const countdownText = eventCountdownTextBn(item);
  if (countdownText) {
    const countdown = document.createElement('div');
    countdown.className = 'countdown';
    countdown.dataset.clock = 'countdown';
    countdown.textContent = countdownText;
    center.appendChild(countdown);
  }
  const versus = document.createElement('div');
  versus.className = 'vs-label';
  versus.textContent = 'VS';
  center.appendChild(versus);

  const right = document.createElement('div');
  right.className = 'universal-side universal-right';
  const league = document.createElement('div');
  league.className = 'universal-league';
  league.textContent = parts.competition || '';
  right.appendChild(league);
  right.appendChild(buildLogoBox(awayBadge, teamTwo));
  const nameTwo = document.createElement('div');
  nameTwo.className = 'universal-team-name';
  nameTwo.textContent = teamTwo;
  right.appendChild(nameTwo);

  const chevron = document.createElement('span');
  chevron.className = 'universal-chevron';
  chevron.setAttribute('aria-hidden', 'true');
  chevron.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none"'
    + ' stroke="currentColor" stroke-width="2"><path d="M9 18l6-6-6-6"/></svg>';

  card.replaceChildren(left, center, right, chevron);
  return card;
}

function createEventCard(item, visualIndex) {
  const card = document.createElement('div');
  const playable = isPlayable(item);
  const rawParts = eventDisplayParts(item);
  const parts = { title: stripStreamNoise(rawParts.title), competition: rawParts.competition };
  const channelOnly = isChannelOnlyEventCard(item);
  const sport = eventSport(item);
  const ctx = { card, playable, parts, channelOnly, sport };

  return state.view === VIEW.EVENT
    ? createTodayMatchCardV2(item, visualIndex, ctx)
    : createUpcomingTeamRow(item, visualIndex, ctx);
}

function htmlToNodes(html) {
  const template = document.createElement('template');
  template.innerHTML = html;
  return Array.from(template.content.childNodes);
}

// Guide 17 again: the countdown has to keep moving. Rewriting only the clock
// rows keeps the 30s tick off the render path, so scroll position, focus and
// image decoding all survive untouched.
function updateEventCardClocks() {
  if (state.view !== VIEW.UPCOMING && state.view !== VIEW.EVENT) return;
  qsa('.event-ref-card[data-uid]', sidebarList).forEach((card) => {
    const item = state.currentItems.find((entry) => entry._uid === card.dataset.uid);
    if (!item) return;
    const liveLike = ['LIVE_NOW', 'CHANNEL_LIVE'].includes(eventUiStatus(item));
    const write = (selector, text) => {
      const node = qs(selector, card);
      if (!node) return;
      const icon = qs('i', node)?.outerHTML || '';
      if (text) {
        node.innerHTML = `${icon}${escapeHtml(text)}`;
        node.removeAttribute('hidden');
      } else {
        node.setAttribute('hidden', '');
      }
    };
    const countdown = liveLike ? '' : eventCountdownTextBn(item);
    // The clock chip keeps its verification tick across a tick update.
    const metaNode = qs('[data-clock="meta"]', card);
    if (metaNode && !liveLike) {
      const icon = qs('i', metaNode)?.outerHTML || '';
      const tickHtml = qs('.event-verified-tick', metaNode)?.outerHTML || '';
      metaNode.innerHTML = `${icon}${escapeHtml(eventMetaRowTextBn(item, eventStreamSummary(item)))}${tickHtml}`;
    }
    const scheduleNode = qs('[data-clock="schedule"]', card);
    const tick = scheduleNode ? qs('.event-verified-tick', scheduleNode)?.outerHTML || '' : '';
    write('[data-clock="schedule"]', liveLike ? eventStartedTextBn(item) : '');
    if (tick && scheduleNode && !qs('.event-verified-tick', scheduleNode)) {
      scheduleNode.insertAdjacentHTML('beforeend', tick);
    }
    // On an upcoming card the countdown *is* the status pill, so it is
    // rewritten rather than hidden - hiding it would leave the card with no
    // status at all once a fixture's kickoff passes.
    const countdownNode = qs('[data-clock="countdown"]', card);
    if (countdownNode) {
      const isPill = countdownNode.classList.contains('event-status-pill');
      if (isPill) {
        const label = countdown || eventStatusLabel(eventUiStatus(item));
        countdownNode.classList.toggle('is-countdown', Boolean(countdown));
        const icon = qs('i', countdownNode);
        if (icon) icon.className = `fas ${countdown ? 'fa-hourglass-half' : 'fa-calendar-alt'}`;
        countdownNode.innerHTML = `${qs('i', countdownNode)?.outerHTML || ''}${escapeHtml(label)}`;
      } else {
        write('[data-clock="countdown"]', countdown);
      }
    }
    write('[data-clock="phase"]', liveLike ? eventLivePhaseTextBn(item) : '');
    card.classList.toggle('has-countdown', Boolean(countdown));
  });
}

function createChannelCard(item, visualIndex) {
  if (state.view === VIEW.UPCOMING || state.view === VIEW.EVENT) return createEventCard(item, visualIndex);
  const card = document.createElement('div');
  card.className = `sidebar-item tv-focusable${state.view === VIEW.CHANNEL ? ' channel-ref-card' : ''}`;
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
        <span class="source-route-badges">${playbackBadgesHtml(item)}</span>
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
  const newBadge = movieIsNew(item)
    ? '<span class="movie-new-badge">NEW</span>'
    : '';
  card.innerHTML = `
    <span class="movie-rank-badge">#${visualIndex + 1}</span>
    ${newBadge}
    ${rating}
    ${createImageHtml(item, 'movie-poster')}
    <div class="movie-hover-play"><i class="fas fa-play"></i></div>
    <div class="movie-card-overlay">
      <div class="movie-card-title">${escapeHtml(item.name)}</div>
      <div class="movie-card-year">${escapeHtml(year)}</div>
      <div class="source-route-badges movie-source-route-badges">${playbackBadgesHtml(item)}</div>
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

function closeEventPreview() {
  const preview = $('eventPreviewOverlay');
  if (!preview) return;
  preview.classList.remove('show');
  preview.setAttribute('aria-hidden', 'true');
}

function showEventPreview(item) {
  const preview = $('eventPreviewOverlay');
  if (!preview || !item) return;
  const parts = eventDisplayParts(item);
  const art = $('eventPreviewArt');
  const logo = String(item.logo || '').trim();
  art.replaceChildren();
  art.style.removeProperty('background-image');
  if (logo) {
    art.style.backgroundImage = `linear-gradient(90deg,rgba(8,13,22,.08),rgba(8,13,22,.74)),url("${logo.replace(/["\\]/g, '\\$&')}")`;
  } else {
    const initials = parts.title.split(/\s+/).filter(Boolean).map((word) => word[0]).join('').slice(0, 3).toUpperCase();
    art.innerHTML = `<span>${escapeHtml(initials || 'TV')}</span>`;
  }
  $('eventPreviewTitle').textContent = stripStreamNoise(parts.title);
  $('eventPreviewLeague').textContent = parts.competition || 'Live Sports';
  const countdown = eventCountdownText(item);
  // Two sentences in this panel were fixed text, and once kickoff passed both
  // of them were false: it announced "Upcoming Match" over "Stream link will
  // be added before the match starts" for a match already under way. The
  // elapsed clock is the same one the live badge reads, so the panel now says
  // how long ago the match began instead of only when it was due.
  const startedAgo = countdown ? '' : eventStartedAgoText(item);
  const hasStarted = Boolean(startedAgo);
  qs('span', $('eventPreviewTime')).textContent =
    [eventScheduleText(item), countdown, startedAgo].filter(Boolean).join(' • ');

  const statusEl = $('eventPreviewStatus');
  if (statusEl) {
    statusEl.innerHTML = hasStarted
      ? '<i class="fas fa-circle-play" aria-hidden="true"></i> Match Started'
      : '<i class="far fa-calendar-alt" aria-hidden="true"></i> Upcoming Match';
  }
  const noteEl = $('eventPreviewNote');
  if (noteEl) {
    noteEl.innerHTML = hasStarted
      ? '<i class="fas fa-satellite-dish" aria-hidden="true"></i> Kickoff has passed and no stream link has been found yet. It appears here as soon as one is.'
      : '<i class="fas fa-satellite-dish" aria-hidden="true"></i> Stream link will be added before the match starts.';
  }

  // Guide 29 and 32. The card stays clean; the level 3 facts surface here.
  const facts = $('eventPreviewFacts');
  if (facts) {
    const sport = eventSport(item);
    const streams = eventStreamSummary(item);
    const chips = [
      [sport.icon, sport.label],
      ['fa-circle-check', eventVerificationLabel(item)],
      [streams.ready ? 'fa-circle-play' : 'fa-hourglass-start', streams.text],
      item?.venue ? ['fa-location-dot', cleanDisplayName(item.venue)] : null
    ].filter(Boolean);
    facts.innerHTML = chips
      .map(([icon, text]) => `<span class="event-preview-fact"><i class="fas ${icon}" aria-hidden="true"></i>${escapeHtml(text)}</span>`)
      .join('');
  }
  preview.classList.add('show');
  preview.setAttribute('aria-hidden', 'false');
  showControlsTemporarily();
}

$('eventPreviewClose')?.addEventListener('click', closeEventPreview);

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
  // Card design section 7. The channel strip is its own control surface. Without
  // this the shell's data-uid would catch a click on the strip's padding and
  // restart playback on the default channel - the opposite of what the viewer
  // asked for by reaching into the selector.
  if (!card || event.target.closest('.card-fav-btn, .card-remind-btn, .event-channel-strip')) return;
  const item = state.currentItems.find((entry) => entry._uid === card.dataset.uid);
  if (!item) return;
  if (seriesModule?.handleCatalogClick(item)) return;
  if (!isPlayable(item)) {
    // Guide 18 and 32. Level 3 detail belongs in the popup, so any event card
    // without a link opens it rather than firing a toast that says less.
    if (item._sourceKind === VIEW.UPCOMING || state.view === VIEW.UPCOMING || state.view === VIEW.EVENT) {
      showEventPreview(item);
    } else {
      showToast(item.start_time ? `শুরু হবে: ${item.start_time}` : 'এই ইভেন্ট এখনো শুরু হয়নি');
    }
    return;
  }
  closeEventPreview();
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

// Every scroll event used to start its own recursive rAF chain. Several chains
// then grew the same list at once, which multiplied partially rendered pages.
let sidebarScrollBusy = false;
const SIDEBAR_SCROLL_MAX_STEPS = 60;

async function handleSidebarScroll() {
  if (state.seriesDetailMode || seriesModule?.detailActive) return;
  if (sidebarScrollBusy) return;
  sidebarScrollBusy = true;

  try {
    const mobileFlow = window.matchMedia('(max-width: 1000px)').matches;
    const scrollHost = sidebarScrollArea || sidebarList;
    const nearBottom = () =>
      scrollHost.scrollTop + scrollHost.clientHeight >= scrollHost.scrollHeight - (mobileFlow ? 360 : 260);
    const nextFrame = () => new Promise((resolve) => requestAnimationFrame(resolve));

    for (let step = 0; step < SIDEBAR_SCROLL_MAX_STEPS && nearBottom(); step += 1) {
      if (state.renderedCount < state.filteredItems.length) {
        appendNextChunk();
        if (!mobileFlow) return;
        await nextFrame();
        continue;
      }

      const canLoadMorePages = state.view === VIEW.MOVIE &&
        !state.moviePreviewMode &&
        state.movieIndex &&
        state.moviePageCursor < state.movieIndex.pages.length;
      if (!canLoadMorePages) return;

      const loaded = await loadNextMoviePage();
      if (!loaded || !mobileFlow) return;
      await nextFrame();
    }
  } finally {
    sidebarScrollBusy = false;
  }
}

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

const handleSearch = debounce(async () => {
  if (state.view === VIEW.MOVIE && !state.selectedMovieCategory) return;
  if (state.view === VIEW.MOVIE && state.searchQuery) {
    await preloadAllMoviePagesForSearch();
  }
  renderCurrentList(true);
}, 220);

function openDesktopSearch() {
  if (!desktopSearchWrap || window.matchMedia('(max-width: 980px)').matches) return;
  desktopSearchWrap.classList.add('search-open');
  try { searchInput.focus({ preventScroll: true }); } catch (_) { searchInput.focus(); }
}

function closeDesktopSearchIfEmpty() {
  if (!desktopSearchWrap || searchInput.value.trim()) return;
  desktopSearchWrap.classList.remove('search-open');
}

searchInput.addEventListener('input', (event) => {
  setSearchQuery(event.target.value, searchInput);
  handleSearch();
});
mobileSearchInput.addEventListener('input', (event) => {
  setSearchQuery(event.target.value, mobileSearchInput);
  handleSearch();
});
$('searchBtnSubmit').addEventListener('pointerdown', (event) => {
  if (desktopSearchWrap?.classList.contains('search-open')) return;
  event.preventDefault();
  openDesktopSearch();
});
$('searchBtnSubmit').addEventListener('click', async (event) => {
  event.preventDefault();
  openDesktopSearch();
  setSearchQuery(searchInput.value, searchInput);
  if (state.view === VIEW.MOVIE && state.searchQuery) await preloadAllMoviePagesForSearch();
  renderCurrentList(true);
  openDesktopSearch();
});
document.addEventListener('pointerdown', (event) => {
  if (!desktopSearchWrap?.classList.contains('search-open')) return;
  if (desktopSearchWrap.contains(event.target)) return;
  closeDesktopSearchIfEmpty();
});
searchInput.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (searchInput.value) {
    setSearchQuery('', searchInput);
    searchInput.value = '';
    renderCurrentList(true);
  }
  desktopSearchWrap?.classList.remove('search-open');
  searchInput.blur();
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
  if (!Array.isArray(list)) return [];
  const localAudit = ['127.0.0.1', 'localhost'].includes(location.hostname);
  return list.filter((value) =>
    /^https:\/\//i.test(value) ||
    (localAudit && /^http:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?$/i.test(value))
  );
}

function sourcePlaybackKey(source = {}) {
  return String(source.playback_id || source.url || source.link || source.stream_url || '').trim();
}

function itemPlaybackKey(item = {}) {
  return String(item.id || item.playback_id || item.url || item._uid || '').trim();
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
  const proxyOrigin = String(proxy).replace(/\/$/, '');
  if (source.playback_id) {
    return `${proxyOrigin}/hls?id=${encodeURIComponent(source.playback_id)}`;
  }
  const route = state.runtime?.playback_proxy_route || '/hls?url=';
  let output = `${proxyOrigin}${route}${encodeURIComponent(source.url)}`;
  const profile = source.header_profile || '';
  const type = source.stream_type || inferStreamType(source);
  if (type) output += `&type=${encodeURIComponent(type)}`;
  if (profile) output += `&profile=${encodeURIComponent(profile)}`;
  if (source.inherit_manifest_query) output += '&inherit=1';
  return output;
}

// ---------------------------------------------------------------------------
// Sections 6-14 and 26-30: channels, channel selection, and the embed renderer.
// Additive only. Native playback keeps every route it has today; channels change
// the ORDER those routes are tried in, and an embed is tried only after all of
// them have failed.
// ---------------------------------------------------------------------------

function eventChannels(item) {
  const channels = item?.channels;
  return Array.isArray(channels) ? channels.filter((entry) => entry && entry.id) : [];
}

function eventChannelId(item) {
  return String(item?.id || item?._uid || '');
}

// Section 13. Which channel is in force: the viewer's pick if there is one,
// otherwise the scanner's default.
function activeChannelId(item) {
  const channels = eventChannels(item);
  if (!channels.length) return '';
  const chosen = state.channelSelection[eventChannelId(item)];
  if (chosen && channels.some((entry) => entry.id === chosen)) return chosen;
  const preferred = String(item.default_channel_id || '');
  if (preferred && channels.some((entry) => entry.id === preferred)) return preferred;
  return channels[0].id;
}

function isChannelUserSelected(item) {
  const chosen = state.channelSelection[eventChannelId(item)];
  return Boolean(chosen && eventChannels(item).some((entry) => entry.id === chosen));
}

// Section 14. Selected channel primary, its backups, then the next independent
// healthy channel and its backups. Without a selection the scanner's own channel
// order is used, which already puts one channel's primary before another's.
function channelStreamOrder(item) {
  const channels = eventChannels(item);
  if (!channels.length) return [];
  const selected = activeChannelId(item);
  const ordered = [...channels].sort((left, right) =>
    Number(right.id === selected) - Number(left.id === selected));
  const routes = [];
  ordered.forEach((channel) => {
    (channel.streams || []).forEach((stream) => {
      routes.push({ channel, stream });
    });
  });
  return routes;
}

// Section 27. Native first, always. A channel-ordered native route list, then
// whatever native sources the channels did not mention, and embeds nowhere near
// either of them - they are handled only after every native route has failed.
function orderSourcesByChannel(item, sources) {
  const routes = channelStreamOrder(item);
  if (!routes.length || !Array.isArray(sources) || sources.length < 2) return sources;

  const rank = new Map();
  routes.forEach(({ stream }, index) => {
    const id = String(stream.playback_id || '');
    if (id && !rank.has(id)) rank.set(id, index);
  });
  if (!rank.size) return sources;

  // A source the channel strip does not mention used to sort to 9999 and then
  // fall outside the six-attempt slice - which is where the scanner's own
  // verified primary lives whenever a fixture publishes more channels than the
  // event-level backup cap allows. Measured 2026-08-20 on "Sri Lanka vs India
  // 1st Test": every channel-listed route answered 403/404 and the one route
  // that returned a playable manifest was the event primary, ranked last.
  // Unlisted-but-verified sources are interleaved right after the selected
  // channel's own routes instead of being exiled to the tail.
  const selectedRouteCount = channelStreamOrder(item)
    .filter(({ channel }) => channel.id === activeChannelId(item)).length;
  const fallbackRank = (source) => (
    source.verified === true || String(source.verification_status || '').startsWith('verified')
      ? selectedRouteCount + 0.5
      : 9999
  );
  return [...sources].sort((left, right) => {
    const leftKey = String(left.playback_id || '');
    const rightKey = String(right.playback_id || '');
    const leftRank = rank.has(leftKey) ? rank.get(leftKey) : fallbackRank(left);
    const rightRank = rank.has(rightKey) ? rank.get(rightKey) : fallbackRank(right);
    return leftRank - rightRank;
  });
}

// Section 26. Embed routes, which exist only as the last resort.
function embedRoutes(item) {
  const routes = [];
  const seen = new Set();
  const add = (url, label) => {
    const clean = String(url || '').trim();
    if (!clean || seen.has(clean) || !/^https?:\/\//i.test(clean)) return;
    seen.add(clean);
    routes.push({ url: clean, label: String(label || 'Embed') });
  };
  eventChannels(item).forEach((channel) => {
    (channel.streams || []).forEach((stream) => {
      if (stream.playback_type === 'embed') add(stream.embed_url, channel.name);
    });
  });
  (item?.embed_backups || []).forEach((entry) => add(entry.embed_url, entry.name));
  return routes;
}

// Section 28. The embed renderer lives inside the existing player shell. The
// iframe is absolutely positioned to fill #videoContainer, so the container's
// width, height, aspect ratio and position are exactly what they were - nothing
// here touches the shell's geometry.
function mountEmbedRenderer(route, item) {
  if (!route?.url) return false;
  unmountEmbedRenderer();

  const frame = document.createElement('iframe');
  frame.className = 'embed-renderer';
  frame.id = 'embedRenderer';
  frame.src = route.url;
  frame.allow = 'autoplay; fullscreen; encrypted-media; picture-in-picture';
  frame.allowFullscreen = true;
  frame.setAttribute('referrerpolicy', 'origin-when-cross-origin');
  frame.setAttribute('title', `${item?.name || 'Event'} - ${route.label}`);

  // Section 28: the native renderer is suspended, not destroyed, so switching
  // back does not have to rebuild it.
  try {
    video.pause();
    video.removeAttribute('autoplay');
  } catch (_) { /* a paused element that refuses to pause is still paused enough */ }

  videoContainer.appendChild(frame);
  document.body.classList.add('embed-mode');
  videoContainer.classList.add('embed-active');

  state.embedSession = {
    url: route.url,
    label: route.label,
    itemUid: item?._uid || '',
    startedAt: Date.now(),
    loaded: false
  };

  // Section 30. A cross-origin iframe cannot tell us whether video is playing,
  // and pretending otherwise produces a flapping loop. "load" means the renderer
  // loaded - nothing more is claimed, and nothing auto-switches on a guess.
  frame.addEventListener('load', () => {
    if (state.embedSession && state.embedSession.url === route.url) {
      state.embedSession.loaded = true;
    }
    hidePlayerMessage();
  }, { once: true });

  applyEmbedControlMode(true);
  return true;
}

function unmountEmbedRenderer() {
  const existing = document.getElementById('embedRenderer');
  if (existing) {
    // Blank it before removal so a provider player cannot keep audio alive.
    try { existing.src = 'about:blank'; } catch (_) { /* ignore */ }
    existing.remove();
  }
  document.body.classList.remove('embed-mode');
  videoContainer.classList.remove('embed-active');
  state.embedSession = null;
  applyEmbedControlMode(false);
}

// Section 29. Native-only controls cannot reach inside a cross-origin iframe, so
// in embed mode they are disabled rather than left looking functional. They keep
// their space in the layout - no shift - and are restored on the way back.
function applyEmbedControlMode(active) {
  const nativeOnly = ['qualityBtn', 'networkBtn'];
  nativeOnly.forEach((id) => {
    const control = document.getElementById(id);
    if (!control) return;
    if (active) {
      control.setAttribute('data-embed-disabled', '1');
      control.setAttribute('aria-disabled', 'true');
      control.disabled = true;
    } else if (control.getAttribute('data-embed-disabled')) {
      control.removeAttribute('data-embed-disabled');
      control.removeAttribute('aria-disabled');
      control.disabled = false;
    }
  });
}

function isEmbedActive() {
  return Boolean(state.embedSession);
}

// Section 27 priority 5. Called only when every native route is gone.
function tryEmbedFallback(item, reason) {
  const routes = embedRoutes(item);
  if (!routes.length) return false;
  const attempted = state.embedSession?.url || '';
  const next = routes.find((route) => route.url !== attempted) || routes[0];
  if (!next) return false;
  console.warn('Native routes exhausted, using embed fallback', { reason, label: next.label });
  showPlayerMessage(`${next.label} embed player চালু হচ্ছে…`);
  return mountEmbedRenderer(next, item);
}

// Section 13/14. The channel selector the Card/UI phase will call. Pinning a
// channel restarts playback on it; picking the one already playing does nothing,
// so a stray click cannot interrupt a healthy stream.
async function selectEventChannel(eventId, channelId) {
  const item = state.currentItem && eventChannelId(state.currentItem) === String(eventId)
    ? state.currentItem
    : (state.currentItems || []).find((entry) => eventChannelId(entry) === String(eventId));
  if (!item) return false;
  const channels = eventChannels(item);
  if (!channels.some((entry) => entry.id === String(channelId))) return false;
  if (activeChannelId(item) === String(channelId) && state.currentItem === item && !isEmbedActive()) {
    return true;
  }

  state.channelSelection[eventChannelId(item)] = String(channelId);
  writeJsonStorage(STORAGE_KEYS.channelSelection, state.channelSelection);
  unmountEmbedRenderer();
  await startPlayback(item, true);
  return true;
}

function clearEventChannelSelection(eventId) {
  if (!eventId) return;
  delete state.channelSelection[String(eventId)];
  writeJsonStorage(STORAGE_KEYS.channelSelection, state.channelSelection);
}

window.selectEventChannel = selectEventChannel;
window.clearEventChannelSelection = clearEventChannelSelection;
window.eventChannels = eventChannels;
window.activeChannelId = activeChannelId;
window.isEmbedActive = isEmbedActive;


// Section 14, corrected. A channel is only really selectable if its own
// stream can end up in the attempt list at all. The event-level primary+
// backups list is capped (MAX_PUBLISHED_BACKUPS on the scanner side), but a
// fixture can publish more channels than that cap allows through - this
// widens the candidate pool to every channel's own stream before ordering, so
// a channel beyond the cap is exactly as reachable as one inside it.
function sourcesReachableForEveryChannel(item, rankedSources) {
  const base = Array.isArray(rankedSources) ? rankedSources.slice() : [];
  const known = new Set(
    base.map((source) => String(source.playback_id || source.url || '')).filter(Boolean)
  );
  eventChannels(item).forEach((channel) => {
    (channel.streams || []).forEach((stream) => {
      if (stream.playback_type === 'embed') return; // embeds are never in this list
      const key = String(stream.playback_id || stream.url || '');
      if (!key || known.has(key)) return;
      known.add(key);
      // A minimal, honest source: everything a playback_id-routed attempt
      // needs, and nothing the public channel stream entry does not actually
      // carry (headers/DRM/credentials stay resolved server-side, by design -
      // see scanner/channel_groups.py:_public_stream).
      base.push({
        playback_id: stream.playback_id || '',
        url: stream.url || '',
        stream_type: stream.stream_type || '',
        resolution: stream.resolution || '',
        resolution_height: stream.resolution_height || 0,
        host: stream.host || '',
        verified: Boolean(stream.verified),
        verification_status: stream.verification_status || '',
      });
    });
  });
  return base;
}

function buildAttemptPlan(item) {
  const plan = [];
  const secondSweep = [];
  let preferred = state.routePreferences[itemPlaybackKey(item)] || null;
  const rankedSources = sourcesReachableForEveryChannel(
    item, item._sources?.length ? item._sources : rankSources(item)
  );
  // Sections 14/27. Channel order first, native routes only. The list itself is
  // unchanged - every route the player would have tried is still here, and an
  // embed is not in it at all.
  const channelOrdered = orderSourcesByChannel(item, rankedSources);
  // Section 14, corrected. routePreferences remembers one "this stream worked
  // last time" slot per EVENT, from the older single-source stickiness feature
  // - it knows nothing about channels. Left unscoped, the very first channel
  // that ever succeeded got remembered and then re-sorted back to the front on
  // every later buildAttemptPlan call, silently overriding any other channel
  // the viewer picked afterwards. The memory is honored only when it names a
  // stream that belongs to the channel actually selected right now; otherwise
  // it is set aside so the channel order above is what actually plays. A
  // single-channel item (no channels[] at all - a movie, a plain TV feed) has
  // no "active channel" to check against, so its stickiness is unchanged.
  const channels = eventChannels(item);
  if (preferred?.sourceKey && channels.length) {
    const activeChannel = channels.find((entry) => entry.id === activeChannelId(item));
    const belongsToActiveChannel = Boolean(activeChannel) && (activeChannel.streams || [])
      .some((stream) => sourcePlaybackKey(stream) === preferred.sourceKey);
    if (!belongsToActiveChannel) preferred = null;
  }
  const sources = [...channelOrdered].sort((left, right) => {
    if (!preferred?.sourceKey) return 0;
    return Number(sourcePlaybackKey(right) === preferred.sourceKey) - Number(sourcePlaybackKey(left) === preferred.sourceKey);
  });

  sources.slice(0, 6).forEach((source, sourceIndex) => {
    const sourceUrl = String(source.url || '').trim();
    const playbackId = String(source.playback_id || '').trim();
    if (!sourceUrl && !playbackId) return;
    const healthTarget = sourceUrl || `playback:${playbackId}`;

    const isHttp = sourceUrl.toLowerCase().startsWith('http://');
    const mixedContent = location.protocol === 'https:' && isHttp;
    const configuredMode = source.proxy_mode || inferProxyMode(source, source.header_profile || '');
    const sourceType = source.stream_type || inferStreamType(source);
    const isEvent = item?._sourceKind === VIEW.EVENT || item?._sourceKind === VIEW.UPCOMING ||
      state.view === VIEW.EVENT || state.view === VIEW.UPCOMING;
    const hasDrm = Boolean(item?.drm || source?.drm);
    const protectedSource = Boolean(
      source.protected_source || source.requires_credentials ||
      source.requires_headers || item?.protected_source || item?.requires_credentials || item?.requires_headers
    );

    let mode = configuredMode;
    if (isEvent && sourceType === 'dash' && hasDrm && mode !== 'direct_only') {
      // ClearKey/DRM DASH direct mode অনেক device-এ manifest খুললেও segment-এ আটকে যায়.
      mode = 'proxy_only';
    } else if (protectedSource && !['direct_only', 'proxy_only'].includes(mode)) {
      mode = 'proxy_first';
    }

    // Every scanned source has an ID. Ordinary public sources still start
    // direct; credentialed or URL-hidden sources must use the ID-aware proxy.
    if (playbackId && (!sourceUrl || protectedSource)) mode = 'proxy_only';

    // Two proxies per source doubled the cost of a source that is dead at the
    // origin - both proxies fetch the same upstream URL and both get the same
    // 404. The healthiest proxy goes in the first sweep; the second is
    // appended after every source has had one turn, so a working source is
    // reached before a dead one is retried.
    const proxyDepth = sources.length > 1 ? 1 : 2;
    let proxies = rankHealthyProxies(healthTarget, false).slice(0, proxyDepth);
    if (!proxies.length && mode !== 'direct_only') {
      proxies = rankHealthyProxies(healthTarget, true).slice(0, proxyDepth);
    }
    const secondProxy = proxyDepth === 1
      ? rankHealthyProxies(healthTarget, true).filter((entry) => entry !== proxies[0])[0]
      : null;

    const canDirect = Boolean(sourceUrl) && !mixedContent && mode !== 'proxy_only';
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

    if (secondProxy && mode !== 'direct_only') {
      secondSweep.push({ source, sourceIndex, route: 'proxy', proxy: secondProxy });
    }
  });
  plan.push(...secondSweep);

  const seen = new Set();
  const deduplicated = plan.filter((attempt) => {
    const key = `${attempt.route}:${attempt.proxy || ''}:${attempt.source.playback_id || attempt.source.url}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (!preferred) return deduplicated;
  return deduplicated.sort((left, right) => {
    const score = (attempt) => {
      const sourceMatch = sourcePlaybackKey(attempt.source) === preferred.sourceKey;
      const routeMatch = attempt.route === preferred.route && (attempt.route !== 'proxy' || attempt.proxy === preferred.proxy);
      return sourceMatch && routeMatch ? 0 : sourceMatch ? 1 : 2;
    };
    return score(left) - score(right);
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
      maxBufferLength: isEvent ? 8 : 8,
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
        maxBufferLength: isEvent ? 5 : 6,
        maxMaxBufferLength: isEvent ? 12 : 18,
        maxBufferSize: 14 * 1024 * 1024,
        backBufferLength: 8,
        liveSyncDurationCount: 2,
        liveMaxLatencyDurationCount: 5
      };
    }
    return {
      label: 'Fast Start', lowLatencyMode: isEvent,
      maxBufferLength: isEvent ? 5 : 6,
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
    // Requirement 9. The staged cap below already holds a live stream at its
    // first low stage, but hls.js still chose the *initial* level from a
    // bandwidth guess made before any data arrived - so the very first segment
    // could be a 1080p one and the picture took seconds to appear. Live
    // playback now starts at the lowest level and climbs from measured
    // bandwidth; a movie keeps automatic selection.
    startLevel: isMovie ? -1 : 0,
    // hls.js will not choose an auto level wider than the video element is
    // displayed at when this is on, and the player here is 937 CSS pixels wide
    // on a 1600px window. Measured on Moonbug Kids, whose master offers 240,
    // 360, 480, 720 and 1080: autoLevelCapping sat at level 3 - 1280x720 - with
    // 1920x1080 present and the connection idle. Turning it off in the live
    // page moved it to level 4 and 1920x1080 within seconds.
    //
    // That is the whole reported fault: quality climbs to 720 and stops, while
    // choosing 1080 by hand works, because a manual choice is not an auto one
    // and this cap only applies to auto.
    //
    // It is a sensible default for a small thumbnail and the wrong one here,
    // where the viewer picked this channel to watch it. Bandwidth is still
    // governed by hls.js's own ABR, the startup ladder still opens low so the
    // first frame arrives quickly, and capLevelOnFPSDrop below still protects a
    // device that genuinely cannot decode what it was given.
    capLevelToPlayerSize: false,
    capLevelOnFPSDrop: true,
    maxStarvationDelay: 2.5,
    maxLoadingDelay: 3.5,
    testBandwidth: true,
    abrEwmaDefaultEstimate: startupEstimate,
    abrBandwidthFactor: isMovie ? 0.86 : 0.80,
    abrBandwidthUpFactor: isMovie ? 0.65 : 0.55
  };
}

// Requirement 9, corrected. How many whole segments of reserve a live stream
// needs before playback is safe from an ordinary network hiccup.
//
// Today Match and Upcoming playback ran for roughly eight seconds, froze, ran
// again and froze again, while Live TV on the same connection was fine. The cause
// was here: the live profiles express the reserve in *seconds* with no reference
// to how long a segment is. Event feeds ship four-second segments, so the event
// profile's `maxBufferLength: 5` was one and a quarter fragments - hls.js stopped
// loading ahead at five seconds, playback drained the two fragments it had, and
// any latency spike inside the next four seconds emptied the buffer. Eight
// seconds of media, then a stall, on repeat. Live TV channels use longer
// segments and a non-low-latency profile, which is why they never showed it.
//
// Raising every buffer number would have hidden this rather than fixed it, and
// would have cost startup time on streams that never had the problem. The reserve
// is instead measured in fragments once the playlist states its own segment
// length, and only ever widened - a stream with short segments keeps its fast
// start because three of its fragments really is a small number of seconds.
const LIVE_MIN_BUFFER_SEGMENTS = 3;
const LIVE_MIN_MAX_BUFFER_SEGMENTS = 6;
const LIVE_SEGMENT_AWARE_CEILING_S = 30;

function applySegmentAwareLiveBuffer(hls, details) {
  if (!hls || !hls.config || !details) return false;
  const segment = Number(details.targetduration || 0)
    || Number(details.averagetargetduration || 0)
    || Number(details.fragments?.[0]?.duration || 0);
  if (!Number.isFinite(segment) || segment <= 0) return false;

  const floor = Math.min(
    LIVE_SEGMENT_AWARE_CEILING_S,
    Math.ceil(segment * LIVE_MIN_BUFFER_SEGMENTS),
  );
  const maxFloor = Math.min(
    LIVE_SEGMENT_AWARE_CEILING_S * 2,
    Math.ceil(segment * LIVE_MIN_MAX_BUFFER_SEGMENTS),
  );

  let changed = false;
  if (Number(hls.config.maxBufferLength || 0) < floor) {
    hls.config.maxBufferLength = floor;
    changed = true;
  }
  if (Number(hls.config.maxMaxBufferLength || 0) < maxFloor) {
    hls.config.maxMaxBufferLength = maxFloor;
    changed = true;
  }
  // Chasing the live edge with a reserve this small is what turned an ordinary
  // 250 ms manifest fetch into a visible freeze. Two fragments of target latency
  // is not enough headroom when a fragment is several seconds long.
  if (segment >= 3 && Number(hls.config.liveSyncDurationCount || 0) < 3) {
    hls.config.liveSyncDurationCount = 3;
    changed = true;
  }
  if (segment >= 3 && hls.config.lowLatencyMode) {
    hls.config.lowLatencyMode = false;
    changed = true;
  }
  if (changed) {
    state.playbackDiagnostics.segmentAwareBuffer = {
      segment_seconds: segment,
      max_buffer_length: hls.config.maxBufferLength,
      max_max_buffer_length: hls.config.maxMaxBufferLength,
      live_sync_duration_count: hls.config.liveSyncDurationCount
    };
  }
  return changed;
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
  // Section 28. Embed -> native: the iframe is blanked and removed, so no stale
  // provider session or audio survives the switch, and the native controls come
  // back with it.
  unmountEmbedRenderer();
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

// A source whose first frame costs an extra round-trip before any media is
// fetched: the browser must ask the proxy for the ClearKey keys (/drm?id=),
// configure the CDM, then fetch and decrypt an init segment.
//
// Measured on the deployed site with Star Jalsha, which is one of sixty cards
// carrying clearkey DRM and no URL of its own. Every part of the chain was
// healthy - /drm?id= returned 200 with keys, /hls?id= returned a live MPD -
// and playback still never started. The console said why:
//
//     Trying next playback attempt: Progress stopped: DASH manifest loaded
//     Playback plan exhausted {reason: attempts_exhausted, attempts: 2}
//
// The attempt budget for a DASH channel is 11.5 s measured from the START of
// the attempt. The DRM fetch and the manifest load through the proxy had
// already spent most of it, so when "manifest loaded" extended the deadline
// there were about 1.5 s left - less than the time to request an init segment,
// decrypt it and paint. The video element ended on readyState 0 with no error
// and not one segment requested.
function isProtectedPlaybackSource(source, item) {
  const drm = source?.drm || item?.drm;
  if (drm && typeof drm === 'object' && Object.keys(drm).length) return true;
  if (source?.protected_source || item?.protected_source) return true;
  if (source?.requires_credentials || item?.requires_credentials) return true;
  // URL-hidden sources resolve through /hls?id=, which is a proxy lookup
  // before the upstream fetch even begins.
  const hasUrl = Boolean(source?.url || item?.url);
  const hasId = Boolean(source?.playback_id || item?.playback_id);
  return hasId && !hasUrl;
}

function playbackAttemptBudgetMs(item) {
  const kind = item?._sourceKind || state.view;
  if (kind === VIEW.EVENT || kind === VIEW.UPCOMING) return EVENT_ATTEMPT_BUDGET_MS;
  if (kind === VIEW.MOVIE) return MOVIE_ATTEMPT_BUDGET_MS;
  return isProtectedPlaybackSource(null, item)
    ? PROTECTED_CHANNEL_ATTEMPT_BUDGET_MS
    : CHANNEL_ATTEMPT_BUDGET_MS;
}

function selectWithoutPlaying(item) {
  /* Show what would play, and wait to be asked.

     The first match used to start on page load. That spends the viewer's data
     without being asked, talks over whatever they were already listening to,
     and chooses the match on their behalf. So the card is marked as the
     selection and the player says what it is holding - and one press starts
     it, from a real gesture, which is also what every browser's autoplay
     policy actually wants. */
  if (!item) return;
  state.pendingSelection = item;
  renderCurrentList(false);
  showPlayerMessage(
    `${item.name || 'ম্যাচ'} — চালু করতে চ্যানেলে চাপুন`,
    false
  );
}

async function startPlayback(item, userInitiated = true) {
  if (!item || !isPlayable(item)) return;
  seriesModule?.handlePlaybackSelection?.(item);
  // Requirement 7. From here the session belongs to the viewer: catalogue
  // refreshes, republished JSON, card reordering and Upcoming -> Today
  // promotion all have to work around it.
  pinPlaybackSession(item);
  markPlaybackActive(true);

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

  // Section 27 says embeds are tried only once every native route is gone -
  // correct when an embed is a fallback nobody chose. It stopped being
  // correct the moment a Streamed-provider channel with no native stream of
  // its own became a first-class, directly clickable entry in the channel
  // strip: buildAttemptPlan's pool is every *reachable* channel's native
  // stream, not just the selected one's, so picking that channel explicitly
  // still built a full plan out of every other channel's native routes and
  // played whichever one was healthiest - never what was actually clicked.
  // The selected channel having no native stream at all is checked directly,
  // so its own embed is tried now instead of after failing routes that were
  // never its own.
  const selectedChannel = eventChannels(item).find((entry) => entry.id === activeChannelId(item));
  const selectedChannelEmbedStream = selectedChannel &&
    !(selectedChannel.streams || []).some((stream) => stream.playback_type !== 'embed') &&
    (selectedChannel.streams || []).find((stream) => stream.playback_type === 'embed' && stream.embed_url);
  if (selectedChannelEmbedStream) {
    if (mountEmbedRenderer({ url: selectedChannelEmbedStream.embed_url, label: selectedChannel.name }, item)) {
      return;
    }
  }

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
  const protectedSource = isProtectedPlaybackSource(
    session.currentAttempt?.source,
    session.item,
  );
  const maxTotal = isMovie
    ? (format === 'direct'
      ? (session.currentAttempt?.route === 'direct' ? 42000 : 30000)
      : (session.currentAttempt?.route === 'direct' ? 30000 : 24000))
    : isEvent && format === 'dash'
      ? 15000
      : format === 'dash'
        ? (protectedSource ? 24000 : 11500)
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

// Whether this URL is one of our own playback proxies.
//
// It matters for exactly one reason: our proxy sends
// `Access-Control-Allow-Origin`, and a third-party origin generally does not.
// See applyNativeCrossOrigin below.
function isOwnPlaybackProxyUrl(url) {
  const text = String(url || '');
  if (!text) return false;
  let origin = '';
  try { origin = new URL(text, location.href).origin; } catch (_) { return false; }
  return playbackProxyList().some((proxy) => {
    try { return new URL(proxy, location.href).origin === origin; }
    catch (_) { return false; }
  });
}

// Chrome refuses an opaque media response it cannot sniff.
//
// A .mkv served cross-origin to a plain `<video src>` is a no-cors request, and
// Chrome's Opaque Response Blocking rejects it with ERR_BLOCKED_BY_ORB because
// it cannot confirm `video/x-matroska` is media - Matroska is not a container
// its sniffer knows. The element then reports MEDIA_ELEMENT_ERROR: Format
// error, which looks like a broken file and is not one.
//
// Measured on 2026-08-30 against the deployed site: 1,029 of 1,248 published
// movies failed this way, every one of them with "Browser blocked the media
// response (ORB)". Reproduced in real Chrome on a bare page, and fixed by one
// attribute:
//
//     plain <video src>                     ERR_BLOCKED_BY_ORB, all three cases
//     crossOrigin='anonymous' + our proxy   plays, 1920x1080 and 1280x720
//     crossOrigin='anonymous' + r2 direct   ERR_FAILED - no ACAO on that origin
//
// So the attribute is set for our own proxy, which sends the header, and left
// off for everything else, where setting it would turn a working direct route
// into a CORS failure.
function applyNativeCrossOrigin(url) {
  if (isOwnPlaybackProxyUrl(url)) video.setAttribute('crossorigin', 'anonymous');
  else video.removeAttribute('crossorigin');
}

async function initNative(url, session, attemptToken, type) {
  applyNativeCrossOrigin(url);
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
  hls.on(Hls.Events.LEVEL_LOADED, (_, data) => {
    if (!isActiveAttempt(session, attemptToken) || isMovie) return;
    applySegmentAwareLiveBuffer(hls, data?.details);
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

async function resolveProtectedDrm(session) {
  const attempt = session?.currentAttempt;
  const source = attempt?.source || {};
  const playbackId = String(source.playback_id || session?.item?.playback_id || '').trim();
  const drmHint = meaningfulPlaybackDrm(source.drm || session?.item?.drm || null);
  if (!playbackId || !attempt?.proxy) return drmHint;

  const endpoint = `${String(attempt.proxy).replace(/\/$/, '')}/drm?id=${encodeURIComponent(playbackId)}`;
  const response = await fetch(endpoint, {
    method: 'GET',
    mode: 'cors',
    cache: 'no-store',
    credentials: 'omit'
  });
  if (response.status === 404) return drmHint;
  if (!response.ok) throw new Error(`Protected DRM profile HTTP ${response.status}`);
  const payload = await response.json();
  return meaningfulPlaybackDrm(payload?.drm) || drmHint;
}

function meaningfulPlaybackDrm(drm) {
  if (!drm || typeof drm !== 'object') return null;
  return normalizePlaybackDrmType(drm) ? drm : null;
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

  const playbackDrm = await resolveProtectedDrm(session);
  if (!isActiveAttempt(session, attemptToken)) return;
  const drmType = normalizePlaybackDrmType(playbackDrm);
  if (drmType === 'clearkey') {
    const clearKeys = parseClearKeys(
      playbackDrm?.clear_keys || playbackDrm?.clearkey || playbackDrm?.license_key
    );
    if (!clearKeys) throw new Error('ClearKey DRM keys are missing or invalid');
    player.configure({ drm: { clearKeys } });
  } else if (['widevine', 'playready', 'fairplay'].includes(drmType)) {
    if (!attempt?.proxy) throw new Error(`${drmType} DRM requires a playback proxy`);
    const proxyOrigin = String(attempt.proxy).replace(/\/$/, '');
    const playbackId = String(attempt?.source?.playback_id || session?.item?.playback_id || '').trim();
    if (!playbackId || !playbackDrm?.license_url) {
      throw new Error(`${drmType} license configuration is incomplete`);
    }
    const keySystem = {
      widevine: 'com.widevine.alpha',
      playready: 'com.microsoft.playready',
      fairplay: 'com.apple.fps',
    }[drmType];
    const licenseEndpoint = `${proxyOrigin}/license?id=${encodeURIComponent(playbackId)}`;
    const drmConfig = { servers: { [keySystem]: licenseEndpoint } };
    if (drmType === 'fairplay' && playbackDrm?.certificate_url) {
      drmConfig.advanced = {
        [keySystem]: {
          serverCertificateUri: `${proxyOrigin}/certificate?id=${encodeURIComponent(playbackId)}`,
        },
      };
    }
    player.configure({ drm: drmConfig });
  } else if (playbackDrm && Object.keys(playbackDrm).length) {
    throw new Error('Unsupported or ambiguous DRM type; playback was not guessed');
  }
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

function normalizePlaybackDrmType(drm) {
  const value = String(drm?.type || drm?.scheme || drm?.license_type || '').trim().toLowerCase();
  if (value.includes('widevine') || value === 'com.widevine.alpha') return 'widevine';
  if (value.includes('playready') || value.includes('microsoft')) return 'playready';
  if (value.includes('fairplay') || value.includes('apple.fps') || value.includes('com.apple')) return 'fairplay';
  if (value.includes('clearkey') || value.includes('clear_key')) return 'clearkey';
  return value ? 'unknown' : '';
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

//
// Raw-TS recovery. A restreamed mpegts source is not a segmented playlist: when
// it stalls or ends there is nothing to seek past, so the only recovery is a new
// player on the same url. mpegts.js will not reload an instance in place -
// load() on a loaded player is ignored - so the sequence below is the whole
// point of this function: pause, unload, detach, destroy, create, attach, load,
// play. Anything shorter is the no-op this replaced.
//
// Bounded on purpose. An origin that has genuinely gone away would otherwise be
// hammered forever, and a burst of recreates on one stall looks exactly like a
// working stream to every metric while showing the viewer nothing.
const MPEGTS_RECOVERY_MAX_ATTEMPTS = 4;
const MPEGTS_RECOVERY_BASE_DELAY_MS = 800;
const MPEGTS_RECOVERY_MIN_GAP_MS = 1500;
let mpegtsRecoveryState = { attempts: 0, lastAt: 0, inFlight: false };

function resetMpegTsRecovery() {
  mpegtsRecoveryState = { attempts: 0, lastAt: 0, inFlight: false };
}

async function recreateMpegTsPlayer(reason = 'recovery') {
  const context = state.mpegtsContext;
  if (!context || !context.url) return false;
  if (!isActiveAttempt(context.session, context.attemptToken)) return false;
  if (mpegtsRecoveryState.inFlight) return false;

  const now = Date.now();
  // Duplicate-burst protection: several signals (stall watchdog, error handler,
  // LOADING_COMPLETE) can fire for one underlying event.
  if (now - mpegtsRecoveryState.lastAt < MPEGTS_RECOVERY_MIN_GAP_MS) return false;

  if (mpegtsRecoveryState.attempts >= MPEGTS_RECOVERY_MAX_ATTEMPTS) {
    // Out of retries. This is a real failure of this route, so hand it to the
    // attempt ladder rather than silently doing nothing - the previous code
    // swallowed the exception and left the viewer on a frozen frame.
    failCurrentAttempt(
      `MPEGTS recovery exhausted after ${mpegtsRecoveryState.attempts} attempt(s): ${reason}`,
      context.attemptToken
    );
    return false;
  }

  mpegtsRecoveryState.inFlight = true;
  mpegtsRecoveryState.attempts += 1;
  mpegtsRecoveryState.lastAt = now;
  const attemptNumber = mpegtsRecoveryState.attempts;

  // Exponential backoff, so a flapping origin is not hit four times in a second.
  const delay = MPEGTS_RECOVERY_BASE_DELAY_MS * Math.pow(2, attemptNumber - 1);
  try {
    markAttemptProgress(
      `MPEGTS recovery ${attemptNumber}/${MPEGTS_RECOVERY_MAX_ATTEMPTS} (${reason})`,
      context.attemptToken
    );
  } catch (_) { /* progress reporting must never block a recovery */ }

  const previous = state.mpegts;
  state.mpegts = null;
  // Teardown. Each step is guarded separately: a player that already errored can
  // throw on pause() while still needing unload() and destroy(), and skipping
  // those leaks a worker and a SourceBuffer per attempt.
  for (const step of ['pause', 'unload', 'detachMediaElement', 'destroy']) {
    try { previous?.[step]?.(); } catch (_) { /* continue tearing down */ }
  }

  await new Promise((resolve) => setTimeout(resolve, delay));
  if (!isActiveAttempt(context.session, context.attemptToken)) {
    mpegtsRecoveryState.inFlight = false;
    return false;
  }

  try {
    await initMpegTs(context.url, context.session, context.attemptToken);
    return true;
  } catch (error) {
    // Deliberately NOT swallowed. A failed rebuild is information the attempt
    // ladder needs; hiding it is what made the old recovery path invisible.
    failCurrentAttempt(
      `MPEGTS recovery failed: ${error?.message || error}`,
      context.attemptToken
    );
    return false;
  } finally {
    mpegtsRecoveryState.inFlight = false;
  }
}

async function initMpegTs(url, session, attemptToken) {
  const mpegts = await ensureMpegTsLibrary();
  if (!mpegts.isSupported()) throw new Error('MPEGTS playback supported নয়');
  state.playerType = 'mpegts';
  // A continuous re-streamed mpegts source (no HLS segments to skip past)
  // stalls visibly on any brief hiccup once its buffer runs dry. Chasing
  // live latency by trimming that buffer back down is worth it for a sports
  // event, not for an ordinary TV channel, where a couple of seconds of
  // extra cushion against a shaky upstream matters far more than staying
  // within a second of real time.
  const isLiveEvent = isLiveEventContext(session.item);
  const player = mpegts.createPlayer({
    type: 'mpegts',
    isLive: session.item._sourceKind !== VIEW.MOVIE,
    url
  }, {
    enableWorker: true,
    lazyLoad: true,
    liveBufferLatencyChasing: isLiveEvent,
    stashInitialSize: resolveAutoProfile() === 'lite' ? 128 * 1024 : 1024 * 1024
  });
  state.mpegts = player;
  // Recreating an mpegts player needs the url and the attempt it belongs to.
  // Without them the recovery path could only call load() on the existing
  // instance, which mpegts.js ignores unless the source was unloaded first -
  // measured, and the reason a stalled raw-TS channel never recovered.
  state.mpegtsContext = { url, session, attemptToken };
  player.attachMediaElement(video);
  player.load();
  player.on(mpegts.Events.ERROR, (_, detail) => {
    if (isActiveAttempt(session, attemptToken) && !isQualityLocked()) failCurrentAttempt(detail || 'MPEGTS error', attemptToken);
  });
  // A raw-TS "live" route can return a FINITE body and end cleanly. Measured on
  // the Zee Bangla route: 10.6 MB then a clean early EOF, direct at 2.46 s and
  // through the proxy at 4.46 s against a 60 s probe. Without this handler the
  // stream simply stopped and nothing in the player knew the source had ended,
  // so no recovery was ever attempted.
  if (mpegts.Events.LOADING_COMPLETE) {
    player.on(mpegts.Events.LOADING_COMPLETE, () => {
      if (!isActiveAttempt(session, attemptToken)) return;
      if (session.item?._sourceKind === VIEW.MOVIE) return; // a movie ending is normal
      void recreateMpegTsPlayer('source ended early');
    });
  }
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
    markProxyResult(
      attempt.proxy,
      attempt.source.url || `playback:${attempt.source.playback_id || ''}`,
      false,
      Date.now() - session.attemptStartedAt
    );
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
  if (mode === NETWORK_MODE.BALANCED) return 1.2;
  if (mode === NETWORK_MODE.STABLE) return 3.0;
  return 1.6;
}

function liveStartupBufferMinimumSeconds(item = state.currentItem) {
  const mode = currentNetworkMode();
  const isEvent = isLiveEventContext(item);
  if (mode === NETWORK_MODE.BALANCED) return isEvent ? 0.8 : 0.5;
  if (mode === NETWORK_MODE.STABLE) return isEvent ? 1.9 : 1.6;
  return isEvent ? 1.5 : 0.8;
}

function liveStartupBufferMaximumWaitMs(item = state.currentItem) {
  const mode = currentNetworkMode();
  const isEvent = isLiveEventContext(item);
  if (isEvent) {
    if (mode === NETWORK_MODE.BALANCED) return 2000;
    if (mode === NETWORK_MODE.STABLE) return 4000;
    return 3000;
  }
  if (mode === NETWORK_MODE.BALANCED) return 1400;
  if (mode === NETWORK_MODE.STABLE) return 3300;
  return 1800;
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

  // A live event has no "correct" wait: the manifest either answers in a
  // couple of seconds or the route is gone. Long per-attempt waits do not
  // rescue a dead link, they only stop the next route from being reached
  // inside the session budget, so each route gets just enough time to prove
  // itself and the budget buys breadth instead.
  if (isEvent && isDrmDash) return attempt?.route === 'proxy' ? 7000 : 6000;
  if (isEvent && format === 'dash') return attempt?.route === 'proxy' ? 6500 : 5500;
  if (format === 'dash') return attempt?.route === 'proxy' ? 7000 : 6500;
  if (attempt?.route === 'direct') return attempt?.sourceIndex === 0 ? 3600 : 3800;
  return 4000;
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
    markProxyResult(
      session.currentAttempt.proxy,
      session.currentAttempt.source.url || `playback:${session.currentAttempt.source.playback_id || ''}`,
      true,
      elapsed
    );
  }

  if (state.currentItem && session.currentAttempt?.source?.url) {
    state.currentItem._activeSourceUrl = sourcePlaybackKey(session.currentAttempt.source);
  }

  const preferenceKey = itemPlaybackKey(session.item);
  if (preferenceKey && session.currentAttempt) {
    state.routePreferences[preferenceKey] = {
      sourceKey: sourcePlaybackKey(session.currentAttempt.source),
      route: session.currentAttempt.route,
      proxy: session.currentAttempt.proxy || '',
      updatedAt: Date.now()
    };
    state.routePreferences = Object.fromEntries(
      Object.entries(state.routePreferences)
        .sort((left, right) => Number(right[1]?.updatedAt || 0) - Number(left[1]?.updatedAt || 0))
        .slice(0, 200)
    );
    writeJsonStorage(STORAGE_KEYS.routePreferences, state.routePreferences);
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
  // Playback is genuinely healthy again, so the raw-TS retry budget goes back to
  // full. Without this the budget is spent once and never returns: a channel
  // that recovered four times over an evening would be dropped on its fifth
  // hiccup even though every earlier recovery worked.
  resetMpegTsRecovery();
  finalizePlaybackSuccess(session);
}

function telemetryEndpoint() {
  return String(state.runtime?.telemetry_url || '').trim();
}

async function initializePlaybackTelemetry() {
  state.telemetryEnabled = false;
  const endpoint = telemetryEndpoint();
  if (!endpoint) return;
  let healthUrl;
  try {
    healthUrl = new URL('/health', endpoint).toString();
  } catch (_) {
    return;
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3500);
  try {
    const response = await fetch(healthUrl, { cache: 'no-store', signal: controller.signal });
    if (!response.ok) return;
    const health = await response.json();
    state.telemetryEnabled = Boolean(health?.ok && health?.kv_bound !== false);
  } catch (_) {
    state.telemetryEnabled = false;
  } finally {
    clearTimeout(timer);
  }
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
  if (!endpoint || !state.telemetryEnabled || !item) return;

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
  // Section 27 priority 5. Every native route has failed; an embed backup is the
  // last thing to try, and only now.
  if (session?.item && !isEmbedActive() && tryEmbedFallback(session.item, reason)) {
    return;
  }
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
  if (state.mpegts) {
    // load() on a player that is already loaded does nothing in mpegts.js, so
    // the old body here was a no-op wrapped in a silent catch: it looked like a
    // recovery and never was. A real recovery has to tear the player down and
    // build a new one.
    void recreateMpegTsPlayer(force ? 'forced recovery' : 'stall recovery');
  }
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
  if (badge) {
    badge.style.display = 'none';
    badge.replaceChildren();
  }

  const currentBadge = $('currentResolutionBadge');
  const currentValue = $('currentResolutionValue');
  if (!currentBadge || !currentValue) return;

  const info = currentVariantInfo();
  const label = String(info?.label || '').trim() || 'Original';
  currentValue.textContent = label;
  const bitrate = formatBitrate(info?.bitrate || 0);
  currentBadge.title = bitrate
    ? `Current playback resolution: ${label} (${bitrate})`
    : `Current playback resolution: ${label}`;
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
    const sourceKey = sourcePlaybackKey(source);
    if (!sourceKey) return;
    const height = sourceResolutionHeight(source);
    const codec = sourceCodecName(source);
    const key = `direct:${height || 0}:${codec || 'default'}`;
    const normalized = { ...source, sourceIndex };
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
  const activeSourceKey = String(
    item?._activeSourceUrl ||
    state.playbackSession?.currentAttempt?.source?.url ||
    state.playbackSession?.currentAttempt?.source?.playback_id ||
    item?.url ||
    item?.playback_id ||
    ''
  );
  return groups.find((group) => group.sources.some((source) => sourcePlaybackKey(source) === activeSourceKey)) ||
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
          source.url || `playback:${source.playback_id || ''}`,
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

    // Same opaque-response rule as the video element: a proxied media response
    // has to be fetched in CORS mode or Chrome blocks it before the decoder
    // sees a byte. The companion carries the audio track for a movie, so
    // without this the picture plays and the sound does not.
    if (isOwnPlaybackProxyUrl(attempt.url)) {
      movieAudioCompanion.setAttribute('crossorigin', 'anonymous');
    } else {
      movieAudioCompanion.removeAttribute('crossorigin');
    }
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
  const selectedSourceKeys = new Set(selectedGroup.sources.map((source) => sourcePlaybackKey(source)));
  const preferredSourceUrl = String(options.preferredSourceUrl || '');
  const orderedSources = originalPool
    .filter((source) => selectedSourceKeys.has(sourcePlaybackKey(source)))
    .filter((source) => !options.audioFallback || !isMovieAudioSourceBlocked(current.id, source.url) || source.url === preferredSourceUrl)
    .map((source) => ({ ...source }))
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
  if (!sourcePlaybackKey(primary)) {
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
    url: primary.url || '',
    playback_id: primary.playback_id || '',
    resolution: primary.resolution || primary.label || selectedGroup.label,
    resolution_height: sourceResolutionHeight(primary),
    label: primary.label || primary.resolution || selectedGroup.label,
    codec: primary.codec || selectedGroup.codec || '',
    stream_type: primary.stream_type || current.stream_type,
    header_profile: primary.header_profile || current.header_profile,
    proxy_mode: primary.proxy_mode || current.proxy_mode || 'direct_first',
    force_proxy: Boolean(primary.force_proxy || primary.proxy_required),
    proxy_required: Boolean(primary.proxy_required),
    protected_source: Boolean(primary.protected_source),
    requires_credentials: Boolean(primary.requires_credentials),
    requires_headers: Boolean(primary.requires_headers),
    drm: primary.drm || current.drm || null,
    inherit_manifest_query: Boolean(primary.inherit_manifest_query),
    backups: orderedSources.slice(1, 6).map((source) => ({ ...source })),
    _sources: orderedSources,
    _qualitySourcePool: originalPool,
    _selectedDirectQualityKey: selectedGroup.key,
    _selectedDirectQualityLabel: selectedGroup.label,
    _activeSourceUrl: sourcePlaybackKey(primary)
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
  state.fitIndex = 1;
  video.style.setProperty('object-fit', 'contain', 'important');
  video.style.setProperty('object-position', 'center center', 'important');
}

function setPlayerControlVisible(id, visible, display = 'inline-flex') {
  const control = $(id);
  if (!control) return;
  control.hidden = !visible;
  control.setAttribute('aria-hidden', visible ? 'false' : 'true');
  control.style.setProperty('display', visible ? display : 'none', 'important');
}

function setMovieControlsLocked(locked) {
  state.movieControlsLocked = Boolean(locked);
  videoContainer.classList.toggle('movie-controls-locked', state.movieControlsLocked);
  const button = $('movieLockBtn');
  if (!button) return;
  button.classList.toggle('active', state.movieControlsLocked);
  button.title = state.movieControlsLocked ? 'Unlock controls' : 'Lock controls';
  button.setAttribute('aria-label', button.title);
  const icon = button.querySelector('i');
  if (icon) icon.className = state.movieControlsLocked ? 'fas fa-lock-open' : 'fas fa-lock';
  showControlsTemporarily();
}

function updateContextualPlayerButtons() {
  const isMovie = isMoviePlaybackContext();
  const phonePlayer = isPhoneSizedPlayer();
  const mobileMovie = isMovie && phonePlayer;
  const mobileFullscreen = wrapperFullscreenElement() === videoContainer && phonePlayer;
  // The compact (non-fullscreen) mobile movie bar keeps only play/pause,
  // previous, next, resolution and fullscreen. The rest come back once
  // fullscreen is entered.
  const compactMobileMovie = mobileMovie && !mobileFullscreen;
  document.documentElement.classList.toggle('movie-playback-context', isMovie);
  // The mobile movie transport row is laid out entirely from CSS. This class is
  // what switches it on, so the row keeps one fixed button order.
  document.documentElement.classList.toggle('mobile-movie-controls', mobileMovie);

  setPlayerControlVisible('skipBackBtn', isMovie && !compactMobileMovie);
  setPlayerControlVisible('skipFwdBtn', isMovie && !compactMobileMovie);
  // Speed is a desktop-only control now; the mobile movie transport (compact
  // or fullscreen) never shows it.
  setPlayerControlVisible('speedBtn', isMovie && !mobileMovie);
  setPlayerControlVisible('networkBtn', !isMovie);
  // Screen Fit belongs to the mobile movie transport row and stays a normal
  // desktop control, but only once fullscreen in the compact bar.
  setPlayerControlVisible('aspectBtn', !phonePlayer || mobileFullscreen);
  setPlayerControlVisible('movieLockBtn', mobileMovie && mobileFullscreen);
  // Rotate screen and Picture-in-Picture are not part of the movie player.
  setPlayerControlVisible('movieRotateBtn', false);
  setPlayerControlVisible('pipBtn', false);

  if (isMovie) $('networkMenu')?.classList.remove('show');
  else $('speedMenu')?.classList.remove('show');
}

// Requirement 10. One flag on <body> while a stream is decoding, so the
// stylesheet can stand the expensive decorative work down instead of the
// feature being removed.
function markPlaybackActive(active) {
  document.body.classList.toggle('playback-active', Boolean(active));
}

function setupPlayerUi(item) {
  const isMovie = item._sourceKind === VIEW.MOVIE || state.view === VIEW.MOVIE;
  setMovieControlsLocked(false);
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
  // Card design section 8. The playing event reads as playing on the shell, and
  // its selected chip picks up the equaliser - both in place, so nothing the
  // player is bound to is rebuilt.
  qsa('[data-event-shell]', sidebarList).forEach((shell) => {
    const item = (state.currentItems || []).find((entry) => entry._uid === shell.dataset.uid);
    const playing = shell.dataset.uid === state.currentItem?._uid;
    shell.classList.toggle('is-playing-event', playing);
    if (item) updateEventChannelStrip(shell, item);
  });
  qsa('[data-uid]', sidebarList).forEach((card) => {
    const active = card.dataset.uid === state.currentItem?._uid;
    card.classList.toggle('active', active);
    // Guide 12 and 13. An event card carries its own NOW PLAYING marker in the
    // markup, so it only needs the action swapped from Watch to Playing.
    if (card.classList.contains('event-ref-card')) {
      const action = qs('.event-card-action.watch', card);
      if (action) {
        const icon = qs('i', action);
        const text = qs('span', action);
        if (icon) icon.className = active ? 'fas fa-pause' : 'fas fa-play';
        if (text) {
          if (!action.dataset.idleLabel) action.dataset.idleLabel = text.textContent;
          text.textContent = active ? 'Playing' : action.dataset.idleLabel;
        }
      }
      return;
    }
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
video.addEventListener('resize', updateStreamInfoBadge);
video.addEventListener('loadeddata', () => markAttemptProgress('data loaded'));
video.addEventListener('canplay', () => markAttemptProgress('can play'));
video.addEventListener('pause', () => {
  updatePlayPauseUi();
  updateMobilePlaybackPerformance();
  hide4KAvailabilityReminder();
  clearMovieAudioCompatibilityCheck();
  clearTimeout(state.hideControlsTimer);
  playerControls.classList.remove('hide');
  videoContainer.classList.remove('hide-cursor');
});
video.addEventListener('play', () => {
  updatePlayPauseUi();
  updateMobilePlaybackPerformance();
  showControlsTemporarily();
  if (!state.userPaused) {
    try { state.hls?.startLoad(-1); } catch (_) {}
  }
});
video.addEventListener('ended', () => {
  if (seriesModule?.handleEnded?.()) return;
  updateMobilePlaybackPerformance();
  clearMovieQualityGuidance();
  clearMovieAudioCompatibilityCheck();

  // A live event, upcoming fixture or TV channel has no legitimate end.
  // Some upstream sources hand out a short, EXT-X-ENDLIST-terminated
  // snapshot instead of a genuinely rolling live window - confirmed by
  // fetching the origin directly, bypassing our own proxy entirely, and
  // finding the identical finite manifest there too. Reloading that same
  // URL only ever reaches the same ENDLIST again, which is why this used
  // to leave the player frozen forever with no retry and no visible
  // error. Escalated the same way the stall detector gives up on a stream
  // it cannot recover (see startStallDetector's own allowRouteFailover
  // escalation): skip the "reload this same stream" recovery path
  // entirely and move straight to the plan's next attempt.
  const session = state.playbackSession;
  const item = session?.item;
  const isContinuousContext = Boolean(item) && (
    isLiveEventContext(item) || item._sourceKind === VIEW.CHANNEL || state.view === VIEW.CHANNEL
  );
  if (session && isContinuousContext && !isMoviePlaybackContext(item)) {
    session.allowRouteFailover = true;
    session.success = false;
    failCurrentAttempt('native_ended_on_continuous_stream');
  }
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
    const positionKey = sourcePlaybackKey(state.currentItem);
    if (!positionKey) return;
    state.playbackPositions[positionKey] = {
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

function drawerPayloadItems(data = {}) {
  if (Array.isArray(data)) return data;
  return data.items || data.channels || data.movies || data.events || [];
}

async function loadFullscreenGlobalCatalog() {
  if (Array.isArray(state.drawerGlobalCatalog)) return state.drawerGlobalCatalog;
  if (state.drawerGlobalCatalogPromise) return state.drawerGlobalCatalogPromise;

  state.drawerGlobalCatalogPromise = (async () => {
    const groups = [];
    const channelTasks = Object.entries(state.manifest?.channels || {})
      .filter(([, entry]) => entry?.visible !== false && entry?.url)
      .map(async ([label, entry]) => {
        const data = await fetchJson(entry.url, { cache: 'no-store' });
        return normalizeList(drawerPayloadItems(data), VIEW.CHANNEL).map((item) => ({
          ...item,
          category: item.category || label
        }));
      });

    const eventTasks = [
      [state.manifest?.today_match, VIEW.EVENT, 'Today Match'],
      [state.manifest?.upcoming, VIEW.UPCOMING, 'Upcoming']
    ].filter(([entry]) => entry?.visible !== false && entry?.url).map(async ([entry, kind, label]) => {
      const data = await fetchJson(entry.url, { cache: 'no-store' });
      return normalizeList(drawerPayloadItems(data), kind).map((item) => ({
        ...item,
        category: item.category || label
      }));
    });

    const movieTasks = Object.entries(state.manifest?.movies || {})
      .filter(([, entry]) => entry?.visible !== false && entry?.index)
      .map(async ([label, entry]) => {
        const indexData = await fetchJson(entry.index, { cache: 'no-store' });
        const slug = String(indexData.slug || entry.slug || label).trim().toLowerCase().replace(/\s+/g, '-');
        const pages = Array.isArray(indexData.pages) ? indexData.pages : [];
        const pageResults = await Promise.allSettled(pages.map((page) => {
          const path = page.path || (page.file ? `data/movies/${slug}/${page.file}` : '');
          return path ? fetchJson(path, { cache: 'no-store' }) : Promise.resolve({ items: [] });
        }));
        const raw = pageResults.flatMap((result) => result.status === 'fulfilled' ? drawerPayloadItems(result.value) : []);
        return normalizeList(raw, VIEW.MOVIE).map((item) => ({
          ...item,
          category: item.category || label
        }));
      });

    const seriesTask = (async () => {
      try {
        const manifest = await fetchJson('/data/series/manifest.json', { cache: 'no-store' });
        const entries = Object.entries(manifest?.categories || {}).filter(([, entry]) => entry?.visible !== false && entry?.index);
        const results = await Promise.allSettled(entries.map(async ([label, entry]) => {
          const data = await fetchJson(entry.index, { cache: 'no-store' });
          const slug = String(entry.slug || label).trim().toLowerCase().replace(/\s+/g, '-');
          return drawerPayloadItems(data).filter((item) => item?.publish_allowed !== false).map((item, index) => ({
            ...item,
            id: item.id || item.series_id || `series-${index + 1}`,
            name: item.name || item.title || `Series ${index + 1}`,
            logo: item.logo || item.poster || item.image || '',
            category: item.category || label,
            content_kind: 'series',
            _isSeries: true,
            _sourceKind: VIEW.MOVIE,
            _uid: `series-search:${slug}:${item.id || item.series_id || index + 1}`
          }));
        }));
        return results.flatMap((result) => result.status === 'fulfilled' ? result.value : []);
      } catch (_) {
        return [];
      }
    })();

    const results = await Promise.allSettled([...channelTasks, ...eventTasks, ...movieTasks, seriesTask]);
    results.forEach((result) => {
      if (result.status === 'fulfilled' && Array.isArray(result.value)) groups.push(...result.value);
    });

    const unique = new Map();
    groups.forEach((item) => {
      const key = `${item._sourceKind || ''}:${item.content_kind || ''}:${item.id || ''}:${canonicalDisplayKey(item.name)}:${canonicalDisplayKey(item.category)}`;
      if (!unique.has(key)) unique.set(key, item);
    });
    state.drawerGlobalCatalog = [...unique.values()];
    return state.drawerGlobalCatalog;
  })().finally(() => { state.drawerGlobalCatalogPromise = null; });

  return state.drawerGlobalCatalogPromise;
}

function currentFullscreenDrawerItems() {
  const currentUid = state.currentItem?._uid;
  if (!currentUid || state.filteredItems.some((item) => item._uid === currentUid)) return state.filteredItems;
  if (!Array.isArray(state.drawerGlobalCatalog)) return state.filteredItems;
  const currentKind = state.currentItem?._sourceKind;
  const currentCategory = canonicalDisplayKey(state.currentItem?.category);
  return state.drawerGlobalCatalog.filter((item) => (
    item._sourceKind === currentKind && canonicalDisplayKey(item.category) === currentCategory
  ));
}

function fullscreenDrawerTitle(query = '') {
  if (String(query || '').trim()) return 'Search results';
  if (seriesModule?.detailActive) return 'Series episodes';
  if (state.view === VIEW.MOVIE) return state.selectedMovieCategory || 'Movies';
  if (state.view === VIEW.EVENT) return 'Today Match';
  if (state.view === VIEW.UPCOMING) return 'Upcoming Match';
  return state.selectedCategory || state.currentItem?.category || 'Current category';
}

function updateFullscreenDrawerHeader(query = '', count = 0, loading = false) {
  const title = $('fsDrawerTitle');
  const countNode = $('fsDrawerCount');
  const clear = $('fsDrawerClear');
  const normalized = String(query || '').trim();
  if (title) title.textContent = fullscreenDrawerTitle(normalized);
  if (countNode) countNode.textContent = loading ? 'Searching…' : `${count} ${count === 1 ? 'item' : 'items'}`;
  if (clear) clear.hidden = !normalized;
  $('fsDrawerList')?.setAttribute('aria-busy', loading ? 'true' : 'false');
}

function drawerHighlightedText(value, query = '') {
  const source = String(value || '');
  const needle = String(query || '').trim();
  if (!needle) return escapeHtml(source);
  const index = source.toLowerCase().indexOf(needle.toLowerCase());
  if (index < 0) return escapeHtml(source);
  return `${escapeHtml(source.slice(0, index))}<mark class="fs-drawer-match">${escapeHtml(source.slice(index, index + needle.length))}</mark>${escapeHtml(source.slice(index + needle.length))}`;
}

function renderFullscreenDrawerStatus(type, title, detail = '') {
  const list = $('fsDrawerList');
  const status = document.createElement('div');
  status.className = `fs-drawer-status ${type}`;
  status.setAttribute('role', type === 'loading' ? 'status' : 'note');
  status.innerHTML = `${type === 'loading' ? '<span class="fs-drawer-spinner" aria-hidden="true"></span>' : '<i class="fas fa-search" aria-hidden="true"></i>'}<strong>${escapeHtml(title)}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ''}`;
  list.appendChild(status);
}

async function populateFullscreenDrawer(query = '') {
  const normalized = String(query || '').trim().toLowerCase();
  if (!normalized && seriesModule?.populateFullscreenDrawer?.('')) {
    const count = qsa('.fs-drawer-item', $('fsDrawerList')).length;
    updateFullscreenDrawerHeader('', count, false);
    return;
  }
  const list = $('fsDrawerList');
  const requestId = ++state.drawerSearchRequestId;
  rememberFullscreenDrawerScroll();
  list.replaceChildren();
  list.classList.remove('series-drawer-detail', 'movie-drawer-grid', 'movie-search-grid', 'channel-drawer-grid', 'global-search-grid');

  let sourceItems = currentFullscreenDrawerItems();
  let contextKey = fullscreenDrawerContextKey();
  if (normalized) {
    updateFullscreenDrawerHeader(normalized, 0, true);
    renderFullscreenDrawerStatus('loading', 'Searching everything…', 'Channels, movies and events');
    sourceItems = await loadFullscreenGlobalCatalog();
    if (requestId !== state.drawerSearchRequestId) return;
    list.replaceChildren();
    list.classList.add('global-search-grid');
    contextKey = `search:all:${normalized}`;
  }

  const allMatches = sourceItems.filter((item) => {
    const haystack = `${item.name || ''} ${item.category || ''} ${item.competition || ''} ${item.year || ''}`.toLowerCase();
    return !normalized || haystack.includes(normalized);
  });
  const items = allMatches.slice(0, FULLSCREEN_DRAWER_RENDER_LIMIT);
  const movieMode = state.view === VIEW.MOVIE || state.currentItem?._sourceKind === VIEW.MOVIE;
  const movieSearchMode = Boolean(normalized && items.length && items.every((item) => item._sourceKind === VIEW.MOVIE));
  list.dataset.contextKey = contextKey;
  if (!normalized) list.classList.add(movieMode ? 'movie-drawer-grid' : 'channel-drawer-grid');
  if (movieSearchMode) list.classList.add('movie-search-grid');
  state.drawerRenderedItems = new Map(items.map((item) => [item._uid, item]));
  updateFullscreenDrawerHeader(normalized, allMatches.length, false);

  if (!items.length) {
    renderFullscreenDrawerStatus('empty', 'No results found', normalized ? `Try another spelling for “${String(query || '').trim()}”.` : 'This category has no available items.');
    state.drawerRenderedForSession = state.dataSessionId;
    return;
  }

  const fragment = document.createDocumentFragment();
  items.forEach((item, index) => {
    const row = document.createElement('button');
    row.type = 'button';
    const itemMovieMode = item._sourceKind === VIEW.MOVIE;
    row.className = `fs-drawer-item tv-focusable${itemMovieMode ? ' is-movie' : ''}${item._uid === state.currentItem?._uid ? ' active' : ''}`;
    row.dataset.uid = item._uid;
    row.setAttribute('aria-label', item.name);
    row.setAttribute('title', item.name);
    if (item._uid === state.currentItem?._uid) row.setAttribute('aria-current', 'true');
    const meta = String(item.category || item.competition || (itemMovieMode ? 'Movie' : 'Live')).trim();
    row.innerHTML = `
      <span class="fs-drawer-rank">${index + 1}</span>
      <span class="fs-drawer-logo-wrap">${createImageHtml(item, '')}</span>
      <span class="fs-drawer-card-copy">
        <strong class="fs-drawer-title">${drawerHighlightedText(item.name, normalized)}</strong>
        <small class="fs-drawer-meta">${escapeHtml(meta)}</small>
      </span>`;
    if (itemMovieMode) {
      const posterWrap = row.querySelector('.fs-drawer-logo-wrap');
      const poster = posterWrap?.querySelector('img');
      const posterUrl = poster?.getAttribute('src');
      if (posterWrap && posterUrl) {
        posterWrap.style.setProperty('background-image', `url("${posterUrl.replace(/["\\]/g, '\\$&')}")`, 'important');
      }
    }
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
  let item = state.drawerRenderedItems.get(row.dataset.uid) || state.filteredItems.find((entry) => entry._uid === row.dataset.uid);
  if (!item && seriesModule) item = seriesModule.episodeByUid?.(row.dataset.uid);
  resetFullscreenDrawerSearch();
  if (item && seriesModule?.handleDrawerClick?.(item)) {
    // Series master/episode selections stay inside the approved Series drawer.
    showControlsTemporarily();
    return;
  }
  if (item && isPlayable(item)) startPlayback(item, true);
  closeFullscreenDrawer(true);
});
$('fsDrawerList').addEventListener('error', (event) => {
  const image = event.target;
  if (image?.tagName !== 'IMG') return;
  const row = image.closest('.fs-drawer-item');
  if (row?.classList.contains('is-movie')) {
    image.parentElement?.style.removeProperty('background-image');
    replaceBrokenMovieImage(image);
  } else {
    replaceBrokenImage(image);
  }
}, true);
$('fsDrawerList').addEventListener('load', (event) => {
  if (event.target?.tagName === 'IMG') event.target.classList.add('is-loaded');
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

function moveFinalNavigationFocus(current, selector, delta) {
  const buttons = qsa(selector).filter((button) => button.offsetParent !== null);
  const index = buttons.indexOf(current);
  if (index < 0 || !buttons.length) return false;
  const next = buttons[Math.max(0, Math.min(buttons.length - 1, index + delta))];
  if (!next || next === current) return false;
  next.focus({ preventScroll: true });
  next.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  return true;
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
      // Backspace inside search/text fields must edit text normally. Previously
      // the TV remote handler blurred the field and blocked the browser edit.
      if (key === 'Backspace' || code === 8) return;
      if (key === 'Escape' && active.id === 'fsDrawerSearch') {
        event.preventDefault();
        if (active.value) {
          resetFullscreenDrawerSearch();
          populateFullscreenDrawer('');
          active.focus();
        } else {
          closeFullscreenDrawer(true);
        }
        return;
      }
      active.blur();
      event.preventDefault();
      return;
    }
    if ($('fsDrawer').classList.contains('open')) {
      closeFullscreenDrawer(true);
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

  const mainNavigationButton = active?.closest?.('.final-main-button');
  if (mainNavigationButton && ['ArrowLeft', 'ArrowRight', 'ArrowDown'].includes(key)) {
    event.preventDefault();
    if (key === 'ArrowLeft' || key === 'ArrowRight') {
      const root = mainNavigationButton.closest('.final-main-nav');
      moveFinalNavigationFocus(mainNavigationButton, `#${root?.id} .final-main-button`, key === 'ArrowLeft' ? -1 : 1);
    } else {
      const subRoot = window.matchMedia('(max-width: 980px)').matches ? mobileSubNav : desktopSubNav;
      const target = qs('.final-sub-button.active', subRoot) || qs('.final-sub-button', subRoot) || qs('.tv-focusable', sidebarList);
      target?.focus({ preventScroll: true });
    }
    return;
  }

  const subNavigationButton = active?.closest?.('.final-sub-button');
  if (subNavigationButton && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(key)) {
    const mobile = window.matchMedia('(max-width: 980px)').matches;
    const previousKey = mobile ? 'ArrowLeft' : 'ArrowUp';
    const nextKey = mobile ? 'ArrowRight' : 'ArrowDown';
    if (key === previousKey || key === nextKey) {
      event.preventDefault();
      const root = subNavigationButton.closest('.final-sub-nav');
      moveFinalNavigationFocus(subNavigationButton, `#${root?.id} .final-sub-button`, key === previousKey ? -1 : 1);
      return;
    }
    if ((!mobile && key === 'ArrowRight') || (mobile && key === 'ArrowDown')) {
      event.preventDefault();
      qs('.tv-focusable', sidebarList)?.focus({ preventScroll: true });
      return;
    }
    if ((!mobile && key === 'ArrowLeft') || (mobile && key === 'ArrowUp')) {
      event.preventDefault();
      const mainRoot = mobile ? mobileMainNav : desktopMainNav;
      (qs('.final-main-button.active', mainRoot) || qs('.final-main-button', mainRoot))?.focus({ preventScroll: true });
      return;
    }
  }

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
  if (!video.paused) {
    state.hideControlsTimer = setTimeout(() => {
      if ($('fsDrawer')?.classList.contains('open')) return;
      if (videoContainer.querySelector('.popup-menu.show')) return;
      playerControls.classList.add('hide');
      $('fsDrawerToggle').classList.add('hide');
      videoContainer.classList.add('hide-cursor');
    }, 3200);
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
  if (state.movieControlsLocked) return;
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
  if (state.movieControlsLocked && !event.target.closest('#movieLockBtn')) return;
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

async function setupServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  try {
    const registration = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    registration.update().catch(() => {});
  } catch (error) {
    console.warn('Click TV service worker registration failed', error);
  }
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
  const notice = $('sticky-header-notice');
  notice.classList.add('notice-dismissed');
  notice.hidden = true;
  syncNoticeHeight();
  try { sessionStorage.setItem(STORAGE_KEYS.noticeDismissed, '1'); } catch (_) {}
}

function syncNoticeHeight() {
  const notice = $('sticky-header-notice');
  const height = notice.hidden || notice.classList.contains('notice-dismissed') ? 0 : notice.offsetHeight;
  document.documentElement.style.setProperty('--notice-height', `${height}px`);
}

function restoreNoticeState() {
  // A dismissal is intentionally limited to the current browser tab. An old
  // persistent flag used to make the mobile notice disappear forever.
  try { localStorage.removeItem(STORAGE_KEYS.noticeDismissed); } catch (_) {}
  if (sessionStorage.getItem(STORAGE_KEYS.noticeDismissed) === '1') {
    const notice = $('sticky-header-notice');
    notice.classList.add('notice-dismissed');
    notice.hidden = true;
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
$('movieLockBtn')?.addEventListener('click', () => setMovieControlsLocked(!state.movieControlsLocked));
$('movieRotateBtn')?.addEventListener('click', async () => {
  try {
    const orientationApi = screen.orientation;
    if (!orientationApi?.lock) throw new Error('Orientation lock unsupported');
    const target = orientationApi.type?.startsWith('landscape') ? 'portrait-primary' : 'landscape';
    if (!wrapperFullscreenElement()) await enterWrapperFullscreen();
    await orientationApi.lock(target);
    setPlayerStatus('info', target.startsWith('landscape') ? 'Landscape mode চালু হয়েছে।' : 'Portrait mode চালু হয়েছে।', 1800);
  } catch (_) {
    setPlayerStatus('info', 'এই browser-এ screen rotation lock support করে না। Device rotate করুন।', 2600);
  }
});
$('pipBtn')?.addEventListener('click', async () => {
  try {
    if (!document.pictureInPictureEnabled || video.disablePictureInPicture) {
      throw new Error('Picture-in-Picture unsupported');
    }
    if (document.pictureInPictureElement) {
      await document.exitPictureInPicture();
      return;
    }
    if (video.readyState < 1) {
      setPlayerStatus('info', 'Movie loading শেষ হলে Picture-in-Picture চালু করুন।', 2200);
      return;
    }
    await video.requestPictureInPicture();
  } catch (_) {
    setPlayerStatus('info', 'এই browser/device-এ Picture-in-Picture support করে না।', 2600);
  }
});
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
  videoContainer.classList.toggle('clicktv-mobile-fullscreen', wrapperActive);

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
function resetFullscreenDrawerSearch() {
  state.drawerSearchRequestId += 1;
  clearTimeout(state.drawerSearchDebounceTimer);
  state.drawerSearchDebounceTimer = null;
  $('fsDrawerSearch').value = '';
  $('fsDrawerClear').hidden = true;
}
function closeFullscreenDrawer(focusToggle = false) {
  const drawer = $('fsDrawer');
  rememberFullscreenDrawerScroll();
  resetFullscreenDrawerSearch();
  drawer.classList.remove('open');
  drawer.setAttribute('aria-hidden', 'true');
  $('fsDrawerToggle').setAttribute('aria-expanded', 'false');
  showControlsTemporarily();
  if (focusToggle) $('fsDrawerToggle').focus({ preventScroll: true });
}
$('fsDrawerToggle').addEventListener('click', (event) => {
  event.stopPropagation();
  const drawer = $('fsDrawer');
  const opening = !drawer.classList.contains('open');
  drawer.classList.toggle('open', opening);
  drawer.setAttribute('aria-hidden', opening ? 'false' : 'true');
  $('fsDrawerToggle').setAttribute('aria-expanded', opening ? 'true' : 'false');
  showControlsTemporarily();
  resetFullscreenDrawerSearch();
  if (opening) {
    populateFullscreenDrawer('');
    requestAnimationFrame(() => $('fsDrawerSearch').focus({ preventScroll: true }));
  }
});
$('fsDrawerClose').addEventListener('click', () => {
  closeFullscreenDrawer(true);
});
$('fsDrawerClear').addEventListener('click', () => {
  resetFullscreenDrawerSearch();
  populateFullscreenDrawer('');
  $('fsDrawerSearch').focus({ preventScroll: true });
});
$('fsDrawerSearch').addEventListener('input', (event) => {
  const query = event.target.value;
  $('fsDrawerClear').hidden = !query.trim();
  clearTimeout(state.drawerSearchDebounceTimer);
  state.drawerSearchDebounceTimer = setTimeout(() => {
    state.drawerSearchDebounceTimer = null;
    populateFullscreenDrawer(query);
  }, 140);
});
$('fsDrawerSearch').addEventListener('keydown', (event) => {
  if (event.key !== 'ArrowDown') return;
  const firstResult = qs('.fs-drawer-item', $('fsDrawerList'));
  if (!firstResult) return;
  event.preventDefault();
  firstResult.focus({ preventScroll: true });
  firstResult.scrollIntoView({ block: 'nearest', inline: 'nearest' });
});
$('fsDrawer').addEventListener('keydown', (event) => {
  if (event.key !== 'Tab') return;
  const focusable = qsa('input, button:not([disabled]), [tabindex]:not([tabindex="-1"])', $('fsDrawer'))
    .filter((element) => !element.hidden && getComputedStyle(element).display !== 'none');
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus({ preventScroll: true });
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus({ preventScroll: true });
  }
});
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
$('mobileBottomSearchBtn')?.addEventListener('click', () => {
  const visible = getComputedStyle($('mobileSearchBox')).display !== 'none';
  if (visible) closeMobileSearch(true);
  else openMobileSearch();
});
$('mobileBottomNoticeBtn')?.addEventListener('click', () => {
  const notice = $('sticky-header-notice');
  if (!notice) return;
  notice.hidden = false;
  notice.classList.remove('notice-dismissed');
  try {
    localStorage.removeItem(STORAGE_KEYS.noticeDismissed);
    sessionStorage.removeItem(STORAGE_KEYS.noticeDismissed);
  } catch (_) {}
  syncNoticeHeight();
  showToast('Click TV notice দেখানো হয়েছে');
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
  const positionKey = sourcePlaybackKey(item);
  if (!isMovie || !positionKey) return;
  const saved = state.playbackPositions[positionKey];
  const duration = Number(saved?.duration || video.duration || 0);
  const position = Number(saved?.position || 0);
  if (position < 60 || (duration && position > duration - 60)) return;
  $('resumeTime').textContent = formatTime(position, duration);
  $('resumeBadge').classList.add('show');
  state.resumeBadgeTimer = setTimeout(hideResumeBadge, 12000);
}

$('resumeBadge').addEventListener('click', () => {
  const saved = state.currentItem && state.playbackPositions[sourcePlaybackKey(state.currentItem)];
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

/* What selectMainView() calls a view, keyed by what state.view calls it.
 *
 * The two vocabularies are not the same and only overlap by luck.
 * VIEW.UPCOMING and VIEW.CHANNEL happen to equal the strings the branches
 * test, VIEW.EVENT ('event') does not equal 'today-match', and VIEW.FAVORITE
 * ('favorite') does not equal 'favorites'. Passing state.view straight in
 * therefore sent Today Match down the final else, where it looked for
 * `manifest.channels['Today Match']`, found nothing, and answered with
 * "এই বিভাগের JSON path পাওয়া যায়নি" over an emptied list - about
 * thirty seconds after a first visit, on nothing more than a tab switch.
 *
 * The list came back on the next scroll or clock tick only because
 * state.currentItems was never cleared, so the very next render put it back.
 * That is why it read as a flicker rather than a failure.
 *
 * VIEW.MOVIE is deliberately absent: movies are not reached through this
 * function at all, and a missing key skips the refresh rather than guessing.
 */
const VIEW_SELECT_KEYS = Object.freeze({
  [VIEW.EVENT]: 'today-match',
  [VIEW.UPCOMING]: 'upcoming',
  [VIEW.CHANNEL]: 'channel',
  [VIEW.FAVORITE]: 'favorites',
  [VIEW.RECENT]: 'recent'
});

function selectKeyForView(view) {
  return VIEW_SELECT_KEYS[view] || '';
}

function setupReturnToTabRefresh() {
  /* A tab left open for an hour shows an hour-old list.

     Nothing refreshed on return, so a viewer who switched away during a match
     and came back was reading a snapshot from before kickoff - with no way to
     tell. Coming back is the clearest signal there is that the list is being
     read again, so it is reloaded then. Playback is left alone: a session the
     viewer started belongs to them, and the list can update underneath it. */
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    if (!state.currentDataPath) return;
    const since = Date.now() - (state.lastDataLoadedAt || 0);
    if (since < 30000) return;
    // Through the same entry point a tap uses, so the reload cannot drift
    // from what a normal section change does - but through it with the key
    // that entry point actually reads. `state.view` is a VIEW value, and
    // selectMainView switches on the string a chip passes, which is not the
    // same alphabet: VIEW.EVENT is 'event' and the branch it needs is
    // 'today-match'.
    const key = selectKeyForView(state.view);
    if (!key) return;
    selectMainView(key, state.selectedCategory, { silent: true })
      .catch(() => {});
  });
}

async function bootstrap() {
  setupReturnToTabRefresh();
  setupFinalNavigationControls();
  setupEventSportFilter();
  initializeSeriesModule();
  setupMobileZoomGuard();
  updatePerformanceClasses();
  restoreNoticeState();
  restorePlayerPreferences();
  updateMuteUi();
  updateClock();
  setInterval(() => {
    // Requirement 10. Skip the repaint when nothing can see it, and drop to a
    // slower cadence on a phone that is already decoding video.
    if (document.hidden) return;
    updateClock();
  }, effectivePerformanceClass() === 'normal' ? 1000 : 30000);
  setInterval(refreshEventCardsForClock, 30000);
  renderDataFreshness();
  setInterval(renderDataFreshness, 30000);
  // On the same cadence as the card clock, so a reminder fires from the tick
  // that already knows the time rather than from a timer per match.
  setInterval(checkDueReminders, 30000);
  setInterval(refreshActiveEventCatalogue, EVENT_CATALOG_REFRESH_MS);
  await setupServiceWorker();
  setupPwaInstall();
  renderNetworkMenu();
  updateNetworkMenuState(readNetworkMode());
  try {
    await loadRuntimeAndManifest();
    void initializePlaybackTelemetry();
  } catch (error) {
    console.error(error);
    showPlayerMessage('Data manifest load হয়নি। Refresh করে আবার চেষ্টা করুন।', false);
    showListMessage('Click TV data load করা যায়নি', 'fa-exclamation-triangle');
    setSidebarCount('0 Items');
  }
}

// Deterministic localhost-only hooks let the release runtime test force one
// proxy into cooldown and prove that the real attempt planner selects another
// proxy. They are never exposed on clicktv.pages.dev.
if (['127.0.0.1', 'localhost'].includes(location.hostname)) {
  window.__clickTvRuntimeTest = {
    resetProxyHealth() {
      state.proxyHealth = {};
      try { localStorage.removeItem(STORAGE_KEYS.proxyHealth); } catch (_) {}
    },
    buildAttempts(item) {
      return buildAttemptPlan(item).map((attempt) => ({
        route: attempt.route,
        proxy: attempt.proxy,
        sourceIndex: attempt.sourceIndex,
        playbackId: attempt.source?.playback_id || '',
        url: attempt.source?.url || '',
      }));
    },
    // Diagnostic for section 14: does selecting a channel actually change
    // which stream the attempt plan tries first? Used to catch the case where
    // a channel exists in channels[] but its stream never made it into the
    // event-level source list the player draws from.
    channelSelectionResolvesTo(item, channelId) {
      const key = eventChannelId(item);
      const previous = state.channelSelection[key];
      state.channelSelection[key] = String(channelId);
      const attempts = buildAttemptPlan(item);
      state.channelSelection[key] = previous;
      const first = attempts[0];
      return {
        firstAttemptPlaybackId: first?.source?.playback_id || '',
        firstAttemptUrl: first?.source?.url || '',
        attemptCount: attempts.length,
      };
    },
    markProxyFailure(proxy, targetUrl) {
      markProxyResult(proxy, targetUrl, false, 100);
    },
    resolveProtectedDrm(playbackId, proxy, hint = null) {
      return resolveProtectedDrm({
        item: { playback_id: playbackId, drm: hint },
        currentAttempt: {
          proxy,
          source: { playback_id: playbackId, drm: hint },
        },
      });
    },
    movieQualityGroups(item) {
      return directMovieQualityGroups({ ...item, _sourceKind: VIEW.MOVIE }).map((group) => ({
        key: group.key,
        height: group.height,
        sources: group.sources.map((source) => ({
          playback_id: source.playback_id || '',
          has_url: Boolean(source.url),
          proxy_mode: source.proxy_mode || '',
          protected_source: Boolean(source.protected_source),
        })),
      }));
    },
    currentItemsPlaybackAudit() {
      return state.currentItems.map((item) => ({
        name: item.name || item.title || '',
        sourceKind: item._sourceKind || '',
        navigatesToSeries: Boolean(seriesModule?.isSeriesItem?.(item)),
        playable: isPlayable(item),
        attemptCount: buildAttemptPlan(item).length,
      }));
    },
    playbackSessionSnapshot() {
      const session = state.playbackSession;
      return session ? {
        itemName: session.item?.name || session.item?.title || '',
        planLength: session.plan?.length || 0,
        attemptsRun: session.attemptsRun || 0,
        attemptIndex: session.attemptIndex || 0,
        currentRoute: session.currentAttempt?.route || '',
        success: Boolean(session.success),
      } : null;
    },
    // Requirement 9. What the live buffer actually became at runtime, and the
    // ability to force the pre-fix numbers back on, so the eight-second freeze can
    // be reproduced and the fix measured against it rather than asserted.
    runtimeConfig() {
      return { play_proxies: (state.runtime?.play_proxies || []).slice() };
    },
    liveBufferSnapshot() {
      const config = state.hls?.config;
      return {
        diagnostics: state.playbackDiagnostics?.segmentAwareBuffer || null,
        config: config ? {
          maxBufferLength: config.maxBufferLength,
          maxMaxBufferLength: config.maxMaxBufferLength,
          liveSyncDurationCount: config.liveSyncDurationCount,
          lowLatencyMode: Boolean(config.lowLatencyMode),
        } : null,
      };
    },
    forceLegacyLiveBuffer() {
      const config = state.hls?.config;
      if (!config) return false;
      config.maxBufferLength = 5;
      config.maxMaxBufferLength = 12;
      config.liveSyncDurationCount = 2;
      config.lowLatencyMode = true;
      return true;
    },
    eventCardUid(eventId) {
      const item = (state.currentItems || []).find((entry) => String(entry.id) === String(eventId));
      return item ? String(item._uid || '') : '';
    },
    nowPlayingName() {
      return String(state.playbackSession?.item?.name || state.currentItem?.name || '');
    },
    startAuditPlayback(item, sourceKind = 'movie') {
      const kind = sourceKind === 'channel' ? VIEW.CHANNEL : VIEW.MOVIE;
      const normalized = normalizeItem({ ...item, _sourceKind: kind });
      state.view = kind;
      state.currentItems = [normalized];
      startPlayback(normalized, true);
      return {
        name: normalized.name || normalized.title || '',
        playable: isPlayable(normalized),
        attemptCount: buildAttemptPlan(normalized).length,
      };
    },
  };
}

bootstrap();


