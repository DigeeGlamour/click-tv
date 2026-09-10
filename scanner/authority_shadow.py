"""Compare every published fixture with the fixture authorities, and change nothing.

This is the observation half of the fixture-truth work. It reads the Today Match
and Upcoming cards a scan has just settled, asks LiveScore and ESPN what they
say about each one, and writes `reports/fixture-authority-shadow.json`. It has no
return path into a card: no verdict here adds one, removes one, or edits a
status, a kickoff, a lifecycle state, a badge or a `fixture_id`.

Three rules carry most of the weight.

**Matching reuses the identity rules that already exist.** `same_fixture` is the
narrow one - one side identical beyond doubt before the other may be merely a
spelling, which is what keeps `Manchester United vs Arsenal` away from
`Manchester City vs Arsenal` - and this module calls it rather than writing a
looser one. A team-name-only match is not accepted.

**A kickoff disagreement is found by lifting only the clock.** To ask "is this
the same fixture at a different time?" the authority's kickoff is replaced with
the card's and the real participant rule is asked again. Nothing about identity
is relaxed; the one thing under test is set aside so it can be reported.

**Two authorities agree only if they are two upstreams.** LiveScore's `Eid` and
ESPN's event `id` live in different id spaces, so neither is read as the other.
An association between them is proven the same way as any other - participants,
competition, kickoff - and `upstream_family` is what makes the count of witnesses
honest.
"""
from __future__ import annotations

import collections
import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from scanner import fixture_authority, fixture_dedupe, upstream_family
except ImportError:                                              # pragma: no cover
    import fixture_authority                                     # type: ignore
    import fixture_dedupe                                        # type: ignore
    import upstream_family                                       # type: ignore

UNKNOWN = fixture_authority.UNKNOWN
LIVE = fixture_authority.LIVE
UPCOMING = fixture_authority.UPCOMING
FINISHED = fixture_authority.FINISHED
INCONCLUSIVE = fixture_authority.INCONCLUSIVE

VERIFIED = fixture_authority.VERIFIED
PARTIAL_AUTHORITY = fixture_authority.PARTIAL_AUTHORITY
UNVERIFIED = fixture_authority.UNVERIFIED
CONFLICT = fixture_authority.CONFLICT

DEFAULT_REPORT_PATH = "reports/fixture-authority-shadow.json"

#: A card state that is a statement about the LINK, not about the match. A
#: fixture badged "link খোঁজা হচ্ছে" during play is the site being honest about
#: what it has, so it is not counted as contradicting an authority.
AWAITING_LINK = "AWAITING_LINK"

#: Click TV's own vocabulary, mapped into the authority vocabulary so the two
#: can be compared at all. Only for the report.
CARD_STATUS_MAP: Dict[str, str] = {
    "LIVE_NOW": LIVE,
    "LIVE": LIVE,
    "IN_PROGRESS": LIVE,
    "CHANNEL_LIVE": LIVE,
    "UPCOMING": UPCOMING,
    "STARTING_SOON": UPCOMING,
    "TIME_UNVERIFIED": UNKNOWN,
    "LINK_UPDATING": AWAITING_LINK,
    "ENDED": FINISHED,
    "END_PENDING": UNKNOWN,
}

#: How far two kickoffs may sit apart and still be reported as one clock.
#: Deliberately the dedupe layer's own number rather than a second opinion.
KICKOFF_SAME_MINUTES = fixture_dedupe.KICKOFF_TOLERANCE_MINUTES
#: Past this, the two sides refer to one fixture but not to one kickoff.
KICKOFF_CONFLICT_MINUTES = 20


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _parse(value: Any) -> Optional[_dt.datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        stamp = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=_dt.timezone.utc)


def card_status(card: Dict[str, Any]) -> str:
    """The card's own claim, in the authority vocabulary.

    `schedule_status` is what the site shows, so it is what gets compared.
    `lifecycle_state` is carried raw in the report beside it rather than
    folded in - they answer different questions and a report that blurs them
    would hide the case where they disagree with each other.
    """
    configured = _text(card.get("schedule_status") or card.get("status")).upper()
    return CARD_STATUS_MAP.get(configured, UNKNOWN)


def _as_fixture_shape(row: Dict[str, Any]) -> Dict[str, Any]:
    """An authority row in the shape the identity rules expect.

    The competition has to travel with it. `genders_compatible` reads gender
    out of the competition as well as the title, so a probe carrying only a
    name is gender-unknown - and it refused `Kuwait vs Qatar` against
    `Kuwait vs Qatar` because our card's competition says "ACC Men's Premier
    Cup" and the bare probe said nothing. Withholding evidence from a rule is
    not the same as the rule being wrong.
    """
    return {
        "name": row.get("name", ""),
        "start_time": row.get("kickoff", ""),
        "competition": row.get("competition", ""),
    }


#: The one way of finding an authority fixture that counts as identifying it.
#:
#: `match_one` below returns four kinds. Only this one asked the clock. The
#: others found a candidate by setting the clock aside, or found more than one,
#: and neither is an identification - measured over 10,230 shadow rows:
#:
#:     same_fixture               9725 readings, kickoff delta median 0, max 30 min
#:     kickoff_lifted              270 readings, delta 1 hour to 25 hours
#:     ambiguous_kickoff_lifted     26 readings
#:     ambiguous                     8 readings
#:
#: The lifted readings resolve to 12 distinct (card, authority fixture) pairs,
#: and every one of them is plausibly the SAME fixture with a disagreeing
#: clock - a Test whose authority record is dated from day one, an FA Cup tie
#: our feed dates a day early, one of our own duplicate cards. None is a
#: proven different meeting. That is the point: a lifted match may be right,
#: and being right by luck is not evidence. Two of them reached HIGH
#: confidence, and a HIGH terminal state applies with no confirmation at all.
VERIFIED_MATCH = "same_fixture"


def match_one(
    card: Dict[str, Any],
    rows: Iterable[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], str]:
    """The authority fixture for this card, and how it was found.

    `("same_fixture")` is the narrow rule passing outright.
    `("kickoff_lifted")` means the participants rule passes once the clock is
    taken out of it - the same fixture, a different stated kickoff.
    `("", None)` is no match, which is a real answer and not a failure.
    """
    candidates = list(rows or ())
    strict = [row for row in candidates
              if fixture_dedupe.same_fixture(card, _as_fixture_shape(row))]
    if len(strict) == 1:
        return strict[0], "same_fixture"
    if len(strict) > 1:
        # Two authority fixtures answering to one card is a question, not an
        # identification - the rule `_absorb_timeless` already applies. The
        # nearest kickoff is reported, and the verdict is only ever partial.
        anchor = _parse(card.get("start_time"))
        if anchor is not None:
            strict.sort(key=lambda row: abs(
                ((_parse(row.get("kickoff")) or anchor) - anchor).total_seconds()))
        return strict[0], "ambiguous"

    anchor = _parse(card.get("start_time"))
    if anchor is None:
        return None, ""
    lifted: List[Dict[str, Any]] = []
    for row in candidates:
        row_kickoff = _parse(row.get("kickoff"))
        if row_kickoff is None:
            continue
        # One clock set aside, every identity rule still asked.
        probe = dict(_as_fixture_shape(row),
                     start_time=card.get("start_time", ""))
        if not fixture_dedupe.same_fixture(card, probe):
            continue
        # And still the same day, so a season's worth of meetings between two
        # clubs cannot volunteer for each other.
        if abs((row_kickoff - anchor).total_seconds()) > 36 * 3600:
            continue
        lifted.append(row)
    if len(lifted) == 1:
        return lifted[0], "kickoff_lifted"
    if len(lifted) > 1:
        lifted.sort(key=lambda row: abs(
            ((_parse(row.get("kickoff")) or anchor) - anchor).total_seconds()))
        return lifted[0], "ambiguous_kickoff_lifted"
    return None, ""


def near_misses(
    card: Dict[str, Any],
    rows: Iterable[Dict[str, Any]],
    limit: int = 3,
) -> List[Dict[str, str]]:
    """Authority fixtures that agree on the clock and on ONE side only.

    An unmatched card is not very informative on its own. This says whether
    the authority had the fixture and the identity rules refused it, which is
    how `Barbados Tridents vs Saint Lucia Kings` against ESPN's `Barbados
    Tridents vs St Lucia Kings` becomes a legible finding rather than a
    silent UNVERIFIED. It reports; it never relaxes anything.
    """
    our_sides = fixture_dedupe.sides(card)
    if not our_sides:
        return []
    found: List[Dict[str, str]] = []
    for row in rows or ():
        probe = _as_fixture_shape(row)
        if not fixture_dedupe.kickoff_matches(card, probe):
            continue
        their_sides = fixture_dedupe.sides(probe)
        if not their_sides:
            continue
        agreed = [index for index in (0, 1)
                  if fixture_dedupe.same_side(our_sides[index], their_sides[index])]
        if len(agreed) != 1:
            continue
        differing = 1 - agreed[0]
        found.append({
            "authority_name": _text(row.get("name")),
            "event_id": _text(row.get("authority_event_id")),
            "agreed_side": our_sides[agreed[0]],
            "ours": our_sides[differing],
            "theirs": their_sides[differing],
        })
        if len(found) >= limit:
            break
    return found


def _kickoff_delta_minutes(card: Dict[str, Any],
                           row: Dict[str, Any]) -> Optional[float]:
    left, right = _parse(card.get("start_time")), _parse(row.get("kickoff"))
    if left is None or right is None:
        return None
    return round((right - left).total_seconds() / 60.0, 1)


def _sport_of(card: Dict[str, Any]) -> str:
    return _text(card.get("sport_type") or card.get("sport")).casefold()


def compare(
    card: Dict[str, Any],
    by_authority: Dict[str, List[Dict[str, Any]]],
    health: Dict[str, Any],
) -> Dict[str, Any]:
    """One report row: what we publish, what each authority says, and the verdict."""
    families = upstream_family.describe(card.get("source_ids")
                                        or [card.get("source_id")])
    ours = card_status(card)
    row: Dict[str, Any] = {
        "fixture_id": _text(card.get("fixture_id")),
        "card_id": _text(card.get("id")),
        "name": _text(card.get("name")),
        "competition": _text(card.get("competition")),
        "kickoff": _text(card.get("start_time")),
        "schedule_status": _text(card.get("schedule_status")),
        "lifecycle_state": _text(card.get("lifecycle_state")),
        "card_status_normalized": ours,
        "sport_type": _text(card.get("sport_type")),
        "schedule_source_url": _text(card.get("schedule_source_url")),
        "provider_identity": _text(card.get("tvg_id")),
        "source_ids": families["source_ids"],
        "upstream_families": families["upstream_families"],
        "independent_witness_count": families["independent_witness_count"],
        "authorities": {},
        "authority_upstreams": [],
        "independent_authority_count": 0,
        #: Upstream families that found a candidate without agreeing on the
        #: clock, or found more than one. Reported so the identity work has
        #: the readings; never counted as authority.
        "near_miss_upstreams": [],
        "near_miss_count": 0,
        "conflict_reasons": [],
    }

    matched_statuses: Dict[str, str] = {}
    partial = False
    for authority_id, rows in sorted(by_authority.items()):
        state = (health.get(authority_id) or {}).get("state")
        if state == INCONCLUSIVE:
            row["authorities"][authority_id] = {"result": INCONCLUSIVE}
            continue
        found, how = match_one(card, rows)
        if found is None:
            entry = {"result": "unmatched"}
            close = near_misses(card, rows)
            if close:
                entry["near_misses"] = close
            row["authorities"][authority_id] = entry
            continue
        delta = _kickoff_delta_minutes(card, found)
        entry = {
            "result": "matched",
            "matched_by": how,
            # What this reading is worth, stated rather than inferred from
            # `matched_by` by every reader in turn. `result` stays "matched"
            # so the report keeps its shape and its history stays comparable.
            "verified": how == VERIFIED_MATCH,
            "event_id": _text(found.get("authority_event_id")),
            "upstream_family": _text(found.get("upstream_family")),
            "name": _text(found.get("name")),
            "home": _text(found.get("home")),
            "away": _text(found.get("away")),
            "home_team_id": _text(found.get("home_team_id")),
            "away_team_id": _text(found.get("away_team_id")),
            "competition": _text(found.get("competition")),
            "competition_id": _text(found.get("competition_id")),
            "kickoff": _text(found.get("kickoff")),
            "kickoff_delta_minutes": delta,
            "status_raw": _text(found.get("status_raw")),
            "status_code": _text(found.get("status_code")),
            "status": _text(found.get("status")),
            "sport": _text(found.get("sport")),
            "round": _text(found.get("round")),
            "format": _text(found.get("format")),
            "gender": _text(found.get("gender")),
        }
        for extra in ("espn_state", "espn_status", "espn_status_detail",
                      "espn_status_description", "espn_completed",
                      "espn_end_date_raw", "authority_uid"):
            if extra in found:
                entry[extra] = found[extra]
        row["authorities"][authority_id] = entry

        family = entry["upstream_family"] or upstream_family.family_for(authority_id)
        if family:
            if entry["verified"]:
                if family not in row["authority_upstreams"]:
                    row["authority_upstreams"].append(family)
            elif family not in row["near_miss_upstreams"]:
                # Counted, and counted separately. A near miss that fed
                # `authority_upstreams` was a second witness that had not
                # identified the fixture, and two of those reached HIGH.
                row["near_miss_upstreams"].append(family)

        if not entry["verified"]:
            partial = True
        if entry["status"] == UNKNOWN:
            partial = True
        elif entry["verified"]:
            # A near miss does not vote on what the authorities say, so it can
            # neither create an agreement nor manufacture a disagreement.
            matched_statuses[authority_id] = entry["status"]

        if delta is not None and abs(delta) > KICKOFF_CONFLICT_MINUTES:
            row["conflict_reasons"].append(
                "kickoff: ours %s, %s %s (%+.1f min)"
                % (row["kickoff"][:16] or "(none)", authority_id,
                   entry["kickoff"][:16] or "(none)", delta))
        our_sport = _sport_of(card)
        their_sport = entry["sport"].casefold()
        if our_sport and their_sport and our_sport != their_sport:
            row["conflict_reasons"].append(
                "sport: ours %s, %s %s" % (our_sport, authority_id, their_sport))

    row["authority_upstreams"].sort()
    row["independent_authority_count"] = len(row["authority_upstreams"])
    row["near_miss_upstreams"].sort()
    row["near_miss_count"] = len(row["near_miss_upstreams"])

    distinct = sorted(set(matched_statuses.values()))
    if len(distinct) > 1:
        row["authority_agreement"] = "DISAGREE"
        row["conflict_reasons"].append(
            "authorities disagree: " + ", ".join(
                "%s=%s" % (key, value)
                for key, value in sorted(matched_statuses.items())))
    elif len(distinct) == 1:
        row["authority_agreement"] = ("AGREE" if len(matched_statuses) > 1
                                      else "SINGLE")
    else:
        row["authority_agreement"] = "NONE"

    # The card against the authorities. AWAITING_LINK is not a claim about the
    # match, and UNKNOWN on either side is not a disagreement.
    if distinct and ours not in (UNKNOWN, AWAITING_LINK):
        against = [value for value in distinct if value != ours]
        if against and len(distinct) == 1:
            row["conflict_reasons"].append(
                "card says %s, authority says %s" % (ours, distinct[0]))
            row["card_status_conflict"] = True
    row.setdefault("card_status_conflict", False)

    if not row["authority_upstreams"]:
        row["verdict"] = UNVERIFIED
    elif row["conflict_reasons"]:
        row["verdict"] = CONFLICT
    elif partial or not matched_statuses:
        row["verdict"] = PARTIAL_AUTHORITY
    else:
        row["verdict"] = VERIFIED
    return row


def build(
    today_items: Iterable[Dict[str, Any]],
    upcoming_items: Iterable[Dict[str, Any]],
    by_authority: Dict[str, List[Dict[str, Any]]],
    health: Dict[str, Any],
    *,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """The whole shadow report, ready to write."""
    reference = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(
        _dt.timezone.utc)
    tabs = (("today_match", list(today_items or ())),
            ("upcoming", list(upcoming_items or ())))

    rows: List[Dict[str, Any]] = []
    for tab, items in tabs:
        for card in items:
            if not isinstance(card, dict):
                continue
            row = compare(card, by_authority, health)
            row["tab"] = tab
            rows.append(row)

    verdicts = collections.Counter(row["verdict"] for row in rows)
    per_authority: Dict[str, Dict[str, int]] = {}
    for authority_id in sorted(by_authority):
        results = collections.Counter(
            (row["authorities"].get(authority_id) or {}).get("result", "unmatched")
            for row in rows)
        per_authority[authority_id] = dict(results)

    both = [row for row in rows if row["independent_authority_count"] >= 2]
    witness_distribution = collections.Counter(
        row["independent_authority_count"] for row in rows)
    reasons = collections.Counter()
    for row in rows:
        for reason in row["conflict_reasons"]:
            reasons[reason.split(":", 1)[0]] += 1

    # Unmatched cards the authority did carry, refused by an identity rule.
    # Named so a spelling gap is findable instead of hiding inside UNVERIFIED.
    spelling: List[Dict[str, Any]] = []
    for row in rows:
        for authority_id, entry in (row.get("authorities") or {}).items():
            for miss in (entry or {}).get("near_misses") or []:
                spelling.append({
                    "fixture": row["name"],
                    "authority": authority_id,
                    "authority_name": miss["authority_name"],
                    "agreed_side": miss["agreed_side"],
                    "ours": miss["ours"],
                    "theirs": miss["theirs"],
                })

    unavailable = sorted(key for key, value in (health or {}).items()
                         if (value or {}).get("state") == INCONCLUSIVE)

    return {
        "generated_at": reference.isoformat(),
        "note": (
            "Shadow mode. Every verdict here is an observation: no card was "
            "added, removed, re-timed, re-badged or re-identified because of "
            "it, and no fixture_id changed. An authority that could not be "
            "read is INCONCLUSIVE - authority unavailable - which is never "
            "evidence that a fixture ended or never existed. A status that "
            "has not been seen in a real response is UNKNOWN rather than "
            "guessed; football half-time is the standing example."
        ),
        "shadow_only": True,
        "authorities": health,
        "authority_unavailable": unavailable,
        "totals": {
            "today_match": sum(1 for row in rows if row["tab"] == "today_match"),
            "upcoming": sum(1 for row in rows if row["tab"] == "upcoming"),
            "fixtures": len(rows),
            "verdicts": dict(verdicts),
            "verified": verdicts.get(VERIFIED, 0),
            "partial_authority": verdicts.get(PARTIAL_AUTHORITY, 0),
            "unverified": verdicts.get(UNVERIFIED, 0),
            "conflict": verdicts.get(CONFLICT, 0),
            "matched_by_authority": per_authority,
            "matched_by_two_independent_authorities": len(both),
            "authority_agreements": sum(
                1 for row in rows if row.get("authority_agreement") == "AGREE"),
            "authority_disagreements": sum(
                1 for row in rows if row.get("authority_agreement") == "DISAGREE"),
            "card_status_conflicts": sum(
                1 for row in rows if row.get("card_status_conflict")),
            "conflict_reason_counts": dict(reasons),
            "identity_near_misses": len(spelling),
            "independent_authority_count_distribution": {
                str(key): value for key, value in sorted(witness_distribution.items())
            },
            "unknown_authority_statuses": {
                key: (value or {}).get("unknown_statuses") or []
                for key, value in (health or {}).items()
            },
        },
        "identity_near_misses": spelling,
        "fixtures": rows,
    }


def write(report: Dict[str, Any],
          path: str | Path = DEFAULT_REPORT_PATH) -> None:
    """Write the report. A write that cannot happen is not a scan failure."""
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    except OSError:
        pass


def summarize(report: Dict[str, Any]) -> Dict[str, Any]:
    """The few numbers worth carrying in reports/event-schedule.json."""
    totals = report.get("totals") or {}
    return {
        "shadow_only": True,
        "fixtures": totals.get("fixtures", 0),
        "verified": totals.get("verified", 0),
        "partial_authority": totals.get("partial_authority", 0),
        "unverified": totals.get("unverified", 0),
        "conflict": totals.get("conflict", 0),
        "matched_by_two_independent_authorities":
            totals.get("matched_by_two_independent_authorities", 0),
        "authority_agreements": totals.get("authority_agreements", 0),
        "authority_disagreements": totals.get("authority_disagreements", 0),
        "card_status_conflicts": totals.get("card_status_conflicts", 0),
        "authority_unavailable": report.get("authority_unavailable") or [],
        "report": DEFAULT_REPORT_PATH,
    }
