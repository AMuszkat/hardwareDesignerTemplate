#!/usr/bin/env python3
"""
The review-mode switch (`bin/review`, kicad_project/tools/session/review.py): when the PCB is
reviewed -- `release` (the default: whole, at the fab release) or `milestones` (as it is built).

    python3 tests/test_review_mode.py

A temp kicad_project/ with two boards and the session scripts copied in.  What must hold:

  * no review.json: every board is RELEASE, and both session briefs say so;
  * one board set to milestones changes that board only; setting the default to milestones
    drops the override that now equals it; `release` brings everything back, with no override;
  * a corrupt review.json or an unknown mode reads as release, and the brief still exits 0
    (a hook must never stop a session);
  * relay.py brief without review.py beside it still runs (an older copy of the scripts);
  * the concept wall refuses a hand edit of review.json.

Last line: PASS / FAIL, as every tests/test_*.py prints.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSION = ROOT / "kicad_project" / "tools" / "session"
BAD = []


def expect(what, ok):
    if not ok:
        BAD.append(what)


def env():
    e = {k: v for k, v in os.environ.items() if k not in ("HW_BOARD", "CLAUDE_ENV_FILE")}
    return dict(e, HW_BOARD="board1")


def review(kp, *a):
    r = subprocess.run([sys.executable, str(kp / "tools" / "session" / "review.py"), *a],
                       capture_output=True, text=True, env=env(), cwd=str(kp), timeout=30)
    return r.returncode, r.stdout + r.stderr


def brief(kp, side):
    r = subprocess.run([sys.executable, str(kp / "tools" / "session" / "relay.py"), "brief", side],
                       input=json.dumps({"session_id": "S", "source": "startup"}), capture_output=True,
                       text=True, env=env(), cwd=str(kp), timeout=30)
    return r.returncode, [l for l in r.stdout.splitlines() if l.startswith("[review]")], r.stdout


def state(kp):
    try:
        return json.loads((kp / "review.json").read_text())
    except FileNotFoundError:
        return None


def main():
    tmp = Path(tempfile.mkdtemp(prefix="revmode-"))
    try:
        kp = tmp / "kicad_project"
        (kp / "tools" / "session").mkdir(parents=True)
        for f in ("relay.py", "review.py", "wall.py"):
            shutil.copy(SESSION / f, kp / "tools" / "session")
        shutil.copy(SESSION.parent / "boardsel.py", kp / "tools")
        (kp / "project.json").write_text(json.dumps({"project": "T", "concept_session": "T_Concept",
                                                     "board_session_prefix": "T_Kicad_"}))
        for b in ("board1_main", "board2_power"):
            (kp / b).mkdir()

        # no file: release everywhere
        expect("no review.json: board1 release", review(kp, "mode", "board1")[1].strip() == "release")
        rc, rv, _ = brief(kp, "board")
        expect(f"board brief, no file: RELEASE line {rv}",
               rc == 0 and len(rv) == 1 and "board1: RELEASE" in rv[0] and "fab release" in rv[0])
        rc, rv, _ = brief(kp, "concept")
        expect(f"concept brief, no file: {rv}", rc == 0 and len(rv) == 1
               and "default RELEASE; board1 RELEASE, board2 RELEASE" in rv[0])

        # one board to milestones
        rc, out = review(kp, "milestones", "board2")
        expect(f"milestones board2 exits 0: {out}", rc == 0)
        expect("board2 milestones", review(kp, "mode", "board2")[1].strip() == "milestones")
        expect("board1 still release", review(kp, "mode", "board1")[1].strip() == "release")
        s = state(kp) or {}
        expect(f"file: default release, override board2 {s}",
               s.get("default") == "release" and s.get("boards") == {"board2": "milestones"})
        rc, rv, _ = brief(kp, "concept")
        expect(f"concept brief names board2 MILESTONES: {rv}", rc == 0 and rv and "board2 MILESTONES" in rv[0])

        # default to milestones: the override equal to it goes
        review(kp, "milestones")
        s = state(kp) or {}
        expect(f"default milestones, no override left {s}", s.get("default") == "milestones" and s.get("boards") == {})
        rc, rv, _ = brief(kp, "board")
        expect(f"board brief: MILESTONES {rv}", rc == 0 and rv and "board1: MILESTONES" in rv[0])

        # board1 back to release, then the default
        review(kp, "release", "board1")
        s = state(kp) or {}
        expect(f"override board1 release under a milestones default {s}",
               s.get("default") == "milestones" and s.get("boards") == {"board1": "release"})
        review(kp, "release")
        s = state(kp) or {}
        expect(f"release: default release, no override {s}", s.get("default") == "release" and s.get("boards") == {})
        rc, out = review(kp, "status")
        expect(f"status lists both boards: {out}", rc == 0 and "board1:   RELEASE" in out and "board2:   RELEASE" in out)

        # bad input
        rc, out = review(kp, "mode", "nonsense")
        expect("a bad board id is refused", rc != 0)
        (kp / "review.json").write_text("{not json")
        rc, rv, _ = brief(kp, "board")
        expect(f"corrupt file: brief exits 0 and reads RELEASE {rv}", rc == 0 and rv and "RELEASE" in rv[0])
        (kp / "review.json").write_text(json.dumps({"default": "weekly", "boards": {"board1": "sometimes"}}))
        expect("unknown modes read as release", review(kp, "mode", "board1")[1].strip() == "release")

        # an older copy: relay.py without review.py
        (kp / "tools" / "session" / "review.py").rename(kp / "tools" / "session" / "review.py.off")
        rc, rv, out = brief(kp, "board")
        expect("relay brief without review.py still runs, no review line", rc == 0 and not rv and "[relay]" in out)
        (kp / "tools" / "session" / "review.py.off").rename(kp / "tools" / "session" / "review.py")

        # the concept wall
        ev = {"tool_name": "Write", "cwd": str(tmp), "tool_input": {"file_path": str(kp / "review.json")}}
        r = subprocess.run([sys.executable, str(kp / "tools" / "session" / "wall.py"), "concept"],
                           input=json.dumps(ev), capture_output=True, text=True, timeout=30)
        expect(f"concept wall blocks review.json (exit {r.returncode})", r.returncode == 2 and "bin/review" in r.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for b in BAD:
        print("  " + b)
    print("FAIL: " + BAD[0] if BAD else
          "PASS: release by default, per-board overrides, the default absorbing an equal override, a bad file "
          "read as release, the brief silent without review.py, the wall on review.json")
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
