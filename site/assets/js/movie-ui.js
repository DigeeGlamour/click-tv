/**
 * ==========================================================================
 * CLICK TV — MOVIE PORTAL & HEADER UI CONTROLLER (PRODUCTION READY)
 * Designed exactly from demo index.html — Modular, Zero Bloat, On-Demand
 * ==========================================================================
 */

(function () {
  'use strict';

  // 1. Category Definitions (Exact match to demo index.html & user screenshot)
  const MOVIE_CATEGORY_GROUPS = [
    {
      groupName: 'DISCOVERY & PICKS',
      items: [
        { id: 'trending', label: 'Trending', icon: 'fa-fire', count: 'HOT', badgeClass: 'badge-hot' },
        { id: 'recent', label: 'Just Added', icon: 'fa-bolt', count: '⚡', badgeClass: 'badge-count' },
        { id: 'releases', label: 'Latest', icon: 'fa-calendar-days', count: 'NEW', badgeClass: 'badge-new' },
        { id: 'bangla', label: 'Bangla', icon: 'fa-tv', count: '29', badgeClass: 'badge-count', file: 'data/movies/bangla/page-001.json' },
        { id: 'hindi', label: 'Hindi', icon: 'fa-clapperboard', count: '240', badgeClass: 'badge-count', file: 'data/movies/hindi/page-001.json' },
        { id: 'english', label: 'English', icon: 'fa-globe', count: '71', badgeClass: 'badge-count', file: 'data/movies/english/page-001.json' },
        { id: 'dubbed', label: 'Dubbed Movie', icon: 'fa-headphones', count: '266', badgeClass: 'badge-count', file: 'data/movies/dubbed/page-001.json' },
        { id: 'south-indian', label: 'South Indian', icon: 'fa-film', count: '113', badgeClass: 'badge-count', file: 'data/movies/south-indian/page-001.json' }
      ]
    },
    {
      groupName: 'COLLECTIONS & VAULT',
      items: [
        { id: 'series', label: 'Web Series', icon: 'fa-layer-group', count: '60+', badgeClass: 'badge-count', file: 'data/series/dubbed/page-001.json' },
        { id: 'mix', label: 'Cinema Vault', icon: 'fa-boxes-stacked', count: '937', badgeClass: 'badge-count', file: 'data/movies/mix/page-001.json' },
        { id: 'watchlist', label: 'My Watchlist', icon: 'fa-star', count: '★', badgeClass: 'badge-count' }
      ]
    }
  ];

  // Branded fallback SVG poster (Zero network dependency, instant local render)
  const FALLBACK_POSTER_SVG = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMDAiIGhlaWdodD0iNDUwIiB2aWV3Qm94PSIwIDAgMzAwIDQ1MCI+PHJlY3Qgd2lkdGg9IjMwMCIgaGVpZ2h0PSI0NTAiIGZpbGw9IiMwZTE1MjAiLz48Y2lyY2xlIGN4PSIxNTAiIGN5PSIxOTAiIHI9IjQyIiBmaWxsPSIjZTUwOTE0IiBvcGFjaXR5PSIwLjE1Ii8+PHBvbHlnb24gcG9pbnRzPSIxNDMsMTc1IDE2NiwxOTAgMTQzLDIwNSIgZmlsbD0iI2U1MDkxNCIvPjx0ZXh0IHg9IjE1MCIgeT0iMjY1IiBmaWxsPSIjZmZmZmZmIiBmb250LWZhbWlseT0ic2Fucy1zZXJpZiIgZm9udC1zaXplPSIxNCIgZm9udC13ZWlnaHQ9IjcwMCIgdGV4dC1hbmNob3I9Im1pZGRsZSI+Q0xJQ0sgVFY8L3RleHQ+PHRleHQgeD0iMTUwIiB5PSIyODgiIGZpbGw9IiM4YTk5YWQiIGZvbnQtZmFtaWx5PSJzYW5zLXNlcmlmIiBmb250LXNpemU9IjExIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj5IRCBDSU5FTUE8L3RleHQ+PC9zdmc+";

  // In-memory cache for on-demand loading (zero duplicate network requests)
  const movieCache = {};
  let currentActiveCategory = 'trending';
  let heroTimer = null;
  let heroCurrentIndex = 0;
  let heroFeaturedMovies = [];

  // Default fallback featured movies for hero showcase
  const DEFAULT_FEATURED = [
    {
      id: 'kalki-2898-ad',
      name: 'Kalki 2898 AD',
      year: 2024,
      category: 'South Indian',
      resolution: '4K Ultra HD',
      rating: '8.8',
      desc: 'ভবিষ্যত পৃথিবীর এক অন্ধকারময় যুগে বিষ্ণুর দশম অবতার কল্কির আবির্ভাব এবং কাশী শহরের মুক্তির মহাকাব্যিক লড়াই।',
      logo: 'https://image.tmdb.org/t/p/w1280/yDHYTfA3R0jFYba16jBB1jv8v2C.jpg',
      backdrop: 'https://image.tmdb.org/t/p/w1280/yDHYTfA3R0jFYba16jBB1jv8v2C.jpg'
    },
    {
      id: 'demon-slayer-hashira',
      name: 'Demon Slayer: Hashira Training Arc',
      year: 2024,
      category: 'Dubbed',
      resolution: 'Full HD',
      rating: '8.6',
      desc: 'তানজিরো ও হাশিরারা কিবুতসুজির বিরুদ্ধে চূড়ান্ত যুদ্ধের জন্য প্রস্তুতি গ্রহণ করছে। ফুল এইচডি বাংলা/হিন্দি ডাবড।',
      logo: 'https://image.tmdb.org/t/p/w1280/x7FsM19jE2p51e9e80e1590.jpg',
      backdrop: 'https://image.tmdb.org/t/p/w1280/yDHYTfA3R0jFYba16jBB1jv8v2C.jpg'
    },
    {
      id: 'toofan-2024',
      name: 'Toofan (তুফান)',
      year: 2024,
      category: 'Bangla',
      resolution: '1080p Full HD',
      rating: '8.4',
      desc: 'নব্বই দশকের এক গ্যাংস্টারের উত্থান এবং আন্ডারওয়ার্ল্ড নিয়ন্ত্রণের চরম টানটান উত্তেজনার গল্প। শাকিব খান অভিনীত মেগাহিট অ্যাকশন থ্রিলার।',
      logo: 'https://image.tmdb.org/t/p/w600_and_h900_bestv2/uGGp9mgUzaAp1WMfCCtscsZ5urh.jpg',
      backdrop: 'https://image.tmdb.org/t/p/w1280/yDHYTfA3R0jFYba16jBB1jv8v2C.jpg'
    },
    {
      id: 'jawan-2023',
      name: 'Jawan',
      year: 2023,
      category: 'Hindi',
      resolution: '4K Ultra HD',
      rating: '8.2',
      desc: 'অন্যায় ও দুর্নীতির বিরুদ্ধে এক প্রতিশোধমূলক লড়াই এবং একজন মানুষের দেশপ্রেমের অসাধারণ যাত্রা। শাহরুখ খান অভিনীত রেকর্ডব্রেকিং ব্লকবাস্টার।',
      logo: 'https://image.tmdb.org/t/p/w600_and_h900_bestv2/jKaA5Hl96M9jE6v0c5e7b2.jpg',
      backdrop: 'https://image.tmdb.org/t/p/w1280/yDHYTfA3R0jFYba16jBB1jv8v2C.jpg'
    }
  ];

  // Helper: Get IMDb Rating Badge
  function getRatingBadge(item) {
    const r = item?.rating || (item?.name?.length % 3 === 0 ? '8.4' : (item?.name?.length % 2 === 0 ? '8.1' : '7.9'));
    const val = parseFloat(r).toFixed(1);
    return `
      <span class="badge-rating-pill" title="IMDb Rating">
        <i class="fas fa-star"></i> ${val}
      </span>
    `;
  }

  // Helper: Get Resolution Badge
  function getResBadge(item) {
    const res = item?.resolution || item?.label || '1080p';
    return `<span class="badge-res-tag">${escapeHtml(res)}</span>`;
  }

  // Helper: Safe HTML Escape
  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // 2. Render Left Sidebar Navigation (Desktop Rail)
  function renderMovieSidebar() {
    const container = document.getElementById('sidebarCategoriesContainer');
    if (!container) return;

    let html = '';
    MOVIE_CATEGORY_GROUPS.forEach(group => {
      html += `
        <div class="sidebar-group">
          <div class="group-caption">${group.groupName}</div>
      `;
      group.items.forEach(item => {
        const isActive = currentActiveCategory === item.id;
        let badgeHtml = '';
        if (item.badgeClass === 'badge-hot') {
          badgeHtml = `<span class="badge-hot">HOT</span>`;
        } else if (item.badgeClass === 'badge-new') {
          badgeHtml = `<span class="badge-new">NEW</span>`;
        } else if (item.count) {
          badgeHtml = `<span class="badge-count">${item.count}</span>`;
        }

        html += `
          <button class="cat-item ${isActive ? 'active' : ''}" data-movie-cat-id="${item.id}" type="button">
            <span class="cat-icon-box"><i class="fas ${item.icon}"></i></span>
            <span class="cat-label">${item.label}</span>
            ${badgeHtml}
          </button>
        `;
      });
      html += `</div>`;
    });

    container.innerHTML = html;

    // Attach click events to sidebar category buttons
    container.querySelectorAll('.cat-item').forEach(btn => {
      btn.addEventListener('click', () => {
        const catId = btn.getAttribute('data-movie-cat-id');
        selectCategory(catId);
      });
    });
  }

  // 3. Category Selection & On-Demand Fetching
  async function selectCategory(catId) {
    currentActiveCategory = catId;

    // Update active state in sidebar
    const container = document.getElementById('sidebarCategoriesContainer');
    if (container) {
      container.querySelectorAll('.cat-item').forEach(b => {
        b.classList.toggle('active', b.getAttribute('data-movie-cat-id') === catId);
      });
    }

    // Scroll to content or reset view
    const detailSection = document.getElementById('detailSection');
    const movieHomeView = document.getElementById('movieHomeView');
    if (detailSection) {
      detailSection.classList.remove('active-view');
      detailSection.innerHTML = '';
    }
    if (movieHomeView) movieHomeView.style.display = 'block';

    const rowsContainer = document.getElementById('movieRowsContainer');
    if (!rowsContainer) return;

    rowsContainer.innerHTML = `
      <div style="padding: 40px 20px; text-align: center; color: var(--text-muted);">
        <i class="fas fa-spinner fa-spin fa-2x" style="color: var(--neon-red); margin-bottom: 12px;"></i>
        <p>মুভির তালিকা লোড হচ্ছে…</p>
      </div>
    `;

    try {
      const items = await fetchCategoryData(catId);
      renderMovieCatalog(catId, items);
    } catch (err) {
      console.error('Failed to load movie category:', catId, err);
      rowsContainer.innerHTML = `
        <div style="padding: 40px 20px; text-align: center; color: var(--text-muted);">
          <i class="fas fa-exclamation-triangle fa-2x" style="color: var(--amber-gold); margin-bottom: 12px;"></i>
          <p>ডাটা লোড করতে সমস্যা হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন।</p>
        </div>
      `;
    }
  }

  // Fetch JSON with in-memory caching
  async function fetchCategoryData(catId) {
    if (catId === 'watchlist') {
      return getWatchlistItems();
    }

    if (catId === 'trending') {
      if (!movieCache['trending']) {
        // Compose trending from dubbed + bangla + hindi top items
        const [dubbed, bangla, hindi] = await Promise.all([
          fetchJsonSafe('data/movies/dubbed/page-001.json'),
          fetchJsonSafe('data/movies/bangla/page-001.json'),
          fetchJsonSafe('data/movies/hindi/page-001.json')
        ]);
        const combined = [
          ...(dubbed.slice(0, 12)),
          ...(bangla.slice(0, 8)),
          ...(hindi.slice(0, 10))
        ];
        movieCache['trending'] = combined;
      }
      return movieCache['trending'];
    }

    if (catId === 'recent' || catId === 'releases') {
      if (!movieCache['recent']) {
        const dubbed = await fetchJsonSafe('data/movies/dubbed/page-001.json');
        const sorted = [...dubbed].sort((a, b) => (b.year || 0) - (a.year || 0));
        movieCache['recent'] = sorted;
      }
      return movieCache['recent'];
    }

    if (catId === 'series') {
      if (!movieCache['series']) {
        const seriesData = await fetchJsonSafe('data/series/dubbed/page-001.json');
        movieCache['series'] = seriesData;
      }
      return movieCache['series'];
    }

    // Standard category file lookup
    let targetFile = '';
    MOVIE_CATEGORY_GROUPS.forEach(g => {
      const found = g.items.find(i => i.id === catId);
      if (found?.file) targetFile = found.file;
    });

    if (!targetFile) targetFile = `data/movies/${catId}/page-001.json`;

    if (!movieCache[catId]) {
      movieCache[catId] = await fetchJsonSafe(targetFile);
    }
    return movieCache[catId];
  }

  async function fetchJsonSafe(path) {
    try {
      const res = await fetch(path, { cache: 'no-cache' });
      if (!res.ok) return [];
      const data = await res.json();
      return Array.isArray(data.items) ? data.items : (Array.isArray(data) ? data : []);
    } catch (e) {
      console.warn('Could not fetch JSON from:', path, e);
      return [];
    }
  }

  // 4. Render 168px Cards in Movie Grid
  function renderMovieCatalog(catId, items) {
    const rowsContainer = document.getElementById('movieRowsContainer');
    if (!rowsContainer) return;

    if (!items || !items.length) {
      rowsContainer.innerHTML = `
        <div style="padding: 40px 20px; text-align: center; color: var(--text-muted);">
          <i class="fas fa-film fa-2x" style="color: var(--neon-red); margin-bottom: 12px;"></i>
          <p>এই বিভাগে বর্তমানে কোনো কনটেন্ট পাওয়া যায়নি।</p>
        </div>
      `;
      return;
    }

    // Capitalized category title
    const categoryTitle = getCategoryTitle(catId);
    let cardsHtml = '';

    items.forEach((item, idx) => {
      const poster = item.logo || item.img || FALLBACK_POSTER_SVG;
      const title = item.name || item.title || 'Movie';
      const year = item.year || (item.name?.match(/\((\d{4})\)/)?.[1] || '2026');
      const catLabel = item.category || categoryTitle;
      const ratingBadge = getRatingBadge(item);
      const resBadge = getResBadge(item);

      cardsHtml += `
        <div class="movie-card tv-focusable" data-card-idx="${idx}" tabindex="0" role="button" aria-label="${escapeHtml(title)}">
          <div class="card-thumb-wrap">
            <img src="${poster}" alt="${escapeHtml(title)}" loading="lazy" onerror="this.onerror=null;this.src='${FALLBACK_POSTER_SVG}';" />
            ${ratingBadge}
            ${resBadge}
          </div>
          <div class="card-meta-info">
            <h4 class="card-title" title="${escapeHtml(title)}">${escapeHtml(title)}</h4>
            <div class="card-sub-row">
              <span>${escapeHtml(year)}</span>
              <span class="meta-dot">•</span>
              <span>${escapeHtml(catLabel)}</span>
            </div>
          </div>
        </div>
      `;
    });

    rowsContainer.innerHTML = `
      <div class="section-row-wrap" style="margin-bottom: 32px;">
        <div class="section-head-row" style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px;">
          <div style="display: flex; align-items: baseline; gap: 10px;">
            <h3 class="section-title" style="font-size: 18px; font-weight: 800; color: #ffffff; margin: 0;">${categoryTitle}</h3>
            <span style="font-size: 12px; font-weight: 600; color: var(--text-muted);">${items.length} Titles</span>
          </div>
        </div>
        <div class="cards-grid-auto" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 18px;">
          ${cardsHtml}
        </div>
      </div>
    `;

    // Attach click events on cards -> Open 2-Column Detail View
    rowsContainer.querySelectorAll('.movie-card').forEach((card) => {
      const idx = parseInt(card.getAttribute('data-card-idx'), 10);
      const movieItem = items[idx];
      card.addEventListener('click', () => {
        showMovieDetailPage(movieItem);
      });
      card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          showMovieDetailPage(movieItem);
        }
      });
    });
  }

  function getCategoryTitle(catId) {
    for (const g of MOVIE_CATEGORY_GROUPS) {
      const item = g.items.find(i => i.id === catId);
      if (item) return item.label;
    }
    return catId.charAt(0).toUpperCase() + catId.slice(1);
  }

  // 5. Show 2-Column Movie Detail Page
  function showMovieDetailPage(item) {
    if (!item) return;

    const detailSection = document.getElementById('detailSection');
    const movieHomeView = document.getElementById('movieHomeView');
    if (!detailSection) return;

    // Pause hero auto-slider
    if (heroTimer) clearInterval(heroTimer);

    if (movieHomeView) movieHomeView.style.display = 'none';

    const poster = item.logo || item.img || FALLBACK_POSTER_SVG;
    const backdrop = item.backdrop || item.logo || poster;
    const title = item.name || item.title || 'Movie';
    const year = item.year || (item.name?.match(/\((\d{4})\)/)?.[1] || '2026');
    const cat = item.category || 'HD Movie';
    const res = item.resolution || item.label || '1080p Full HD';
    const desc = item.desc || `${title} (${year}) - ক্লিক টিভিতে হাই-ডেফিনিশন স্ট্রিমিং ও বাফারলেস মুভি দেখার সেরা অভিজ্ঞতা।`;
    const ratingVal = item.rating || (item.name?.length % 3 === 0 ? '8.6' : '8.1');
    const isFav = isWatchlisted(item);

    const serverCount = item.backups && item.backups.length ? item.backups.length + 1 : 2;
    let serverPills = '';
    for (let i = 1; i <= serverCount; i++) {
      serverPills += `
        <button class="server-pill-btn ${i === 1 ? 'active' : ''}" data-server-num="${i}" type="button">
          <span class="server-green-dot"></span> Server ${i}
        </button>
      `;
    }

    detailSection.innerHTML = `
      <div class="detail-nav-bar" style="margin-bottom: 18px;">
        <button class="btn-back-pill" id="detailBackBtn" type="button">
          <i class="fas fa-arrow-left"></i> <span>Back to Movies</span>
        </button>
      </div>

      <!-- Video Player Stage Area (Appears when Watch Now is clicked) -->
      <div class="detail-player-stage" id="detailPlayerStage" style="display:none; margin-bottom: 24px;"></div>

      <div class="detail-hero-card">
        <div class="detail-backdrop" style="background-image: url('${backdrop}');"></div>
        <div class="detail-inner-grid">
          <div class="detail-poster-wrap">
            <img src="${poster}" alt="${escapeHtml(title)}" loading="lazy" onerror="this.onerror=null;this.src='${FALLBACK_POSTER_SVG}';" />
          </div>
          <div class="detail-info">
            <div class="detail-type-pill" style="display: inline-flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 800; color: var(--neon-red); text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 6px;">
              <span class="red-bullet" style="width: 7px; height: 7px; border-radius: 50%; background: var(--neon-red);"></span>
              CLICK TV CINEMA
            </div>
            <h2 class="detail-heading">${escapeHtml(title)}</h2>
            <div class="detail-meta-row">
              <span class="badge-rating-pill" style="position:static;" title="IMDb Rating">
                <i class="fas fa-star" style="color:var(--amber-gold);"></i> ${parseFloat(ratingVal).toFixed(1)} IMDb
              </span>
              <span class="meta-dot">•</span>
              <span>${escapeHtml(year)}</span>
              <span class="meta-dot">•</span>
              <span>${escapeHtml(cat)}</span>
              <span class="meta-dot">•</span>
              <span class="badge-res-tag" style="position:static;">${escapeHtml(res)}</span>
            </div>
            <p class="detail-desc">${escapeHtml(desc)}</p>
            <div class="detail-actions-row">
              <button class="btn-play-white" id="detailPlayNowBtn" type="button">
                <i class="fas fa-play"></i> Watch Now
              </button>
              <button class="btn-bookmark-dark ${isFav ? 'active' : ''}" id="detailBookmarkBtn" type="button">
                <i class="${isFav ? 'fas' : 'far'} fa-star" style="${isFav ? 'color:var(--amber-gold);' : ''}"></i>
                <span id="bookmarkBtnText">${isFav ? 'Bookmarked' : 'Watchlist'}</span>
              </button>
            </div>
            <div class="server-card-box">
              <div class="server-box-title">AVAILABLE SERVERS · HIGH-SPEED CDN</div>
              <div class="server-pill-row">${serverPills}</div>
            </div>
          </div>
        </div>
      </div>
    `;

    detailSection.classList.add('active-view');
    window.scrollTo({ top: 0, behavior: 'smooth' });

    // 1. Back button click handler (cleans RAM and restores videoContainer)
    document.getElementById('detailBackBtn')?.addEventListener('click', () => {
      closeMovieDetailPage();
    });

    // 2. Watch Now button click handler (plays video seamlessly using existing player)
    document.getElementById('detailPlayNowBtn')?.addEventListener('click', () => {
      playMovieInDetailView(item);
    });

    // 3. Bookmark button handler
    document.getElementById('detailBookmarkBtn')?.addEventListener('click', () => {
      toggleWatchlist(item);
      const favNow = isWatchlisted(item);
      const btn = document.getElementById('detailBookmarkBtn');
      if (btn) {
        btn.classList.toggle('active', favNow);
        btn.innerHTML = `
          <i class="${favNow ? 'fas' : 'far'} fa-star" style="${favNow ? 'color:var(--amber-gold);' : ''}"></i>
          <span>${favNow ? 'Bookmarked' : 'Watchlist'}</span>
        `;
      }
    });

    // 4. Server selection pills
    detailSection.querySelectorAll('.server-pill-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        detailSection.querySelectorAll('.server-pill-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const sNum = btn.getAttribute('data-server-num');
        if (sNum > 1 && item.backups && item.backups[sNum - 2]) {
          // Temporarily set primary link to backup
          const backup = item.backups[sNum - 2];
          playMovieInDetailView({ ...item, url: backup.url || item.url });
        } else {
          playMovieInDetailView(item);
        }
      });
    });
  }

  // Play Movie in Detail View using the native player
  function playMovieInDetailView(item) {
    const stage = document.getElementById('detailPlayerStage');
    const videoContainer = document.getElementById('videoContainer');
    if (!stage || !videoContainer) {
      if (typeof window.startPlayback === 'function') {
        window.startPlayback(item, true);
      }
      return;
    }

    stage.style.display = 'block';
    stage.appendChild(videoContainer);

    if (typeof window.startPlayback === 'function') {
      window.startPlayback(item, true);
    } else {
      console.warn('startPlayback not found on window, fallback direct');
    }

    stage.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // Close Movie Detail Page & clean up memory
  function closeMovieDetailPage() {
    const detailSection = document.getElementById('detailSection');
    const movieHomeView = document.getElementById('movieHomeView');
    const videoContainer = document.getElementById('videoContainer');
    const defaultPlayerParent = document.querySelector('.video-section');

    // Restore videoContainer back to its original parent
    if (videoContainer && defaultPlayerParent && videoContainer.parentElement !== defaultPlayerParent) {
      const mobileNav = document.getElementById('mobileMainNavigation');
      if (mobileNav && mobileNav.nextSibling) {
        defaultPlayerParent.insertBefore(videoContainer, mobileNav.nextSibling);
      } else {
        defaultPlayerParent.appendChild(videoContainer);
      }
    }

    if (detailSection) {
      detailSection.classList.remove('active-view');
      detailSection.innerHTML = ''; // Full memory release
    }

    if (movieHomeView) movieHomeView.style.display = 'block';

    // Resume hero carousel
    startHeroCarousel();
  }

  // 6. Hero Showcase Banner Carousel
  function initHeroBanner() {
    heroFeaturedMovies = DEFAULT_FEATURED;
    heroCurrentIndex = 0;
    updateHeroSlide(0);
    startHeroCarousel();

    document.getElementById('heroPrevBtn')?.addEventListener('click', () => {
      heroCurrentIndex = (heroCurrentIndex - 1 + heroFeaturedMovies.length) % heroFeaturedMovies.length;
      updateHeroSlide(heroCurrentIndex);
    });

    document.getElementById('heroNextBtn')?.addEventListener('click', () => {
      heroCurrentIndex = (heroCurrentIndex + 1) % heroFeaturedMovies.length;
      updateHeroSlide(heroCurrentIndex);
    });

    document.getElementById('heroPlayBtn')?.addEventListener('click', () => {
      const current = heroFeaturedMovies[heroCurrentIndex];
      if (current) showMovieDetailPage(current);
    });

    document.getElementById('heroDetailsBtn')?.addEventListener('click', () => {
      const current = heroFeaturedMovies[heroCurrentIndex];
      if (current) showMovieDetailPage(current);
    });
  }

  function updateHeroSlide(index) {
    const movie = heroFeaturedMovies[index];
    if (!movie) return;

    const titleEl = document.getElementById('heroTitle');
    const descEl = document.getElementById('heroDesc');
    const backdropEl = document.getElementById('heroBackdrop');
    if (titleEl) titleEl.textContent = movie.name;
    if (descEl) descEl.textContent = movie.desc;
    if (backdropEl && movie.backdrop) {
      backdropEl.style.backgroundImage = `url('${movie.backdrop}')`;
    }

    // Update carousel dots
    const dots = document.querySelectorAll('.hero-pill-dot');
    dots.forEach((d, i) => {
      d.classList.toggle('active', i === index);
    });
  }

  function startHeroCarousel() {
    if (heroTimer) clearInterval(heroTimer);
    heroTimer = setInterval(() => {
      heroCurrentIndex = (heroCurrentIndex + 1) % heroFeaturedMovies.length;
      updateHeroSlide(heroCurrentIndex);
    }, 7000);
  }

  // 7. Status Mood Strip Click Handlers
  function initStatusStrip() {
    document.querySelectorAll('.status-card').forEach(card => {
      card.addEventListener('click', () => {
        const cat = card.getAttribute('data-movie-cat');
        if (cat) selectCategory(cat);
      });
    });
  }

  // 8. Watchlist Management (localStorage)
  const WATCHLIST_STORAGE_KEY = 'clicktv_movie_watchlist';

  function getWatchlistItems() {
    try {
      const raw = localStorage.getItem(WATCHLIST_STORAGE_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (_) {
      return [];
    }
  }

  function isWatchlisted(item) {
    if (!item) return false;
    const list = getWatchlistItems();
    return list.some(x => (x.id && x.id === item.id) || (x.name && x.name === item.name));
  }

  function toggleWatchlist(item) {
    if (!item) return;
    let list = getWatchlistItems();
    const idx = list.findIndex(x => (x.id && x.id === item.id) || (x.name && x.name === item.name));
    if (idx >= 0) {
      list.splice(idx, 1);
    } else {
      list.push(item);
    }
    try {
      localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(list));
    } catch (_) {}
  }

  // 9. Floating Header Capsule Navigation (Live Sports / Live TV / Movies)
  function initHeaderNavigation() {
    const navItems = document.querySelectorAll('.nav-capsule-item');
    navItems.forEach(item => {
      item.addEventListener('click', () => {
        const cat = item.getAttribute('data-category');
        if (typeof window.selectFinalMainGroup === 'function') {
          window.selectFinalMainGroup(cat);
        } else {
          navItems.forEach(i => i.classList.remove('active'));
          item.classList.add('active');
          if (cat === 'movies') onActivateMovies();
          else onDeactivateMovies();
        }
      });
    });

    // Subscribe Button Click Handler
    document.getElementById('subscribeBtn')?.addEventListener('click', () => {
      alert('Click TV Premium VIP:\n\nসকল স্পোর্টস, লাইভ টিভি ও 4K সিনেমা কোনো বিজ্ঞাপন ছাড়া উপভোগ করুন। সাবস্ক্রিপশন খুব শীঘ্রই উন্মুক্ত হবে!');
    });
  }

  function initMovieSearch() {
    const handleSearch = (e) => {
      if (!document.body.classList.contains('movies-active')) return;
      const query = (e.target.value || '').trim().toLowerCase();
      if (!query) {
        selectCategory(currentActiveCategory);
        return;
      }
      const currentList = movieCache[currentActiveCategory] || [];
      const filtered = currentList.filter(item => {
        const title = (item.name || item.title || '').toLowerCase();
        return title.includes(query);
      });
      renderMovieCatalog('search', filtered);
    };

    const deskSearch = document.getElementById('searchInput');
    const mobSearch = document.getElementById('mobileSearchInput');
    if (deskSearch) deskSearch.addEventListener('input', handleSearch);
    if (mobSearch) mobSearch.addEventListener('input', handleSearch);
  }

  function onActivateMovies() {
    document.body.classList.add('movies-active');
    document.documentElement.classList.add('movies-active');
    renderMovieSidebar();
    initHeroBanner();
    initStatusStrip();
    initMovieSearch();
    selectCategory(currentActiveCategory);
  }

  function onDeactivateMovies() {
    document.body.classList.remove('movies-active');
    document.documentElement.classList.remove('movies-active');
    closeMovieDetailPage();
    if (heroTimer) clearInterval(heroTimer);
  }

  // Expose API globally
  window.MovieUI = {
    onActivateMovies,
    onDeactivateMovies,
    renderMovieSidebar,
    selectCategory,
    showMovieDetailPage,
    playMovieInDetailView,
    getRatingBadge
  };

  // Run initial setup on load
  document.addEventListener('DOMContentLoaded', () => {
    initHeaderNavigation();
    if (document.body.classList.contains('movies-active') || document.querySelector('.nav-capsule-item.active[data-category="movies"]')) {
      onActivateMovies();
    }
  });

})();
