"""One place the Today/Upcoming lifecycle timings are read from.

WHY THIS EXISTS.

The same threshold lived in three files, and keeping them equal was somebody's
job to remember:

    config/settings.json        events.targeted_window_minutes = 30
    scanner/event_lifecycle.py  DEFAULT_TODAY_ROUTING_MINUTES  = 30
    scanner/events.py:458       TODAY_NO_LINK_GRACE_MINUTES    = 30

All three happened to be 30, so nothing looked wrong. Change one to 25 and the
scanner starts hunting for a link at a different moment than the tab moves the
card, which is a fault nobody would see until a viewer did. The same repository
already has one instance of exactly that shape - `on.schedule` was changed and
the workflow's own mode selector was not, and the targeted scan stopped running
for six days without a single failed run to show for it.

WHAT IS AUTHORITATIVE.

`config/settings.json` -> `event_lifecycle`. The constants below are fallbacks
and nothing else: each one is the value the code uses TODAY, so deleting the
config block reproduces current behaviour exactly rather than quietly adopting
a new one. That is the whole discipline - a fallback is a way to survive a
missing file, not a second opinion about what the timing should be.

READ THE FIELDS, NOT THE FILE. Nothing outside this module should reach into
`settings["event_lifecycle"]` itself, or the drift starts again one caller at a
time.

WHO READS THESE, AND WHEN.

Introduced ahead of its consumers on purpose, so the values can be agreed and
verified before any behaviour hangs off them:

    move_to_today_minutes        Upcoming -> Today routing threshold
    target_retry_interval_min    the width of one attempt slot
    target_retry_until_min       how far past kickoff the hunt continues
    post_match_grace_minutes     how long a finished card is held before removal
    no_link_today_grace_minutes  how long a Today card may sit without a link
    confirmations_required       consecutive scans before a card is retired
    estimate_grace_minutes       slack on an estimated end time
    unscheduled_carry_hours      how long a card with no kickoff is carried
    unscheduled_carry_confirmations
    source_outage_hold_minutes   how long an Upcoming card is held while the
                                 feed that scheduled it produces nothing
    source_outage_memory_hours   how recently that feed must have produced
                                 records for its silence to read as an outage
    source_outage_record_max_age_minutes
                                 how fresh a health record must be to count as
                                 this scan's evidence

Four of these already had consumers and already had these values; they are
listed here so the block is the whole picture rather than half of it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

#: The config section that owns every value below.
SECTION = "event_lifecycle"

DEFAULT_SETTINGS_PATH = "config/settings.json"

#: A field that used to live somewhere else, and where.
#:
#: `events.targeted_window_minutes` was how long before kickoff the targeted
#: trigger hunted for a link, and its own note in settings.json said it should
#: be kept equal to the tab routing threshold - by hand, in two files. It is
#: the same decision as `move_to_today_minutes`, so it is now that key and
#: only that key. This mapping is read ONLY when the new key is absent, which
#: keeps an older settings.json working through one upgrade without leaving
#: two live answers to one question.
LEGACY_KEYS: Dict[str, Tuple[str, str]] = {
    "move_to_today_minutes": ("events", "targeted_window_minutes"),
}

#: field -> (fallback, minimum, maximum)
#:
#: Every fallback is what the code does TODAY, so a missing or unreadable config
#: changes nothing. Where that differs from the value the config now carries, the
#: difference is deliberate and belongs to a later step - see the notes below.
FIELDS: Dict[str, Tuple[int, int, int]] = {
    # scanner/event_lifecycle.py DEFAULT_TODAY_ROUTING_MINUTES is 30 today; the
    # config asks for 25. PROMPT 11 makes the routing read this.
    "move_to_today_minutes": (30, 1, 24 * 60),

    # scanner/targeted_scan.py BUCKET_MINUTES. Same value in both, so this one
    # is already in agreement.
    "target_retry_interval_min": (5, 1, 60),

    # scanner/targeted_scan.py DEFAULT_RETRY_AFTER_KICKOFF_MINUTES. Also already
    # in agreement, and deliberately equal to events.upcoming_past_grace_minutes,
    # which is how long a kicked-off fixture stays on the list being read.
    "target_retry_until_min": (10, 0, 12 * 60),

    # No constant to mirror: today a strong end signal retires a card at once,
    # Read by `decide` through `protect_live_events` since PROMPT 21. The
    # config asks for 20 minutes; the fallback stays 0, which is the
    # immediate retirement this system did before the window existed - so a
    # missing config reproduces the old behaviour instead of inventing a
    # window nobody asked for.
    "post_match_grace_minutes": (0, 0, 12 * 60),

    # scanner/events.py TODAY_NO_LINK_GRACE_MINUTES is 30 today; the config asks
    # for 25. PROMPT 12 makes it read this.
    "no_link_today_grace_minutes": (30, 0, 24 * 60),

    # These four are already read, already at these values, and are included so
    # one block describes the whole lifecycle rather than half of it.
    "confirmations_required": (3, 1, 100),
    "estimate_grace_minutes": (90, 0, 24 * 60),
    "unscheduled_carry_hours": (3, 1, 240),
    "unscheduled_carry_confirmations": (36, 1, 100000),

    # The Upcoming half of unscheduled_carry_hours, read by
    # scanner/source_outage.py. A feed that answers and produces nothing has
    # not said its fixtures are off, so the cards it scheduled are held rather
    # than deleted - for this long, and no longer.
    "source_outage_hold_minutes": (180, 0, 24 * 60),
    # How recently that feed must have produced records for its silence to
    # read as an outage at all. A source that has always been empty protects
    # nothing.
    "source_outage_memory_hours": (6, 0, 240),
    # How fresh a health record must be to count as this scan's evidence.
    "source_outage_record_max_age_minutes": (45, 1, 24 * 60),
}


def _clamp(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    """`_safe_int` from scanner/events.py, kept identical on purpose.

    A value that is missing, null, a string, a list or nonsense becomes the
    fallback; a value out of range is pulled into it. Config is trusted to be
    edited by hand, so it is never trusted to be well formed.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


def defaults() -> Dict[str, int]:
    """What the code does when there is no config at all."""
    return {name: spec[0] for name, spec in FIELDS.items()}


def lifecycle_settings(
    settings: Optional[Dict[str, Any]] = None,
    *,
    settings_path: Path | str = DEFAULT_SETTINGS_PATH,
) -> Dict[str, int]:
    """Every lifecycle timing, resolved and bounded.

    Pass an already-loaded settings dict when there is one - most callers in
    this scanner already hold it - and this reads no file at all. Otherwise it
    loads `settings_path`, and an absent or unreadable file is not an error:
    it is the fallbacks.
    """
    if settings is None:
        try:
            loaded = json.loads(Path(settings_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        settings = loaded if isinstance(loaded, dict) else {}

    section = settings.get(SECTION)
    if not isinstance(section, dict):
        section = {}

    resolved: Dict[str, int] = {}
    for name, (fallback, minimum, maximum) in FIELDS.items():
        supplied = section.get(name)
        if supplied is None and name in LEGACY_KEYS:
            legacy_section, legacy_key = LEGACY_KEYS[name]
            older = settings.get(legacy_section)
            if isinstance(older, dict):
                supplied = older.get(legacy_key)
        resolved[name] = _clamp(supplied, fallback, minimum, maximum)
    return resolved


def targeted_timings(
    settings: Optional[Dict[str, Any]] = None,
    *,
    settings_path: Path | str = DEFAULT_SETTINGS_PATH,
) -> Dict[str, int]:
    """The three numbers the targeted planner runs on, named as it names them.

    A convenience, and a deliberate one: it is the difference between the
    planner reading a config section and the planner reading three unrelated
    keys it has to remember the names of.

        window_minutes          how long BEFORE kickoff the hunt runs
        after_kickoff_minutes   how long AFTER it continues
        retry_interval_minutes  the width of one attempt slot
    """
    values = lifecycle_settings(settings, settings_path=settings_path)
    return {
        "window_minutes": values["move_to_today_minutes"],
        "after_kickoff_minutes": values["target_retry_until_min"],
        "retry_interval_minutes": values["target_retry_interval_min"],
    }


def lifecycle_value(
    name: str,
    settings: Optional[Dict[str, Any]] = None,
    *,
    settings_path: Path | str = DEFAULT_SETTINGS_PATH,
) -> int:
    """One field, for a caller that wants exactly one."""
    if name not in FIELDS:
        raise KeyError(
            "%r is not a lifecycle timing. Known: %s"
            % (name, ", ".join(sorted(FIELDS)))
        )
    return lifecycle_settings(settings, settings_path=settings_path)[name]
