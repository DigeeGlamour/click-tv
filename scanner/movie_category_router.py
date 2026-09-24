"""ধাপ ১১ / A-03 - give the films in "Mix" the category they belong to.

    ৯৩৭টি মুভি (৫৬%) "Mix"-এ — এটি ক্যাটাগরি নয়, আবর্জনার ঝুড়ি।
    ব্যবহারকারী Bangla/Hindi/English-এ খুঁজে পাবে না।

Measured on the live catalogue and the plan's number is exact: 937 of 1,667.
A viewer browsing Bangla sees 29 films while 937 sit in a bin.

The plan's recommendation is `original_language` + `production_countries`
from whichever provider can supply them - TMDB is not a fixed primary. This
module is the decision; collecting the two fields is the providers' job and
applying the answer is `scanner/movies.py`'s.

**Three rules that this is mostly made of, and each exists because the
alternative loses or mislabels a film:**

1.  **Only "Mix" is reconsidered.** A film a source put in Hindi is in Hindi
    because somebody said so. Metadata may fill a blank; it may not overrule
    a statement. Without this rule one bad provider match silently rewrites a
    category a human curated.

2.  **"Dubbed" is never inferred.** It is the only category that describes the
    *audio of this file*, not the film. A Hindi-dubbed Hollywood action film
    has `original_language: en` and would be routed to English, which is
    exactly wrong for a viewer who opened the Dubbed row to hear Hindi.
    "Premium" is likewise a curation label, not a property of the film.

3.  **No evidence keeps it in Mix.** Mix is the honest answer for "we do not
    know", and A-03 is about films we *can* place. Guessing from a country
    alone is how a Tamil film ends up in Hindi: India makes both.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: The categories this router may produce. Deliberately not the whole list:
#: see rule 2. "Mix" is the absence of an answer rather than an answer.
ROUTABLE = ("Bangla", "Hindi", "South Indian", "English")

#: Never produced by this router, whatever the metadata says.
NEVER_INFERRED = ("Dubbed", "Premium", "Mix")

#: ISO 639-1, the field TMDB calls `original_language`. One language, one
#: category - a language that maps to two categories does not belong here.
LANGUAGE_CATEGORY: Dict[str, str] = {
    "bn": "Bangla",
    "hi": "Hindi",
    "ur": "Hindi",  # Urdu films sit with Hindi on this site by convention
    "ta": "South Indian",
    "te": "South Indian",
    "ml": "South Indian",
    "kn": "South Indian",
    "tu": "South Indian",  # Tulu
    "en": "English",
}

#: Country evidence, used ONLY when the language is unknown, and only where
#: the country names one film industry. India is absent on purpose: it makes
#: Hindi and South Indian films alike, so "IN" alone proves nothing (rule 3).
COUNTRY_CATEGORY: Dict[str, str] = {
    "BD": "Bangla",
}

#: Full names, because OMDb answers "Bengali" and "Bangladesh" where TMDB
#: answers "bn" and "BD".
LANGUAGE_NAMES: Dict[str, str] = {
    "bengali": "bn",
    "bangla": "bn",
    "hindi": "hi",
    "urdu": "ur",
    "tamil": "ta",
    "telugu": "te",
    "malayalam": "ml",
    "kannada": "kn",
    "tulu": "tu",
    "english": "en",
}

COUNTRY_NAMES: Dict[str, str] = {
    "bangladesh": "BD",
    "india": "IN",
    "pakistan": "PK",
    "united states": "US",
    "united states of america": "US",
    "united kingdom": "GB",
}

#: Why a film was moved, or why it was not. Recorded on the card so the
#: decision is auditable from the published catalogue rather than only from
#: this run's log.
BY_LANGUAGE = "original_language"
BY_COUNTRY = "production_country"
NO_EVIDENCE = "no_evidence"
NOT_MIX = "category_already_stated"
UNMAPPED = "unmapped_language"


def normalise_language(value: Any) -> str:
    """"bn", "Bengali", "bn-BD" - all the same answer, or "" for nothing."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[text]
    # "bn-BD", "pt_BR": the tag before the region is the language.
    head = text.replace("_", "-").split("-")[0]
    if head in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[head]
    return head if len(head) in (2, 3) else ""


def normalise_countries(value: Any) -> List[str]:
    """A list of ISO 3166-1 alpha-2 codes, from any of the shapes providers
    send: codes, names, or TMDB's `[{"iso_3166_1": "BD", ...}]`."""
    if value is None:
        return []
    items: Iterable[Any]
    if isinstance(value, str):
        items = [part for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        items = [value]

    codes: List[str] = []
    for item in items:
        text = ""
        if isinstance(item, dict):
            text = str(item.get("iso_3166_1") or item.get("code")
                       or item.get("name") or "").strip()
        else:
            text = str(item or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in COUNTRY_NAMES:
            codes.append(COUNTRY_NAMES[lowered])
        elif len(text) == 2:
            codes.append(text.upper())
    # Ordered, de-duplicated: the first country a provider lists is the one it
    # considers primary, and that order is evidence.
    seen: List[str] = []
    for code in codes:
        if code not in seen:
            seen.append(code)
    return seen


def decide(
    language: Any = None,
    countries: Any = None,
    *,
    current_category: str = "Mix",
) -> Tuple[Optional[str], str]:
    """`(category, reason)` - the category is None when nothing changes.

    Never returns a category equal to the current one, so a caller can treat
    any answer as "this moved".
    """
    if str(current_category or "Mix").strip() != "Mix":
        return None, NOT_MIX

    code = normalise_language(language)
    if code:
        mapped = LANGUAGE_CATEGORY.get(code)
        if mapped:
            return mapped, BY_LANGUAGE
        # A language we do not have a row for - Japanese, Korean, Spanish.
        # There is no category for them on this site, so Mix is correct and
        # the reason says it was a decision rather than an absence.
        return None, UNMAPPED

    for country in normalise_countries(countries):
        mapped = COUNTRY_CATEGORY.get(country)
        if mapped:
            return mapped, BY_COUNTRY

    return None, NO_EVIDENCE


def decide_for(
    card: Dict[str, Any], record: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[str], str]:
    """The same decision, reading a card and its cached metadata record.

    The card is asked first: a source that stated a language knows more about
    this file than a provider that matched it by title.
    """
    source: Dict[str, Any] = {}
    for candidate in (card or {}, record or {}):
        if not isinstance(candidate, dict):
            continue
        for field in ("original_language", "production_countries"):
            if not source.get(field) and candidate.get(field):
                source[field] = candidate[field]
    return decide(
        source.get("original_language"),
        source.get("production_countries"),
        current_category=str((card or {}).get("category") or "Mix"),
    )


def redistribute(
    cards: Sequence[Dict[str, Any]],
    records: Optional[Dict[str, Any]] = None,
    *,
    identity=None,
) -> Dict[str, Any]:
    """Move what can be placed. Returns a summary; mutates `category` only.

    Nothing is added, removed or reordered - a film that cannot be placed is
    left exactly as it was, still visible, still in Mix. A-03 is about where a
    film appears, never about whether it appears.
    """
    records = records if isinstance(records, dict) else {}
    if identity is None:
        from scanner import movie_metadata_cache

        identity = movie_metadata_cache.canonical_identity

    moved: Dict[str, int] = {}
    reasons: Dict[str, int] = {}
    examples: List[str] = []
    considered = 0

    for card in cards or ():
        if not isinstance(card, dict):
            continue
        if str(card.get("category") or "Mix").strip() != "Mix":
            continue
        considered += 1
        record = None
        try:
            record = records.get(identity(card))
        except Exception:  # noqa: BLE001 - one card, never the run
            record = None
        category, reason = decide_for(card, record)
        reasons[reason] = reasons.get(reason, 0) + 1
        if not category:
            continue
        card["category"] = category
        card["category_routed_from"] = "Mix"
        card["category_routed_by"] = reason
        moved[category] = moved.get(category, 0) + 1
        if len(examples) < 5:
            examples.append(f"{card.get('name') or card.get('title')} -> {category}")

    return {
        "considered": considered,
        "moved": sum(moved.values()),
        "moved_by_category": dict(sorted(moved.items())),
        "reasons": dict(sorted(reasons.items())),
        "examples": examples,
        "remaining_in_mix": considered - sum(moved.values()),
    }


def describe(summary: Dict[str, Any]) -> str:
    """One line for the scan log."""
    if not summary or not summary.get("considered"):
        return ""
    moved = summary.get("moved", 0)
    if not moved:
        return (
            f"Mix redistribution: {summary['considered']} in Mix, none placeable "
            "yet (metadata has no language for them)"
        )
    spread = ", ".join(
        f"{category} {count}"
        for category, count in (summary.get("moved_by_category") or {}).items()
    )
    return (
        f"Mix redistribution: {moved} of {summary['considered']} placed "
        f"({spread}); {summary.get('remaining_in_mix', 0)} stay in Mix"
    )
