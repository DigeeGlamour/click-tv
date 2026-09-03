"""Where the scanner's own cross-run state and reports live.

Every module that keeps state between runs anchored its default path to
`__file__` - the repository this code was IMPORTED from, not the directory it
is running in. That is right for a scan and wrong for a test suite:
`python -m unittest discover` wrote into the real state/ and reports/ of the
checkout it imported, from whatever working directory it was started in.

It was never cosmetic. Measured on 2026-08-29, one suite run left
state/route-persistence.json 13,736 lines shorter - the ledger holding
per-route observation history, and the whole reason a channel is not given up
on after one bad answer. Because CI validated before it scanned, every run
since the suite grew started from an emptied ledger and then committed it:
497 KB at 150d3487c, 22 KB three commits later, 216 KB at 3796cd144, 23 KB at
71fda2082.

The workflow also runs the suite in a throwaway git worktree, which stops the
modules that use paths relative to the working directory. These two overrides
stop the ones that cannot be stopped that way, because their paths are
absolute.

Nothing changes unless an override is set. With no environment variable the
answer is the same repository directory these modules used before, computed
the same way - so a scan, a Colab run and a local PC run all behave exactly as
they did. Only a caller that deliberately asks for somewhere else, which in
practice means the test runner, gets somewhere else.
"""
from __future__ import annotations

import os

#: The repository this module was imported from.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Set either of these to send every default state/reports path somewhere
#: else. The workflow points them at a temporary directory while it runs the
#: test suite.
STATE_ROOT_ENV = "CLICKTV_STATE_ROOT"
REPORTS_ROOT_ENV = "CLICKTV_REPORTS_ROOT"


def _rooted(env_name: str, default_name: str) -> str:
    override = os.environ.get(env_name, "").strip()
    if override:
        return override
    return os.path.join(REPO_ROOT, default_name)


def state_root() -> str:
    """The directory holding the scanner's cross-run ledgers."""
    return _rooted(STATE_ROOT_ENV, "state")


def reports_root() -> str:
    """The directory holding the scanner's per-run reports."""
    return _rooted(REPORTS_ROOT_ENV, "reports")


def state_path(*parts: str) -> str:
    """A path inside the state directory, honouring the override."""
    return os.path.join(state_root(), *parts)


def reports_path(*parts: str) -> str:
    """A path inside the reports directory, honouring the override."""
    return os.path.join(reports_root(), *parts)
