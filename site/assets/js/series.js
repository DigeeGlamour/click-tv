'use strict';

(() => {
  const SERIES_PROGRESS_KEY = 'clicktv_series_progress_v1';
  const EPISODE_PROGRESS_KEY = 'clicktv_series_episode_progress_v1';
  const SERIES_MANIFEST_URL = 'data/series/manifest.json';
  const MAX_CACHED_SERIES = 24;
  const MAX_CACHED_SEASONS = 36;
  const NEXT_EPISODE_SECONDS = 8;

  let bridge = null;
  let initialized = false;
  let manifestPromise = null;
  let catalogItems = [];
  let detailActive = false;
  let activeCategorySlug = '';
  let activeSeriesItem = null;
  let activeSeriesData = null;
  let activeSeasonNumber = 0;
  let activeEpisodes = [];
  let seasonRequestId = 0;
  let nextEpisodeTimer = null;
  let nextEpisodeCountdown = 0;
  const seriesCache = new Map();
  const seasonCache = new Map();

  function safeJsonParse(value, fallback) {
    try {
      return value ? JSON.parse(value) : fallback;
    } catch (_) {
      return fallback;
    }
  }

  function readStorage(key, fallback) {
    try {
      const value = localStorage.getItem(key);
      return safeJsonParse(value, fallback);
    } catch (_) {
      return fallback;
    }
  }

  function writeStorage(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (_) {}
  }

  function escapeHtml(value) {
    if (bridge?.escapeHtml) return bridge.escapeHtml(value);
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function safeText(value, fallback = '') {
    const text = String(value ?? '').trim();
    return text || fallback;
  }

  function numberValue(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function twoDigits(value) {
    return String(Math.max(0, Number(value || 0))).padStart(2, '0');
  }

  function categoryLabelFromSlug(slug) {
    const found = bridge?.movieOrder?.find((entry) => entry[1] === slug);
    return found?.[0] || String(slug || '').replaceAll('-', ' ');
  }

  function seriesProgressMap() {
    const value = readStorage(SERIES_PROGRESS_KEY, {});
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  }

  function seriesProgress(seriesId) {
    return seriesProgressMap()[seriesId] || null;
  }

  function episodeProgressKey(seriesId, seasonNumber, episodeNumber, episodeKey = '') {
    const key = safeText(episodeKey);
    return key
      ? `${safeText(seriesId)}:s${twoDigits(seasonNumber)}:${key}`
      : `${safeText(seriesId)}:s${twoDigits(seasonNumber)}:e${twoDigits(episodeNumber)}`;
  }

  function episodeDisplayLabel(episode) {
    const explicit = safeText(episode?.episode_label);
    if (explicit) return explicit;
    return `Episode ${twoDigits(episode?.episode_number)}`;
  }

  function canonicalEpisodeText(value) {
    return safeText(value)
      .toLowerCase()
      .replace(/[–—]/g, '-')
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();
  }

  function episodePresentationTitle(episode) {
    const label = episodeDisplayLabel(episode);
    const title = safeText(episode?.episode_title || episode?.title);
    if (!title) return label;
    const labelKey = canonicalEpisodeText(label);
    const titleKey = canonicalEpisodeText(title);
    if (!titleKey || titleKey === labelKey || titleKey === 'episode' || titleKey === `${labelKey} episode`) return label;
    if (/^episode\s*\d+(?:\s*[-–]\s*\d+)?$/i.test(title) && labelKey.startsWith('episode ')) return label;
    return `${label} · ${title}`;
  }

  function episodeProgressMap() {
    const value = readStorage(EPISODE_PROGRESS_KEY, {});
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  }

  function episodeProgress(episode) {
    if (!episode) return null;
    return episodeProgressMap()[episodeProgressKey(episode.series_id, episode.season_number, episode.episode_number, episode.episode_key)] || null;
  }

  function persistEpisodeProgress(episode, progress) {
    if (!episode || !progress) return;
    const all = episodeProgressMap();
    const key = episodeProgressKey(episode.series_id, episode.season_number, episode.episode_number, episode.episode_key);
    all[key] = progress;
    const entries = Object.entries(all)
      .sort((a, b) => numberValue(b[1]?.updated_at) - numberValue(a[1]?.updated_at))
      .slice(0, 500);
    writeStorage(EPISODE_PROGRESS_KEY, Object.fromEntries(entries));
  }

  function persistSeriesProgress(seriesId, progress) {
    if (!seriesId || !progress) return;
    const all = seriesProgressMap();
    all[seriesId] = progress;
    const entries = Object.entries(all)
      .sort((a, b) => numberValue(b[1]?.updated_at) - numberValue(a[1]?.updated_at))
      .slice(0, 200);
    writeStorage(SERIES_PROGRESS_KEY, Object.fromEntries(entries));
  }

  function progressPercent(progress) {
    const duration = numberValue(progress?.duration);
    const position = numberValue(progress?.position);
    if (duration <= 0) return 0;
    return Math.max(0, Math.min(100, (position / duration) * 100));
  }

  function continueText(item) {
    const progress = seriesProgress(item.id);
    if (!progress) return 'Start Series';
    return `Continue S${twoDigits(progress.season_number)} · ${safeText(progress.episode_label, `Episode ${twoDigits(progress.episode_number)}`)}`;
  }

  function normalizeSeriesSummary(raw, index, slug) {
    const id = safeText(raw.id || raw.series_id, `series-${index + 1}`);
    const name = safeText(raw.name || raw.title, `Series ${index + 1}`);
    const category = safeText(raw.category, categoryLabelFromSlug(slug));
    const totalSeasons = Math.max(0, numberValue(raw.total_seasons || raw.season_count));
    const totalEpisodes = Math.max(0, numberValue(raw.total_episodes || raw.episode_count));
    const year = numberValue(raw.year || String(raw.release_date || '').match(/(?:19|20)\d{2}/)?.[0]);
    return {
      ...raw,
      id,
      name,
      title: name,
      category,
      year,
      logo: safeText(raw.logo || raw.poster || raw.image),
      backdrop: safeText(raw.backdrop || raw.banner),
      content_kind: 'series',
      _isSeries: true,
      _sourceKind: bridge?.VIEW?.MOVIE || 'movie',
      _uid: `series:${slug}:${id}`,
      seqNumber: index + 1,
      total_seasons: totalSeasons,
      total_episodes: totalEpisodes,
      status: safeText(raw.status, 'ongoing').toLowerCase(),
      latest_episode: safeText(raw.latest_episode),
      series_manifest: safeText(raw.series_manifest || raw.manifest || raw.index),
      manual_source: raw.manual_source !== false,
      verification_status: safeText(raw.verification_status, 'manual_trusted'),
      publish_allowed: raw.publish_allowed !== false,
      url: ''
    };
  }

  async function fetchJson(url, options = {}) {
    if (!url) throw new Error('Series JSON path missing');
    if (bridge?.fetchJson) return bridge.fetchJson(url, options);
    const response = await fetch(url, { cache: 'no-store', ...options });
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${url}`);
    return response.json();
  }

  async function loadManifest() {
    if (manifestPromise) return manifestPromise;
    manifestPromise = fetchJson(SERIES_MANIFEST_URL, { cache: 'no-store' })
      .then((data) => (data && typeof data === 'object' ? data : {}))
      .catch((error) => {
        console.info('Series manifest unavailable; Movie system continues without Series data.', error?.message || error);
        return { schema_version: 1, categories: {} };
      });
    return manifestPromise;
  }

  function categoryEntry(manifest, slug) {
    const categories = manifest?.categories;
    if (!categories || typeof categories !== 'object') return null;
    const label = categoryLabelFromSlug(slug);
    return categories[label] || categories[slug] || Object.values(categories).find((entry) => entry?.slug === slug) || null;
  }

  async function loadCategory(slug) {
    activeCategorySlug = slug;
    const manifest = await loadManifest();
    const entry = categoryEntry(manifest, slug);
    if (!entry || entry.visible === false || numberValue(entry.count) <= 0 || !entry.index) {
      catalogItems = [];
      return [];
    }
    try {
      const indexData = await fetchJson(entry.index, { cache: 'no-store' });
      const rawItems = Array.isArray(indexData.items) ? indexData.items : [];
      catalogItems = rawItems
        .filter((item) => item && item.publish_allowed !== false)
        .map((item, index) => normalizeSeriesSummary(item, index, slug));
      return catalogItems.slice();
    } catch (error) {
      console.warn(`Series category load failed: ${slug}`, error);
      catalogItems = [];
      return [];
    }
  }

  function mergeCategoryItems(seriesItems = catalogItems) {
    if (!bridge?.state || !Array.isArray(seriesItems)) return;
    const existing = bridge.state.currentItems.filter((item) => !isSeriesItem(item));
    const merged = [...existing, ...seriesItems].sort((a, b) => {
      const yearA = numberValue(a?.year);
      const yearB = numberValue(b?.year);
      if (yearA !== yearB) return yearB - yearA;
      const manualA = a?.manual_source === true || String(a?.verification_status || '').toLowerCase() === 'manual_trusted';
      const manualB = b?.manual_source === true || String(b?.verification_status || '').toLowerCase() === 'manual_trusted';
      if (manualA !== manualB) return manualA ? -1 : 1;
      return numberValue(a?.seqNumber, 999999) - numberValue(b?.seqNumber, 999999) || safeText(a?.name).localeCompare(safeText(b?.name));
    });
    merged.forEach((item, index) => {
      item.seqNumber = index + 1;
      if (isSeriesItem(item)) item._uid = `series:${activeCategorySlug}:${item.id}`;
      else if (!String(item._uid || '').startsWith('movie:')) item._uid = `movie:${item.id}:${index}`;
    });
    bridge.state.currentItems = merged;
  }

  function isSeriesItem(item) {
    return Boolean(item && (item._isSeries === true || String(item.content_kind || '').toLowerCase() === 'series'));
  }

  function isEpisodeItem(item) {
    return Boolean(item && String(item.content_kind || '').toLowerCase() === 'episode');
  }

  function currentSeriesId() {
    if (isEpisodeItem(bridge?.state?.currentItem)) return safeText(bridge.state.currentItem.series_id);
    return detailActive ? safeText(activeSeriesItem?.id) : '';
  }

  function createPosterHtml(item) {
    const logo = safeText(item.logo);
    if (!logo) {
      return '<div class="movie-poster-placeholder series-poster-placeholder"><i class="fas fa-layer-group"></i><span>Series Poster নেই</span></div>';
    }
    return `<img class="movie-poster" src="${escapeHtml(logo)}" alt="${escapeHtml(item.name)}" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-name="${escapeHtml(item.name)}">`;
  }

  function statusLabel(item) {
    const status = safeText(item.status, 'ongoing').toLowerCase();
    if (status === 'complete' || status === 'completed') return 'COMPLETE';
    return 'ONGOING';
  }

  function createSeriesCard(item, visualIndex) {
    const card = document.createElement('div');
    card.className = 'movie-card series-card tv-focusable';
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `${item.name}, ${item.total_seasons} seasons, ${item.total_episodes} episodes`);
    card.dataset.uid = item._uid;
    card.dataset.itemIndex = String(visualIndex);
    card.dataset.seriesId = item.id;

    const progress = seriesProgress(item.id);
    const progressWidth = progressPercent(progress);
    const playing = currentSeriesId() === item.id && isEpisodeItem(bridge?.state?.currentItem);
    const seasonBadge = item.total_seasons > 0 ? `S${item.total_seasons}` : 'SERIES';
    const summary = `${item.total_seasons || 0} Season${item.total_seasons === 1 ? '' : 's'} · ${item.total_episodes || 0} EP`;
    const resumeLabel = continueText(item);

    card.innerHTML = `
      <span class="movie-rank-badge">#${visualIndex + 1}</span>
      <span class="series-type-badge">SERIES</span>
      <span class="series-season-badge">${escapeHtml(seasonBadge)}</span>
      ${createPosterHtml(item)}
      <div class="series-card-overlay">
        <div class="movie-card-title">${escapeHtml(item.name)}</div>
        <div class="series-card-summary">${escapeHtml(summary)}</div>
        <div class="series-card-status-row">
          <span class="series-status ${statusLabel(item).toLowerCase()}">${statusLabel(item)}</span>
          ${item.latest_episode ? `<span class="series-latest">${escapeHtml(item.latest_episode)}</span>` : ''}
        </div>
        <div class="series-continue-row">
          <i class="fas ${progress ? 'fa-play-circle' : 'fa-list-ul'}"></i>
          <span>${escapeHtml(playing ? `PLAYING · S${twoDigits(bridge.state.currentItem.season_number)} · ${episodeDisplayLabel(bridge.state.currentItem)}` : resumeLabel)}</span>
        </div>
        <div class="series-progress-track" aria-hidden="true"><span style="width:${progressWidth.toFixed(2)}%"></span></div>
      </div>`;

    const image = card.querySelector('img');
    image?.addEventListener('error', () => {
      image.replaceWith(Object.assign(document.createElement('div'), {
        className: 'movie-poster-placeholder series-poster-placeholder',
        innerHTML: '<i class="fas fa-layer-group"></i><span>Series Poster নেই</span>'
      }));
    });
    return card;
  }

  function seriesCacheKey(item) {
    return safeText(item?.series_manifest || item?.manifest || item?.id);
  }

  function rememberSeriesCache(key, data) {
    if (!key || !data) return;
    if (seriesCache.has(key)) seriesCache.delete(key);
    seriesCache.set(key, data);
    while (seriesCache.size > MAX_CACHED_SERIES) {
      const first = seriesCache.keys().next().value;
      seriesCache.delete(first);
    }
  }

  function rememberSeasonCache(key, data) {
    if (!key || !data) return;
    if (seasonCache.has(key)) seasonCache.delete(key);
    seasonCache.set(key, data);
    while (seasonCache.size > MAX_CACHED_SEASONS) {
      const first = seasonCache.keys().next().value;
      seasonCache.delete(first);
    }
  }

  async function loadSeriesData(item) {
    const key = seriesCacheKey(item);
    if (seriesCache.has(key)) return seriesCache.get(key);
    const path = safeText(item.series_manifest);
    if (!path) throw new Error(`Series manifest missing: ${item.name}`);
    const data = await fetchJson(path, { cache: 'no-store' });
    rememberSeriesCache(key, data);
    return data;
  }

  function seasonList(data = activeSeriesData) {
    return Array.isArray(data?.seasons) ? data.seasons.slice().sort((a, b) => numberValue(a.number) - numberValue(b.number)) : [];
  }

  function findSeason(number) {
    return seasonList().find((season) => numberValue(season.number) === numberValue(number)) || null;
  }

  function episodePathForSeason(season) {
    return safeText(season?.path || season?.file || season?.episodes_file);
  }

  function normalizeEpisode(raw, index, seasonNumber) {
    const episodeNumber = Math.max(1, numberValue(raw.episode_number || raw.number, index + 1));
    const seriesId = safeText(activeSeriesItem?.id || activeSeriesData?.id || raw.series_id);
    const seriesName = safeText(activeSeriesData?.name || activeSeriesItem?.name || raw.series_name, 'Series');
    const episodeLabel = safeText(raw.episode_label, `Episode ${twoDigits(episodeNumber)}`);
    const episodeKey = safeText(raw.episode_key, `episode-${twoDigits(episodeNumber)}`);
    const episodeTitle = safeText(raw.episode_title || raw.title || raw.name, episodeLabel);
    const playbackRaw = {
      ...raw,
      id: safeText(raw.id, `${seriesId}-s${twoDigits(seasonNumber)}e${twoDigits(episodeNumber)}`),
      name: `${seriesName} — S${twoDigits(seasonNumber)} — ${episodeLabel} — ${episodeTitle}`,
      title: episodeTitle,
      category: safeText(activeSeriesItem?.category || raw.category),
      logo: safeText(raw.thumbnail || raw.logo || activeSeriesItem?.logo),
      content_kind: 'episode',
      series_id: seriesId,
      series_name: seriesName,
      series_manifest: safeText(activeSeriesItem?.series_manifest),
      series_logo: safeText(activeSeriesItem?.logo || activeSeriesData?.poster || activeSeriesData?.logo),
      season_number: seasonNumber,
      episode_number: episodeNumber,
      episode_label: episodeLabel,
      episode_key: episodeKey,
      episode_title: episodeTitle,
      manual_source: raw.manual_source !== false,
      verification_status: safeText(raw.verification_status, 'manual_trusted'),
      publish_allowed: raw.publish_allowed !== false,
      proxy_mode: safeText(raw.proxy_mode, 'direct_first'),
      header_profile: safeText(raw.header_profile, 'android_tv'),
      stream_type: safeText(raw.stream_type, 'media')
    };

    const normalized = bridge?.normalizeItem
      ? bridge.normalizeItem(playbackRaw, index, bridge.VIEW.MOVIE)
      : playbackRaw;
    normalized.content_kind = 'episode';
    normalized.series_id = seriesId;
    normalized.series_name = seriesName;
    normalized.series_manifest = playbackRaw.series_manifest;
    normalized.series_logo = playbackRaw.series_logo;
    normalized.season_number = seasonNumber;
    normalized.episode_number = episodeNumber;
    normalized.episode_label = episodeLabel;
    normalized.episode_key = episodeKey;
    normalized.episode_title = episodeTitle;
    normalized.duration_seconds = numberValue(raw.duration_seconds || raw.duration);
    normalized.release_date = safeText(raw.release_date);
    normalized.thumbnail = safeText(raw.thumbnail || raw.logo);
    normalized._uid = `episode:${seriesId}:s${twoDigits(seasonNumber)}:${episodeKey}`;
    normalized._sourceKind = bridge?.VIEW?.MOVIE || 'movie';
    normalized.seqNumber = index + 1;
    return normalized;
  }

  async function loadSeason(number, options = {}) {
    const season = findSeason(number);
    if (!season) {
      activeEpisodes = [];
      renderSeriesDetail();
      return [];
    }
    const requestId = ++seasonRequestId;
    const cacheKey = `${activeSeriesItem.id}:${numberValue(season.number)}`;
    activeSeasonNumber = numberValue(season.number);
    renderSeriesDetail({ loading: true });

    try {
      let payload = seasonCache.get(cacheKey);
      if (!payload) {
        payload = await fetchJson(episodePathForSeason(season), { cache: 'no-store' });
        rememberSeasonCache(cacheKey, payload);
      }
      if (requestId !== seasonRequestId) return [];
      const rawItems = Array.isArray(payload.items) ? payload.items : Array.isArray(payload.episodes) ? payload.episodes : [];
      activeEpisodes = rawItems
        .filter((episode) => episode && episode.publish_allowed !== false && episode.enabled !== false)
        .map((episode, index) => normalizeEpisode(episode, index, activeSeasonNumber));
      renderSeriesDetail();
      if (options.playEpisode) {
        const wanted = activeEpisodes.find((episode) => numberValue(episode.episode_number) === numberValue(options.playEpisode));
        if (wanted) playEpisode(wanted);
      }
      return activeEpisodes.slice();
    } catch (error) {
      if (requestId !== seasonRequestId) return [];
      activeEpisodes = [];
      renderSeriesDetail({ error: 'এই Season-এর Episode তালিকা লোড করা যায়নি' });
      console.error('Season load failed:', error);
      return [];
    }
  }

  function defaultSeasonNumber() {
    const progress = seriesProgress(activeSeriesItem?.id);
    const available = seasonList();
    if (progress && available.some((season) => numberValue(season.number) === numberValue(progress.season_number))) {
      return numberValue(progress.season_number);
    }
    return numberValue(activeSeriesData?.default_season || available[0]?.number, 1);
  }

  async function openSeries(item, options = {}) {
    if (!isSeriesItem(item)) return false;
    clearNextEpisodePrompt();
    detailActive = true;
    activeSeriesItem = item;
    activeCategorySlug = safeText(item._seriesCategorySlug || activeCategorySlug || bridge?.state?.selectedMovieCategory);
    bridge?.scrollSidebarToTop?.();
    bridge?.showListMessage?.('Series তথ্য লোড হচ্ছে…', 'fa-spinner', true);
    bridge?.setSidebarTitle?.(item.name);
    bridge?.setSidebarCount?.('Loading Series...');

    try {
      activeSeriesData = await loadSeriesData(item);
      activeSeasonNumber = numberValue(options.season || defaultSeasonNumber(), 1);
      await loadSeason(activeSeasonNumber, { playEpisode: options.episode || 0 });
      return true;
    } catch (error) {
      console.error('Series open failed:', error);
      detailActive = false;
      bridge?.showListMessage?.('Series তথ্য লোড করা যায়নি', 'fa-exclamation-triangle');
      bridge?.setSidebarCount?.('Series unavailable');
      return false;
    }
  }

  function seriesSummaryText() {
    const seasons = numberValue(activeSeriesData?.total_seasons || activeSeriesItem?.total_seasons || seasonList().length);
    const episodes = numberValue(activeSeriesData?.total_episodes || activeSeriesItem?.total_episodes);
    const status = safeText(activeSeriesData?.status || activeSeriesItem?.status, 'ongoing');
    return `${seasons} Season${seasons === 1 ? '' : 's'} · ${episodes} Episodes · ${status.toLowerCase() === 'complete' ? 'Complete' : 'Ongoing'}`;
  }

  function episodeState(episode) {
    const progress = episodeProgress(episode);
    const isCurrent = isEpisodeItem(bridge?.state?.currentItem) && bridge.state.currentItem._uid === episode._uid;
    if (isCurrent) return { label: 'PLAYING', className: 'playing' };
    if (progress) {
      const percent = progressPercent(progress);
      if (percent >= 92) return { label: 'WATCHED', className: 'watched' };
      if (numberValue(progress.position) >= 30) return { label: 'RESUME', className: 'resume' };
    }
    const releaseDate = Date.parse(episode.release_date || '');
    if (Number.isFinite(releaseDate) && Date.now() - releaseDate < 14 * 86400000) return { label: 'NEW', className: 'new' };
    return { label: '', className: '' };
  }

  function episodeDurationLabel(episode) {
    const seconds = numberValue(episode.duration_seconds);
    if (seconds > 0) {
      const minutes = Math.round(seconds / 60);
      return `${minutes} min`;
    }
    return safeText(episode.duration_label || episode.duration_text);
  }

  function episodeMetaText(episode) {
    return [episodeDurationLabel(episode), safeText(episode?.resolution)].filter(Boolean).join(' · ');
  }

  function episodeThumbnailHtml(episode) {
    const url = safeText(episode.thumbnail || episode.logo || activeSeriesItem?.logo);
    if (!url) return `<div class="series-episode-thumb-placeholder">E${twoDigits(episode.episode_number)}</div>`;
    return `<img src="${escapeHtml(url)}" alt="${escapeHtml(episode.episode_title)}" loading="lazy" decoding="async" referrerpolicy="no-referrer">`;
  }

  function renderSeriesDetail(options = {}) {
    if (!detailActive || !activeSeriesItem || !bridge?.sidebarList) return;
    const list = bridge.sidebarList;
    list.classList.remove('movie-grid', 'upcoming-grid');
    list.classList.add('series-detail-list');
    list.replaceChildren();

    const progress = seriesProgress(activeSeriesItem.id);
    const continueLabel = progress
      ? `Continue S${twoDigits(progress.season_number)} · ${safeText(progress.episode_label, `Episode ${twoDigits(progress.episode_number)}`)}`
      : 'Start Series';
    const hero = document.createElement('section');
    hero.className = 'series-detail-hero';
    hero.innerHTML = `
      <div class="series-detail-toolbar">
        <button type="button" class="series-back-button tv-focusable"><i class="fas fa-arrow-left"></i><span>Back to Movies</span></button>
        <button type="button" class="series-bookmark-button tv-focusable"><i class="fas fa-star"></i><span>Bookmark</span></button>
      </div>
      <div class="series-detail-main">
        <div class="series-detail-poster">${createPosterHtml(activeSeriesItem)}</div>
        <div class="series-detail-copy">
          <div class="series-detail-badges"><span>SERIES</span><span>${escapeHtml(safeText(activeSeriesItem.category))}</span></div>
          <h3>${escapeHtml(activeSeriesData?.name || activeSeriesItem.name)}</h3>
          <p class="series-detail-summary">${escapeHtml(seriesSummaryText())}</p>
          <p class="series-detail-description">${escapeHtml(safeText(activeSeriesData?.description || activeSeriesItem.description, 'Season নির্বাচন করে Episode দেখুন।'))}</p>
          <button type="button" class="series-continue-button tv-focusable"><i class="fas fa-play"></i><span>${escapeHtml(continueLabel)}</span></button>
        </div>
      </div>
      <div class="series-season-strip" role="tablist"></div>`;

    hero.querySelector('.series-back-button').addEventListener('click', closeDetail);
    hero.querySelector('.series-bookmark-button').addEventListener('click', (event) => toggleSeriesFavorite(event));
    hero.querySelector('.series-continue-button').addEventListener('click', () => continueSeries());
    const strip = hero.querySelector('.series-season-strip');
    seasonList().forEach((season) => {
      const number = numberValue(season.number);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `series-season-button tv-focusable${number === activeSeasonNumber ? ' active' : ''}`;
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-selected', number === activeSeasonNumber ? 'true' : 'false');
      button.textContent = number === 0 ? 'Specials' : `Season ${number}`;
      button.addEventListener('click', () => loadSeason(number));
      strip.appendChild(button);
    });
    list.appendChild(hero);

    const heading = document.createElement('div');
    heading.className = 'series-episode-heading';
    const currentSeason = findSeason(activeSeasonNumber);
    heading.innerHTML = `<strong>${activeSeasonNumber === 0 ? 'Specials' : `Season ${activeSeasonNumber}`}</strong><span>${numberValue(currentSeason?.episode_count || activeEpisodes.length)} Episodes</span>`;
    list.appendChild(heading);

    if (options.loading) {
      const loading = document.createElement('div');
      loading.className = 'series-detail-message';
      loading.innerHTML = '<i class="fas fa-spinner fa-spin"></i><span>Episode তালিকা লোড হচ্ছে…</span>';
      list.appendChild(loading);
    } else if (options.error) {
      const error = document.createElement('div');
      error.className = 'series-detail-message error';
      error.innerHTML = `<i class="fas fa-exclamation-triangle"></i><span>${escapeHtml(options.error)}</span>`;
      list.appendChild(error);
    } else if (!activeEpisodes.length) {
      const empty = document.createElement('div');
      empty.className = 'series-detail-message';
      empty.innerHTML = '<i class="fas fa-info-circle"></i><span>এই Season-এ কোনো Episode পাওয়া যায়নি</span>';
      list.appendChild(empty);
    } else {
      const episodeList = document.createElement('div');
      episodeList.className = 'series-episode-list';
      activeEpisodes.forEach((episode) => {
        const state = episodeState(episode);
        const row = document.createElement('button');
        row.type = 'button';
        row.className = `series-episode-card tv-focusable${state.className === 'playing' ? ' active' : ''}`;
        row.dataset.uid = episode._uid;
        row.dataset.episodeUid = episode._uid;
        row.innerHTML = `
          <span class="series-episode-thumb">${episodeThumbnailHtml(episode)}</span>
          <span class="series-episode-copy">
            <strong>${escapeHtml(episodePresentationTitle(episode))}</strong>
            <small>${escapeHtml(episodeMetaText(episode) || 'Quality information unavailable')}</small>
            ${episodeProgress(episode) && progressPercent(episodeProgress(episode)) < 92 ? `<span class="series-episode-progress"><span style="width:${progressPercent(episodeProgress(episode)).toFixed(2)}%"></span></span>` : ''}
            ${state.label ? `<em class="series-episode-state ${state.className}">${escapeHtml(state.label)}</em>` : ''}
          </span>
          <span class="series-episode-arrow"><i class="fas fa-chevron-right"></i></span>`;
        row.addEventListener('click', (event) => { event.stopPropagation(); playEpisode(episode); });
        episodeList.appendChild(row);
      });
      list.appendChild(episodeList);
    }

    bridge.setSidebarCount?.(seriesSummaryText());
    bridge.scrollSidebarToTop?.();
    updateSeriesFavoriteButton();
  }

  function closeDetail() {
    detailActive = false;
    const label = bridge?.movieOrder?.find((entry) => entry[1] === activeCategorySlug)?.[0] || 'Movies';
    bridge?.setSidebarTitle?.(`${label} Series & Movies`);
    seasonRequestId += 1;
    if (bridge?.sidebarList) bridge.sidebarList.classList.remove('series-detail-list');
    bridge?.renderCurrentList?.(true);
  }

  async function continueSeries() {
    const progress = seriesProgress(activeSeriesItem?.id);
    const season = progress?.season_number ?? defaultSeasonNumber();
    const episode = progress?.episode_number ?? 1;
    if (numberValue(season) !== activeSeasonNumber || !activeEpisodes.some((item) => numberValue(item.episode_number) === numberValue(episode))) {
      await loadSeason(numberValue(season), { playEpisode: numberValue(episode) });
      return;
    }
    const target = activeEpisodes.find((item) => numberValue(item.episode_number) === numberValue(episode)) || activeEpisodes[0];
    if (target) playEpisode(target);
  }

  function playEpisode(episode) {
    if (!episode || !bridge?.startPlayback) return;
    clearNextEpisodePrompt();
    bridge.startPlayback(episode, true);
    renderSeriesDetail();
    if (bridge?.state) bridge.state.drawerRenderedForSession = -1;
  }

  async function playRelativeEpisode(direction) {
    const current = bridge?.state?.currentItem;
    if (!isEpisodeItem(current)) return false;
    const currentIndex = activeEpisodes.findIndex((episode) => episode._uid === current._uid);
    if (currentIndex >= 0) {
      const next = activeEpisodes[currentIndex + direction];
      if (next) {
        playEpisode(next);
        return true;
      }
    }

    const seasons = seasonList();
    const seasonIndex = seasons.findIndex((season) => numberValue(season.number) === numberValue(current.season_number));
    const adjacentSeason = seasons[seasonIndex + direction];
    if (!adjacentSeason) return true;
    await loadSeason(numberValue(adjacentSeason.number));
    const next = direction > 0 ? activeEpisodes[0] : activeEpisodes[activeEpisodes.length - 1];
    if (next) playEpisode(next);
    return true;
  }

  async function openEpisodeContext(item) {
    if (!isEpisodeItem(item)) return false;
    const seriesItem = catalogItems.find((entry) => entry.id === item.series_id) || {
      id: item.series_id,
      name: item.series_name || 'Series',
      title: item.series_name || 'Series',
      category: item.category || '',
      logo: item.series_logo || item.logo || '',
      content_kind: 'series',
      _isSeries: true,
      _sourceKind: bridge?.VIEW?.MOVIE || 'movie',
      _uid: `series:${activeCategorySlug || 'saved'}:${item.series_id}`,
      series_manifest: item.series_manifest || '',
      total_seasons: item.total_seasons || 0,
      total_episodes: item.total_episodes || 0,
      status: item.status || 'ongoing',
      manual_source: true,
      verification_status: 'manual_trusted',
      publish_allowed: true,
      url: ''
    };
    return openSeries(seriesItem, {
      season: numberValue(item.season_number),
      episode: numberValue(item.episode_number)
    });
  }

  function handleCatalogClick(item) {
    if (isSeriesItem(item)) {
      openSeries(item);
      return true;
    }
    if (isEpisodeItem(item)) {
      openEpisodeContext(item);
      return true;
    }
    return false;
  }

  function handleDrawerClick(item) {
    if (isSeriesItem(item)) {
      openSeries(item).then(() => populateFullscreenDrawer(''));
      return true;
    }
    if (isEpisodeItem(item)) {
      playEpisode(item);
      return true;
    }
    return false;
  }

  function episodeByUid(uid) {
    return activeEpisodes.find((episode) => episode._uid === uid) || null;
  }

  function populateFullscreenDrawer(query = '') {
    const current = bridge?.state?.currentItem;
    const seriesContext = detailActive || isEpisodeItem(current);
    if (!seriesContext || !activeSeriesItem || !activeSeriesData || !bridge?.fsDrawerList) return false;

    const list = bridge.fsDrawerList;
    list.replaceChildren();
    list.classList.remove('movie-drawer-grid');
    list.classList.add('series-drawer-grid');
    const normalized = String(query || '').trim().toLowerCase();

    const header = document.createElement('div');
    header.className = 'series-drawer-context';
    header.innerHTML = `
      <button type="button" class="series-drawer-back"><i class="fas fa-arrow-left"></i></button>
      <span><strong>${escapeHtml(activeSeriesData.name || activeSeriesItem.name)}</strong><small>${isEpisodeItem(current) ? `Playing · S${twoDigits(current.season_number)} · ${episodeDisplayLabel(current)}` : seriesSummaryText()}</small></span>
      <div class="series-drawer-seasons"></div>`;
    header.querySelector('.series-drawer-back').addEventListener('click', () => {
      detailActive = false;
      bridge.fsDrawerList.classList.remove('series-drawer-grid');
      bridge.populateDefaultFullscreenDrawer?.(query);
    });
    const strip = header.querySelector('.series-drawer-seasons');
    seasonList().forEach((season) => {
      const number = numberValue(season.number);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = number === activeSeasonNumber ? 'active' : '';
      button.textContent = number === 0 ? 'SP' : `S${number}`;
      button.addEventListener('click', async () => {
        await loadSeason(number);
        populateFullscreenDrawer(query);
      });
      strip.appendChild(button);
    });
    list.appendChild(header);

    const items = activeEpisodes.filter((episode) => {
      const haystack = `${episode.episode_title} ${episode.episode_number}`.toLowerCase();
      return !normalized || haystack.includes(normalized);
    });
    items.forEach((episode) => {
      const state = episodeState(episode);
      const row = document.createElement('button');
      row.type = 'button';
      row.className = `fs-drawer-item series-drawer-episode tv-focusable${state.className === 'playing' ? ' active' : ''}`;
      row.dataset.uid = episode._uid;
      row.innerHTML = `
        <span class="fs-drawer-rank">${escapeHtml(episodeDisplayLabel(episode))}</span>
        <span class="fs-drawer-logo-wrap">${episodeThumbnailHtml(episode)}</span>
        <span class="fs-drawer-title">${escapeHtml(episodePresentationTitle(episode))}</span>
        ${state.label ? `<span class="series-drawer-state ${state.className}">${escapeHtml(state.label)}</span>` : ''}`;
      list.appendChild(row);
    });
    if (!items.length) {
      const message = document.createElement('div');
      message.className = 'fs-drawer-limit-note';
      message.textContent = 'কোনো Episode পাওয়া যায়নি';
      list.appendChild(message);
    }
    return true;
  }

  function decorateMetadata(item) {
    if (!isEpisodeItem(item)) return false;
    const title = document.getElementById('metaTitle');
    const category = document.getElementById('metaCategory');
    const watching = document.getElementById('metaWatchingCount');
    if (title) title.textContent = item.series_name || activeSeriesItem?.name || item.name;
    if (category) category.textContent = 'SERIES';
    if (watching) {
      watching.style.display = 'inline';
      watching.textContent = `S${twoDigits(item.season_number)} · ${episodePresentationTitle(item)}`;
    }
    const osdName = document.getElementById('osdName');
    if (osdName) osdName.textContent = `${item.series_name} — S${twoDigits(item.season_number)} · ${episodeDisplayLabel(item)}`;
    return true;
  }

  function updateActiveCards() {
    if (!bridge?.sidebarList) return;
    const activeSeriesId = currentSeriesId();
    bridge.sidebarList.querySelectorAll('.series-card[data-series-id]').forEach((card) => {
      const active = Boolean(activeSeriesId && card.dataset.seriesId === activeSeriesId);
      card.classList.toggle('active', active);
    });
    if (detailActive) {
      bridge.sidebarList.querySelectorAll('[data-episode-uid]').forEach((row) => {
        row.classList.toggle('active', row.dataset.episodeUid === bridge.state.currentItem?._uid);
      });
    }
    updateSeriesFavoriteButton();
  }

  function updateProgress(item, position, duration) {
    if (!isEpisodeItem(item)) return false;
    const now = Date.now();
    const progress = {
      series_id: item.series_id,
      season_number: numberValue(item.season_number),
      episode_number: numberValue(item.episode_number),
      episode_label: safeText(item.episode_label, `Episode ${twoDigits(item.episode_number)}`),
      episode_key: safeText(item.episode_key),
      episode_id: item.id,
      episode_uid: item._uid,
      episode_title: item.episode_title,
      position: Math.max(0, numberValue(position)),
      duration: Math.max(0, numberValue(duration)),
      updated_at: now
    };
    persistSeriesProgress(item.series_id, progress);
    persistEpisodeProgress(item, progress);
    return true;
  }

  function compactSeriesItem(item) {
    return {
      id: item.id,
      name: item.name,
      logo: item.logo || '',
      category: item.category || '',
      year: item.year || '',
      content_kind: 'series',
      total_seasons: item.total_seasons || 0,
      total_episodes: item.total_episodes || 0,
      status: item.status || 'ongoing',
      latest_episode: item.latest_episode || '',
      series_manifest: item.series_manifest || '',
      manual_source: item.manual_source !== false,
      verification_status: item.verification_status || 'manual_trusted',
      _sourceKind: bridge?.VIEW?.MOVIE || 'movie'
    };
  }

  function favoriteIds() {
    const key = bridge?.STORAGE_KEYS?.favorites || 'clicktv_favorites_v1';
    const value = readStorage(key, []);
    return Array.isArray(value) ? value : [];
  }

  function toggleSeriesFavorite(event) {
    event?.stopPropagation?.();
    const item = activeSeriesItem || catalogItems.find((entry) => entry.id === currentSeriesId());
    if (!item) return false;
    const favoritesKey = bridge?.STORAGE_KEYS?.favorites || 'clicktv_favorites_v1';
    const snapshotsKey = bridge?.STORAGE_KEYS?.favoriteItems || 'clicktv_favorite_items_v1';
    const favorites = favoriteIds();
    const active = favorites.includes(item.id);
    writeStorage(favoritesKey, active ? favorites.filter((id) => id !== item.id) : [...favorites, item.id]);
    const snapshots = readStorage(snapshotsKey, []);
    const clean = (Array.isArray(snapshots) ? snapshots : []).filter((entry) => (entry.id || entry.url) !== item.id);
    writeStorage(snapshotsKey, active ? clean : [compactSeriesItem(item), ...clean].slice(0, 300));
    bridge?.showToast?.(active ? 'Series Bookmark সরানো হয়েছে' : 'Series Bookmark যোগ করা হয়েছে');
    bridge?.updateFavoriteUi?.();
    updateSeriesFavoriteButton();
    return true;
  }

  function handleFavorite(uid, event) {
    const item = bridge?.state?.currentItems?.find((entry) => entry._uid === uid) || bridge?.state?.currentItem;
    if (isSeriesItem(item)) {
      activeSeriesItem = item;
      return toggleSeriesFavorite(event);
    }
    if (isEpisodeItem(item)) return toggleSeriesFavorite(event);
    return false;
  }

  function updateSeriesFavoriteButton() {
    const button = document.getElementById('favActionBtn');
    if (!button) return;
    const id = currentSeriesId();
    if (!id) return;
    button.classList.toggle('active', favoriteIds().includes(id));
    const localButton = bridge?.sidebarList?.querySelector('.series-bookmark-button');
    if (localButton) localButton.classList.toggle('active', favoriteIds().includes(id));
  }

  function clearNextEpisodePrompt() {
    if (nextEpisodeTimer) clearInterval(nextEpisodeTimer);
    nextEpisodeTimer = null;
    nextEpisodeCountdown = 0;
    document.querySelector('.series-next-episode-prompt')?.remove();
  }

  function nextEpisodeCandidate() {
    const current = bridge?.state?.currentItem;
    if (!isEpisodeItem(current)) return null;
    const index = activeEpisodes.findIndex((episode) => episode._uid === current._uid);
    return index >= 0 ? activeEpisodes[index + 1] || null : null;
  }

  function handleEnded() {
    const next = nextEpisodeCandidate();
    if (!next || !bridge?.videoContainer) return false;
    clearNextEpisodePrompt();
    nextEpisodeCountdown = NEXT_EPISODE_SECONDS;
    const prompt = document.createElement('div');
    prompt.className = 'series-next-episode-prompt';
    prompt.innerHTML = `
      <div><small>Next Episode</small><strong>${escapeHtml(episodePresentationTitle(next))}</strong></div>
      <button type="button" class="series-next-play">Play in <span>${nextEpisodeCountdown}</span>s</button>
      <button type="button" class="series-next-cancel">Cancel</button>`;
    prompt.querySelector('.series-next-play').addEventListener('click', () => playEpisode(next));
    prompt.querySelector('.series-next-cancel').addEventListener('click', clearNextEpisodePrompt);
    bridge.videoContainer.appendChild(prompt);
    nextEpisodeTimer = setInterval(() => {
      nextEpisodeCountdown -= 1;
      const counter = prompt.querySelector('.series-next-play span');
      if (counter) counter.textContent = String(Math.max(0, nextEpisodeCountdown));
      if (nextEpisodeCountdown <= 0) {
        clearNextEpisodePrompt();
        playEpisode(next);
      }
    }, 1000);
    return true;
  }

  function resetDetail(options = {}) {
    detailActive = false;
    seasonRequestId += 1;
    clearNextEpisodePrompt();
    bridge?.sidebarList?.classList.remove('series-detail-list');
    const preservePlaybackContext = options.preservePlaybackContext !== false && isEpisodeItem(bridge?.state?.currentItem);
    if (preservePlaybackContext) return;
    activeSeriesItem = null;
    activeSeriesData = null;
    activeSeasonNumber = 0;
    activeEpisodes = [];
  }

  function handlePlaybackSelection(item) {
    clearNextEpisodePrompt();
    if (isEpisodeItem(item)) return false;
    resetDetail({ preservePlaybackContext: false });
    return false;
  }

  function countCurrentSeries() {
    return bridge?.state?.currentItems?.filter(isSeriesItem).length || 0;
  }

  function init(value) {
    if (initialized) return api;
    bridge = value;
    initialized = true;
    return api;
  }

  const api = {
    init,
    isSeriesItem,
    isEpisodeItem,
    loadCategory,
    mergeCategoryItems,
    countCurrentSeries,
    createSeriesCard,
    handleCatalogClick,
    handleDrawerClick,
    openSeries,
    openEpisodeContext,
    closeDetail,
    resetDetail,
    playRelativeEpisode,
    populateFullscreenDrawer,
    decorateMetadata,
    updateActiveCards,
    updateProgress,
    handleFavorite,
    handleEnded,
    handlePlaybackSelection,
    episodeByUid,
    episodePresentationTitle,
    get detailActive() { return detailActive; },
    get activeSeriesItem() { return activeSeriesItem; },
    get activeEpisodes() { return activeEpisodes.slice(); }
  };

  window.ClickTvSeries = api;
})();
