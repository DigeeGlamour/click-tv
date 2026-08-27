"""One channel, however its playlist chose to spell it.

Measured problem, and it is the reason "so many sources and still no good
route" was true. Every spelling of a channel became a separate channel:

    Zee Bangla        -> tv:zee-bangla
    Zee Bangla HD     -> tv:zee-bangla-hd        <- different group
    [BD] Zee Bangla   -> tv:bd-zee-bangla        <- different group
    Zee Bangla VIP    -> tv:zee-bangla-vip       <- different group
    Star Jalsha HD    -> tv:star-jalsha-hd       <- different group
    Star Jolsha       -> tv:star-jolsha          <- different group

Groups are what compete for a card's primary and backup slots, so the routes
that could have served Zee Bangla were scattered across five of them and its
own group only ever saw the two or three URLs literally named "Zee Bangla".
The jio "Zee Bangla HD" route, the toffee "[BD] Zee Bangla" route and the
`103.159.180.34` "ZEE BANGLA" route could never become its primary or backup,
however healthy they were.

The owner's rule decides what may be folded and it is not symmetrical:

    "Zee Bangla HD is another SOURCE of the same channel and is acceptable.
     Zee Bangla Cinema and Zee Bangla Sonar are DIFFERENT CHANNELS entirely -
     substituting one of those would put the wrong programme on the card,
     which is worse than a channel that stutters."

So this module folds only markers that describe the FEED - resolution, region
tag, playlist tier - and refuses to fold anything carrying a word that names a
different service. When in doubt it does not merge, because a wrong merge shows
the wrong programme and a missed merge only costs a route.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

#: Words that mark a DIFFERENT channel, not another feed of the same one. If a
#: name contains one of these, its canonical form keeps it and it can never
#: merge with the bare name. Every entry here is a real channel that exists
#: alongside its namesake in this project's own sources.
DISTINGUISHING_WORDS: Tuple[str, ...] = (
    "cinema", "sonar", "sansar", "movies", "movie", "music", "natok", "cine",
    "gold", "classic", "action", "comedy", "kids", "junior", "news", "sports",
    "life", "prime", "premier", "premiere", "select", "pictures", "aath",
    "bangla cinema", "digital", "originals", "drama", "thriller", "vip plus",
    "marathi", "telugu", "tamil", "kannada", "malayalam", "keralam", "punjabi",
    "bhojpuri", "odia", "assamese", "urdu", "hindi", "english",
)

#: Markers that describe the feed rather than the channel: resolution, tier and
#: region. Removing these is what lets one card gather every front of the same
#: channel.
FEED_MARKERS: Tuple[str, ...] = (
    "uhd", "fhd", "qhd", "hd", "sd", "ld",
    "4k", "2k", "1080p", "1080i", "720p", "576p", "480p", "360p",
    "full hd", "ultra hd", "high", "low",
    "vip", "backup", "alt", "alternate", "mirror", "server",
    "feed", "live", "channel", "tv channel",
)

#: Spellings of the same name. Bengali channel names reach these playlists
#: through several transliterations and each one used to make its own channel.
TRANSLITERATIONS: Dict[str, str] = {
    "jolsha": "jalsha",
    "jalsa": "jalsha",
    "jolsa": "jalsha",
    "bangla": "bangla",
    "bengali": "bangla",
    "zeebangla": "zee bangla",
    "starjalsha": "star jalsha",
}

_LEADING_TAG = re.compile(r"^\s*(?:[\[(<{][^\])>}]*[\])>}]\s*)+")
_TRAILING_TAG = re.compile(r"\s*(?:[\[(<{][^\])>}]*[\])>}])+\s*$")
_PUNCT = re.compile(r"[^a-z0-9]+")

#: "&TV" is a channel and "TV" is not a name. Dropping the ampersand as
#: punctuation left "&TV HD" canonicalising to "tv", which would have collected
#: any feed whose name reduced to that - the widest possible wrong merge. Spelt
#: out instead, so "&TV" is "and tv" and stays its own channel.
_AMPERSAND = re.compile(r"&")

#: A pipe in an EXTINF name means the playlist put a group label in the field:
#: "NEWS | India TV". Canonicalising that produced "news india tv", which is a
#: real and DIFFERENT channel from India TV - a wrong merge that would put one
#: broadcaster's stream on another's card. Names like this are left alone, so
#: they group by themselves. Under-merging costs a route; a wrong merge shows
#: the wrong programme.
_AMBIGUOUS_SEPARATOR = re.compile(r"[|/]{1}")


def _words(text: str) -> List[str]:
    return [w for w in _PUNCT.sub(" ", text).split() if w]


def has_distinguishing_word(name: Any) -> Optional[str]:
    """The word that makes this a different channel, or None."""
    text = str(name or "").casefold()
    flat = " ".join(_words(text))
    for word in DISTINGUISHING_WORDS:
        if " " in word:
            if word in flat:
                return word
        elif word in flat.split():
            return word
    return None


def canonical_channel_name(name: Any) -> str:
    """The channel this feed belongs to, as a normalised name.

    Region tags and feed markers are removed; a distinguishing word is kept, so
    "Zee Bangla Cinema" canonicalises to "zee bangla cinema" and never collides
    with "zee bangla".

    Feed markers are only stripped from the END, and only while the remainder
    still has a word left. "HD" alone, or a name that is nothing but markers,
    is not a channel name and is returned unchanged rather than emptied - an
    empty identity would silently merge unrelated feeds, which is the exact
    failure this module exists to avoid.
    """
    text = str(name or "").strip()
    if not text:
        return ""

    if _AMBIGUOUS_SEPARATOR.search(text):
        # Group label smuggled into the name field. Kept in its own namespace:
        # flattening alone was not enough, because "NEWS | India TV" flattens to
        # exactly the canonical form of the real and different channel "News
        # India TV" and merged with it anyway.
        flattened = " ".join(_words(text.casefold()))
        return f"unparsed:{flattened}" if flattened else ""

    text = _LEADING_TAG.sub("", text)
    text = _TRAILING_TAG.sub("", text)
    text = _AMPERSAND.sub(" and ", text)
    words = _words(text.casefold())
    if not words:
        return ""

    # Transliterations first, so "star jolsha" and "star jalsha" agree before
    # anything else is decided about them.
    words = [TRANSLITERATIONS.get(word, word) for word in words]

    # Trailing feed markers always come off, longest phrase first so "full hd"
    # goes before "hd" can leave "full" behind.
    #
    # An earlier version skipped this entirely when the name carried a
    # distinguishing word, and that was wrong twice over. The two lists are
    # disjoint - no feed marker is a distinguishing word - so stripping can
    # never remove "cinema" or "sonar" and the guard protected nothing. What it
    # did do was block every channel whose name legitimately contains a generic
    # word: "sports", "news", "kids", "hindi". "Star Sports 1 HD" therefore
    # stayed separate from "Star Sports 1", losing routes for hundreds of
    # channels for no gain.
    changed = True
    while changed and len(words) > 1:
        changed = False
        for marker in sorted(FEED_MARKERS, key=len, reverse=True):
            parts = marker.split()
            if len(parts) <= len(words) - 1 and words[-len(parts):] == parts:
                words = words[: -len(parts)]
                changed = True
                break

    return " ".join(words)


def same_channel(left: Any, right: Any) -> bool:
    """Whether two playlist names describe one channel."""
    left_canonical = canonical_channel_name(left)
    right_canonical = canonical_channel_name(right)
    if not left_canonical or not right_canonical:
        return False
    if left_canonical != right_canonical:
        return False
    # Belt and braces: identical canonical forms cannot carry different
    # distinguishing words, but assert it rather than assume it.
    return has_distinguishing_word(left) == has_distinguishing_word(right)


def alias_report(names: Any) -> Dict[str, List[str]]:
    """canonical name -> the spellings that map to it. For audits."""
    groups: Dict[str, Set[str]] = {}
    for name in names or ():
        canonical = canonical_channel_name(name)
        if not canonical:
            continue
        groups.setdefault(canonical, set()).add(str(name))
    return {key: sorted(value) for key, value in sorted(groups.items())}
