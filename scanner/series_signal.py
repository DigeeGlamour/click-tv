"""ধারা ৪.৬ - is this row a series, and which season and episode is it?

Read from the title, with regex and no API call at all. The plan checked this
against the data and it holds: of the 348 rows in this catalogue that carry an
explicit season/episode marker, 348 state both numbers in the title. An API is
needed to learn a *show's* canonical name and metadata - once per show - never
to learn which episode a row is.

Run **before** the title cleaner, which is the whole point of putting it here:
the cleaner strips release noise, and `S01E15` sitting between the show name
and `Bangla Dubbed` is exactly the kind of token a cleaner removes. Extracting
after cleaning means extracting from a title the evidence has been taken out
of.

The two rules that are not negotiable
-------------------------------------
**An episode number is never invented.** `S01` on its own means the season is
known and the episode is not. It does not mean episode 1. The plan spells this
out because the earlier draft would have turned 197 such rows into "Episode 1",
each one a false claim about what a viewer is about to watch.

**A series identity never contains a year.** A movie is `title + exact year`
(King Kong 1933 is not King Kong 2005). A show is not: its seasons arrive in
different years, and this catalogue proves it - House of the Dragon appears as
2022, 2024 and 2026; Reacher as 2025 and 2026; Dirilis Ertugrul as 2023 and
2024. Grouping on title+year would split one show into three cards, which is
the very problem this work exists to fix, wearing a different hat.

Title protection
----------------
A number in a title is usually part of the title: 1917, 2012, Drishyam 2,
Blade Runner 2049. So a season marker has to be a standalone `S<digits>` token
- not preceded by a word character or an apostrophe, not followed by one. That
last exclusion is not hypothetical: this catalogue contains "Newton's 3rd Law",
and a looser pattern reads `'s 3` as season 3.

Measured while writing this, against all 1,667 published cards:

    SxxExx (explicit episode)          348
    season marker only                 221
    of those, a pack signal             15
    "S1 P01"  (Part, not Episode)        1
    false positives from apostrophes     0

The naive pattern `S\\s?(\\d+)\\b(?!\\s?E)` was measured too and rejected: its
"not followed by E" guard also rejects "Peaky Blinders S01 English", losing
five real season-only rows including the one the plan names as its own
worked example.

Stdlib only. Decides nothing, publishes nothing - it reads a string.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

#: Bumped when the rules below change. It belongs in every cache key that
#: stores a result derived from them (ধারা ৪.৭ negative-cache policy), so a
#: rule change invalidates what the old rules produced instead of leaving it
#: to sit behind a 90-day TTL.
CLASSIFIER_VERSION = 1

#: A season token, protected against numbers that are part of a title.
#: `(?<![\w'])` keeps "Newton's 3rd Law" out; `(?![\w])` keeps "S01E15" from
#: being read as a bare season.
_SEASON = r"(?<![\w'])S\s?0?(\d{1,2})(?![\w])"

#: S01E15, S1 E 5, S01EP15. The episode side allows an optional `P` because
#: `EP05` appears in the wild.
_SXXEXX = re.compile(
    _SEASON.replace("(?![\\w])", "") + r"\s*E\s?P?\s?0?(\d{1,3})(?![\w])",
    re.IGNORECASE,
)

#: A range, in the three spellings this catalogue actually uses:
#:   "S06E11-20"   "S01E1 4"   "S1 E 1 to 10"
#: The bare-space form is why this is a separate pattern rather than an
#: optional tail on the one above: "S06E11 20 Dual Audio" is episodes 11 to 20,
#: and reading it as episode 11 alone would publish a ten-episode batch link
#: under one episode's name.
#:
#: The separator is MANDATORY, and that is the whole correctness of this
#: pattern. With it optional, `S01E15` matched as "1" then "5" - so the first
#: version of this module read episode 15 as episodes 1 to 5, and would have
#: done the same to all 348 explicit episodes in the catalogue. Caught by
#: running it against real titles before trusting it.
_SXXEXX_RANGE = re.compile(
    _SEASON.replace("(?![\\w])", "")
    + r"\s*E\s?P?\s?0?(\d{1,3})(?:\s*[-–&]\s*|\s+to\s+|\s+)0?(\d{1,3})(?![\w])",
    re.IGNORECASE,
)

#: Numbers that follow an episode and are not an episode. A title ending
#: "S01E15 1080p" would otherwise read as episodes 15 to 1080.
_NOT_AN_EPISODE_NUMBER = frozenset({360, 480, 576, 720, 1080, 1440, 2160})

#: The largest gap a range may span. Long-running serials do exceed 200
#: episodes, but a *range* that wide in a title is a resolution or a year far
#: more often than it is a batch link.
_MAX_RANGE_END = 200

#: "S1 P01" - a Part, which is not an episode and must never become one.
_SEASON_PART = re.compile(
    _SEASON.replace("(?![\\w])", "") + r"\s*P\s?0?(\d{1,2})(?![\w])",
    re.IGNORECASE,
)

#: A season marker with nothing after it. The lookahead lets "S01 English"
#: through - it is a language, not an episode - while still refusing "S01E15",
#: "S01 EP15" and "S01 P01", which the patterns above own.
_SEASON_ONLY = re.compile(
    _SEASON + r"(?!\s*(?:E\s?P?|P)\s?\d)",
    re.IGNORECASE,
)

_SEASON_WORD = re.compile(
    r"\bseason\s*0?(\d{1,2})\b(?!\s*(?:,|\s)*episode)", re.IGNORECASE)

_SEASON_EPISODE_WORDS = re.compile(
    r"\bseason\s*0?(\d{1,2})\b[^\w]{0,6}\bepisode\s*0?(\d{1,3})\b",
    re.IGNORECASE,
)

#: ধারা ৪.৬ tier 1. Only these count as a pack claim; anything vaguer is a
#: guess, and a wrong "Complete Season" is a promise to a viewer that the card
#: cannot keep.
_PACK_SIGNAL = re.compile(
    r"\b(?:complete(?:\s+season)?|full\s*season|season\s*pack|all\s*episodes)\b",
    re.IGNORECASE,
)

_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_BRACKETED_YEAR = re.compile(r"[\(\[]\s*(?:19|20)\d{2}\s*[\)\]]")

PATTERN_SXXEXX = "sxxexx"
PATTERN_SXXEXX_RANGE = "sxxexx_range"
PATTERN_SEASON_EPISODE_WORDS = "season_episode_words"
PATTERN_SEASON_PART = "season_part"
PATTERN_SEASON_ONLY = "season_only"


def _first_year(title: str) -> int:
    """The year the title states, bracketed form preferred.

    Bracketed first because "Vikings (2019) S06E11" has exactly one year and
    "Blade Runner 2049 (2017)" has two, only one of which is a year.
    """
    bracketed = _BRACKETED_YEAR.search(title or "")
    if bracketed:
        found = _YEAR.search(bracketed.group(0))
        if found:
            return int(found.group(0))
    found = _YEAR.search(title or "")
    return int(found.group(0)) if found else 0


def _base_show(title: str, marker_start: int) -> str:
    """The show's name: everything before the season marker, year removed.

    Deliberately simple. Everything after the marker is season/episode/release
    noise, and everything before it is the show - which is true of all 569
    rows in this catalogue that carry a marker.
    """
    head = (title or "")[:marker_start]
    head = _BRACKETED_YEAR.sub(" ", head)
    head = _YEAR.sub(" ", head)
    head = re.sub(r"[\s._\-]+", " ", head)
    return head.strip(" -._")


def show_key(base_show: Any) -> str:
    """The year-free identity two spellings of one show have to agree on.

    Built with the merger's own title normaliser - the canonical one - and then
    deliberately *without* the year the movie identity would append. That
    omission is the rule from ধারা ৪.৬, not an oversight:
    House of the Dragon 2022/2024/2026 is one show.

    This is priority 2 of the plan's three-step series identity. Priority 1 is
    an external show id (TMDB TV / TVDB / IMDb) and belongs to the
    classification cache, which does not exist yet; priority 3 is a
    provider's first-air-year, used only to break a collision. This catalogue
    has no such collision today - 262 distinct base names were checked and the
    three with several years are all legitimately one show - so priority 2 is
    doing all the work, and the other two are future protection.
    """
    text = str(base_show or "").strip()
    if not text:
        return ""
    try:
        from scanner.merger import _normalize_movie_title

        normalized = _normalize_movie_title(_YEAR.sub(" ", text))
    except Exception:  # noqa: BLE001 - identity must not take a report down
        normalized = " ".join(
            re.sub(r"[^a-z0-9]+", " ", _YEAR.sub(" ", text).casefold()).split()
        )
    normalized = " ".join(normalized.split())
    if not normalized:
        return ""
    return "show:" + normalized.replace(" ", "-")


def detect(title: Any) -> Dict[str, Any]:
    """Everything the title says about season and episode, and nothing more.

    `episode` is `None` whenever the title does not state one. There is no
    branch in this function that can produce a number the title did not
    contain.
    """
    raw = str(title or "")
    result: Dict[str, Any] = {
        "raw_title": raw,
        "base_show": "",
        "show_key": "",
        "year": _first_year(raw),
        "season": None,
        "episode": None,
        "episode_end": None,
        "part": None,
        "detected_pattern": "",
        "pack_signal": bool(_PACK_SIGNAL.search(raw)),
        "is_series_signal": False,
        "classifier_version": CLASSIFIER_VERSION,
    }
    if not raw.strip():
        return result

    match = _SXXEXX_RANGE.search(raw)
    pattern = PATTERN_SXXEXX_RANGE
    if match:
        start, end = int(match.group(2)), int(match.group(3))
        # Three ways a "range" is not one, each of which would invent episodes
        # the title never claimed. Falling back to the single-episode reading
        # is always the smaller claim, so it is what a failed check does.
        if (
            end <= start
            or end > _MAX_RANGE_END
            or end in _NOT_AN_EPISODE_NUMBER
            or (1900 <= end <= 2100)
        ):
            match = None
    if not match:
        match = _SXXEXX.search(raw)
        pattern = PATTERN_SXXEXX
    if not match:
        match = _SEASON_EPISODE_WORDS.search(raw)
        pattern = PATTERN_SEASON_EPISODE_WORDS
    if not match:
        match = _SEASON_PART.search(raw)
        pattern = PATTERN_SEASON_PART
    if not match:
        match = _SEASON_ONLY.search(raw)
        pattern = PATTERN_SEASON_ONLY
    if not match:
        match = _SEASON_WORD.search(raw)
        pattern = PATTERN_SEASON_ONLY
    if not match:
        return result

    result["detected_pattern"] = pattern
    result["is_series_signal"] = True
    result["season"] = int(match.group(1))
    if pattern == PATTERN_SXXEXX_RANGE:
        result["episode"] = int(match.group(2))
        result["episode_end"] = int(match.group(3))
    elif pattern in (PATTERN_SXXEXX, PATTERN_SEASON_EPISODE_WORDS):
        result["episode"] = int(match.group(2))
    elif pattern == PATTERN_SEASON_PART:
        # A Part is not an Episode. Recorded so the evidence is not thrown
        # away, and deliberately not copied into `episode`.
        result["part"] = int(match.group(2))

    result["base_show"] = _base_show(raw, match.start())
    result["show_key"] = show_key(result["base_show"])
    return result


# ---------------------------------------------------------------------------
# ধারা ৪.৬ - the three evidence tiers for a season-only row
# ---------------------------------------------------------------------------

TIER_EXPLICIT_EPISODE = "explicit_episode"
TIER_PACK_SIGNAL = "pack_signal"
TIER_SIBLING_EPISODE = "sibling_episode"
TIER_NO_EVIDENCE = "no_evidence"

#: Which tiers may move a card onto a series card, and which may not.
#: ধারা ৪.৬: an unproven row stays exactly where it is today - a movie card -
#: with `classification_pending`. "অনিশ্চয়তা থাকলে আমরা অনুমান করব না, হারাবও
#: না, এবং লুকাবও না."
TIERS_THAT_MOVE = frozenset({
    TIER_EXPLICIT_EPISODE, TIER_PACK_SIGNAL, TIER_SIBLING_EPISODE,
})


def classify_rows(signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add an evidence tier to each detected signal, using only the catalogue.

    Tier 2 is the interesting one and it costs nothing: a season-only row whose
    show already has an explicit `SxxExx` sibling somewhere in the same
    catalogue is *proved* to be a series without asking any provider. As new
    episodes arrive, rows sitting at tier 3 climb to tier 2 on their own.
    """
    shows_with_episodes = {
        signal.get("show_key")
        for signal in signals
        if signal.get("show_key") and signal.get("episode") is not None
    }

    classified: List[Dict[str, Any]] = []
    for signal in signals:
        record = dict(signal)
        if not record.get("is_series_signal"):
            record["evidence_tier"] = ""
            record["moves_to_series"] = False
            classified.append(record)
            continue

        if record.get("episode") is not None:
            tier = TIER_EXPLICIT_EPISODE
        elif record.get("pack_signal"):
            tier = TIER_PACK_SIGNAL
        elif record.get("show_key") in shows_with_episodes:
            tier = TIER_SIBLING_EPISODE
        else:
            tier = TIER_NO_EVIDENCE

        record["evidence_tier"] = tier
        record["moves_to_series"] = tier in TIERS_THAT_MOVE
        # The flag a row carries while it waits, and the reason it is still a
        # visible movie card rather than a quarantined one.
        record["classification_pending"] = tier == TIER_NO_EVIDENCE
        classified.append(record)
    return classified


def season_label(signal: Dict[str, Any]) -> str:
    """What a season entry is called when the episode is not known.

    Never "Episode 1". ধারা ৪.৬ names these two spellings and no third.
    """
    season = signal.get("season")
    if season is None:
        return ""
    if signal.get("pack_signal"):
        return f"Season {season} — Complete Season"
    if signal.get("episode") is None:
        return f"Season {season} — Unspecified"
    return f"Season {season}"
