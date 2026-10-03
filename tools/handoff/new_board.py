"""
Create a board: both sides of the wall, then publish its first handoff.

    python tools/handoff/new_board.py 1 main "Main board"
    python tools/handoff/new_board.py 2 power "Power stage" --fab example_standard

Writes, and refuses if any of them already exists:

  concept side   tools/handoff/boards/boardN.py      (from board_template.py)
                 studies/components_boardN.py        (the value bus)
                 tools/handoff/answers/boardN.md
  board side     kicad_project/boardN_<slug>/        (from kicad_project/tools/board_scaffold/)
                 kicad_project/boardN_<slug>/handoff/ (publish.py, rev 1)

The board-side files are the board session's from the moment they exist --
this is the one-time bootstrap, done before that session starts. After it,
concept writes only `handoff/` there (through publish.py) and reads
QUESTIONS.md. The KiCad project itself is NOT created here: the board
session creates it through Konnect, named after the folder.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCAFFOLD = os.path.join(ROOT, "kicad_project", "tools", "board_scaffold")

BUS = '''"""
Value bus for {title} ({bid}) -- the physical component values.

The integration point between the concept session and the board session:
publish.py renders it as handoff/01_components.md. Written by the concept
session only; a value the board session needs that is missing or wrong is a
question in its QUESTIONS.md, never a local edit.

STATUS
  set       derived or measured; the note says by what -- commit it
  proposed  analysed but not verified -- confirm before committing
  tbd       requirement stated, value or part not chosen -- renders as
            PLACEHOLDER, and the board session places NO part for it

A line names the MPN in `value` where a part is chosen; publish.py ships the
datasheet of every MPN the handoff names (except chip R/C/ferrite beads in
0201-2512, which use KiCad's stock footprints). Several designators may share
a line: "R1..R8", "C3, C4".
"""

# (refdes, function, value, status, note)
COMPONENTS = [
    # ("U1", "3.3 V LDO", "TI TLV75533PDBVR -- 3.3 V, 500 mA, SOT-23-5", "set",
    #  "Dropout 238 mV at 500 mA (datasheet p.6) against 1.7 V of headroom."),
    # ("C1, C2", "U1 input/output capacitors", "1 uF, 16 V, X7R, 0603", "set",
    #  "U1 datasheet p.18: >= 0.47 uF effective."),
    # ("U2", "microcontroller", "TBD", "tbd",
    #  "Needs 2 UARTs and 12-bit ADC; part not chosen (05_open.md #1)."),
]

# Optional: what the board is accepted against.
# ACCEPTANCE = {{"node": "VOUT", "ripple_mV_pp": 10, "note": "Measured at ..."}}
'''

ANSWERS = """# Answers — {title} ({bid})

Concept writes here and nowhere else; publish.py renders it as
`handoff/07_answers.md`. One heading per question, numbered as in the board
session's QUESTIONS.md. A durable fact goes into the generated tables (the
value bus, NETS, LAYOUT, PIN_TREATMENT) too, never only into an answer.

Nothing answered yet.
"""

FAB = '''FAB = {{
    "profile": "{fab}",
    "layers": None,               # 2, 4, ... a stackup the profile has -- UNSET until decided
    "outline": None,              # (W, H) mm -- UNSET until decided
    "quantity": None,             # boards to order -- UNSET until decided
    "order": [],
    "constraints": [],
    "carries": {{}},
    "netclasses": [],
    "rules": [],
    "why": [],
    "sources": [],
}}'''


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("number", type=int)
    ap.add_argument("slug", help="lower-case folder suffix, e.g. main, power, sensor_front")
    ap.add_argument("title")
    ap.add_argument("--fab", help="fab profile in tools/handoff/fabs/ (default: no 14_fab.md yet)")
    ap.add_argument("--no-publish", action="store_true")
    a = ap.parse_args()

    if a.number < 1:
        sys.exit("new_board: the board number starts at 1")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_]*", a.slug):
        sys.exit("new_board: slug must be lower-case letters, digits and _")
    if a.fab and not os.path.exists(os.path.join(HERE, "fabs", a.fab + ".py")):
        have = sorted(f[:-3] for f in os.listdir(os.path.join(HERE, "fabs")) if f.endswith(".py"))
        sys.exit(f"new_board: no fab profile '{a.fab}' (have: {', '.join(have)})")
    bid = f"board{a.number}"
    board = f"{bid}_{a.slug}"
    kp = os.path.join(ROOT, "kicad_project")
    targets = [os.path.join(HERE, "boards", bid + ".py"),
               os.path.join(ROOT, "studies", f"components_{bid}.py"),
               os.path.join(HERE, "answers", bid + ".md")]
    taken = [os.path.relpath(t, ROOT) for t in targets if os.path.exists(t)]
    if os.path.isdir(kp):
        taken += [f"kicad_project/{d}" for d in os.listdir(kp)
                  if d == bid or d.startswith(bid + "_")]
    if taken:
        sys.exit("new_board: refusing to overwrite an existing board:\n  " + "\n  ".join(taken))

    if "\n" in a.title or not a.title.strip():
        sys.exit("new_board: the title is one non-empty line")
    # The title lands inside Python string literals and docstrings.
    tq = a.title.replace("\\", "\\\\").replace('"', '\\"')
    tpl = open(os.path.join(HERE, "board_template.py")).read()
    tpl = (tpl.replace("{{BOARD}}", board).replace("{{TITLE}}", tq)
              .replace("{{ID}}", bid))
    if a.fab:
        tpl, n = re.subn(r"^FAB = None$", lambda _m: FAB.format(fab=a.fab), tpl, flags=re.M)
        if n != 1:
            sys.exit("new_board: board_template.py no longer has exactly one `FAB = None` line")
    for d in ("boards", "answers"):
        os.makedirs(os.path.join(HERE, d), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "studies"), exist_ok=True)
    open(targets[0], "w").write(tpl)
    open(targets[1], "w").write(BUS.format(title=tq, bid=bid))
    open(targets[2], "w").write(ANSWERS.format(title=a.title, bid=bid))

    dst = os.path.join(kp, board)
    if os.path.isdir(SCAFFOLD):
        shutil.copytree(SCAFFOLD, dst)
    else:
        print("WARNING: kicad_project/tools/board_scaffold/ is missing; the board "
              "folder gets only lib/ and no QUESTIONS.md")
        os.makedirs(os.path.join(dst, "lib"))
    for f in ("QUESTIONS.md", "ROUTING_PLAN.md"):
        p = os.path.join(dst, f)
        if os.path.exists(p):
            t = open(p).read()
            open(p, "w").write(t.replace("{{BOARD}}", board).replace("{{TITLE}}", a.title)
                                .replace("{{ID}}", bid))

    for t in targets:
        print(f"created {os.path.relpath(t, ROOT)}")
    print(f"created kicad_project/{board}/")
    if not a.no_publish:
        r = subprocess.run([sys.executable, os.path.join(HERE, "publish.py"), "--board", bid])
        if r.returncode:
            # All or nothing: a half-made board blocks every later publish.
            for t in targets:
                os.remove(t)
            shutil.rmtree(dst, ignore_errors=True)
            sys.exit(f"new_board: the first publish failed (above); {bid} was NOT created")
    # STATUS.md's generated block lists the boards; keep it current (lint check 9)
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "status.py")])
    print(f"\nNext: fill studies/components_{bid}.py and tools/handoff/boards/{bid}.py, "
          f"then `.venv/bin/python tools/handoff/publish.py --board {bid}`. Start the "
          f"board session with `bin/board {bid}`.")


if __name__ == "__main__":
    main()
