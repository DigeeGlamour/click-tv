"""Canonical movie genre names, and the eight the frontend surfaces (PART 05).

Five providers spell the same genre five ways. TMDB says "Science Fiction"
for a film and "Sci-Fi & Fantasy" for a series, OMDb and Cinemeta say
"Sci-Fi", TVMaze says "Science-Fiction", AniList says "Sci-Fi". Left alone
that is five different genres in the index and a film that answers to none
of them, so every provider's genres go through canonical_genres() before
they are cached.

Two separate ideas live here, and conflating them is the mistake to avoid:

  - The CANONICAL name is just the agreed spelling of a real genre. Drama,
    Documentary and Biography are all real and all kept - the backend is
    not lossy just because the frontend is currently selective.

  - The FRONTEND set is the eight genres the movie UI shows today, fixed by
    the final Category/Genre reference: Action, Comedy, Horror, Romance,
    Thriller, Animation, Sci-Fi, Crime. Only these get an index file. A
    ninth genre appearing in the data is not a bug; it simply has no chip
    to be clicked yet.

Nothing here invents a genre. A title with no provider genres stays with no
genres, which is why a genre index can legitimately be short while the
metadata backfill is still working through the catalogue.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

#: The eight genres the movie frontend shows, in the reference's order.
FRONTEND_GENRES: Tuple[str, ...] = (
    "Action",
    "Comedy",
    "Horror",
    "Romance",
    "Thriller",
    "Animation",
    "Sci-Fi",
    "Crime",
)

#: Index filename per frontend genre: data/movies/genres/<slug>.json
GENRE_SLUGS: Dict[str, str] = {
    "Action": "action",
    "Comedy": "comedy",
    "Horror": "horror",
    "Romance": "romance",
    "Thriller": "thriller",
    "Animation": "animation",
    "Sci-Fi": "sci-fi",
    "Crime": "crime",
}

#: Every canonical spelling this system recognises. Genres outside the
#: frontend eight are kept in the data, they simply have no index file.
_CANONICAL_NAMES: Tuple[str, ...] = FRONTEND_GENRES + (
    "Adventure",
    "Biography",
    "Documentary",
    "Drama",
    "Family",
    "Fantasy",
    "Film-Noir",
    "History",
    "Music",
    "Musical",
    "Mystery",
    "News",
    "Reality",
    "Short",
    "Sport",
    "Supernatural",
    "Talk Show",
    "War",
    "Western",
    "Game Show",
    "Slice of Life",
    "Psychological",
    "Mecha",
    "Ecchi",
)

#: Provider spellings that mean one of the names above. Keys are matched
#: after normalisation (casefold, non-alphanumerics collapsed), so
#: "Sci-Fi", "sci fi" and "SciFi" all arrive here as "scifi".
_ALIASES: Dict[str, str] = {
    "sciencefiction": "Sci-Fi",
    "scifi": "Sci-Fi",
    "anime": "Animation",
    "animated": "Animation",
    "cartoon": "Animation",
    "realitytv": "Reality",
    "talkshow": "Talk Show",
    "gameshow": "Game Show",
    "filmnoir": "Film-Noir",
    "sports": "Sport",
    "kids": "Family",
    "children": "Family",
    "romantic": "Romance",
    "thrillers": "Thriller",
    "sliceoflife": "Slice of Life",
    "suspense": "Thriller",
    "crimedrama": "Crime",
    "horrorthriller": "Horror",
}

#: Combined genres a provider ships as one string (TMDB's TV list mostly).
#: Each expands to several real genres rather than being dropped.
_COMPOUND: Dict[str, Tuple[str, ...]] = {
    "actionadventure": ("Action", "Adventure"),
    "scififantasy": ("Sci-Fi", "Fantasy"),
    "warpolitics": ("War",),
    "actioncrime": ("Action", "Crime"),
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

#: Provider "no value" sentinels. OMDb answers "N/A" for a title it has no
#: genre for, and taken literally that becomes a genre called "N/a" sitting
#: in the data next to Action and Crime. An absent genre must stay absent.
_NOT_A_GENRE = frozenset({"", "na", "n", "none", "null", "unknown", "undefined", "nil", "0"})


def _normalise(value: Any) -> str:
    return _NON_ALNUM.sub("", str(value or "").casefold())


_LOOKUP: Dict[str, str] = {_normalise(name): name for name in _CANONICAL_NAMES}
_LOOKUP.update({key: value for key, value in _ALIASES.items()})


def canonical_genre(value: Any) -> List[str]:
    """One provider genre string -> zero or more canonical genre names.

    Zero when the string is empty or unrecognised noise; more than one when
    the provider ships a compound genre such as "Action & Adventure".
    """
    text = str(value or "").strip()
    if not text:
        return []
    key = _normalise(text)
    if not key or key in _NOT_A_GENRE:
        return []
    if key in _COMPOUND:
        return list(_COMPOUND[key])
    known = _LOOKUP.get(key)
    if known:
        return [known]
    # An unrecognised but real genre is kept rather than discarded - the
    # providers are allowed to know about genres this list has not met yet.
    # Title-cased so the data stays consistent to read and to index.
    return [" ".join(part.capitalize() for part in re.split(r"\s+", text) if part)]


def canonical_genres(values: Iterable[Any]) -> List[str]:
    """A provider's genre list -> canonical names, de-duplicated, in order."""
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        return []
    ordered: Dict[str, None] = {}
    for value in values:
        for name in canonical_genre(value):
            if name not in ordered:
                ordered[name] = None
    return list(ordered)


def frontend_genres(values: Iterable[Any]) -> List[str]:
    """Just the eight the movie UI shows today, in the reference's order."""
    present = set(canonical_genres(values))
    return [name for name in FRONTEND_GENRES if name in present]
