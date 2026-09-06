"""Requirement 3 - Today Match source coverage report.

One row per configured source, and the row says where that source's records
were lost:

    raw_items -> parsed_events -> sport_allowed_events -> deduped_events
              -> published_today + published_upcoming

The point is diagnostic honesty. When a source contributes nothing, the report
says whether it was never reached, fetched nothing, parsed into no event,
carried no cricket or football, folded into another source's card, or reached
verification and failed - and names the reason. Guessing from a count of zero
is what made the earlier "source has matches but the scanner found none"
reports impossible to act on.

Two facts the counters here cannot see are read from the artefacts that do own
them, rather than being counted a second time:

    state/source-health.json    fetch_status, http_status, raw_items
    the candidates themselves   verified_streams, failed_streams
"""
from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPORT_FILE = Path("reports/today-source-coverage.json")

#: The pipeline whose configuration decides the rows. The report is named for
#: Today Match and its config file is the list of sources a scan is supposed to
#: read, which is the only list that can say whether one is missing.
COVERAGE_PIPELINE = "today_match"

HEALTH_FILE = Path("state") / "source-health.json"

#: state/source-health.json says `failed`; the report says `error`. Same fact,
#: one spelling, because a reader comparing the two files should not have to
#: learn both.
FETCH_STATUS_NAMES = {
    "success": "success",
    "success_empty": "success_empty",
    "empty": "success_empty",
    "failed": "error",
    "error": "error",
}

#: A candidate whose route was checked and answered. `verified_global` and
#: `verified_proxy` are the two ways a check can pass in this scanner.
FAILED_VERIFICATION_STATUSES = frozenset({
    "failed", "unreachable_from_browser", "error", "timeout",
})

#: FINAL_3, অংশ ৩. A feed's freshness is not what its payload claims about
#: itself - bingstream's own `last_update_time` said fifteen days old while it
#: was carrying 85 fixtures for today and tomorrow, and believing it would have
#: deleted 68.5% of a correct Upcoming tab. Freshness is measured against the
#: clock instead: a feed that has really stopped moving watches its own
#: fixtures fall into the past one by one, whatever it writes in its header.
CONTENT_STATES = ("FRESH", "STALE", "EMPTY", "UNREACHABLE")

#: Where a fixture's kickoff is written, in the order the pipeline prefers.
START_FIELDS = ("start_time", "start_at", "source_start_time", "event_start")

DROP_REASONS = {
    "verification_failed": "reached verification and no link passed",
    "not_publishable": "verified but the publish gate rejected it",
    "no_event_identity": "no reliable event or channel identity",
    "duplicate_stream": "exact duplicate of a stream already kept",
    "merged_into_other_source": "folded into a card another source leads",
    "routed_elsewhere": "published under a different pipeline",
}


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


def load_configured_sources(
    config_dir: str | Path = "config",
    pipeline: str = COVERAGE_PIPELINE,
) -> List[Dict[str, Any]]:
    """The enabled sources of config/sources/today-match.json, in file order.

    The list used to be derived from `raw_event_candidates` - that is, from the
    sources that had already contributed something. A source that returned
    nothing could therefore not appear in the report whose job is to say that
    it returned nothing, and the builder's own "nothing fetched" branch was
    unreachable code. Ten of the twenty-one configured sources were absent from
    the report on the day this was measured, including one that had fetched 283
    items and put its name on 48 published cards.

    Reading the same file the loader reads is what makes the count verifiable:
    as many rows as the registry has enabled sources, whatever that number
    becomes.
    """
    try:
        from scanner.source_loader import load_sources_config
    except ImportError:  # flat layout
        try:
            from source_loader import load_sources_config  # type: ignore
        except ImportError:
            return []
    try:
        configured = load_sources_config(config_dir).get(pipeline)
    except Exception:  # noqa: BLE001 - a report must never break a scan
        return []
    if not isinstance(configured, list):
        return []

    enabled: List[Dict[str, Any]] = []
    seen = set()
    for entry in configured:
        if not isinstance(entry, dict):
            continue
        # An absent `enabled` means enabled: that is how the loader reads it,
        # and a row list that disagreed with the fetch list would be the same
        # class of bug over again.
        if entry.get("enabled") is False:
            continue
        source_id = str(entry.get("id") or entry.get("source_id") or "").strip()
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        enabled.append({"id": source_id, "name": str(entry.get("name") or "")})
    return enabled


def load_source_health(path: Path | str = HEALTH_FILE) -> Dict[str, Dict[str, Any]]:
    """Per-source fetch outcome, as the loader recorded it.

    `fetch_status`, `http_status` and `raw_items` are the loader's own numbers.
    Recomputing them from surviving candidates is exactly the mistake this
    report was written to stop: a source that fetched 743 records and lost
    every one of them to an exact-duplicate check must show 743, not 0.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(sources, dict):
        return {}
    return {
        str(source_id): entry
        for source_id, entry in sources.items()
        if isinstance(entry, dict)
    }


def _source_ids(item: Dict[str, Any]) -> List[str]:
    """Every source that contributed to a published card, not just the winner -
    otherwise a source whose stream became a backup looks like it published
    nothing."""
    ids: List[str] = []
    primary = str(item.get("source_id") or "").strip()
    if primary:
        ids.append(primary)
    for field in ("source_ids", "alias_source_ids"):
        value = item.get(field)
        if isinstance(value, list):
            ids.extend(str(entry).strip() for entry in value if str(entry).strip())
    provenance = item.get("source_provenance")
    if isinstance(provenance, list):
        for entry in provenance:
            if isinstance(entry, dict):
                candidate = str(entry.get("source_id") or "").strip()
                if candidate:
                    ids.append(candidate)
    for backup in item.get("backups") or []:
        if isinstance(backup, dict):
            candidate = str(backup.get("source_id") or "").strip()
            if candidate:
                ids.append(candidate)
    return ids


def _count_by_source(items: Iterable[Dict[str, Any]], *, credit_all: bool = False) -> Counter:
    """Count items per source.

    `credit_all` follows a card's whole provenance, so a source whose stream
    became someone else's backup is not reported as having published nothing.
    """
    counter: Counter = Counter()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if credit_all:
            for source_id in set(_source_ids(item)):
                counter[source_id] += 1
        else:
            source_id = str(item.get("source_id") or "").strip()
            if source_id:
                counter[source_id] += 1
    return counter


def _parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _fixture_identity(item: Dict[str, Any]) -> str:
    return (str(item.get("fixture_id") or item.get("id") or item.get("name") or "")
            .strip().casefold())


def _fixture_horizon(
    per_source: Dict[str, Dict[str, datetime]],
    now: datetime,
) -> Dict[str, Dict[str, Any]]:
    """The furthest kickoff a source is carrying, and how many are still ahead."""
    horizon: Dict[str, Dict[str, Any]] = {}
    for source_id, fixtures in per_source.items():
        starts = list(fixtures.values())
        newest = max(starts) if starts else None
        horizon[source_id] = {
            "newest_fixture_start": newest.isoformat() if newest else "",
            "future_fixture_count": sum(1 for start in starts if start > now),
        }
    return horizon


def _collect_fixture_starts(
    *sources: tuple[Iterable[Dict[str, Any]], bool],
) -> Dict[str, Dict[str, datetime]]:
    """Kickoffs per source, counted once per fixture.

    A source is credited for a fixture it carried even when its own record lost
    an exact-duplicate check to another source's: srhady-axsports-live reaches
    the published tabs only through the cards its identity survives on, and
    reading it as carrying no fixtures at all would report a source with 95
    future kickoffs as stale.
    """
    per_source: Dict[str, Dict[str, datetime]] = {}
    for items, credit_all in sources:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            start = None
            for field in START_FIELDS:
                start = _parse_time(item.get(field))
                if start is not None:
                    break
            if start is None:
                continue
            identity = _fixture_identity(item)
            if not identity:
                continue
            ids = (set(_source_ids(item)) if credit_all
                   else {str(item.get("source_id") or "").strip()})
            for source_id in ids:
                if source_id:
                    per_source.setdefault(source_id, {})[identity] = start
    return per_source


def _content_state(fetch_status: str, raw_items: int, future_fixtures: int) -> str:
    """FINAL_3, অংশ ৩ - in its own order.

    UNREACHABLE and EMPTY are asked first because they are about the response,
    not about its contents: a source that could not be read has no horizon to
    measure, and one that answered with nothing is not stale, it is empty.
    """
    if fetch_status == "error":
        return "UNREACHABLE"
    if raw_items <= 0:
        return "EMPTY"
    if future_fixtures > 0:
        return "FRESH"
    return "STALE"


def _stream_verdicts(items: Iterable[Dict[str, Any]]) -> tuple[Counter, Counter]:
    """Routes this scan checked, split into passed and failed, per source."""
    verified: Counter = Counter()
    failed: Counter = Counter()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or "").strip()
        if not source_id:
            continue
        status = str(item.get("verification_status") or "").strip().lower()
        if item.get("verified") is True or status.startswith("verified"):
            verified[source_id] += 1
        elif status in FAILED_VERIFICATION_STATUSES:
            failed[source_id] += 1
    return verified, failed


def build_source_coverage(
    configured_sources: Iterable[Dict[str, Any]],
    raw_candidates: Iterable[Dict[str, Any]],
    parsed_candidates: Iterable[Dict[str, Any]],
    matched_candidates: Iterable[Dict[str, Any]],
    published_items: Optional[Iterable[Dict[str, Any]]] = None,
    fetch_errors: Dict[str, str] | None = None,
    *,
    source_health: Optional[Dict[str, Dict[str, Any]]] = None,
    deduped_candidates: Optional[Iterable[Dict[str, Any]]] = None,
    published_today_items: Optional[Iterable[Dict[str, Any]]] = None,
    published_upcoming_items: Optional[Iterable[Dict[str, Any]]] = None,
    metadata_contributions: Optional[Dict[str, Dict[str, Any]]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    errors = dict(fetch_errors or {})
    health = dict(source_health or {})
    metadata = dict(metadata_contributions or {})

    raw_candidates = list(raw_candidates or [])
    parsed = _count_by_source(
        parsed_candidates if parsed_candidates is not None else raw_candidates)
    sport_allowed = _count_by_source(matched_candidates)
    deduped = (_count_by_source(deduped_candidates, credit_all=True)
               if deduped_candidates is not None else None)

    today_list = list(published_today_items or [])
    upcoming_list = list(published_upcoming_items or [])
    if published_items is None:
        published_items = today_list + upcoming_list
    published = _count_by_source(published_items, credit_all=True)
    published_today = _count_by_source(today_list, credit_all=True)
    published_upcoming = _count_by_source(upcoming_list, credit_all=True)

    reference = now or datetime.now(timezone.utc)
    # Every fixture a source carried, however it reached the tabs.
    horizon = _fixture_horizon(
        _collect_fixture_starts(
            (raw_candidates, False),
            (deduped_candidates or [], True),
            (today_list, True),
            (upcoming_list, True),
        ),
        reference,
    )

    verified_streams, failed_streams = _stream_verdicts(raw_candidates)
    candidates_by_source = _count_by_source(raw_candidates)

    def row(source_id: str, configured: bool) -> Dict[str, Any]:
        entry = health.get(source_id) or {}
        parsed_events = parsed.get(source_id, 0)
        allowed = sport_allowed.get(source_id, 0)
        deduped_events = (deduped.get(source_id, 0) if deduped is not None else allowed)
        row_today = published_today.get(source_id, 0)
        row_upcoming = published_upcoming.get(source_id, 0)
        row_published = published.get(source_id, 0)

        if source_id in errors:
            fetch_status = "error"
        else:
            fetch_status = FETCH_STATUS_NAMES.get(
                str(entry.get("status") or "").strip().lower(), "")
        raw_items = entry.get("raw_items")
        if not isinstance(raw_items, int):
            # No health record: the candidates that reached this scan are the
            # only evidence of what the source returned.
            raw_items = candidates_by_source.get(source_id, parsed_events)
        if not fetch_status:
            fetch_status = "success" if raw_items else "success_empty"
        http_status = entry.get("http_status")
        http_status = http_status if isinstance(http_status, int) else 0

        dropped_count = max(0, deduped_events - row_published)

        fixtures = horizon.get(source_id) or {}
        newest_fixture_start = str(fixtures.get("newest_fixture_start") or "")
        future_fixture_count = int(fixtures.get("future_fixture_count") or 0)
        content_state = _content_state(fetch_status, raw_items, future_fixture_count)

        contribution = metadata.get(source_id)

        reasons: List[str] = []
        if contribution is not None and not row_published:
            # A metadata layer publishes no card by design, so "0
            # published" is not a failure to explain - it is the
            # arrangement. What it did do belongs in the same row,
            # because a reader who sees only the zero concludes the
            # fetch was wasted. This one matched 50 published fixtures
            # and put artwork on 50 of them in the scan it was measured.
            reasons.append(
                "metadata layer: card authority denied by design; "
                "matched %d published fixture(s), supplied artwork to %d"
                % (int(contribution.get("matched") or 0),
                   int(contribution.get("artwork") or 0)))
        if fetch_status == "error":
            detail = errors.get(source_id) or str(entry.get("error") or "").strip()
            reasons.append(f"fetch failed: {detail}" if detail else "fetch failed")
        elif raw_items == 0:
            reasons.append("nothing fetched from this source")
        elif parsed_events == 0:
            if row_published:
                # srhady-axsports-live: 743 records fetched, every one an exact
                # duplicate of another source's, and still credited on the 96
                # cards its identity survives on.
                reasons.append(
                    "every record was an exact duplicate of another source's; "
                    f"still credited on {row_published} published card(s)")
            else:
                reasons.append("fetched but no record reached the event pipeline")
        if content_state == "STALE":
            reasons.append(
                "every fixture this source carries has already kicked off")
        if parsed_events and allowed == 0:
            reasons.append(
                "parsed, but no event survived enrichment - "
                + DROP_REASONS["no_event_identity"] + ", or not cricket/football")
        if allowed and deduped_events == 0:
            # Merged into someone else's card, or killed at verification. The
            # two look identical in the counts, so the routes decide it: a
            # source that published nothing and had every route fail did not
            # fold into anything.
            if not row_published and failed_streams.get(source_id, 0):
                reasons.append(DROP_REASONS["verification_failed"])
            else:
                reasons.append(DROP_REASONS["merged_into_other_source"])
        if deduped_events and row_published == 0:
            reasons.append(DROP_REASONS["verification_failed"])
        elif dropped_count:
            reasons.append(DROP_REASONS["merged_into_other_source"])
        if allowed > row_published and not reasons:
            # FINAL_3's invariant is that no recognised cricket or football
            # candidate goes unaccounted for. A source that carried 220 of them
            # and leads 70 cards has not lost 150 fixtures: a card is one
            # fixture and a candidate is one route, and the merge is where many
            # become one. Saying nothing left that difference looking like loss.
            reasons.append(
                "%d cricket/football candidate route(s) became %d published "
                "card(s); the rest were folded into them or into cards another "
                "source leads" % (allowed, row_published))

        return {
            "source_id": source_id,
            "configured": configured,
            "fetch_status": fetch_status,
            "http_status": http_status,
            "raw_items": raw_items,
            "parsed_events": parsed_events,
            "sport_allowed_events": allowed,
            "deduped_events": deduped_events,
            "published_unique_fixtures": row_published,
            "published_today": row_today,
            "published_upcoming": row_upcoming,
            "dropped_count": dropped_count,
            "drop_reasons": reasons,
            "newest_fixture_start": newest_fixture_start,
            "future_fixture_count": future_fixture_count,
            "content_state": content_state,
            "verified_streams": verified_streams.get(source_id, 0),
            "failed_streams": failed_streams.get(source_id, 0),
            **({"metadata_contribution": contribution}
               if contribution is not None else {}),
        }

    configured_ids: List[str] = []
    known = set()
    for entry in configured_sources:
        source_id = str(entry.get("id") or entry.get("source_id") or "").strip()
        if source_id and source_id not in known:
            known.add(source_id)
            configured_ids.append(source_id)

    observed = sorted({
        source_id for source_id in
        set(parsed) | set(sport_allowed) | set(published) | set(candidates_by_source)
        | set(deduped or {}) | set(errors)
        if source_id
    })

    # One row per configured source, whatever it did or did not produce. With
    # no readable configuration there is nothing to be faithful to, so the
    # observed sources are reported rather than an empty file - and the rows
    # say `configured: false`, so the difference is never invisible.
    rows = [row(source_id, configured=bool(configured_ids))
            for source_id in (configured_ids or observed)]

    # Everything that contributed without being in the configuration:
    # `streamed-fixtures` fetches hundreds of records per scan and is in no
    # config file. It must not sit among the configured rows - that is what
    # made twelve rows look like a plausible twenty-one - but dropping it would
    # lose real accounting, so it is reported beside them, not inside them.
    unconfigured = ([row(source_id, configured=False) for source_id in observed
                     if source_id not in known] if configured_ids else [])

    def totals(entries: List[Dict[str, Any]]) -> Dict[str, int]:
        return {
            key: sum(entry[key] for entry in entries)
            for key in (
                "raw_items", "parsed_events", "sport_allowed_events",
                "deduped_events", "published_unique_fixtures", "published_today",
                "published_upcoming", "dropped_count", "verified_streams",
                "failed_streams",
            )
        }

    states = Counter(row["content_state"] for row in rows)

    return {
        "generated_at": reference.isoformat(),
        "configured_source_count": len(configured_ids),
        "content_states": {state: states.get(state, 0) for state in CONTENT_STATES},
        "source_count": len(rows),
        "unconfigured_source_count": len(unconfigured),
        "totals": totals(rows),
        "unconfigured_totals": totals(unconfigured),
        "sources": rows,
        "unconfigured_sources": unconfigured,
    }


def _fixture_key(item: Dict[str, Any]) -> str:
    """One fixture, however many cards or sources carry it."""
    return (str(item.get("fixture_id") or item.get("id") or "").strip().casefold())


def check_invariants(
    report: Dict[str, Any],
    *,
    configured_sources: Optional[Iterable[Dict[str, Any]]] = None,
    today_items: Optional[Iterable[Dict[str, Any]]] = None,
    upcoming_items: Optional[Iterable[Dict[str, Any]]] = None,
    stream_health: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """FINAL_3, অংশ ৬ - the checks a coverage report has to survive.

    A report that is wrong about itself is worse than no report: every one of
    these was written because a number in it had already been believed once.
    A failure here is stated, never swallowed - the caller writes the list into
    the report and raises it into the scan summary, so an invariant that starts
    failing is visible in the same place the numbers are read.

    This checks the report. It changes nothing: no source is accepted or
    refused, no card is routed, nothing is filtered.
    """
    rows = [row for row in (report.get("sources") or []) if isinstance(row, dict)]
    extra = [row for row in (report.get("unconfigured_sources") or [])
             if isinstance(row, dict)]
    today = [item for item in (today_items or []) if isinstance(item, dict)]
    upcoming = [item for item in (upcoming_items or []) if isinstance(item, dict)]

    configured_ids: List[str] = []
    if configured_sources is not None:
        for entry in configured_sources:
            source_id = str(entry.get("id") or entry.get("source_id") or "").strip()
            if source_id:
                configured_ids.append(source_id)
    row_ids = [row.get("source_id") for row in rows]

    checks: List[Dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    # 1. The count the whole report exists to make verifiable.
    configured_count = int(report.get("configured_source_count") or 0)
    check(
        "configured_source_count == coverage_row_count",
        configured_count == len(rows),
        "configured %d, rows %d" % (configured_count, len(rows)),
    )

    # 2. One row each - not two, and none missing.
    if configured_ids:
        missing = [s for s in configured_ids if s not in set(row_ids)]
        duplicated = sorted({s for s in row_ids if row_ids.count(s) > 1})
        check(
            "every enabled configured source has exactly one row",
            not missing and not duplicated,
            "missing %s, duplicated %s" % (missing[:5], duplicated[:5]),
        )
    else:
        duplicated = sorted({s for s in row_ids if row_ids.count(s) > 1})
        check("every enabled configured source has exactly one row",
              not duplicated, "duplicated %s" % duplicated[:5])

    # 3. A source that put its name on a published card must be in the report -
    #    in the configured rows if it is configured, beside them if it is not.
    known = set(row_ids) | {row.get("source_id") for row in extra}
    published_sources = set()
    for item in today + upcoming:
        published_sources.update(_source_ids(item))
    unreported = sorted(s for s in published_sources if s and s not in known)
    check(
        "every published source_id appears in coverage",
        not unreported,
        "not reported: %s" % unreported[:5],
    )

    # 4. The whole point of reading the config: a source that returned nothing
    #    is in the report saying so.
    silent = [row for row in rows if not int(row.get("raw_items") or 0)]
    check(
        "a fetched configured source has a row even at raw_items 0",
        all(row.get("drop_reasons") for row in silent),
        "%d row(s) at zero, %d without a reason"
        % (len(silent), sum(1 for row in silent if not row.get("drop_reasons"))),
    )

    # 5. Nothing disappears quietly. A source that carried cricket or football
    #    past the sport filter and published less than it carried says why.
    unexplained = [
        row.get("source_id") for row in rows + extra
        if (int(row.get("sport_allowed_events") or 0)
            > int(row.get("published_unique_fixtures") or 0))
        and not row.get("drop_reasons")
    ]
    check(
        "a candidate that is not published has an explicit drop reason",
        not unexplained,
        "unexplained: %s" % unexplained[:5],
    )

    # 6-7. Tab hygiene. The same fixture on both tabs is the oldest fault in
    #      this system, and a fixture routed to Today leaving a copy behind on
    #      Upcoming is the same fault wearing a different hat.
    today_keys = {_fixture_key(item) for item in today} - {""}
    upcoming_keys = {_fixture_key(item) for item in upcoming} - {""}
    both = sorted(today_keys & upcoming_keys)
    check("no fixture is on Today and Upcoming at once", not both,
          "both tabs: %s" % both[:5])
    today_ids = {str(item.get("id") or "") for item in today} - {""}
    upcoming_ids = {str(item.get("id") or "") for item in upcoming} - {""}
    check("no fixture routed to Today survives on Upcoming",
          not (today_ids & upcoming_ids),
          "shared ids: %s" % sorted(today_ids & upcoming_ids)[:5])

    # 8. `streamed-fixtures` was in the report and in no config file.
    if configured_ids:
        intruders = [s for s in row_ids if s not in set(configured_ids)]
        check("no non-configured source sits among the configured rows",
              not intruders, "intruders: %s" % intruders[:5])

    # 9. Per row the two tabs add up, and every published card is credited to
    #    at least one row - otherwise a card exists that the report cannot see.
    mismatched = [
        row.get("source_id") for row in rows + extra
        if (int(row.get("published_today") or 0)
            + int(row.get("published_upcoming") or 0))
        != int(row.get("published_unique_fixtures") or 0)
    ]
    check("published_today + published_upcoming == published_unique_fixtures",
          not mismatched, "mismatched: %s" % mismatched[:5])

    uncredited = [
        str(item.get("name") or item.get("id") or "")
        for item in today + upcoming
        if not (set(_source_ids(item)) & known)
    ]
    check(
        "every published card is credited to a source in the report",
        not uncredited,
        "%d of %d uncredited: %s"
        % (len(uncredited), len(today) + len(upcoming), uncredited[:3]),
    )

    # 10. The stream counters, against themselves and against the warning.
    if isinstance(stream_health, dict):
        with_stream = int(stream_health.get("fixtures_with_stream") or 0)
        fresh = int(stream_health.get("fixtures_with_fresh_stream") or 0)
        carried = int(stream_health.get("fixtures_with_carried_stream") or 0)
        published_fixtures = int(stream_health.get("published_fixtures") or 0)
        tabs = (int(stream_health.get("today_with_stream") or 0)
                + int(stream_health.get("upcoming_with_stream") or 0))
        warnings = list(stream_health.get("warnings") or [])
        degraded = str(stream_health.get("state") or "") == "degraded"
        check(
            "stream counters are internally consistent",
            fresh + carried == with_stream == tabs <= published_fixtures,
            "with_stream %d, fresh %d, carried %d, tabs %d, published %d"
            % (with_stream, fresh, carried, tabs, published_fixtures),
        )
        check(
            "a degraded stream state carries a warning, and vice versa",
            degraded == bool(warnings),
            "state %s, %d warning(s)"
            % (stream_health.get("state"), len(warnings)),
        )
        check(
            "no playable route anywhere is never a silent success",
            not (published_fixtures and not with_stream) or bool(warnings),
            "published %d, with a stream %d, warnings %d"
            % (published_fixtures, with_stream, len(warnings)),
        )

    failed = [entry for entry in checks if not entry["passed"]]
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "passed": len(checks) - len(failed),
        "failed": len(failed),
        "failures": [entry["name"] for entry in failed],
        "checks": checks,
    }


def write_source_coverage(report: Dict[str, Any], path: Path | str = REPORT_FILE) -> None:
    _atomic_write(Path(path), report)


def format_source_coverage(report: Dict[str, Any]) -> str:
    header = (
        f"{'source':34s} {'status':>13s} {'raw':>6s} {'parse':>6s} {'sport':>6s} "
        f"{'dedup':>6s} {'today':>5s} {'upcom':>5s} {'drop':>5s} {'state':>11s}  reason"
    )
    lines = [header]
    for row in report.get("sources", []):
        reasons = row.get("drop_reasons") or []
        lines.append(
            f"{row['source_id'][:34]:34s} {row['fetch_status']:>13s} "
            f"{row['raw_items']:6d} {row['parsed_events']:6d} "
            f"{row['sport_allowed_events']:6d} {row['deduped_events']:6d} "
            f"{row['published_today']:5d} {row['published_upcoming']:5d} "
            f"{row['dropped_count']:5d} {row['content_state']:>11s}  "
            f"{reasons[0] if reasons else ''}"
        )
    totals = report.get("totals", {})
    lines.append(
        f"{'TOTAL':34s} {'':>13s} {totals.get('raw_items', 0):6d} "
        f"{totals.get('parsed_events', 0):6d} {totals.get('sport_allowed_events', 0):6d} "
        f"{totals.get('deduped_events', 0):6d} {totals.get('published_today', 0):5d} "
        f"{totals.get('published_upcoming', 0):5d} {totals.get('dropped_count', 0):5d}"
    )
    return "\n".join(lines)
