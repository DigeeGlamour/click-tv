"""One match, one card - even when two feeds spell the teams differently.

The merge folds two cards together when their participants match, and that
match is exact. So a fixture arriving from two sources under two spellings
publishes twice, side by side, and a viewer sees the same match offered as two
different things:

    Cagliari vs Inter                    Cagliari Vs Inter Milan
    Argentinos JRS vs Aldosivi           Argentinos Juniors Vs Aldosivi
    Deportivo vs Valencia                Deportivo de A Coruna Vs Valencia
    Independ Rivadavia vs Racing Club    Independiente Rivadavia Vs Racing Club

Every pair above shares a kickoff instant to the second and one side spelled
identically; only the other side differs, and it differs the way a feed
abbreviates rather than the way two clubs differ - a truncation ("Independ" for
"Independiente"), a dropped qualifier ("Inter" for "Inter Milan"), or an
initialism ("JRS" for "Juniors").

That is the whole rule, and it is deliberately narrow:

    the same kickoff, to the second
  + one side the same club beyond doubt - identical once the corporate suffix
    one feed keeps and another drops is set aside, so "Baniyas" and
    "Baniyas SC" anchor a fold and nothing looser does
  + the other side a truncation, an initialism, or a longer form of its pair

Two genuinely different fixtures do not clear that bar. "Manchester United vs
Arsenal" and "Manchester City vs Arsenal" share a kickoff and a side, and
"United" is not a truncation of "City", so they stay apart - which is the case
that makes a looser rule dangerous, because both sides would look equally
mergeable to anything that only counted matching words.

The richer card wins and absorbs the other's routes, so folding never costs a
stream: a card with five channels and a card with one become one card with the
five, plus whatever of the one it did not already have.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

_SPLIT = re.compile(r"\s+(?:vs?\.?|v|versus)\s+", re.IGNORECASE)
#: Decoration feeds add to a title that says nothing about which fixture it is.
_NOISE = re.compile(
    r"\b(?:live|hd|fhd|uhd|sd|4k|full\s*match|\d{1,2}\s+\w{3}\s+\d{4})\b",
    re.IGNORECASE)
#: Corporate suffixes that one feed keeps and another drops.
_SUFFIX = re.compile(
    r"\b(?:fc|afc|sc|cf|ac|as|ss|ssc|cd|ud|sd|club|futbol|football)\b",
    re.IGNORECASE)
_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)

#: Club abbreviations one feed prints and another drops. Leading ones matter as
#: much as trailing: `CD Toluca` and `Toluca`, `Club Leon` and `Leon`,
#: `CF Monterrey` and `Monterrey` are the same clubs, and the site published
#: each of those pairs as two cards for one match.
#:
#: Only true abbreviations. `Deportivo`, `Atletico`, `Sporting` and `Real` were
#: here for one draft and are the club's identity rather than decoration -
#: stripping "Deportivo" left `Deportivo vs Valencia` unable to match
#: `Deportivo de A Coruna Vs Valencia`, which is the pair this was meant to
#: fold in the first place.
_AFFIX = re.compile(
    r"\b(?:fc|afc|sc|cf|ac|as|ss|ssc|cd|ud|sd|cs|rc|sk|nk|hk|fk|club|clube)\b",
    re.IGNORECASE)

#: City and club spellings that differ by language rather than by club:
#: `SK Rapid Wien` and `Rapid Vienna` are one team in two languages.
_SAME_PLACE = {
    "wien": "vienna", "muenchen": "munchen", "munich": "munchen",
    "milano": "milan", "torino": "turin", "roma": "rome",
    "napoli": "naples", "firenze": "florence", "genova": "genoa",
    "sevilla": "seville", "zaragoza": "saragossa", "praha": "prague",
    "moskva": "moscow", "koln": "cologne", "koeln": "cologne",
    "lisboa": "lisbon", "beograd": "belgrade", "bucuresti": "bucharest",
    "warszawa": "warsaw", "kyiv": "kiev", "athina": "athens",
}


def _fold_accents(text: str) -> str:
    """`Concepcion` for `Concepcion`, so an accent is not a different club.

    Four of the seven duplicate pairs on the site differed by nothing else:
    `Universidad de Concepcion`, `Velez Sarsfield`, `Club Leon` and
    `Club America` each appeared twice, once with its accents and once
    without.
    """
    stripped = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in stripped if not unicodedata.combining(ch))

#: Below this a "truncation" is just a short word that happens to start the
#: same way - "San" against "Santos" would fold two different clubs.
MIN_TRUNCATION = 5
#: An initialism has to be long enough to mean something: "j" inside "juniors"
#: is not evidence, "jrs" is.
MIN_INITIALISM = 3


def _clean(text: Any) -> str:
    plain = _PUNCT.sub(" ", _fold_accents(text))
    plain = _NOISE.sub(" ", plain)
    return " ".join(plain.split()).casefold()


def _identity_module():
    """scanner.team_identity, or None when it cannot be imported."""
    try:
        from scanner import team_identity
    except ImportError:  # pragma: no cover - flat layout
        try:
            import team_identity  # type: ignore
        except ImportError:
            return None
    return team_identity


def _canonical(name: str, gender: str = "") -> str:
    """The club's canonical spelling, from the shared alias table.

    An exact lookup and nothing else - see scanner/team_identity.py. A club
    with no entry comes back unchanged, so this can only ever relate two
    spellings somebody has verified are one club.

    `gender` is the fixture's own evidence, and it only ever *narrows* what
    the lookup may use: an entry the table scopes to one category is
    reachable only by a fixture whose own title or competition states that
    category. No case is named here - they live in the table, as data.
    """
    module = _identity_module()
    if module is None:
        return name
    try:
        return module.canonical_team(name, gender) or name
    except Exception:  # noqa: BLE001 - a name must never break grouping
        return name


def fixture_gender(item: Dict[str, Any]) -> str:
    """The fixture's own gender evidence, from the shared helper."""
    module = _identity_module()
    if module is None:
        return ""
    try:
        return module.fixture_gender(item)
    except Exception:  # noqa: BLE001
        return ""


def sides(item: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """The two teams, cleaned and canonical, or None when the title is not
    a fixture.

    Comparison only: `correct_home_away` rewrites a title from `_teams_of`,
    which is the feed's own spelling, so a canonical name is never what a
    viewer reads.
    """
    name = str(item.get("name") or item.get("match_name") or "")
    gender = fixture_gender(item)
    parts = [_canonical(_clean(part), gender) for part in _SPLIT.split(name)]
    parts = [part for part in parts if len(part) > 2]
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def _bare(side: str) -> str:
    """The side with the decoration two feeds disagree about taken off.

    Affixes go from both ends, and a place name is spelled one way: what is
    left is the part of the name that actually identifies the club.
    """
    plain = _AFFIX.sub(" ", _SUFFIX.sub(" ", side))
    words = [_SAME_PLACE.get(word, word) for word in plain.split()]
    return " ".join(words)


def _is_initialism(short: str, long: str) -> bool:
    """Is `short` an abbreviation of `long`? "jrs" of "juniors".

    Letters in order is not enough on its own: "san" reads as the letters of
    "santos" in order, and folded San Lorenzo into Santos. A real abbreviation
    drops the vowels - "jrs", "utd", "bcn" - so a vowel anywhere but the first
    character means the short form is a word in its own right rather than a
    contraction of the long one.
    """
    if len(short) < MIN_INITIALISM or len(long) - len(short) < 2:
        return False
    if any(letter in "aeiou" for letter in short[1:]):
        return False
    position = 0
    for letter in short:
        position = long.find(letter, position)
        if position < 0:
            return False
        position += 1
    return True


def _same_token(left: str, right: str) -> bool:
    if left == right:
        return True
    short, long = sorted((left, right), key=len)
    if len(short) >= MIN_TRUNCATION and long.startswith(short):
        return True
    return _is_initialism(short, long)


def same_side(left: str, right: str) -> bool:
    """Whether two spellings name the same team.

    Compared token by token from the front, so a longer form only agrees when
    every token it shares with the shorter one agrees: "deportivo" matches
    "deportivo de a coruna", and "manchester united" does not match
    "manchester city".
    """
    if left == right:
        return True
    left_tokens = _bare(left).split()
    right_tokens = _bare(right).split()
    if not left_tokens or not right_tokens:
        return False
    if left_tokens == right_tokens:
        return True
    shared = min(len(left_tokens), len(right_tokens))
    return all(_same_token(left_tokens[index], right_tokens[index])
               for index in range(shared))


def _kickoff(item: Dict[str, Any]) -> str:
    for field in ("start_time", "start_at"):
        value = str(item.get(field) or "").strip()
        if value:
            return value
    return ""


#: Two feeds can disagree by a few minutes about when one match starts.
#: `Belfast Wolves vs Edinburgh Castle Rockers` was published twice on
#: 2026-09-02 - Willow's own schedule said 12:55 for "European T20 Premier
#: League 2026 - 11th Match" and bingstream said 13:15 for "ETPL" - and
#: requiring the same instant to the second kept both cards on the page.
#: A pair does not play twice inside an hour, so this is wide enough to
#: fold a disagreement and far too narrow to fold a double-header.
KICKOFF_TOLERANCE_MINUTES = 45


def _kickoff_instant(item: Dict[str, Any]) -> Optional[datetime]:
    written = _kickoff(item)
    if not written:
        return None
    try:
        parsed = datetime.fromisoformat(written.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def kickoff_matches(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    """Same kickoff, allowing for two feeds rounding it differently."""
    written = _kickoff(left)
    if not written or not _kickoff(right):
        return False
    if written == _kickoff(right):
        return True
    first, second = _kickoff_instant(left), _kickoff_instant(right)
    if first is None or second is None:
        return False
    return abs(first - second) <= timedelta(minutes=KICKOFF_TOLERANCE_MINUTES)

def same_fixture(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    """The narrow rule, stated once."""
    if not kickoff_matches(left, right):
        return False
    # A men's fixture and a women's fixture between the same two clubs are
    # two fixtures. The merge layer has always refused to fold a neutral
    # title into a gendered one - participant_fold_key carries the category
    # in the key - and this is the same refusal, so the tabs' narrower rule
    # and the merge layer cannot answer differently.
    module = _identity_module()
    if module is not None:
        try:
            if not module.genders_compatible(left, right):
                return False
        except Exception:  # noqa: BLE001
            pass
    left_sides, right_sides = sides(left), sides(right)
    if not left_sides or not right_sides:
        return False
    # One side the same team beyond doubt, the other merely spelled
    # differently. The anchor compares without the corporate suffix one feed
    # keeps and another drops - "Baniyas" and "Baniyas SC" are one club - but
    # nothing looser than that, which is what keeps "Manchester United vs
    # Arsenal" and "Manchester City vs Arsenal" apart: their anchor is Arsenal,
    # and "united" is not a spelling of "city".
    for a, b in ((0, 1), (1, 0)):
        if (_bare(left_sides[a]) == _bare(right_sides[a])
                and same_side(left_sides[b], right_sides[b])):
            return True

    # The same two teams with home and away the other way round. One feed had
    # `Real Sociedad vs RC Celta` and another `Celta Vigo vs Real Sociedad`
    # for one LaLiga fixture, and the site showed both. A team plays one match
    # at a time, so two cards naming the same pair at the same instant are the
    # same match however each feed ordered them.
    crossed = (_bare(left_sides[0]) == _bare(right_sides[1])
               and _bare(left_sides[1]) == _bare(right_sides[0]))
    if crossed:
        return True
    return (same_side(left_sides[0], right_sides[1])
            and same_side(left_sides[1], right_sides[0]))


def _weight(item: Dict[str, Any]) -> Tuple[int, int, int]:
    """How much a card is worth keeping: routes, then sources, then name length."""
    channels = item.get("channels")
    backups = item.get("backups")
    sources = item.get("source_ids")
    return (
        (len(channels) if isinstance(channels, list) else 0)
        + (len(backups) if isinstance(backups, list) else 0)
        + (1 if str(item.get("url") or "").strip() else 0),
        len(sources) if isinstance(sources, list) else 0,
        len(str(item.get("name") or "")),
    )


def _absorb(keeper: Dict[str, Any], other: Dict[str, Any]) -> None:
    """Move the folded card's routes and sources into the one being kept."""
    seen = {str(row.get("url") or "")
            for row in (keeper.get("backups") or []) if isinstance(row, dict)}
    seen.add(str(keeper.get("url") or ""))

    backups = list(keeper.get("backups") or [])
    for row in [other] + list(other.get("backups") or []):
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        # A backup is a route, not a card, so the folded card's own
        # bookkeeping does not travel with it. `_source_timezone` holds a
        # ZoneInfo, and nesting one here made the whole event payload
        # unserialisable: the 18:26 scan on 2026-09-02 did all its work,
        # then refused its own snapshot with "Object of type ZoneInfo is
        # not JSON serializable" and carried both event files forward
        # unchanged, so four home/away corrections never reached the page.
        backups.append(row if row is not other else
                       {key: value for key, value in other.items()
                        if key not in ("backups", "channels", "source_ids")
                        and not str(key).startswith("_")})
    if backups:
        keeper["backups"] = backups

    keeper_channels = list(keeper.get("channels") or [])
    known = {str(c.get("id") or c.get("name") or "")
             for c in keeper_channels if isinstance(c, dict)}
    for channel in other.get("channels") or []:
        if isinstance(channel, dict):
            marker = str(channel.get("id") or channel.get("name") or "")
            if marker and marker not in known:
                known.add(marker)
                keeper_channels.append(channel)
    if keeper_channels:
        keeper["channels"] = keeper_channels

    merged_sources = list(keeper.get("source_ids") or [])
    for source in other.get("source_ids") or []:
        if source not in merged_sources:
            merged_sources.append(source)
    if merged_sources:
        keeper["source_ids"] = merged_sources


def _crossed(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    """Whether the two cards order the same fixture the opposite way."""
    left_sides, right_sides = sides(left), sides(right)
    if not left_sides or not right_sides:
        return False
    if _bare(left_sides[0]) == _bare(right_sides[0]):
        return False
    return (same_side(left_sides[0], right_sides[1])
            and same_side(left_sides[1], right_sides[0]))


def _teams_of(item: Dict[str, Any]) -> Tuple[str, str]:
    """The two sides as the feed spelled them, not folded."""
    name = str(item.get("name") or item.get("match_name") or "")
    parts = [part.strip(" .-|,") for part in _SPLIT.split(name)]
    return (parts[0], parts[1]) if len(parts) == 2 else ("", "")


def _fix_home_away(keeper: Dict[str, Any], other: Dict[str, Any],
                   home_lookup: Optional[Any]) -> str:
    """Put the home side first, asked of the fixture rather than guessed.

    Two feeds ordered one LaLiga match two ways - `Real Sociedad vs RC Celta`
    and `Celta Vigo vs Real Sociedad` - and the site published both. Folding
    them leaves the question of which order is right, and neither card can
    answer it, so the fixture is asked. Returns the name it settled on, or ""
    when nobody could say and the keeper's own order stands.
    """
    if home_lookup is None:
        return ""
    home, away = _teams_of(keeper)
    if not home or not away:
        return ""
    date = _kickoff(keeper)[:10]
    try:
        told = home_lookup(home, away, date)
    except Exception:  # noqa: BLE001 - a lookup must never break a scan
        return ""
    if not told or told == home:
        return ""
    # The fixture says the other side is at home, so the card is turned round.
    corrected = f"{away} vs {home}"
    keeper["name_before_home_away_fix"] = keeper.get("name")
    keeper["name"] = corrected
    keeper["home_away_corrected"] = True
    return corrected


def fold(items: List[Dict[str, Any]],
         home_lookup: Optional[Any] = None
         ) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Fold duplicate spellings of one fixture together. Returns (kept, report).

    `home_lookup(home, away, date)` names the home side when two cards
    disagree about the order. Left out, the keeper's own order stands.
    """
    kept: List[Dict[str, Any]] = []
    report: List[Dict[str, str]] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        match = next((existing for existing in kept if same_fixture(existing, item)),
                     None)
        if match is None:
            kept.append(item)
            continue
        # The richer card leads, so folding never costs a stream.
        if _weight(item) > _weight(match):
            keeper, folded = item, match
            kept[kept.index(match)] = item
        else:
            keeper, folded = match, item
        was_crossed = _crossed(keeper, folded)
        _absorb(keeper, folded)
        corrected = _fix_home_away(keeper, folded, home_lookup) if was_crossed else ""
        report.append({
            "kept": str(keeper.get("name") or "")[:70],
            "folded": str(folded.get("name") or "")[:70],
            "kickoff": _kickoff(keeper),
            "home_away_corrected": corrected,
        })

    return kept, report

def correct_home_away(items: List[Dict[str, Any]],
                      resolver: Optional[Any] = None) -> List[Dict[str, str]]:
    """Put the home side first on every card, not only on folded pairs.

    `_fix_home_away` only runs while folding two spellings of one fixture,
    because that is where the question announces itself. A card that arrived
    once, from one feed, in the wrong order has nothing to be compared with -
    so `Real Madrid vs Real Betis` stayed on the page after the duplicate
    work was done, for a match played at Betis.

    `resolver(home, away, date)` is asked and believed only when it names the
    other side; silence leaves the feed's order alone, because reversing a
    fixture that was already right is the same defect in the other
    direction. Returns one row per card turned round.
    """
    if resolver is None:
        return []
    changed: List[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("home_away_corrected"):
            continue
        home, away = _teams_of(item)
        date = _kickoff(item)[:10]
        if not home or not away or not date:
            continue
        try:
            told = resolver(home, away, date)
        except Exception:  # noqa: BLE001 - a lookup must never break a scan
            continue
        if not told or same_side(told, home) or not same_side(told, away):
            continue
        item["name_before_home_away_fix"] = item.get("name")
        item["name"] = f"{away} vs {home}"
        item["home_away_corrected"] = True
        changed.append({"was": f"{home} vs {away}", "now": item["name"]})
    return changed
