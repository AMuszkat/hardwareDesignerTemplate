"""
Which board folder a tool works on -- ONE rule for every tool in tools/.

    import boardsel
    board_dir = boardsel.resolve(args.board)          # a pathlib.Path

`--board` may be the board id (`board1`), the folder name (`board1_main`), or a
path. Left out, the answer is, in order: the board folder the current directory
is in; the board this session owns (`owner()` below); the only board there is.
Anything else is an error -- never a guess, because a guess writes into another
board's files.

    board, how = boardsel.owner(session_id)           # ("board1", "env"|"claim"|"only") or ("", "")

WHICH BOARD A SESSION OWNS is answered in one place, for the tools, the
Edit/Write wall (tools/session/wall.py) and the session-start brief
(tools/session/relay.py): `HW_BOARD` (exported by `bin/board`); else this
session's CLAIM in kicad_project/local.sessions.json ({session id: board},
written at every session start that has `HW_BOARD`, so a session resumed
outside `bin/board` keeps its board); else the only board there is. A hook
knows its session id (every hook input carries it); a tool run through Bash
does not, so the brief also exports `HW_BOARD` into a resumed session's shell.

A session started with `bin/board boardN` may not touch another board: these
tools run through Bash, which the Edit/Write wall does not see, so the wall is
repeated here. A directory that is not a board folder of this project (a
scratch copy) is always allowed.
"""
import datetime
import json
import os
import re
import sys
from pathlib import Path

KP = Path(__file__).resolve().parent.parent           # kicad_project/
BOARD_RE = re.compile(r"(board\d+)_\w+")
CLAIMS = KP / "local.sessions.json"                    # per machine, gitignored
CLAIM_DAYS = 30                                       # a claim older than this is dropped at the next write


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


def claims():
    """{session id: {"board": "board1", "at": iso time}}; {} when the file is absent or unreadable
    (a torn or hand-broken file must never stop a session: the wall then blocks, which is safe)."""
    try:
        data = json.loads(CLAIMS.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("board")} if isinstance(data, dict) else {}


def claim(session_id, board):
    """Record that `session_id` is `board`'s session. Atomic replace; old claims dropped."""
    if not session_id or not re.fullmatch(r"board\d+", board or ""):
        return
    now = datetime.datetime.now()
    keep = {}
    for sid, c in claims().items():
        try:
            age = (now - datetime.datetime.fromisoformat(c.get("at", ""))).days
        except ValueError:
            age = CLAIM_DAYS + 1
        if age <= CLAIM_DAYS:
            keep[sid] = c
    keep[session_id] = {"board": board, "at": now.isoformat(timespec="seconds")}
    tmp = CLAIMS.with_name(CLAIMS.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(keep, indent=1) + "\n")
    os.replace(tmp, CLAIMS)


def owner(session_id=None):
    """(board id, how) for the board this session owns: `HW_BOARD` -> "env"; this session's
    claim -> "claim"; the only board folder -> "only"; else ("", "")."""
    mine = os.environ.get("HW_BOARD", "")
    if mine:
        return mine, "env"
    have = boards()
    if session_id:
        c = claims().get(session_id)
        if c and c["board"] in have:
            return c["board"], "claim"
    if len(have) == 1:
        return next(iter(have)), "only"
    return "", ""


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
    mine = owner()[0]
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
    holder = _owner(path)
    if mine and holder and holder != mine:
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
