"""Poster reachability, so a dead artwork URL is never published as artwork.

Why this exists
---------------
A source feed's ``tvg-logo`` was trusted on syntax alone. ``_valid_poster_url``
asks "does this start with https://", never "does this return an image", so a
host that has since died kept its place at the top of the poster priority
order and blocked every working fallback beneath it. Measured on the published
catalogue: 1,312 of 1,667 posters answered HTTP 403 from a Bangladesh egress -
an image proxy (``srhady-live-stream.hf.space``) in front of
``jrtyh.b-cdn.net``, plus a second worker. The same URLs answer 200 with real
image bytes from a GitHub runner, which is why 403 is treated below as a fact
about the asker rather than about the image.

The rule this module enforces is the simple one: a poster that is provably
not an image is not a poster. It loses its priority, the resolver falls
through to TMDB/the supplementary providers using the *clean* title and year,
and if nothing real is found the field is published empty so the site draws
its designed placeholder. Nothing here invents artwork.

Three verdicts, not two
-----------------------
``ok``      the URL returned image bytes.
``dead``    the URL refused in a way that is about the resource - 404, 410,
            400, 405 - or answered 200 with something that is not an image.
            This is what the scanner acts on.
``unknown`` a timeout, a DNS failure, a 5xx, or a refusal that is about the
            asker rather than the image (401/403/451). Our egress having a bad
            moment - or being geo-blocked - is not evidence about the artwork,
            so an unknown never demotes a URL that is already published; it
            simply is not counted as verified.

Cost control
------------
Probing every poster on every scan would add thousands of requests. Three
things keep it cheap:

  * a persisted verdict cache with separate TTLs - a live poster is re-checked
    weekly, a dead one monthly, because artwork that has gone does not usually
    come back;
  * a per-host circuit breaker - once a host has refused ``HOST_BREAKER_TRIPS``
    distinct URLs with nothing succeeding, the whole host is treated as dead
    for the rest of the run without another request. That is what turns 1,197
    probes into five;
  * a whole-pass wall-clock budget, after which every remaining URL is left
    ``unknown`` rather than the scan being held up.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

DEFAULT_CACHE_PATH = "state/movie-poster-validity.json"

#: A live poster is re-checked weekly; a dead one monthly. Artwork that has
#: been withdrawn rarely returns, and re-asking a dead host costs a timeout.
OK_TTL_SECONDS = 7 * 24 * 3600
DEAD_TTL_SECONDS = 30 * 24 * 3600

REQUEST_TIMEOUT_SECONDS = 8
#: Enough bytes for every magic number below, and nothing like a whole image.
PROBE_BYTES = 1024
#: Distinct refusals from one host before the rest of that host is assumed
#: dead. Kept low because the failure this was built for is host-wide.
HOST_BREAKER_TRIPS = 4
DEFAULT_TIME_BUDGET_SECONDS = 240.0

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

#: A refusal about the *resource*: the image is not there. Safe to act on.
DEFINITIVE_REFUSALS = frozenset({400, 404, 405, 410})

#: A refusal about the *asker*, not the resource - and the difference is not
#: academic here. Measured on the same URL on the same day:
#:
#:     from a Bangladesh egress   HTTP 403
#:     from a GitHub runner       HTTP 200, real JPEG bytes
#:
#: so 1,312 posters read as "dead" from one vantage and perfectly healthy from
#: another. Retiring artwork on that evidence would blank a poster for every
#: viewer who can see it, on the word of one network that cannot. scanner/
#: verifier.py already draws this line for streams (VANTAGE_SHAPED_CODES);
#: artwork is held to the same rule.
#:
#: Viewers behind the blocked vantage are not left with a broken image: the
#: page swaps in the designed placeholder on the img error event, which is
#: where a per-viewer failure belongs.
VANTAGE_SHAPED_REFUSALS = frozenset({401, 402, 403, 407, 451})

OK = "ok"
DEAD = "dead"
UNKNOWN = "unknown"

#: Leading bytes of the formats a browser will actually paint. SVG and ICO are
#: matched on content-type instead, since neither has a usable magic number.
_IMAGE_MAGIC: Tuple[Tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
)


def _looks_like_image(payload: bytes, content_type: str) -> bool:
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return True
    if payload[4:12] in (b"ftypavif", b"ftypavis") or payload[4:8] == b"ftyp" and b"avif" in payload[:32]:
        return True
    for magic, _name in _IMAGE_MAGIC:
        if payload.startswith(magic):
            return True
    # SVG and ICO carry no reliable magic number, so the declared type is the
    # only evidence available for them.
    kind = (content_type or "").split(";", 1)[0].strip().lower()
    if kind in {"image/svg+xml", "image/x-icon", "image/vnd.microsoft.icon"}:
        return True
    return False


def _host_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return ""


class PosterValidator:
    """Verdicts for poster URLs, cached across runs and cheap within one.

    ``enabled=False`` turns every network probe off and answers ``unknown``
    for anything not already cached, which is what test and offline paths
    want: the cached verdicts still apply, no new requests are made.
    """

    def __init__(
        self,
        cache_path: str | Path = DEFAULT_CACHE_PATH,
        *,
        enabled: bool = True,
        time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
        opener: Any = None,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.enabled = bool(enabled)
        self.time_budget_seconds = float(time_budget_seconds)
        self._opener = opener or urllib.request.urlopen
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._dirty = False
        self._started = time.time()
        self._budget_spent = False
        #: host -> [refusals, successes]
        self._host_scores: Dict[str, list] = {}
        self._dead_hosts: set[str] = set()
        #: host -> [vantage refusals, answers]. Separate from the scores above
        #: because a 403 must never reach the "dead" side of that ledger.
        self._refusal_scores: Dict[str, list] = {}
        self._refusing_hosts: set[str] = set()
        self.stats: Dict[str, int] = {
            "probed": 0, "cache_hits": 0, "ok": 0, "dead": 0,
            "unknown": 0, "host_short_circuits": 0, "hosts_refusing_all": 0,
        }
        self._load()

    # ---------------------------------------------------------------- cache

    def _load(self) -> None:
        try:
            with self.cache_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        entries = payload.get("urls") if isinstance(payload, dict) else None
        if not isinstance(entries, dict):
            return
        for url, entry in entries.items():
            if isinstance(entry, dict) and entry.get("verdict") in (OK, DEAD):
                self._entries[str(url)] = entry
        hosts = payload.get("dead_hosts") if isinstance(payload, dict) else None
        if isinstance(hosts, list):
            # A host proven dead in an earlier run starts this one dead, so the
            # very first poster on it costs nothing either.
            self._dead_hosts.update(str(host).lower() for host in hosts if host)

    def save(self) -> None:
        if not self._dirty:
            return
        payload = {
            "version": 1,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "dead_hosts": sorted(self._dead_hosts),
            "urls": dict(sorted(self._entries.items())),
        }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=1, ensure_ascii=False)
                handle.write("\n")
            os.replace(temporary, self.cache_path)
            self._dirty = False
        except OSError:
            # A cache that cannot be written costs speed on the next run and
            # nothing else. It must never fail a scan.
            pass

    def _cached(self, url: str) -> Optional[str]:
        entry = self._entries.get(url)
        if not entry:
            return None
        verdict = entry.get("verdict")
        ttl = OK_TTL_SECONDS if verdict == OK else DEAD_TTL_SECONDS
        try:
            checked_at = float(entry.get("checked_at") or 0)
        except (TypeError, ValueError):
            return None
        if time.time() - checked_at > ttl:
            return None
        return verdict if verdict in (OK, DEAD) else None

    def _remember(self, url: str, verdict: str, status: int = 0) -> None:
        if verdict not in (OK, DEAD):
            return
        self._entries[url] = {
            "verdict": verdict,
            "checked_at": time.time(),
            "status": int(status or 0),
        }
        self._dirty = True

    # --------------------------------------------------------------- probing

    def _note_refusal(self, host: str) -> None:
        """A vantage-shaped refusal, counted per host and nowhere else.

        The image is NOT dead - 403 says something about this runner, and that
        rule does not move. But a host that answers 403 for every URL we try,
        and 200 for none, is a host from which we cannot prove a single image
        loads. ধারা ৮ measures exactly that: "ছবিটি সত্যিই লোড হয় কি না".

        Measured on 2026-09-26: `srhady-live-stream.hf.space` served 1,005
        published posters and refused every probe, from two continents and
        through the site's own Cloudflare worker; so did 135 more behind an
        image proxy whose own error read "Failed to fetch image. Status: 403".
        Every one of those cards shows the placeholder to every viewer.

        Kept apart from `_dead_hosts` on purpose. Nothing here retires
        artwork; it only lets a caller prefer a poster it CAN prove over one
        it cannot.
        """
        if not host:
            return
        score = self._refusal_scores.setdefault(host, [0, 0])
        score[0] += 1
        if score[1] == 0 and score[0] >= HOST_BREAKER_TRIPS:
            self._refusing_hosts.add(host)

    def refuses_everything(self, url: str) -> bool:
        """Has this URL's host refused every probe and answered none?"""
        return _host_of(str(url or "")) in self._refusing_hosts

    def _note_host(self, host: str, refused: bool) -> None:
        if not host:
            return
        # A host that answers at all is not a host that refuses everything.
        if not refused:
            self._refusal_scores.setdefault(host, [0, 0])[1] += 1
            self._refusing_hosts.discard(host)
        score = self._host_scores.setdefault(host, [0, 0])
        if refused:
            score[0] += 1
        else:
            score[1] += 1
        # One success is enough to keep a host alive: the breaker is for hosts
        # that are gone, not for hosts with a few withdrawn images.
        if score[1] == 0 and score[0] >= HOST_BREAKER_TRIPS:
            if host not in self._dead_hosts:
                self._dead_hosts.add(host)
                self._dirty = True
        elif score[1] > 0:
            self._dead_hosts.discard(host)

    def _probe(self, url: str) -> Tuple[str, int]:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
            method="GET",
        )
        try:
            with self._opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                status = int(getattr(response, "status", 200) or 200)
                content_type = str(response.headers.get("Content-Type") or "")
                payload = response.read(PROBE_BYTES)
        except urllib.error.HTTPError as error:
            status = int(getattr(error, "code", 0) or 0)
            if status in VANTAGE_SHAPED_REFUSALS:
                # Says something about this runner, not about the image.
                return UNKNOWN, status
            return (DEAD if status in DEFINITIVE_REFUSALS else UNKNOWN), status
        except Exception:  # noqa: BLE001 - DNS, TLS, timeout, reset, ...
            return UNKNOWN, 0
        if status in VANTAGE_SHAPED_REFUSALS:
            return UNKNOWN, status
        if status in DEFINITIVE_REFUSALS:
            return DEAD, status
        if status >= 500:
            return UNKNOWN, status
        # A 200 that is an HTML error page is the other half of this failure:
        # the dead proxy answers some URLs with a courtesy page, not an image.
        return (OK if _looks_like_image(payload, content_type) else DEAD), status

    # ------------------------------------------------------------------ api

    def verdict(self, url: str) -> str:
        """``ok`` / ``dead`` / ``unknown`` for one poster URL."""
        url = str(url or "").strip()
        if not url:
            return UNKNOWN
        if url.startswith("data:image/"):
            # Self-contained, nothing to reach out to.
            return OK
        if not url.startswith(("http://", "https://")):
            return DEAD

        cached = self._cached(url)
        if cached:
            self.stats["cache_hits"] += 1
            return cached

        host = _host_of(url)
        if host in self._dead_hosts:
            self.stats["host_short_circuits"] += 1
            self.stats["dead"] += 1
            self._remember(url, DEAD, 0)
            return DEAD

        if not self.enabled:
            self.stats["unknown"] += 1
            return UNKNOWN

        if self._budget_spent or (time.time() - self._started) > self.time_budget_seconds:
            self._budget_spent = True
            self.stats["unknown"] += 1
            return UNKNOWN

        self.stats["probed"] += 1
        result, status = self._probe(url)
        self.stats[result] = self.stats.get(result, 0) + 1
        if result in (OK, DEAD):
            self._note_host(host, refused=(result == DEAD))
            self._remember(url, result, status)
        elif status in VANTAGE_SHAPED_REFUSALS:
            # Unknown, and stays unknown - but counted, so a host that answers
            # nothing at all can be told from one that simply was not asked.
            before = len(self._refusing_hosts)
            self._note_refusal(host)
            if len(self._refusing_hosts) > before:
                self.stats["hosts_refusing_all"] += 1
        return result

    def is_dead(self, url: str) -> bool:
        return self.verdict(url) == DEAD

    def summary_line(self) -> str:
        return (
            "{probed} probed, {ok} live, {dead} dead, {unknown} unresolved, "
            "{cache_hits} cached, {host_short_circuits} skipped on dead hosts"
        ).format(**self.stats)
