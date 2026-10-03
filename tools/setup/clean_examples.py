#!/usr/bin/env python3
"""
Remove the example studies the template ships with, and everything that
belongs to them, in one step:

    ./setup.sh clean-examples          (or: python3 tools/setup/clean_examples.py [--dry-run])

  studies/s01_rc_corner.py, s02_divider_tolerance.py, s03_ldo_thermal.py
  the EXAMPLE rows of studies/designpoint.py        (PARAMS becomes empty)
  the *(example)* rows of design/SPECS.md
  the example entries of history/HISTORY.md
  studies/baselines/headline.json                    (their blessed numbers)

They go together: a SPECS row citing a study that is gone fails the doc lint,
and a baseline key no study produces fails `--check`.  Run it when your first
real study exists -- or right away, if you would rather start from empty.

NOT touched, because your first real decision replaces them by hand: the
example Decided row and Open item in design/STATUS.md, the example sheet
tools/diagrams/system.py, and its row in design/DIAGRAMS.md.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DRY = "--dry-run" in sys.argv
STUDIES = ["s01_rc_corner.py", "s02_divider_tolerance.py", "s03_ldo_thermal.py"]


def p(*rel):
    return os.path.join(ROOT, *rel)


def write(rel, text, what):
    print(f"  {rel}: {what}")
    if not DRY:
        with open(p(rel), "w") as f:
            f.write(text)


def main():
    done = 0
    for s in STUDIES:
        if os.path.exists(p("studies", s)):
            print(f"  studies/{s}: removed")
            done += 1
            if not DRY:
                os.remove(p("studies", s))
    bl = p("studies", "baselines", "headline.json")
    if os.path.exists(bl):
        print("  studies/baselines/headline.json: removed (bless your own with run_all.py --check --bless)")
        done += 1
        if not DRY:
            os.remove(bl)

    t = open(p("studies", "designpoint.py")).read()
    m = re.search(r"PARAMS = \{\n    # ===== EXAMPLE rows.*?\n\}\n", t, re.S)
    if m:
        write("studies/designpoint.py",
              t[:m.start()] + "PARAMS = {\n    # \"name\": (value, \"unit\", \"set\" | \"default\", "
              "\"source: SPECS section, datasheet page, or study\"),\n}\n" + t[m.end():],
              "EXAMPLE rows removed")
        done += 1

    t = open(p("design", "SPECS.md")).read()
    keep = [ln for ln in t.splitlines() if "*(example)*" not in ln]
    if len(keep) != len(t.splitlines()):
        write("design/SPECS.md", "\n".join(keep) + "\n",
              f"{len(t.splitlines()) - len(keep)} example row(s) removed")
        done += 1

    t = open(p("history", "HISTORY.md")).read()
    new, n = re.subn(r"\n### Study \d+ — EXAMPLE.*?(?=\n### |\Z)", "", t, flags=re.S)
    if n:
        write("history/HISTORY.md", new.rstrip("\n") + "\n", f"{n} example entr(ies) removed")
        done += 1

    if not done:
        print("  nothing to remove: the examples are already gone")
    elif DRY:
        print("(dry run, nothing changed)")
    else:
        print("done. Next: ./verify.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
