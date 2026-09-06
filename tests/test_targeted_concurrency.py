"""PROMPT 44/45 - the five-minute hunt gets a queue of its own.

FINAL_1 P3 and FINAL_2 ধাপ ৯: `upcoming-targeted` shared
`live-signal-events-v4` with the twenty-minute today scan. A GitHub concurrency
group holds one run in progress and one pending and drops everything else, and
a today run takes far longer than five minutes end to end - checkout, tests,
scan, push, rebase retry. Every targeted trigger arriving during one was queued
behind it or thrown away, so the retry ladder lost ticks it could never see.
Measured on 2026-08-30, the real gaps between runs of this workflow were 122,
79, 44, 229 and 194 minutes against a five-minute cron.

Two changes, and only for targeted:

    live-signal-targeted-v1    upcoming-targeted     cancel-in-progress true
    live-signal-catalogue-v4   channels/movies/all   false, unchanged
    live-signal-events-v4      today/upcoming        false, unchanged

The expressions are evaluated here the way GitHub evaluates them, against each
trigger this workflow declares - not grepped. A test that only matches strings
cannot tell a working expression from one that never fires, which is exactly
the fault PROMPT 01 found in the mode selector.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKFLOW = ROOT / ".github" / "workflows" / "scan.yml"

TARGETED_CRON = "1-59/5 * * * *"
TODAY_CRON = "3,23,43 * * * *"
CHANNELS_CRON = "17 0,6,12,18 * * *"
MOVIES_CRON = "37 4 * * *"

TARGETED_GROUP = "live-signal-targeted-v1"
EVENTS_GROUP = "live-signal-events-v4"
CATALOGUE_GROUP = "live-signal-catalogue-v4"


def _load():
    try:
        import yaml
    except ImportError:  # pragma: no cover - yaml ships with the runner
        raise unittest.SkipTest("pyyaml unavailable")
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# A small evaluator for the subset of the GitHub expression language these two
# expressions use: string literals, `==`, `&&`, `||`, parentheses, and the two
# contexts available at workflow level. GitHub's `&&`/`||` return an operand
# rather than a boolean, and an absent context value is the empty string.
# --------------------------------------------------------------------------
_TOKEN = re.compile(r"""\s*(?:(?P<lp>\()|(?P<rp>\))|(?P<and>&&)|(?P<or>\|\|)
                        |(?P<eq>==)|(?P<str>'[^']*')
                        |(?P<name>[A-Za-z_][A-Za-z0-9_.]*))""", re.X)


def _tokens(text):
    pos, out = 0, []
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            if text[pos:].strip():
                raise ValueError("unparsed: %r" % text[pos:])
            break
        pos = match.end()
        kind = match.lastgroup
        out.append((kind, match.group(kind)))
    return out


class _Parser:
    def __init__(self, tokens, context):
        self.tokens, self.index, self.context = tokens, 0, context

    def peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else (None, None)

    def take(self):
        token = self.peek()
        self.index += 1
        return token

    def parse(self):
        value = self.parse_or()
        assert self.index == len(self.tokens), "trailing tokens"
        return value

    def parse_or(self):
        left = self.parse_and()
        while self.peek()[0] == "or":
            self.take()
            right = self.parse_and()
            left = left if _truthy(left) else right
        return left

    def parse_and(self):
        left = self.parse_eq()
        while self.peek()[0] == "and":
            self.take()
            right = self.parse_eq()
            left = right if _truthy(left) else left
        return left

    def parse_eq(self):
        left = self.parse_atom()
        while self.peek()[0] == "eq":
            self.take()
            left = left == self.parse_atom()
        return left

    def parse_atom(self):
        kind, text = self.take()
        if kind == "lp":
            value = self.parse_or()
            assert self.take()[0] == "rp", "unbalanced parentheses"
            return value
        if kind == "str":
            return text[1:-1]
        if kind == "name":
            return self.context.get(text, "")
        raise ValueError("unexpected token %r" % (text,))


def _truthy(value):
    return bool(value) and value != ""


def _render(expression, *, schedule="", mode=""):
    """Substitute every ${{ ... }} the way the runner does."""
    context = {"github.event.schedule": schedule, "inputs.mode": mode}

    def one(match):
        value = _Parser(_tokens(match.group(1)), context).parse()
        if value is True:
            return "true"
        if value is False:
            return "false"
        return str(value)

    return re.sub(r"\$\{\{(.+?)\}\}", one, expression, flags=re.S).strip()


class TheEvaluatorMatchesGitHub(unittest.TestCase):
    """The evaluator is the instrument. If it is wrong, every result below is."""

    def test_and_or_return_operands_not_booleans(self):
        self.assertEqual("yes", _render("${{ 'a' == 'a' && 'yes' || 'no' }}"))
        self.assertEqual("no", _render("${{ 'a' == 'b' && 'yes' || 'no' }}"))

    def test_an_absent_context_value_is_empty_and_falsey(self):
        self.assertEqual("no", _render("${{ inputs.mode == 'today' && 'yes' || 'no' }}"))

    def test_parentheses_group(self):
        self.assertEqual(
            "inner",
            _render("${{ ('x' == 'y' || 'x' == 'x') && 'inner' || 'outer' }}"))


class EveryTriggerLandsInTheRightQueue(unittest.TestCase):
    def setUp(self):
        self.concurrency = _load()["concurrency"]

    def group(self, **kwargs):
        return _render(self.concurrency["group"], **kwargs)

    def cancels(self, **kwargs):
        return _render(str(self.concurrency["cancel-in-progress"]), **kwargs)

    # ---- PROMPT 44: the group
    def test_the_scheduled_targeted_cron_gets_its_own_group(self):
        self.assertEqual(TARGETED_GROUP, self.group(schedule=TARGETED_CRON))

    def test_the_today_cron_is_not_in_the_targeted_group(self):
        group = self.group(schedule=TODAY_CRON)
        self.assertEqual(EVENTS_GROUP, group)
        self.assertNotEqual(TARGETED_GROUP, group)

    def test_channels_and_movies_keep_the_catalogue_group(self):
        self.assertEqual(CATALOGUE_GROUP, self.group(schedule=CHANNELS_CRON))
        self.assertEqual(CATALOGUE_GROUP, self.group(schedule=MOVIES_CRON))
        for mode in ("channels", "movies", "all"):
            self.assertEqual(CATALOGUE_GROUP, self.group(mode=mode), mode)

    def test_manual_targeted_gets_the_targeted_group(self):
        self.assertEqual(TARGETED_GROUP, self.group(mode="upcoming-targeted"))

    def test_manual_today_and_upcoming_keep_the_events_group(self):
        for mode in ("today", "upcoming"):
            self.assertEqual(EVENTS_GROUP, self.group(mode=mode), mode)

    def test_an_unknown_trigger_still_lands_somewhere_valid(self):
        """Fail-safe: never an empty group name, which GitHub rejects."""
        for group in (self.group(), self.group(schedule="0 0 * * *"),
                      self.group(mode="something-else")):
            self.assertTrue(group.startswith("live-signal-"))
            self.assertNotIn("$", group)
            self.assertEqual(EVENTS_GROUP, group)

    # ---- PROMPT 45: cancel-in-progress
    def test_only_the_targeted_run_cancels_its_predecessor(self):
        self.assertEqual("true", self.cancels(schedule=TARGETED_CRON))
        self.assertEqual("true", self.cancels(mode="upcoming-targeted"))

    def test_today_and_upcoming_still_never_cancel(self):
        self.assertEqual("false", self.cancels(schedule=TODAY_CRON))
        for mode in ("today", "upcoming"):
            self.assertEqual("false", self.cancels(mode=mode), mode)

    def test_channels_and_movies_still_never_cancel(self):
        self.assertEqual("false", self.cancels(schedule=CHANNELS_CRON))
        self.assertEqual("false", self.cancels(schedule=MOVIES_CRON))
        for mode in ("channels", "movies", "all"):
            self.assertEqual("false", self.cancels(mode=mode), mode)

    def test_nothing_can_cancel_a_run_in_another_group(self):
        """cancel-in-progress only ever reaches the run's own group, so the
        one mode that cancels is the only one that can be cancelled."""
        cancelling = [
            (schedule, mode)
            for schedule in ("", TARGETED_CRON, TODAY_CRON, CHANNELS_CRON, MOVIES_CRON)
            for mode in ("", "today", "upcoming", "upcoming-targeted",
                         "channels", "movies", "all")
            if self.cancels(schedule=schedule, mode=mode) == "true"
        ]
        self.assertTrue(cancelling)
        for schedule, mode in cancelling:
            self.assertEqual(TARGETED_GROUP,
                             self.group(schedule=schedule, mode=mode))


class TheWorkflowItselfIsStillSound(unittest.TestCase):
    def setUp(self):
        self.workflow = _load()
        # YAML 1.1 reads the key `on` as the boolean True.
        self.triggers = self.workflow.get("on") or self.workflow.get(True)

    def test_the_declared_crons_are_the_ones_the_expressions_match(self):
        """The group matches crons by text. A cron renamed without changing
        these strings would silently move a mode into the wrong queue."""
        declared = {entry["cron"] for entry in self.triggers["schedule"]}
        expression = self.workflow["concurrency"]["group"]
        for cron in (TARGETED_CRON, CHANNELS_CRON, MOVIES_CRON):
            self.assertIn(cron, declared)
            self.assertIn(cron, expression)

    def test_upcoming_targeted_is_a_dispatch_option(self):
        options = self.triggers["workflow_dispatch"]["inputs"]["mode"]["options"]
        self.assertIn("upcoming-targeted", options)

    def test_the_workflow_was_not_split_into_several_files(self):
        """FINAL_2 keeps that for Phase 2 - one file still owns the push
        retry, the count reconciliation and the state restore."""
        workflows = list((ROOT / ".github" / "workflows").glob("scan*.yml"))
        self.assertEqual([WORKFLOW.name], [path.name for path in workflows])

    def test_the_mode_selector_was_not_touched(self):
        names = [str(step.get("name") or "")
                 for step in self.workflow["jobs"]["scan"]["steps"]]
        self.assertIn("Select scan mode", names)


if __name__ == "__main__":
    unittest.main()
