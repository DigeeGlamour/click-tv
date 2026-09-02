"""Register the episode playback profiles the committed catalogue is missing.

The scan repairs this itself from now on. The files already in the repository
were published before the check existed, and the Pages validator reads those
files, so this runs the same module once over them.

Prints what it registered and writes nothing else.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scanner.series_catalogue import missing_episode_profiles, reconcile  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def main() -> int:
    before = missing_episode_profiles(DATA)
    if not before:
        print("Every published episode already has a playback profile.")
        return 0

    print(f"{len(before)} published episode(s) have no playback profile.")
    report = reconcile(DATA)
    after = missing_episode_profiles(DATA)

    print(f"  registered  : {report['registered']}")
    for line in report["examples"]:
        print(f"    {line}")
    if report["unexplained"]:
        print(f"  unexplained : {len(report['unexplained'])}")
        print(json.dumps(report["unexplained"][:5], ensure_ascii=False, indent=2))
    print(f"  still missing: {len(after)}")
    return 1 if after else 0


if __name__ == "__main__":
    raise SystemExit(main())
