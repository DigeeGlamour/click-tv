'use strict';

// CLICKTV_SERIES_FINAL_DESIGN_20260806_V2

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
  let detailRequestId = 0;
  let catalogSnapshot = null;
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

  // The number the source published for an episode - "04", "Episode 04", or a
  // batch link covering "01-07". Published data carries it outright since the
  // publisher started writing episode_start_number; anything published before
  // that is read back out of its own key/label rather than being re-guessed.
  const EPISODE_NUMBER_RE = /(?:^|[^\d])(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?(?!\d)/;

  function publishedEpisodeRange(raw) {
    const start = Number(raw?.episode_start_number);
    if (Number.isFinite(start) && start > 0) {
      const end = Number(raw?.episode_end_number);
      return { start, end: Number.isFinite(end) && end >= start ? end : start };
    }
    for (const candidate of [raw?.episode_key, raw?.episode_label, raw?.episode_title, raw?.title]) {
      const match = EPISODE_NUMBER_RE.exec(safeText(candidate));
      if (!match) continue;
      const first = Number(match[1]);
      const second = match[2] ? Number(match[2]) : first;
      return second >= first ? { start: first, end: second } : { start: second, end: first };
    }
    // An episode the source never numbered keeps no number. Filling one in
    // would read exactly like a real one on the card.
    return { start: null, end: null };
  }

  /**
   * Numeric ascending, so E02 comes before E10 and a season published out of
   * order still reads correctly. A batch link leads the run it covers, source
   * order breaks ties, and unnumbered extras keep their order at the end.
   * Ordering only - no episode is renumbered here, because episode_number is
   * half of the stored identity and must stay exactly what was published.
   */
  function orderEpisodes(list) {
    return list
      .map((episode, index) => ({ episode, index, range: publishedEpisodeRange(episode) }))
      .sort((a, b) => {
        const aMissing = a.range.start === null;
        const bMissing = b.range.start === null;
        if (aMissing !== bMissing) return aMissing ? 1 : -1;
        if (!aMissing && a.range.start !== b.range.start) return a.range.start - b.range.start;
        if (!aMissing && a.range.end !== b.range.end) return b.range.end - a.range.end;
        return a.index - b.index;
      })
      .map((entry) => entry.episode);
  }

  /** The badge a viewer reads: the real number, or nothing when there isn't one. */
  function episodeBadge(episode) {
    const start = Number(episode?.episode_start_number);
    if (!Number.isFinite(start) || start <= 0) return '';
    const end = Number(episode?.episode_end_number);
    return Number.isFinite(end) && end > start
      ? `E${twoDigits(start)}-${twoDigits(end)}`
      : `E${twoDigits(start)}`;
  }

  /**
   * An episode with nothing to play is shown as unavailable rather than
   * offered and then failing. Mirrors the app's own isPlayable rule.
   */
  function episodePlayable(episode) {
    if (!episode || episode.metadata_only) return false;
    if (episode.playback_id || episode.url || episode.link || episode.stream_url) return true;
    if (Array.isArray(episode._sources) && episode._sources.some((source) => source?.url || source?.playback_id)) return true;
    return Array.isArray(episode.backups) && episode.backups.some((source) => (
      typeof source === 'string' ? Boolean(source.trim()) : Boolean(source?.url || source?.link || source?.stream_url || source?.playback_id)
    ));
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

  function episodeProgressKey(seriesId, seasonNumber, episodeNumber) {
    return `${safeText(seriesId)}:s${twoDigits(seasonNumber)}:e${twoDigits(episodeNumber)}`;
  }

  function episodeProgressMap() {
    const value = readStorage(EPISODE_PROGRESS_KEY, {});
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  }

  function episodeProgress(episode) {
    if (!episode) return null;
    return episodeProgressMap()[episodeProgressKey(episode.series_id, episode.season_number, episode.episode_number)] || null;
  }

  function persistEpisodeProgress(episode, progress) {
    if (!episode || !progress) return;
    const all = episodeProgressMap();
    const key = episodeProgressKey(episode.series_id, episode.season_number, episode.episode_number);
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
    return `Continue S${twoDigits(progress.season_number)} E${twoDigits(progress.episode_number)}`;
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
      // Its own category, remembered on the record. Web Series loads all seven
      // categories in turn, so the module-level "active" slug is whichever one
      // finished last - not this series' own.
      _seriesCategorySlug: slug,
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
    const manualMovies = existing.filter((item) => item?.manual_source === true || String(item?.verification_status || '').toLowerCase() === 'manual_trusted');
    const otherMovies = existing.filter((item) => !manualMovies.includes(item));
    const merged = [...manualMovies, ...seriesItems, ...otherMovies];
    merged.forEach((item, index) => {
      item.seqNumber = index + 1;
      if (isSeriesItem(item)) item._uid = `series:${safeText(item._seriesCategorySlug, activeCategorySlug)}:${item.id}`;
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

  /**
   * The poster as the DETAIL card draws it.
   *
   * `createPosterHtml` is the catalogue card's poster and carries
   * `.movie-poster`, which the detail card has no size for - dropped into the
   * card it grew to the full width and painted over the title and the buttons.
   * The detail's own class is what the card is built around.
   */
  function seriesDetailPosterHtml(item) {
    const logo = safeText(item?.logo);
    if (!logo) {
      return '<div class="movie-detail-poster movie-detail-poster-fallback" role="img" aria-label="'
        + escapeHtml((item?.name || 'Series') + ' poster unavailable') + '">'
        + '<i class="fas fa-layer-group" aria-hidden="true"></i><span>Poster নেই</span></div>';
    }
    return `<img class="movie-detail-poster" src="${escapeHtml(logo)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">`;
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

  /**
   * Is this panel the player's episode column, or the series page?
   *
   * Set when an episode starts playing, cleared when a series is opened or the
   * detail is closed - both of which run through this module, so the answer
   * cannot go stale the way a class left on <html> did.
   */
  let playerPanelMode = false;

  function createSeriesCard(item, visualIndex) {
    const card = document.createElement('div');
    card.className = 'catalog-series-card series-card tv-focusable';
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
      <div class="series-catalog-poster">
        <span class="series-type-badge">SERIES</span>
        <span class="series-season-badge">${escapeHtml(seasonBadge)}</span>
        ${createPosterHtml(item)}
      </div>
      <div class="series-catalog-copy">
        <h3>${escapeHtml(item.name)}</h3>
        <p>${escapeHtml(summary)}</p>
        <button type="button" class="series-catalog-continue" tabindex="-1">${escapeHtml(playing ? `PLAYING · S${twoDigits(bridge.state.currentItem.season_number)} E${twoDigits(bridge.state.currentItem.episode_number)}` : resumeLabel)}</button>
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
    const episodeTitle = safeText(raw.episode_title || raw.title || raw.name, `Episode ${episodeNumber}`);
    const playbackRaw = {
      ...raw,
      id: safeText(raw.id, `${seriesId}-s${twoDigits(seasonNumber)}e${twoDigits(episodeNumber)}`),
      name: `${seriesName} — S${twoDigits(seasonNumber)} E${twoDigits(episodeNumber)} — ${episodeTitle}`,
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
    normalized.episode_title = episodeTitle;
    // The number the source actually published, kept beside the position-based
    // episode_number so the card can show "Episode 10" instead of "E02".
    const published = publishedEpisodeRange(raw);
    if (published.start !== null) {
      normalized.episode_start_number = published.start;
      normalized.episode_end_number = published.end;
    }
    normalized.duration_seconds = numberValue(raw.duration_seconds || raw.duration);
    normalized.release_date = safeText(raw.release_date);
    normalized.thumbnail = safeText(raw.thumbnail || raw.logo);
    normalized._uid = `episode:${seriesId}:s${twoDigits(seasonNumber)}e${twoDigits(episodeNumber)}`;
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
      activeEpisodes = orderEpisodes(
        rawItems.filter((episode) => episode && episode.publish_allowed !== false && episode.enabled !== false)
      ).map((episode, index) => normalizeEpisode(episode, index, activeSeasonNumber));
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

  function captureCatalogSnapshot() {
    if (!bridge?.state) return null;
    return {
      renderedCount: Math.max(1, numberValue(bridge.state.renderedCount, 20)),
      scrollTop: Math.max(0, numberValue(bridge.getSidebarScrollTop?.(), 0)),
      selectedMovieCategory: safeText(bridge.state.selectedMovieCategory),
      searchQuery: safeText(bridge.state.searchQuery),
      sortMode: safeText(bridge.state.currentSortMode, 'default')
    };
  }

  function restoreCatalogSnapshot(snapshot = catalogSnapshot) {
    bridge?.setSeriesDetailMode?.(false);
    if (!snapshot) {
      bridge?.renderCurrentList?.(true);
      return;
    }
    bridge?.renderCurrentList?.(true, {
      initialLimit: snapshot.renderedCount,
      preserveScroll: true
    });
    bridge?.restoreSidebarScroll?.(snapshot.scrollTop);
  }

  async function openSeries(item, options = {}) {
    if (!isSeriesItem(item)) return false;
    clearNextEpisodePrompt();
    // Opening a series is always the page, whatever was playing a moment ago.
    playerPanelMode = false;
    const requestId = ++detailRequestId;
    if (!detailActive) catalogSnapshot = captureCatalogSnapshot();
    detailActive = true;
    bridge?.setSeriesDetailMode?.(true);
    activeSeriesItem = item;
    activeSeriesData = null;
    activeEpisodes = [];
    activeCategorySlug = safeText(item._seriesCategorySlug || activeCategorySlug || bridge?.state?.selectedMovieCategory);
    bridge?.scrollSidebarToTop?.();
    bridge?.showListMessage?.('Series তথ্য লোড হচ্ছে…', 'fa-spinner', true);
    bridge?.setSidebarCount?.('');

    try {
      const loaded = await loadSeriesData(item);
      if (requestId !== detailRequestId || !detailActive || activeSeriesItem?.id !== item.id) return false;
      activeSeriesData = loaded;
      activeSeasonNumber = numberValue(options.season || defaultSeasonNumber(), 1);
      await loadSeason(activeSeasonNumber, { playEpisode: options.episode || 0 });
      return requestId === detailRequestId && detailActive;
    } catch (error) {
      if (requestId !== detailRequestId) return false;
      console.error('Series open failed:', error);
      detailActive = false;
      bridge?.setSeriesDetailMode?.(false);
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

  /**
   * The facts line under a series title: year, category, rating and genres,
   * each shown only when the catalogue actually carries it. A series with no
   * rating shows no rating - it does not show an empty one, and it never
   * borrows a number from somewhere else. The rating always travels with the
   * source that issued it, so a TMDB score is never presented as IMDb.
   */
  function seriesFacts() {
    const source = activeSeriesData || activeSeriesItem || {};
    const fallback = activeSeriesItem || {};
    // Year and Category are on the card's own facts line now, so repeating
    // them underneath it is the clutter the movie detail was just cleared of.
    const facts = [];
    const genres = (Array.isArray(source.genres) ? source.genres : Array.isArray(fallback.genres) ? fallback.genres : [])
      .map((genre) => safeText(genre))
      .filter(Boolean);
    if (genres.length) facts.push(['Genres', genres.join(', ')]);
    const rating = safeText(source.rating ?? fallback.rating);
    if (rating) {
      const ratingSource = safeText(source.rating_source || fallback.rating_source);
      facts.push(['Rating', ratingSource ? `${rating} (${ratingSource})` : rating]);
    }
    return facts;
  }

  function seriesFactsHtml() {
    const facts = seriesFacts();
    if (!facts.length) return '';
    return `<dl class="series-detail-facts">${facts.map((pair) => (
      `<div class="series-detail-fact"><dt>${escapeHtml(pair[0])}</dt><dd>${escapeHtml(pair[1])}</dd></div>`
    )).join('')}</dl>`;
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
    // No runtime published means no runtime shown. The old fallback printed
    // the word "Episode" in the runtime slot, which reads like a measurement.
    return safeText(episode.duration_label || episode.duration_text);
  }

  /** The sub-line under an episode title: only the facts this episode has. */
  function episodeSubline(episode) {
    return [episodeDurationLabel(episode), safeText(episode.resolution)]
      .filter(Boolean)
      .join(' · ');
  }

  function episodeThumbnailHtml(episode) {
    const url = safeText(episode.thumbnail || episode.logo || activeSeriesItem?.logo);
    if (!url) return `<div class="series-episode-thumb-placeholder">${escapeHtml(episodeBadge(episode) || 'EP')}</div>`;
    return `<img src="${escapeHtml(url)}" alt="${escapeHtml(episode.episode_title)}" loading="lazy" decoding="async" referrerpolicy="no-referrer">`;
  }

  function renderSeriesDetail(options = {}) {
    if (!detailActive || !activeSeriesItem || !bridge?.sidebarList) return;
    const list = bridge.sidebarList;
    bridge?.setSeriesDetailMode?.(true);
    list.classList.remove('movie-grid', 'upcoming-grid');
    list.classList.add('series-detail-list');
    list.replaceChildren();

    const progress = seriesProgress(activeSeriesItem.id);
    const continueLabel = progress
      ? `Continue S${twoDigits(progress.season_number)} E${twoDigits(progress.episode_number)}`
      : 'Start Series';

    const source = activeSeriesData || activeSeriesItem;
    const poster = safeText(source.logo || activeSeriesItem.logo);
    // The description the record really carries. The old stand-in - "Season
    // নির্বাচন করে Episode দেখুন।" - is an instruction, not a synopsis, and it
    // printed on every series that had none.
    const description = safeText(activeSeriesData?.description || activeSeriesItem.description);
    const seasons = numberValue(activeSeriesData?.total_seasons || activeSeriesItem?.total_seasons || seasonList().length);
    const episodeCount = activeEpisodes.length;

    const metaBits = [];
    const year = numberValue(source.year || activeSeriesItem.year);
    if (year > 0) metaBits.push(String(year));
    const category = safeText(source.category || activeSeriesItem.category);
    if (category) metaBits.push(category);
    metaBits.push(`Season ${numberValue(activeSeasonNumber)}`);
    if (episodeCount) metaBits.push(`${episodeCount} Episode${episodeCount === 1 ? '' : 's'}`);
    else if (seasons) metaBits.push(`${seasons} Season${seasons === 1 ? '' : 's'}`);

    // Both labels are rendered; the stylesheet shows the pair that belongs to
    // the state the page is actually in. The side column during playback is
    // 390px wide and the demo draws only the episode list there, so the title
    // card is hidden by the same rule rather than by a flag read here - this
    // code runs before `movie-playback-context` is set, and a flag read now
    // would answer for the state the page was in a moment ago.
    const detail = document.createElement('section');
    detail.className = 'series-detail-shell movie-detail-inner detail-section-inner'
      + (playerPanelMode ? ' series-panel-mode' : '');
    detail.innerHTML = `
      <div class="detail-nav-bar">
        <span class="detail-nav-heading"><span class="detail-nav-dot" aria-hidden="true"></span>${
          playerPanelMode ? `S${numberValue(activeSeasonNumber)} Episodes` : 'Series Details'}</span>
        <button type="button" class="btn-back-pill series-back-button tv-focusable"><i class="fas fa-arrow-left" aria-hidden="true"></i> <span>${
          playerPanelMode ? 'Back to Series' : 'Back to Movies'}</span></button>
      </div>
      <div class="detail-hero-card movie-detail-hero series-detail-hero">
        ${poster ? '<div class="detail-backdrop movie-detail-backdrop"></div>' : ''}
        <div class="detail-inner-grid movie-detail-grid">
          <div class="detail-poster-wrap movie-detail-poster-wrap">${seriesDetailPosterHtml(activeSeriesItem)}</div>
          <div class="detail-info movie-detail-main">
            <span class="detail-type-pill movie-detail-kind"><span class="red-bullet" aria-hidden="true"></span>SERIES</span>
            <h1 class="detail-heading movie-detail-title">${escapeHtml(source.name || activeSeriesItem.name)}</h1>
            <div class="detail-meta-row movie-detail-meta">
              ${metaBits.map((bit) => `<span>${escapeHtml(bit)}</span>`).join('<i class="meta-dot movie-detail-dot" aria-hidden="true">\u2022</i>')}
            </div>
            ${description ? `<p class="detail-desc movie-detail-plot">${escapeHtml(description)}</p>` : ''}
            <div class="detail-actions-row movie-detail-actions">
              <button type="button" class="btn-play-white series-continue-button tv-focusable"><i class="fas fa-play" aria-hidden="true"></i> ${escapeHtml(continueLabel)}</button>
            </div>
            ${seriesFactsHtml()}
          </div>
        </div>
      </div>
      <section class="movie-row series-episode-section">
        <div class="movie-row-head series-when-browsing-block">
          <div>
            <h2>S${numberValue(activeSeasonNumber)} Episodes</h2>
            <p>${escapeHtml(seriesSummaryText())}</p>
          </div>
        </div>
        <div class="series-season-strip" role="tablist"></div>
        <div class="series-episode-region"></div>
      </section>`;

    // The backdrop image is set as a property, never written into the markup:
    // a URL inside a quoted attribute is how the movie card's backdrop stayed
    // invisible for weeks.
    const backdropLayer = detail.querySelector('.movie-detail-backdrop');
    if (backdropLayer && poster) {
      backdropLayer.style.backgroundImage = `url("${poster.replaceAll('"', '%22')}")`;
    }

    detail.querySelector('.series-back-button').addEventListener('click', closeDetail);
    detail.querySelector('.series-continue-button')?.addEventListener('click', () => continueSeries());

    const strip = detail.querySelector('.series-season-strip');
    // One season is not a choice. The row above already names it.
    if (seasonList().length < 2) strip.hidden = true;
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

    const region = detail.querySelector('.series-episode-region');
    if (options.loading) {
      region.innerHTML = '<div class="series-detail-message"><i class="fas fa-spinner fa-spin"></i><span>Episode তালিকা লোড হচ্ছে…</span></div>';
    } else if (options.error) {
      region.innerHTML = `<div class="series-detail-message error"><i class="fas fa-exclamation-triangle"></i><span>${escapeHtml(options.error)}</span></div>`;
    } else if (!activeEpisodes.length) {
      region.innerHTML = '<div class="series-detail-message"><i class="fas fa-info-circle"></i><span>এই Season-এ কোনো Episode পাওয়া যায়নি</span></div>';
    } else {
      const episodeList = document.createElement('div');
      episodeList.className = 'series-episode-list';
      activeEpisodes.forEach((episode) => {
        const state = episodeState(episode);
        const playable = episodePlayable(episode);
        const badge = episodeBadge(episode);
        const subline = episodeSubline(episode);
        const row = document.createElement('button');
        row.type = 'button';
        row.className = `series-episode-card tv-focusable${state.className === 'playing' ? ' active' : ''}${playable ? '' : ' unavailable'}`;
        row.dataset.uid = episode._uid;
        row.dataset.episodeUid = episode._uid;
        if (!playable) row.disabled = true;
        // The demo puts a control on the right of every episode: a play glyph,
        // and the now-playing bars on the one that is playing. Ours had an
        // empty placeholder there, so a card did not read as pressable.
        const trailing = !playable
          ? '<em class="series-episode-state unavailable">UNAVAILABLE</em>'
          : state.className === 'playing'
            ? '<i class="fas fa-chart-simple series-episode-icon is-playing" aria-hidden="true"></i>'
            : state.label
              ? `<em class="series-episode-state ${state.className}">${escapeHtml(state.label)}</em>`
              : '<i class="far fa-circle-play series-episode-icon" aria-hidden="true"></i>';
        row.innerHTML = `
          <span class="series-episode-number${badge.includes('-') ? ' range' : ''}">${escapeHtml(badge || '·')}</span>
          <span class="series-episode-copy">
            <strong>${escapeHtml(episode.episode_title)}</strong>
            ${subline ? `<small>${escapeHtml(subline)}</small>` : ''}
          </span>
          ${trailing}`;
        if (playable) {
          row.addEventListener('click', (event) => { event.stopPropagation(); playEpisode(episode); });
        }
        episodeList.appendChild(row);
      });
      region.appendChild(episodeList);
    }

    list.appendChild(detail);
    bridge.setSidebarCount?.('');
    updateSeriesFavoriteButton();
  }

  function closeDetail() {
    if (!detailActive) return;
    playerPanelMode = false;
    detailActive = false;
    detailRequestId += 1;
    seasonRequestId += 1;
    bridge?.sidebarList?.classList.remove('series-detail-list');
    const snapshot = catalogSnapshot;
    catalogSnapshot = null;
    restoreCatalogSnapshot(snapshot);
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
    if (!episode || !bridge?.startPlayback) return Promise.resolve(false);
    clearNextEpisodePrompt();
    // From here the panel lives in the player's side column.
    if (!playerPanelMode) {
      playerPanelMode = true;
      renderSeriesDetail();
    }
    const result = bridge.startPlayback(episode, true);
    if (bridge?.state) bridge.state.drawerRenderedForSession = -1;
    return Promise.resolve(result).finally(() => {
      updateActiveCards();
      const drawer = document.getElementById('fsDrawer');
      if (drawer?.classList.contains('open')) {
        populateFullscreenDrawer(document.getElementById('fsDrawerSearch')?.value || '');
      }
    });
  }

  async function playRelativeEpisode(direction) {
    const current = bridge?.state?.currentItem;
    if (!isEpisodeItem(current)) return false;
    const currentIndex = activeEpisodes.findIndex((episode) => episode._uid === current._uid);
    if (currentIndex >= 0) {
      const ahead = direction > 0
        ? activeEpisodes.slice(currentIndex + 1)
        : activeEpisodes.slice(0, currentIndex).reverse();
      const next = ahead.find(episodePlayable);
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
    const candidates = direction > 0 ? activeEpisodes : activeEpisodes.slice().reverse();
    const next = candidates.find(episodePlayable);
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
    bridge?.rememberFullscreenDrawerScroll?.();
    list.replaceChildren();
    list.classList.remove('movie-drawer-grid', 'channel-drawer-grid');
    list.classList.add('series-drawer-detail');
    const contextKey = `series:${activeSeriesItem.id}:season:${activeSeasonNumber}`;
    list.dataset.contextKey = contextKey;
    const normalized = String(query || '').trim().toLowerCase();

    const progress = seriesProgress(activeSeriesItem.id);
    const continueLabel = progress
      ? `Continue S${twoDigits(progress.season_number)} E${twoDigits(progress.episode_number)}`
      : 'Start Series';

    const shell = document.createElement('section');
    shell.className = 'fs-series-shell';
    shell.innerHTML = `
      <div class="fs-series-top">
        <div class="fs-series-poster">${createPosterHtml(activeSeriesItem)}</div>
        <div class="fs-series-copy">
          <h3>${escapeHtml(activeSeriesData.name || activeSeriesItem.name)}</h3>
          <p class="fs-series-summary">${escapeHtml(seriesSummaryText())}</p>
          <p class="fs-series-description">${escapeHtml(safeText(activeSeriesData.description || activeSeriesItem.description, 'Season নির্বাচন করে Episode দেখুন।'))}</p>
          <button type="button" class="fs-series-continue">${escapeHtml(continueLabel)}</button>
        </div>
      </div>
      <div class="fs-series-seasons" role="tablist"></div>
      <div class="fs-series-episodes"></div>`;

    shell.querySelector('.fs-series-continue').addEventListener('click', () => continueSeries());

    const strip = shell.querySelector('.fs-series-seasons');
    seasonList().forEach((season) => {
      const number = numberValue(season.number);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `fs-series-season${number === activeSeasonNumber ? ' active' : ''}`;
      button.textContent = number === 0 ? 'Specials' : `Season ${number}`;
      button.addEventListener('click', async () => {
        bridge?.rememberFullscreenDrawerScroll?.();
        await loadSeason(number);
        populateFullscreenDrawer(query);
      });
      strip.appendChild(button);
    });

    const episodeRegion = shell.querySelector('.fs-series-episodes');
    const items = activeEpisodes.filter((episode) => {
      const haystack = `${episode.episode_title} ${episode.episode_number}`.toLowerCase();
      return !normalized || haystack.includes(normalized);
    });

    items.forEach((episode) => {
      const status = episodeState(episode);
      const playable = episodePlayable(episode);
      const badge = episodeBadge(episode);
      const subline = episodeSubline(episode);
      const row = document.createElement('button');
      row.type = 'button';
      row.className = `fs-series-episode fs-drawer-item tv-focusable${status.className === 'playing' ? ' active' : ''}${playable ? '' : ' unavailable'}`;
      row.dataset.uid = episode._uid;
      if (!playable) row.disabled = true;
      const trailing = !playable
        ? '<em class="fs-series-state unavailable">UNAVAILABLE</em>'
        : status.label
          ? `<em class="fs-series-state ${status.className}">${escapeHtml(status.label)}</em>`
          : '<span class="fs-series-state-placeholder" aria-hidden="true"></span>';
      row.innerHTML = `
        <span class="fs-series-episode-number${badge.includes('-') ? ' range' : ''}">${escapeHtml(badge || '·')}</span>
        <span class="fs-series-episode-copy">
          <strong>${escapeHtml(episode.episode_title)}</strong>
          ${subline ? `<small>${escapeHtml(subline)}</small>` : ''}
        </span>
        ${trailing}`;
      if (playable) {
        row.addEventListener('click', (event) => {
          event.stopPropagation();
          void playEpisode(episode);
        });
      }
      episodeRegion.appendChild(row);
    });

    if (!items.length) {
      const message = document.createElement('div');
      message.className = 'fs-drawer-limit-note';
      message.textContent = 'কোনো Episode পাওয়া যায়নি';
      episodeRegion.appendChild(message);
    }

    list.appendChild(shell);
    bridge?.restoreFullscreenDrawerScroll?.(contextKey);
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
      watching.textContent = `S${twoDigits(item.season_number)} E${twoDigits(item.episode_number)} · ${item.episode_title}`;
    }
    const osdName = document.getElementById('osdName');
    if (osdName) osdName.textContent = `${item.series_name} — S${twoDigits(item.season_number)} E${twoDigits(item.episode_number)}`;
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
    if (index < 0) return null;
    // An episode with nothing to play is not offered as "next" - the prompt
    // would count down to a failure.
    return activeEpisodes.slice(index + 1).find(episodePlayable) || null;
  }

  function handleEnded() {
    const next = nextEpisodeCandidate();
    if (!next || !bridge?.videoContainer) return false;
    clearNextEpisodePrompt();
    nextEpisodeCountdown = NEXT_EPISODE_SECONDS;
    const prompt = document.createElement('div');
    prompt.className = 'series-next-episode-prompt';
    prompt.innerHTML = `
      <div><small>Next Episode</small><strong>${escapeHtml([episodeBadge(next), next.episode_title].filter(Boolean).join(' · '))}</strong></div>
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
    playerPanelMode = false;
    detailActive = false;
    detailRequestId += 1;
    seasonRequestId += 1;
    clearNextEpisodePrompt();
    bridge?.sidebarList?.classList.remove('series-detail-list');
    bridge?.setSeriesDetailMode?.(false);
    if (options.preserveCatalogSnapshot !== true) catalogSnapshot = null;
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
    // Pure helpers, exported so the browser smoke test can drive the ordering
    // and labelling rules directly instead of inferring them from the DOM.
    publishedEpisodeRange,
    orderEpisodes,
    episodeBadge,
    episodePlayable,
    seriesFacts,
    get detailActive() { return detailActive; },
    get activeSeriesItem() { return activeSeriesItem; },
    get activeEpisodes() { return activeEpisodes.slice(); }
  };

  window.ClickTvSeries = api;
})();
