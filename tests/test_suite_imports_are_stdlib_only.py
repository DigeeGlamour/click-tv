"""The test suite itself must import nothing the scanner does not ship with.

This exists because of a real production outage, not a style preference.

`requirements.txt` says, in as many words, that the scanner is built purely
with the standard library, so CI installs nothing. A test module that does
`import yaml` at module scope therefore raises at *collection* time. unittest
turns that into a single loader ERROR, the suite exits 1, and - because the
scan job runs the suite as a gate before the scanner - every step after it is
skipped. The site's Today Match data simply stops being refreshed, and the
log's most prominent line is an unrelated diagnostic printed by a passing
test, so the real cause reads as something else entirely.

Nine test modules parse workflow YAML. Eight of them guard the import inside
the function that needs it and skip when it is unavailable; one did not, and
that one was enough. This test keeps the count at zero.

A third-party import inside a function or a `try:` is fine and is the pattern
to copy - the point is only that importing the *module* must never fail.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

#: Everything a workflow/browser test might reasonably reach for that is not
#: in the standard library and is not installed on the runner.
NOT_INSTALLED = frozenset({
    "yaml", "requests", "bs4", "lxml", "numpy", "pandas",
    "pytest", "playwright", "selenium", "jsonschema", "dateutil",
})


def _module_level_imports(path: Path):
    """Only `import x` / `from x import y` at the top level of the module.

    Anything nested in a function, a class body or a try/except is reached
    lazily, so it cannot break collection and is deliberately not counted.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".", 1)[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            yield (node.module or "").split(".", 1)[0]


class SuiteCollectsWithoutInstallingAnything(unittest.TestCase):
    def test_no_test_module_imports_an_uninstalled_package_at_module_scope(self):
        offenders = []
        for path in sorted(TESTS.glob("test_*.py")):
            for name in _module_level_imports(path):
                if name in NOT_INSTALLED:
                    offenders.append(f"{path.name}: import {name}")
        self.assertEqual(
            offenders,
            [],
            "these modules fail collection on a runner that installs nothing, "
            "which fails the whole suite and skips the scan: "
            + "; ".join(offenders)
            + " - move the import inside the function and skip on ImportError",
        )

    def test_requirements_still_declares_no_dependencies(self):
        """If this ever stops being true, the rule above can be relaxed."""
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        declared = [
            line.strip()
            for line in requirements.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertEqual(
            declared, [], f"requirements.txt now installs {declared}"
        )

    def test_every_test_module_still_parses(self):
        """A syntax error here would be invisible until CI collected it."""
        for path in sorted(TESTS.glob("test_*.py")):
            with self.subTest(module=path.name):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


if __name__ == "__main__":
    unittest.main()
