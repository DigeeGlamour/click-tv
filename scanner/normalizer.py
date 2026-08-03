"""
Candidate Normalizer Engine

Cleans titles, strips quality/release/status tokens (longest token first) while preserving
movie years, applies canonical channel aliases and safe blacklists (handling symbols like 18+),
resolves pipelines and categories accurately, converts logo URLs to HTTPS, and merges domain
header profiles using clean hostname resolution.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote, urlparse

from scanner.content_router import route_candidate


GITHUB_LOGO_BASE = "https://raw.githubusercontent.com/0matbank/livetest/main/assets/logos/"

DEFAULT_QUALITY_TOKENS = (
    "4k", "2k", "uhd", "fhd", "full hd", "fullhd", "hd", "sd",
    "2160p", "1440p", "1080p", "1080", "720p", "720", "576p", "480p", "360p",
)

DEFAULT_RELEASE_TOKENS = (
    "web-dl", "webdl", "web.dl", "webrip", "hdrip", "dvdrip", "camrip",
    "x264", "x265", "hevc", "h264", "h265", "aac", "dd5.1",
)

DEFAULT_STATUS_TOKENS = (
    "live now", "live channel", "official live",
    "server 1", "server 2", "server 3", "server1", "server2", "server3",
)

CANONICAL_HEADERS = {
    "cookie": "Cookie",
    "authorization": "Authorization",
    "referer": "Referer",
    "referrer": "Referer",
    "http-referer": "Referer",
    "http-referrer": "Referer",
    "origin": "Origin",
    "http-origin": "Origin",
    "user-agent": "User-Agent",
    "http-user-agent": "User-Agent",
    "accept": "Accept",
    "accept-language": "Accept-Language",
}


def _canonical_header_name(name: str) -> str:
    normalized = name.strip().lower().replace("_", "-")
    return CANONICAL_HEADERS.get(normalized, name.strip())


def _load_json_file(file_path: str | Path) -> Dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalize_separators(text: str) -> str:
    """Convert underscores, dots, slashes, and hyphens to spaces."""
    if not text:
        return ""
    s = re.sub(r"[_\./\\:\?&=#\-]+", " ", unquote(text))
    return " ".join(s.split()).strip()


def _tokenize(text: str) -> List[str]:
    """Tokenize text split by separators, punctuation, and slashes."""
    cleaned = _normalize_separators(text.lower())
    return [t for t in cleaned.split() if t]


def _match_domain_pattern(domain: str, pattern: str) -> bool:
    dom = domain.lower().strip()
    pat = pattern.lower().strip()
    if not dom or not pat:
        return False
    if pat.startswith("*."):
        suffix = pat[2:]
        return dom == suffix or dom.endswith("." + suffix)
    if pat.startswith("."):
        return dom == pat[1:] or dom.endswith(pat)
    return dom == pat or dom.endswith("." + pat)


class Normalizer:
    def __init__(
        self,
        aliases_path: str = "config/channel-aliases.json",
        headers_path: str = "config/header-profiles.json",
        settings_path: str = "config/settings.json",
    ):
        self.aliases_config = _load_json_file(aliases_path)
        self.headers_config = _load_json_file(headers_path)
        self.settings = _load_json_file(settings_path)

        norm_rules = self.aliases_config.get("normalization", {})
        
        cfg_q = norm_rules.get("remove_quality_tokens", [])
        self.remove_quality = set(DEFAULT_QUALITY_TOKENS) | set(cfg_q)

        cfg_r = norm_rules.get("remove_release_tokens", [])
        self.remove_release = set(DEFAULT_RELEASE_TOKENS) | set(cfg_r)

        cfg_s = norm_rules.get("remove_status_tokens", [])
        self.remove_status = set(DEFAULT_STATUS_TOKENS) | set(cfg_s)

        raw_aliases = self.aliases_config.get("channel_aliases", {})
        self.channel_aliases: Dict[str, List[str]] = {}
        if isinstance(raw_aliases, dict):
            for k, v in raw_aliases.items():
                if isinstance(v, list):
                    self.channel_aliases[k] = [str(x) for x in v]
                elif isinstance(v, str):
                    self.channel_aliases[k] = [v]

        self.exact_blacklist = {
            _normalize_separators(b).lower()
            for b in self.aliases_config.get("blacklist_exact_names", [])
            if b
        }
        self.keyword_blacklist = [
            k.strip().lower()
            for k in self.aliases_config.get("blacklist_keywords", [])
            if k
        ]

        self.header_profiles = self.headers_config.get("profiles", {})
        raw_rules = self.headers_config.get("domain_rules", [])
        self.domain_rules = raw_rules if isinstance(raw_rules, list) else []
        self.default_profile = self.headers_config.get("default_profile", "android_tv")

    def is_blacklisted(self, raw_name: str, clean_name: str = "") -> bool:
        for name in (raw_name, clean_name):
            if not name:
                continue

            lower_raw = name.strip().lower()
            norm_name = _normalize_separators(lower_raw)

            if lower_raw in self.exact_blacklist or norm_name in self.exact_blacklist:
                return True

            tokens = set(_tokenize(lower_raw))
            for kw in self.keyword_blacklist:
                kw_clean = kw.strip().lower()
                kw_norm = _normalize_separators(kw_clean)

                if "+" in kw_clean or " " in kw_clean or "-" in kw_clean:
                    if kw_clean in lower_raw or kw_norm in norm_name:
                        return True
                else:
                    if kw_norm in tokens or kw_clean in tokens:
                        return True

        return False

    def clean_title(self, name: str) -> str:
        if not name:
            return ""

        text = name.strip()

        # Some source collectors accidentally prepend poster-query fragments to
        # the real channel name, for example:
        #   q 85 ... posters ... png", Independent TV
        # Keep only the human-readable tail before alias/category detection.
        if re.search(r"\bq[\s_-]*85\b", text, re.IGNORECASE) and re.search(
            r"\bposters?\b", text, re.IGNORECASE
        ):
            tail = re.split(r"[,|]", text)[-1].strip(" \"'")
            if tail:
                text = tail

        if re.search(r"\b(?:jpe?g|png|webp|gif)\b", text, re.IGNORECASE) and re.search(
            r"\bgroup\s*title\b", text, re.IGNORECASE
        ) and "," in text:
            tail = text.rsplit(",", 1)[-1].strip(" \"'")
            if tail:
                text = tail

        text = re.sub(
            r"^(?:Live Match\s*-\s*|Match\s*-\s*|Live Coverage\s*-\s*)",
            "",
            text,
            flags=re.IGNORECASE,
        )

        years = re.findall(r"\b(19\d\d|20\d\d)\b", text)
        year_str = f" ({years[0]})" if years else ""

        def _replace_bracket(m):
            val = (m.group(1) or m.group(2) or m.group(3) or "").strip()
            if re.fullmatch(r"(?:19\d\d|20\d\d)", val):
                return f" ({val}) "
            return " "

        text = re.sub(r"\[([^\]]*)\]|\(([^\)]*)\)|\{([^\}]*)\}", _replace_bracket, text)

        text_norm = _normalize_separators(text)

        # Strip tokens in order of length descending
        all_tokens = set(self.remove_quality) | set(self.remove_release) | set(self.remove_status)
        for token in sorted(
            all_tokens,
            key=lambda value: len(_normalize_separators(str(value))),
            reverse=True,
        ):
            token_clean = _normalize_separators(token)
            if not token_clean:
                continue
            pattern = rf"(?i)\b{re.escape(token_clean)}\b"
            text_norm = re.sub(pattern, " ", text_norm)

        clean_name = " ".join(text_norm.split()).strip()

        if year_str and year_str.strip(" ()") not in clean_name:
            clean_name += year_str

        clean_lower_norm = _normalize_separators(clean_name).lower()
        for canonical, aliases in self.channel_aliases.items():
            canonical_norm = _normalize_separators(canonical).lower()
            alias_norms = [_normalize_separators(a).lower() for a in aliases]

            if clean_lower_norm == canonical_norm or clean_lower_norm in alias_norms:
                return canonical

        return clean_name or name.strip()

    def generate_slug_id(self, name: str) -> str:
        clean = re.sub(r"[^\w\s-]", "", _normalize_separators(name).lower())
        slug = re.sub(r"[-\s]+", "-", clean).strip("-")
        return slug or "unnamed-item"

    def normalize_logo(self, logo_url: str) -> str:
        if not logo_url:
            return ""
        url = logo_url.strip()

        if url.startswith("//"):
            url = "https:" + url

        if url.startswith(("./assets/", "/assets/", "assets/")):
            clean_path = re.sub(r"^(\.|\/)?assets\/(logos\/)?", "", url)
            url = GITHUB_LOGO_BASE + clean_path

        if url.startswith("http://"):
            url = url.replace("http://", "https://", 1)

        return url

    def resolve_header_profile_name(self, stream_url: str) -> str:
        domain = ""
        try:
            domain = (urlparse(stream_url).hostname or "").lower()
        except Exception:
            pass

        if domain:
            for rule in self.domain_rules:
                if not isinstance(rule, dict):
                    continue
                patterns = rule.get("patterns", [])
                if any(_match_domain_pattern(domain, str(pattern)) for pattern in patterns):
                    profile = str(rule.get("profile") or "").strip()
                    if profile:
                        return profile

        return str(self.default_profile or "android_tv").strip()

    @staticmethod
    def _stream_type(stream_url: str, candidate: Dict[str, Any]) -> str:
        explicit = str(
            candidate.get("stream_type")
            or candidate.get("type")
            or candidate.get("format")
            or ""
        ).strip().lower()
        if explicit in {"hls", "dash", "media", "mpegts", "key", "subtitle"}:
            return explicit

        path = ""
        try:
            path = (urlparse(stream_url).path or "").lower()
        except Exception:
            path = str(stream_url or "").split("?", 1)[0].lower()

        if path.endswith(".mpd"):
            return "dash"
        if path.endswith(".m3u8"):
            return "hls"
        if path.endswith((".ts", ".mpegts", ".flv")):
            return "mpegts"
        return "media"

    @staticmethod
    def _headers_require_proxy(existing_headers: Dict[str, str]) -> bool:
        if not isinstance(existing_headers, dict):
            return False
        sensitive_or_browser_forbidden = {
            "cookie",
            "authorization",
            "referer",
            "referrer",
            "origin",
            "user-agent",
        }
        return any(
            str(key).strip().lower().replace("_", "-")
            in sensitive_or_browser_forbidden
            and str(value or "").strip()
            for key, value in existing_headers.items()
        )

    @staticmethod
    def _proxy_mode(
        stream_url: str,
        candidate: Dict[str, Any],
        header_profile: str,
        headers_required: bool,
    ) -> str:
        explicit = str(candidate.get("proxy_mode") or "").strip().lower()
        if explicit in {
            "direct_first",
            "proxy_first",
            "proxy_only",
            "direct_only",
            "auto",
        }:
            return explicit

        if bool(candidate.get("force_proxy") or candidate.get("proxy_required")):
            return "proxy_only"

        status = str(candidate.get("verification_status") or "").strip().lower()
        if status == "verified_proxy":
            return "proxy_first"

        if str(stream_url or "").lower().startswith("http://"):
            return "proxy_first"

        if headers_required:
            return "proxy_first"

        # Toffee's Bangladesh CDN often performs best directly for local users;
        # the same profile remains available to the playback proxy as fallback.
        if header_profile in {"toffee_okhttp", "toffee"}:
            return "direct_first"

        if header_profile and header_profile not in {"android_tv", "android_chrome"}:
            return "proxy_first"

        return "direct_first"

    def resolve_headers(self, stream_url: str, existing_headers: Dict[str, str]) -> Dict[str, str]:
        final_headers: Dict[str, str] = {}
        domain = ""
        try:
            domain = (urlparse(stream_url).hostname or "").lower()
        except Exception:
            pass

        matched_profile_name = ""
        if domain:
            for rule in self.domain_rules:
                if isinstance(rule, dict):
                    patterns = rule.get("patterns", [])
                    if any(_match_domain_pattern(domain, p) for p in patterns):
                        matched_profile_name = rule.get("profile", "")
                        break

        if not matched_profile_name:
            matched_profile_name = self.default_profile

        profile_obj = self.header_profiles.get(matched_profile_name, {})
        profile_headers = profile_obj.get("headers", profile_obj) if isinstance(profile_obj, dict) else {}

        if isinstance(profile_headers, dict):
            for k, v in profile_headers.items():
                if v is not None and not isinstance(v, (dict, list)):
                    final_headers[_canonical_header_name(str(k))] = str(v)

        if isinstance(existing_headers, dict):
            for k, v in existing_headers.items():
                if v is not None and not isinstance(v, (dict, list)):
                    final_headers[_canonical_header_name(str(k))] = str(v)

        return final_headers

    def resolve_pipeline(self, candidate: Dict[str, Any]) -> str:
        pipeline = candidate.get("source_pipeline", "tv")
        group_title = candidate.get("group_title", "").strip().lower()

        if pipeline == "manual":
            if group_title.startswith("movie:") or group_title in {"movie", "movies"}:
                return "movies"
            if "today match" in group_title or "today_match" in group_title:
                return "today_match"
            if "upcoming" in group_title:
                return "upcoming"

            # Manual M3U entries are TV by default.  Movie/event entries must
            # opt in through their group-title markers above.  This keeps the
            # manual source usable while preventing a separate "manual"
            # pipeline from bypassing TV dedupe/merge logic.
            return "tv"

        return pipeline

    def detect_movie_category(self, name: str, group_title: str = "", url: str = "", default_cat: str = "Mix") -> str:
        combined = f"{name} {group_title} {url}"
        tokens = set(_tokenize(combined))
        combined_lower = _normalize_separators(combined).lower()

        # 1. Dubbed
        if (
            any(t in tokens for t in ["dubbed", "dub", "dualaudio", "multiaudio"])
            or "dual audio" in combined_lower
            or "multi audio" in combined_lower
            or "hindi dubbed" in combined_lower
            or "bangla dubbed" in combined_lower
            or "bengali dubbed" in combined_lower
            or "hindidub" in combined_lower
            or "hindi dub" in combined_lower
        ):
            return "Dubbed"

        # 2. Bangla
        if (
            any(t in tokens for t in ["bangla", "bengali", "natok", "telefilm", "chorki", "hoichoi"])
            or "indianbangla" in combined_lower
            or "banglamovie" in combined_lower
            or "bangla movie" in combined_lower
        ):
            return "Bangla"

        # 3. Hindi
        if any(t in tokens for t in ["hindi", "bollywood"]) or "bollywood" in combined_lower:
            return "Hindi"

        # 4. South Indian
        if (
            any(t in tokens for t in ["south", "tamil", "telugu", "malayalam", "kannada"])
            or "south indian" in combined_lower
            or "southindian" in combined_lower
        ):
            return "South Indian"

        # 5. English
        if any(t in tokens for t in ["english", "hollywood"]) or "hollywood" in combined_lower:
            return "English"

        return default_cat or "Mix"

    def detect_tv_category(self, name: str, group_title: str = "", url: str = "", mode: str = "detect", force_cat: str = "") -> str:
        if force_cat:
            return force_cat

        name_norm = _normalize_separators(name).lower()
        group_norm = _normalize_separators(group_title).lower()

        name_tokens = set(_tokenize(name_norm))
        group_tokens = set(_tokenize(group_norm))

        foreign_news_kw = [
            "bbc", "cnn", "aljazeera", "wion", "cgtn", "euronews", "bloomberg",
            "cnbc", "reuters", "msnbc", "cna", "nhk"
        ]

        # 1. Check Channel Name First
        if any(kw in name_tokens for kw in foreign_news_kw) or "al jazeera" in name_norm or "sky news" in name_norm or "france 24" in name_norm or "dw news" in name_norm or "fox news" in name_norm or "rt news" in name_norm or "russia today" in name_norm:
            return "Foreign News"

        islamic_kw = ["islam", "quran", "makkah", "madinah", "sunnah", "iqra", "halal"]
        if any(kw in name_tokens for kw in islamic_kw) or "peace tv" in name_norm or "al huda" in name_norm:
            return "Islamic"

        cartoon_kw = ["cartoon", "nick", "disney", "pogo", "hungama", "sonic", "kids", "doraemon", "animax", "duronto"]
        if any(kw in name_tokens for kw in cartoon_kw):
            return "Cartoon"

        sports_kw = ["sport", "sports", "cricket", "football", "tsports", "bein", "fox", "tsn", "willow", "eurosport", "fifa", "fancode", "espn", "dazn"]
        if any(kw in name_tokens for kw in sports_kw) or "t sports" in name_norm or "star sports" in name_norm or "a sports" in name_norm:
            return "Sports"

        bangla_kw = ["somoy", "jamuna", "independent", "ntv", "deepto", "atn", "ekattor", "btv", "nagorik", "massranga", "rtv", "ekushey", "dbc", "banglavision", "toffee", "boishakhi", "deshtv"]
        if any(kw in name_tokens for kw in bangla_kw) or "channel i" in name_norm or "channel 24" in name_norm or "gazi tv" in name_norm or "gtv" in name_norm or "news 24" in name_norm:
            return "Bangla"

        indian_kw = ["zee", "sony", "colors", "goldmines", "b4u", "9xm", "zoom", "bollywood"]
        if any(kw in name_tokens for kw in indian_kw) or "star plus" in name_norm or "star bharat" in name_norm or "star jalsha" in name_norm or "aaj tak" in name_norm:
            return "Indian"

        # 2. Check Group Title Second
        if "foreign news" in group_norm or "international news" in group_norm:
            return "Foreign News"
        if "islamic" in group_norm or "islam" in group_norm:
            return "Islamic"
        if "cartoon" in group_norm or "kids" in group_norm:
            return "Cartoon"
        if "sports" in group_norm or "sport" in group_norm:
            return "Sports"
        if "bangla" in group_norm or group_norm in ["bd", "bangladeshi"]:
            return "Bangla"
        if "indian" in group_norm or "hindi" in group_norm:
            return "Indian"

        return "quarantine"

    def normalize_candidate(self, candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # Resolve manual group markers first, then run the content router. This
        # corrects mixed public playlists where direct movie files were placed
        # inside a configured TV source.
        routed_input = dict(candidate)
        routed_input["source_pipeline"] = self.resolve_pipeline(routed_input)
        routed_input = route_candidate(routed_input)

        raw_name = routed_input.get("name", "")
        clean_name = self.clean_title(raw_name)

        if self.is_blacklisted(raw_name, clean_name):
            return None

        if not clean_name:
            return None

        pipeline = str(routed_input.get("source_pipeline") or "tv")
        url = routed_input.get("url", "")
        group_title = routed_input.get("group_title", "")

        if pipeline == "movies":
            category = self.detect_movie_category(
                clean_name,
                group_title,
                url,
                default_cat=routed_input.get("default_category", "Mix"),
            )
        elif pipeline in ("today_match", "upcoming"):
            category = routed_input.get("force_output") or pipeline
        else:
            category = self.detect_tv_category(
                clean_name,
                group_title,
                url,
                mode=routed_input.get("category_mode", "detect"),
                force_cat=routed_input.get("force_category", ""),
            )

        source_headers = routed_input.get("headers", {})
        headers = self.resolve_headers(url, source_headers)
        header_profile = self.resolve_header_profile_name(url)
        headers_required = self._headers_require_proxy(source_headers)
        stream_type = self._stream_type(url, routed_input)
        proxy_mode = self._proxy_mode(
            url,
            routed_input,
            header_profile,
            headers_required,
        )
        logo = self.normalize_logo(routed_input.get("logo", ""))

        normalized_item = dict(routed_input)
        normalized_item["name"] = clean_name
        normalized_item["id"] = self.generate_slug_id(clean_name)
        normalized_item["logo"] = logo
        normalized_item["category"] = category
        normalized_item["headers"] = headers
        normalized_item["header_profile"] = header_profile
        normalized_item["proxy_mode"] = proxy_mode
        normalized_item["stream_type"] = stream_type
        normalized_item["requires_headers"] = headers_required
        normalized_item["source_pipeline"] = pipeline

        return normalized_item


def normalize_all_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Batch normalize all candidate items."""
    normalizer = Normalizer()
    normalized_list = []

    for candidate in candidates:
        item = normalizer.normalize_candidate(candidate)
        if item:
            normalized_list.append(item)

    return normalized_list
