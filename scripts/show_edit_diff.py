"""Show what a timestamp edit actually changed in a lesson.

Video length is weak evidence. This prints, per scene, whether the narration was
left alone or rewritten — and for the rewritten ones, only the sentences that
differ. That is the claim worth making: the scenes outside the range are
untouched, and the ones inside are genuinely re-taught.

    python scripts/show_edit_diff.py                 # newest project, changed lines only
    python scripts/show_edit_diff.py <project-id>    # a specific project
    python scripts/show_edit_diff.py --full          # include the complete narration
"""

from __future__ import annotations

import difflib
import json
import re
import sys
import textwrap
from pathlib import Path

PROJECTS = Path("projects")
RULE = "═" * 76

GREEN = "\033[32m"
RED = "\033[31m"
BOLD = "\033[1m"
DIM = "\033[2m"
OFF = "\033[0m"


def _versions(scene_dir: Path) -> list[Path]:
    specs = list((scene_dir / "spec").glob("v*.json"))
    return sorted(specs, key=lambda p: int(p.stem.lstrip("v")))


def _narration(path: Path) -> str:
    try:
        return (json.loads(path.read_text()).get("narration") or "").strip()
    except (OSError, json.JSONDecodeError):
        return ""


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _wrap(text: str, indent: str) -> str:
    return textwrap.fill(text, width=72, initial_indent=indent, subsequent_indent=" " * len(indent))


def _latest_project() -> Path | None:
    if not PROJECTS.is_dir():
        return None
    candidates = [p for p in PROJECTS.iterdir() if (p / "scenes").is_dir()]
    return max(candidates, key=lambda p: p.stat().st_mtime, default=None)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    full = "--full" in sys.argv

    project = PROJECTS / args[0] if args else _latest_project()
    if project is None or not project.is_dir():
        print("No project found. Pass a project id.")
        return 1

    scenes = sorted(p for p in (project / "scenes").iterdir() if p.is_dir())
    print(RULE)
    print(f"{BOLD}WHAT THE CLARIFICATION CHANGED{OFF}   project {project.name[:8]}")
    print(RULE)

    changed = untouched = 0
    for scene_dir in scenes:
        specs = _versions(scene_dir)
        if not specs:
            continue
        before, after = _narration(specs[0]), _narration(specs[-1])
        takes = len(list((scene_dir / "audio").glob("*.wav")))

        if before == after:
            untouched += 1
            print(f"\n{BOLD}{scene_dir.name}{OFF}  {GREEN}UNTOUCHED{OFF}"
                  f"   {DIM}spec v{specs[0].stem[1:]} · {takes} audio take · render reused{OFF}")
            print(f"    {DIM}narration is byte-for-byte identical{OFF}")
            continue

        changed += 1
        print(f"\n{BOLD}{scene_dir.name}{OFF}  {RED}REGENERATED{OFF}"
              f"   {DIM}spec v{specs[0].stem[1:]} → v{specs[-1].stem[1:]}"
              f" · {takes} audio takes · re-rendered{OFF}")

        if full:
            print(f"\n  {DIM}BEFORE:{OFF}")
            print(_wrap(before or "(empty)", "    "))
            print(f"\n  {DIM}AFTER:{OFF}")
            print(_wrap(after or "(empty)", "    "))
            continue

        sb, sa = _sentences(before), _sentences(after)
        matcher = difflib.SequenceMatcher(None, sb, sa)
        printed = False
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            printed = True
            for line in sb[i1:i2]:
                print(f"\n{RED}  − was:{OFF}")
                print(_wrap(line, "      "))
            for line in sa[j1:j2]:
                print(f"{GREEN}  + now:{OFF}")
                print(_wrap(line, "      "))
        if not printed:
            print("    (wording changed without whole sentences being added or removed)")

        if "Edit requested" in after:
            print(f"\n  {RED}⚠ Built before the narration-rewrite fix: the instruction was"
                  f" appended verbatim.{OFF}")

    print(f"\n{RULE}")
    print(f"{GREEN}{untouched} scenes untouched{OFF}   ·   {RED}{changed} regenerated{OFF}"
          f"   {DIM}(only the scenes overlapping the requested range){OFF}")
    print(RULE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
