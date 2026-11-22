#!/usr/bin/env python3
"""
The review-mode switch: when the concept session's pcb-reviewer looks at a board's PCB.

    tools/session/review.py status [boardN]          # each board's mode, and the default
    tools/session/review.py release [boardN]         # the default, or one board, set to release
    tools/session/review.py milestones [boardN]      # the default, or one board, set to milestones
    tools/session/review.py mode boardN              # prints release|milestones (for skills, scripts)
    tools/session/review.py brief concept|board [boardN]   # the session-start lines (relay.py brief)

(from the repo root: `bin/review status|release|milestones [boardN]`.)

The two modes differ in the PCB only.  EVERY SCHEMATIC CHANGE IS REVIEWED IN BOTH.

  release      (the default)  The PCB is reviewed whole, once it is finished: every group placed
               and routed, every board gate clean -- `pcb: fab release`.  Before that the board's
               own gates are the gate (place.py check / drc, prelayout.py, route.py audit with
               --findings) and the board builds on them without waiting.  From the fab release on,
               the fix loop runs as often as it takes (`pcb: fab release, fixes for RVk`): judged
               from the snapshot when every finding carries a check, else one fix-depth agent run.
  milestones   The PCB is reviewed as it is built: placement whole, then routing at fanout/escape,
               buses, power, pours and close-out, each a full agent run the board waits on (a group
               may be requested alone), then the fab release.  For a first board on a new fab, or
               a design where one wrong group costs days of copper.

Why release is the default: one board's pcb-reviewer ran 42 times at a median of 19 minutes (to
RV64), most of them on copper that moved again before the fab release.  A milestone review is a
full-depth agent run and a board that waits; what a number decides, the board's gates already catch.

kicad_project/review.json -- {"default": "release"|"milestones", "boards": {"board1": ...},
"changed": ..., "by": ...}.  COMMITTED: it is the project's policy, the same on every machine.
Missing = release.  Written only by this script, and only at the user's request.  A board session
reads its mode at session start (the [review] line); a live one is told by the user, or rung.
"""
import datetime
import getpass
import json
import os
import re
import sys
from pathlib import Path

KP = Path(__file__).resolve().parents[2]          # kicad_project/
REVIEW = KP / "review.json"
MODES = ("release", "milestones")
DEFAULT = "release"
BOARD_RE = re.compile(r"board\d+")


def _load():
    try:
        with open(REVIEW) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save(data):
    tmp = REVIEW.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, REVIEW)


def boards():
    """Board ids that have a folder: board1_main -> board1."""
    out = []
    for d in sorted(KP.iterdir()):
        m = re.fullmatch(r"(board\d+)_\w+", d.name)
        if d.is_dir() and m:
            out.append((m.group(1), d.name))
    return out


def default_mode(data=None):
    d = (data if data is not None else _load()).get("default")
    return d if d in MODES else DEFAULT


def mode(board=None, data=None):
    data = data if data is not None else _load()
    m = (data.get("boards") or {}).get(board) if board else None
    return m if m in MODES else default_mode(data)


def _board_id(arg):
    m = BOARD_RE.fullmatch(arg) or BOARD_RE.match(arg)
    if not m:
        sys.exit(f"not a board id: {arg!r} (expected board1, board2, ...)")
    return m.group(0)


def set_mode(new, board=None):
    data = _load()
    was = mode(board, data)
    per = {b: m for b, m in (data.get("boards") or {}).items() if m in MODES}
    if board:
        per[board] = new
        dflt = default_mode(data)
    else:
        dflt = new
    per = {b: m for b, m in per.items() if m != dflt}       # an override equal to the default is none
    data.update({"default": dflt, "boards": per,
                 "changed": datetime.datetime.now().isoformat(timespec="seconds"),
                 "by": getpass.getuser()})
    _save(data)
    print(f"review mode {board or 'default'}: {new.upper()}" + (" (unchanged)" if was == new else ""))
    print("A board session reads it at its next start. Tell a live one "
          f"'review mode is now {new}' (relay on: the concept session rings it). "
          "Commit kicad_project/review.json: it is the project's policy.")


def status(board=None):
    data = _load()
    print(f"review default: {default_mode(data).upper()}"
          + (f"  (changed {data['changed']} by {data.get('by', '?')})" if data.get("changed") else
             "  (kicad_project/review.json missing: release)" if not REVIEW.exists() else ""))
    rows = [(board, "")] if board else boards()
    for bid, folder in rows:
        own = (data.get("boards") or {}).get(bid) in MODES
        print(f"{bid}:   {mode(bid, data).upper()}" + ("  (set for this board)" if own else "")
              + (f"  ({folder}/)" if folder else ""))
    print("Schematic changes are reviewed in both modes; the mode decides the PCB only.")


GATES = "place.py check and drc, prelayout.py, route.py audit with --findings"


def brief_lines(side, board=None):
    """Lines for the session-start brief (relay.py brief).  Never raises: a hook must not stop a
    session."""
    try:
        data = _load()
        if side == "concept":
            per = ", ".join(f"{b} {mode(b, data).upper()}" for b, _ in boards()) or "no boards yet"
            return [f"[review] Review mode (kicad_project/review.json; the user sets it with bin/review): "
                    f"default {default_mode(data).upper()}; {per}. Schematic changes are reviewed in both "
                    "modes. RELEASE: no placement, milestone or single-group PCB review -- the first PCB "
                    "review is `pcb: fab release`, and a milestone ring is answered NOT REVIEWED with no "
                    "snapshot and no agent (review-board skill, step 1). MILESTONES: the PCB at milestones."]
        m = mode(board, data)
        who = board or "this board"
        if m == "release":
            return [f"[review] Review mode for {who}: RELEASE (kicad_project/review.json; the user sets it "
                    "with bin/review). Every SCHEMATIC change is reviewed as always. The PCB is not reviewed "
                    "as it is built: no placement or routing milestone review, no group alone -- your own "
                    f"gates ({GATES}) are the gate, and you build on them without waiting. When every group "
                    "is routed and those gates are clean, request `pcb: fab release` (kicad_project/CLAUDE.md, "
                    "'Placement and routing are done in groups')."]
        return [f"[review] Review mode for {who}: MILESTONES (kicad_project/review.json; the user sets it with "
                "bin/review). Every schematic change is reviewed; the PCB is reviewed as it is built -- "
                "placement as a whole, routing at fanout/escape, buses, power, pours and close-out -- each "
                "waiting for its verdict, independent work going on meanwhile (kicad_project/CLAUDE.md, "
                "RELAY_PROTOCOL.md section 10)."]
    except Exception:
        return []


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]
    if cmd == "status" and len(rest) <= 1:
        status(_board_id(rest[0]) if rest else None)
    elif cmd in MODES and len(rest) <= 1:
        set_mode(cmd, _board_id(rest[0]) if rest else None)
    elif cmd == "mode" and len(rest) == 1:
        print(mode(_board_id(rest[0])))
    elif cmd == "brief" and rest and rest[0] in ("concept", "board"):
        print("\n".join(brief_lines(rest[0], _board_id(rest[1]) if len(rest) > 1 else None)))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
