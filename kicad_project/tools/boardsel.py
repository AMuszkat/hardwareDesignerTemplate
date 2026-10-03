"""
Which board folder a tool works on -- ONE rule for every tool in tools/.

    import boardsel
    board_dir = boardsel.resolve(args.board)          # a pathlib.Path

`--board` may be the board id (`board1`), the folder name (`board1_main`), or a
path. Left out, the answer is, in order: the board folder the current directory
is in; the board this session owns (`HW_BOARD`, exported by `bin/board`); the
only board there is. Anything else is an error -- never a guess, because a
guess writes into another board's files.

A session started with `bin/board boardN` may not touch another board: these
tools run through Bash, which the Edit/Write wall does not see, so the wall is
repeated here. A directory that is not a board folder of this project (a
scratch copy) is always allowed.
"""
import os
import re
import sys
from pathlib import Path

KP = Path(__file__).resolve().parent.parent           # kicad_project/
BOARD_RE = re.compile(r"(board\d+)_\w+")


def die(msg):
    print(f"board: {msg}", file=sys.stderr)
    sys.exit(2)


def boards():
    """{board id: folder} for every boardN_<slug>/ in kicad_project/."""
    out = {}
    for d in sorted(KP.iterdir()):
        m = BOARD_RE.fullmatch(d.name)
        if m and d.is_dir():
            out.setdefault(m.group(1), d)
    return out


def _owner(path):
    """The board id whose folder holds `path`, or None outside every board."""
    try:
        top = path.resolve().relative_to(KP).parts[0]
    except (ValueError, IndexError):
        return None
    m = BOARD_RE.fullmatch(top)
    return m.group(1) if m else None


def resolve(arg=None):
    have = boards()
    mine = os.environ.get("HW_BOARD", "")
    if arg in (None, ""):
        here = _owner(Path.cwd())
        if here:
            path = have[here]
        elif mine:
            if mine not in have:
                die(f"HW_BOARD={mine} but there is no kicad_project/{mine}_*/")
            path = have[mine]
        elif len(have) == 1:
            path = next(iter(have.values()))
        else:
            die("which board? pass --board boardN (have: "
                f"{', '.join(have) or 'none'}), or start the session with bin/board")
    else:
        a = str(arg).rstrip("/")
        if a in have:
            path = have[a]
        else:
            cands = [Path(a)] if os.path.isabs(a) else [Path.cwd() / a, KP / a]
            path = next((c for c in cands if c.is_dir()), None)
            if path is None:
                die(f"no such board: {arg!r} (have: {', '.join(have) or 'none'})")
            path = path.resolve()
            top = _owner(path)
            if top:                                    # inside a board: its folder
                path = have[top]
            elif path == KP or KP in path.parents:
                die(f"{arg!r} is not a board folder (have: {', '.join(have) or 'none'})")
    owner = _owner(path)
    if mine and owner and owner != mine:
        die(f"{path.name}/ belongs to another board session; this session is {mine}. "
            "A fact you need from that board is a QUESTIONS.md entry.")
    return path


def one_pcb(board_dir):
    """The board's .kicad_pcb: exactly one, autosaves and backups ignored."""
    pcbs = [p for p in sorted(Path(board_dir).glob("*.kicad_pcb"))
            if not p.name.startswith(("_autosave-", "~"))]
    if len(pcbs) != 1:
        die(f"{Path(board_dir).name}: expected one .kicad_pcb, found {len(pcbs)}"
            + ("" if pcbs else " -- the board session creates the KiCad project "
               "first (Konnect, named after the folder)"))
    return pcbs[0]


def in_board(board_dir, rel):
    """A plan/spec path given on the command line: as typed if it exists from
    the current directory, else relative to the board folder."""
    p = Path(rel)
    if p.is_absolute() or p.exists():
        return p
    q = Path(board_dir) / rel
    return q if q.exists() else p
