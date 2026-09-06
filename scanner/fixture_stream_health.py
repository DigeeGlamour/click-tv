"""Per-fixture stream health - FINAL_2 ধাপ ৬, FINAL_3 অংশ ৫.

The scan reports a number of source errors - 326 on the day this was planned,
805 on the day it was built - and nobody can act on it, because the number
counts *routes*. One fixture carried by three feeds with four backups each is
twelve routes; eleven of them can fail while the match plays perfectly. Read as
matches, the same number says the tab is destroyed. Read as routes, it may say
nothing happened at all.

So the unit here is the fixture, and the row says what happened to its routes:

    fixture_id · source_id · candidate_stream_count · verified_stream_count
    failed_stream_count · failure_codes[] · fallback_available
    published_without_stream

A fixture is listed when it was published, or when this scan checked at least
one route for it. Nothing here accepts, refuses, ranks or groups anything: it
reads what the scan already decided, and no URL, token or header is written
into it.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPORT_FILE = Path("reports") / "fixture-stream-health.json"

#: A route that passed. `verified_global` and `verified_proxy` are the two ways
#: a check can pass in this scanner.
VERIFIED_PREFIX = "verified"

#: A route that was checked and did not pass. `metadata_only` is neither: it is
#: a fixture that carries no link yet, which is a state and not a failure.
FAILED_STATUSES = frozenset({
    "failed", "failed_bd", "unreachable_from_browser", "error", "timeout",
    "404_quarantined", "rejected_low_quality", "quarantine",
})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False,
        prefix=f".{path.name}.", suffix=".tmp",
    )
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)


def _slug(value: Any) -> str:
    text = _text(value).casefold()
    return "-".join(part for part in re.split(r"[^a-z0-9]+", text) if part)


def fixture_identity(item: Dict[str, Any]) -> str:
    """The key a route and a card are joined on.

    Not `fixture_id`. A published card carries
    `provider:chelsea-vs-arsenal|premier league|2026-09-05` while the
    candidate that became it carries no fixture_id at all - the provider path
    assigns one later, and measured on a real scan not one of 214 published
    cards joined to its routes that way.

    What both sides do carry is the slug the merge settled on: the verifier
    writes it as `_verification_group` ("today_match:chelsea-vs-arsenal") and
    the card wears it as its own id. That joins 203 of the 214.
    """
    group = _text(item.get("_verification_group"))
    if group:
        # Slugged like every other key here. A card id keeps the letters a feed
        # wrote - `fc-schalke-04-vs-bayern-münchen` - and a raw group key kept
        # them too, so the same fixture arrived under two spellings and its
        # ladder attempts landed on the row that had none.
        return _slug(group.split(":", 1)[-1])
    event_id = _slug(item.get("id"))
    if event_id:
        return event_id
    name = _slug(item.get("name"))
    if name:
        return name
    return _text(item.get("fixture_id")).casefold()


def _is_route(item: Dict[str, Any]) -> bool:
    """A candidate carrying something that was meant to be played."""
    if item.get("metadata_only") is True:
        return False
    return bool(_text(item.get("url")) or _text(item.get("final_url")))


def _route_verdict(item: Dict[str, Any]) -> str:
    status = _text(item.get("verification_status")).casefold()
    if item.get("verified") is True or status.startswith(VERIFIED_PREFIX):
        return "verified"
    if status in FAILED_STATUSES:
        return "failed"
    return "unchecked"


def _card_is_playable(card: Dict[str, Any]) -> bool:
    if card.get("metadata_only") is True:
        return False
    if int(card.get("available_link_count") or 0) > 0:
        return True
    return bool(_text(card.get("url")))


def _card_fallbacks(card: Dict[str, Any]) -> int:
    """Routes behind the primary that a viewer could fall back to."""
    backups = card.get("backups")
    if isinstance(backups, list):
        usable = [entry for entry in backups if isinstance(entry, dict)
                  and _text(entry.get("url"))]
        if usable:
            return len(usable)
        return len([entry for entry in backups if _text(entry)])
    return max(0, int(card.get("available_link_count") or 0) - 1)


def _source_ids(item: Dict[str, Any]) -> List[str]:
    ids: List[str] = []
    primary = _text(item.get("source_id"))
    if primary:
        ids.append(primary)
    for field in ("source_ids", "alias_source_ids"):
        value = item.get(field)
        if isinstance(value, list):
            ids.extend(_text(entry) for entry in value if _text(entry))
    provenance = item.get("source_provenance")
    if isinstance(provenance, list):
        for entry in provenance:
            if isinstance(entry, dict) and _text(entry.get("source_id")):
                ids.append(_text(entry.get("source_id")))
    for backup in item.get("backups") or []:
        if isinstance(backup, dict) and _text(backup.get("source_id")):
            ids.append(_text(backup.get("source_id")))
    return ids


#: FINAL_2 ধাপ ৬ asks for `first_seen_link_at`; FINAL_3 অংশ ৫ asks for
#: `first_link_at`. The ledger written by PROMPT 04-08 calls it `first_link_at`,
#: and so do its accessor and its tests. One name, and it is the one already in
#: the data - renaming a field in a state file to match a note in a plan would
#: lose every timestamp already written under the old key.
LEDGER_FIELDS = ("target_attempt_count", "last_attempt_bucket", "first_link_at")


#: What a route failure is called. Every name here is derived from evidence the
#: verifier already writes - `verification_error_kind`, `verification_status`,
#: `verification_mode`, `http_status`, and the same message words
#: bd_verifier._error_kind and fast_pipeline._error_kind already read. Nothing
#: is invented: a failure this scanner cannot describe is reported as
#: `verification_failed` rather than given a name that sounds like a diagnosis.
#:
#: A code is a code, never the message. The real errors carry host addresses
#: ("the playback proxy cannot fetch a bare IP host (193.47.62.41)") and a URL
#: can carry a play token, so no raw text reaches this report.
UNCLASSIFIED_FAILURE = "verification_failed"

#: Statuses that already name their own failure.
STATUS_FAILURE_CODES = {
    "unreachable_from_browser": "browser_unreachable",
    "404_quarantined": "http_404",
    "rejected_low_quality": "rejected_low_quality",
    "quarantine": "quarantined",
}

#: Verification modes that are themselves the reason.
MODE_FAILURE_CODES = {
    "undeliverable_bare_ip": "undeliverable_bare_ip",
    "same_run_player_gate": "player_gate_failed",
}

#: The message words the existing classifiers read, in their order.
MESSAGE_FAILURE_CODES = (
    (("name or service not known", "dns"), "dns"),
    (("certificate", "ssl", "tls"), "ssl"),
    # Before the connection words, because "connection timed out" is a timeout
    # and bd_verifier._error_kind has always read it that way. Its neighbour
    # keeps fast_pipeline's narrow spelling for the same reason.
    (("timed out", "timeout"), "timeout"),
    (("connection refused", "connection reset"), "connection"),
    (("network is unreachable", "no route", "network"), "network"),
    (("stream url is empty",), "empty_stream_url"),
    (("manifest", "invalid", "html"), "invalid_content"),
)


def failure_code(item: Dict[str, Any]) -> str:
    """One stable, actionable name for why this route failed.

    Read in the order the evidence is trustworthy: what the verifier explicitly
    called it, then what its status or mode already means, then the HTTP status
    the host answered with, then the message words - which is exactly the order
    bd_verifier._error_kind and fast_pipeline._error_kind use.
    """
    explicit = _text(item.get("verification_error_kind")
                     or item.get("error_kind")).casefold()
    if explicit:
        return explicit

    status = _text(item.get("verification_status")).casefold()
    if status in STATUS_FAILURE_CODES:
        return STATUS_FAILURE_CODES[status]

    mode = _text(item.get("verification_mode")).casefold()

    http_status = item.get("http_status")
    try:
        code = int(http_status)
    except (TypeError, ValueError):
        code = 0
    if code >= 400:
        return "http_%d" % code

    message = _text(item.get("verification_error")).casefold()
    for needles, name in MESSAGE_FAILURE_CODES:
        if any(needle in message for needle in needles):
            return name

    if mode in MODE_FAILURE_CODES:
        return MODE_FAILURE_CODES[mode]
    return UNCLASSIFIED_FAILURE


def _load_ledger(path: Any = None) -> Dict[str, Any]:
    try:
        from scanner import targeted_scan
    except ImportError:  # flat layout
        try:
            import targeted_scan  # type: ignore
        except ImportError:
            return {}
    try:
        return targeted_scan.load_ledger(path or targeted_scan.STATE_FILE)
    except Exception:  # noqa: BLE001 - a report never breaks a scan
        return {}


def _ledger_index(ledger: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Ledger entries by the same key the rows are built on.

    The ledger keys a fixture by its published card id where there is one -
    which is the slug the rows use - and by `name@2026090512` where there is
    not. Both are indexed, so a fixture that was targeted before it had a card
    is still found after the promotion gave it one.
    """
    index: Dict[str, Dict[str, Any]] = {}
    fixtures = ledger.get("fixtures")
    if not isinstance(fixtures, dict):
        return index
    for key, record in fixtures.items():
        if not isinstance(record, dict):
            continue
        index.setdefault(_slug(str(key).split("@", 1)[0]), record)
        name = _slug(record.get("name"))
        if name:
            index.setdefault(name, record)
    return index


def _ledger_evidence(record: Dict[str, Any]) -> Dict[str, Any]:
    """What the ladder did for this fixture. Never an invented attempt.

    A fixture the targeted trigger has never touched reports 0 and empty
    strings - not null-as-unknown and not a guess from the card, because the
    ledger is the only thing that knows, and "never targeted" is an answer.
    """
    try:
        attempts = max(0, int(record.get("attempts") or 0))
    except (TypeError, ValueError):
        attempts = 0
    return {
        "target_attempt_count": attempts,
        "last_attempt_bucket": _text(record.get("last_attempt_bucket")),
        # Written once, when a link FIRST existed. A later scan that finds the
        # same fixture again must not move it - that timestamp is how long the
        # ladder took, and rewriting it every scan would always read as zero.
        "first_link_at": _text(record.get("first_link_at")),
        "targeted": bool(record),
    }


def build_fixture_stream_health(
    candidates: Iterable[Dict[str, Any]],
    today_items: Iterable[Dict[str, Any]],
    upcoming_items: Iterable[Dict[str, Any]],
    *,
    fixtures: Optional[Iterable[Dict[str, Any]]] = None,
    ledger: Optional[Dict[str, Any]] = None,
    ledger_path: Any = None,
    now: Optional[datetime] = None,
    classify: Optional[Any] = None,
) -> Dict[str, Any]:
    """One row per fixture, built from the routes this scan actually checked.

    `fixtures` is what the pipeline recognised as a cricket or football
    fixture. Every candidate group still gets a row - a route that failed
    failed - but the totals are counted over the recognised ones, because a
    tennis broadcast the sport filter deliberately discarded is not a lost
    match and must never be added to a number that reads like one.

    `ledger` is state/upcoming-targeting.json - the targeted trigger's own
    record of what it tried. It is read, never written: this report says
    what the ladder did, and changes nothing about what it does next.

    `classify` names a route failure. It defaults to this module's own
    classifier, which reads what the verifier already recorded; pass False
    to count failures without naming them. Without one the row
    still says how many routes failed - it just does not say what kind of
    failure it was.
    """
    reference = now or datetime.now(timezone.utc)
    classifier = (failure_code if classify is None
                  else (classify or None))
    failure_routes: Dict[str, int] = {}
    today = [card for card in today_items if isinstance(card, dict)]
    upcoming = [card for card in upcoming_items if isinstance(card, dict)]

    recognised = {
        fixture_identity(item) for item in (fixtures or [])
        if isinstance(item, dict)
    } | {fixture_identity(card) for card in today + upcoming}
    recognised.discard("")

    ladder = _ledger_index(
        ledger if ledger is not None else _load_ledger(ledger_path))

    rows: Dict[str, Dict[str, Any]] = {}

    def row_for(identity: str, item: Dict[str, Any]) -> Dict[str, Any]:
        row = rows.get(identity)
        if row is None:
            row = {
                "fixture_id": _text(item.get("fixture_id")) or identity,
                "fixture_key": identity,
                "fixture_name": _text(item.get("name")),
                "recognised_fixture": identity in recognised,
                "source_id": "",
                "source_ids": [],
                "candidate_stream_count": 0,
                "verified_stream_count": 0,
                "failed_stream_count": 0,
                "unchecked_stream_count": 0,
                "failure_codes": [],
                "fallback_available": False,
                "published": False,
                "published_tab": "",
                "published_without_stream": False,
                "target_attempt_count": 0,
                "last_attempt_bucket": "",
                "first_link_at": "",
                "targeted": False,
            }
            row.update(_ledger_evidence(ladder.get(identity) or {}))
            rows[identity] = row
        if not row["fixture_name"]:
            row["fixture_name"] = _text(item.get("name"))
        return row

    # 1. What this scan checked. A candidate is one route, and a fixture's
    #    routes are counted together - which is the whole point of the file.
    for item in candidates or []:
        if not isinstance(item, dict) or not _is_route(item):
            continue
        identity = fixture_identity(item)
        if not identity:
            continue
        row = row_for(identity, item)
        row["candidate_stream_count"] += 1
        verdict = _route_verdict(item)
        if verdict == "verified":
            row["verified_stream_count"] += 1
        elif verdict == "failed":
            row["failed_stream_count"] += 1
            if classifier is not None:
                code = classifier(item)
                if code:
                    if code not in row["failure_codes"]:
                        # One code per kind, however many routes hit it: a
                        # fixture with forty 403s has one problem, not forty.
                        # The count of failed routes is a separate number and
                        # stays a count of routes.
                        row["failure_codes"].append(code)
                    failure_routes[code] = failure_routes.get(code, 0) + 1
        else:
            row["unchecked_stream_count"] += 1
        for source_id in _source_ids(item):
            if source_id not in row["source_ids"]:
                row["source_ids"].append(source_id)

    # 2. What was published. The card is the authority on whether a viewer can
    #    play this fixture right now - a route carried from an earlier scan is
    #    playable and was never a candidate here.
    for tab, cards in (("today", today), ("upcoming", upcoming)):
        for card in cards:
            identity = fixture_identity(card)
            if not identity:
                continue
            row = row_for(identity, card)
            row["recognised_fixture"] = True
            row["published"] = True
            row["published_tab"] = tab
            playable = _card_is_playable(card)
            row["published_without_stream"] = not playable
            row["fallback_available"] = _card_fallbacks(card) > 0
            lead = _text(card.get("source_id"))
            if lead:
                row["source_id"] = lead
            for source_id in _source_ids(card):
                if source_id not in row["source_ids"]:
                    row["source_ids"].append(source_id)

    for row in rows.values():
        if not row["source_id"] and row["source_ids"]:
            row["source_id"] = row["source_ids"][0]

    ordered = sorted(
        rows.values(),
        key=lambda row: (-row["failed_stream_count"], row["fixture_name"]),
    )

    known = [row for row in ordered if row["recognised_fixture"]]
    other = [row for row in ordered if not row["recognised_fixture"]]
    published_rows = [row for row in known if row["published"]]
    failed_routes = sum(row["failed_stream_count"] for row in known)
    fixtures_touched = [row for row in known if row["failed_stream_count"]]
    lost = [row for row in fixtures_touched
            if not row["verified_stream_count"] and not row["published"]]

    return {
        "generated_at": reference.isoformat(),
        "note": (
            "One row per fixture. A route is not a match: a fixture carried by "
            "three feeds with backups is a dozen routes, and most of them can "
            "fail while the match plays. Route failures are counted here "
            "against the fixtures they belong to."
        ),
        "totals": {
            "fixtures": len(known),
            "published_fixtures": len(published_rows),
            "fixtures_with_verified_stream": sum(
                1 for row in known if row["verified_stream_count"]),
            "fixtures_with_zero_verified": sum(
                1 for row in known if not row["verified_stream_count"]),
            "published_without_stream": sum(
                1 for row in published_rows if row["published_without_stream"]),
            "fallback_available": sum(
                1 for row in known if row["fallback_available"]),
            "targeted_fixtures": sum(1 for row in known if row["targeted"]),
            "targeted_attempts": sum(
                row["target_attempt_count"] for row in known),
            "fixtures_with_a_first_link_time": sum(
                1 for row in known if row["first_link_at"]),
            "candidate_routes": sum(row["candidate_stream_count"] for row in known),
            "verified_routes": sum(row["verified_stream_count"] for row in known),
            "failed_routes": failed_routes,
            # The sentence the aggregate could never say.
            "fixtures_touched_by_a_route_failure": len(fixtures_touched),
            "fixtures_left_with_nothing": len(lost),
            "distinct_failure_codes": len(
                {code for row in known for code in row["failure_codes"]}),
            # Candidate groups that never became a cricket or football
            # fixture - a tennis broadcast, a wrestling show, a channel
            # header. Their routes failed too, and none of it is a lost
            # match.
            "unrecognised_candidate_groups": len(other),
            "unrecognised_failed_routes": sum(
                row["failed_stream_count"] for row in other),
        },
        # Routes per code, and fixtures per code. They are different
        # questions: 496 routes answering 403 may be four fixtures or four
        # hundred, and only the second number says whether anything is lost.
        "failure_codes": dict(sorted(
            failure_routes.items(), key=lambda pair: -pair[1])),
        "failure_code_fixtures": dict(sorted(
            ((code, sum(1 for row in known if code in row["failure_codes"]))
             for code in {c for row in known for c in row["failure_codes"]}),
            key=lambda pair: -pair[1])),
        "fixtures": ordered,
    }


def refresh_ladder_fields(
    path: Path | str = REPORT_FILE,
    *,
    ledger: Optional[Dict[str, Any]] = None,
    ledger_path: Any = None,
) -> Dict[str, Any]:
    """Re-read the ladder columns of a report that is already on disk.

    A targeted scan saves its ledger after the outputs are published, so the
    report was built from the ledger as it stood before this run - on a real
    run that attempted 8 fixtures and resolved 5, the report said 0. Rather
    than reorder a scan for the sake of a report, the three ladder columns and
    their totals are refreshed once the ledger is final.

    Only those columns move. Route counts, published state and failure codes
    are this scan's own findings and are left exactly as they were written.
    """
    target = Path(path)
    try:
        report = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"refreshed": 0}
    rows = report.get("fixtures")
    if not isinstance(rows, dict) and not isinstance(rows, list):
        return {"refreshed": 0}

    ladder = _ledger_index(
        ledger if ledger is not None else _load_ledger(ledger_path))
    refreshed = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        identity = _text(row.get("fixture_key")) or fixture_identity(row)
        evidence = _ledger_evidence(ladder.get(identity) or {})
        if any(row.get(field) != value for field, value in evidence.items()):
            refreshed += 1
        row.update(evidence)

    known = [row for row in rows
             if isinstance(row, dict) and row.get("recognised_fixture")]
    totals = report.get("totals")
    if isinstance(totals, dict):
        totals["targeted_fixtures"] = sum(1 for row in known if row.get("targeted"))
        totals["targeted_attempts"] = sum(
            int(row.get("target_attempt_count") or 0) for row in known)
        totals["fixtures_with_a_first_link_time"] = sum(
            1 for row in known if row.get("first_link_at"))
    _atomic_write(target, report)
    return {"refreshed": refreshed, "rows": len(rows)}


def write_fixture_stream_health(
    report: Dict[str, Any], path: Path | str = REPORT_FILE
) -> None:
    _atomic_write(Path(path), report)
