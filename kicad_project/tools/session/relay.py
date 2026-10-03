#!/usr/bin/env python3
"""
The relay switch and the project's session identity.

    tools/session/relay.py status              # on/off, the names, when it changed
    tools/session/relay.py on | off            # flip the switch
    tools/session/relay.py name concept        # -> <Project>_Concept
    tools/session/relay.py name board1         # -> <Project>_Kicad_board1
    tools/session/relay.py brief concept|board # session-start context (a hook runs it)
    tools/session/relay.py init <Project> [--permission-mode M] [--kicad-python P]

(from the repo root: `bin/relay on|off|status`.)

Files in kicad_project/, the one directory BOTH sessions may read:

  project.json  WHO: project name, the session names, the shared permission
                mode.  Committed.  Written by `./setup.sh init`.
  relay.json    WHETHER: {"enabled": bool, "changed": ..., "by": ...}.  Per
                machine (gitignored; missing = off).  Written only by this
                script, and only at the user's request.
  local.json    this machine's pcbnew interpreter (tools/kpy).  Gitignored.

The relay is OFF by default.  Off, the sessions never message each other: a
stuck board session writes QUESTIONS.md and tells the user, and the user
carries the news.  On, they ring each other per kicad_project/RELAY_PROTOCOL.md.
The files are the record either way, so flipping the switch loses nothing.
"""
import datetime
import getpass
import json
import os
import re
import sys
from pathlib import Path

KP = Path(__file__).resolve().parents[2]          # kicad_project/
PROJECT = KP / "project.json"
RELAY = KP / "relay.json"
LOCAL = KP / "local.json"
BOARD_RE = re.compile(r"board\d+")


def _load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _save(path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def project():
    p = _load(PROJECT)
    if not p:
        sys.exit("kicad_project/project.json is missing: run ./setup.sh init <ProjectName> first")
    return p


def enabled():
    return bool((_load(RELAY, {}) or {}).get("enabled"))


def name(who):
    p = project()
    if who == "concept":
        return p["concept_session"]
    m = BOARD_RE.fullmatch(who) or BOARD_RE.match(who)
    if not m:
        sys.exit(f"not a board id: {who!r} (expected board1, board2, ...)")
    return p["board_session_prefix"] + m.group(0)


def boards():
    """Board ids that have a folder: board1_main -> board1."""
    out = []
    for d in sorted(KP.iterdir()):
        m = re.fullmatch(r"(board\d+)_\w+", d.name)
        if d.is_dir() and m:
            out.append((m.group(1), d.name))
    return out


def set_enabled(on):
    project()
    was = enabled()
    _save(RELAY, {"enabled": on,
                  "changed": datetime.datetime.now().isoformat(timespec="seconds"),
                  "by": getpass.getuser()})
    state = "ON" if on else "OFF"
    print(f"relay {state}" + (" (unchanged)" if was == on else ""))
    if on:
        print("Next: tell the concept session \"arm the relay\" -- it sends the arming line "
              "to every live board session. A session that is not running picks the "
              "state up when it starts.")
    else:
        print("Sessions stop messaging each other at their next step (the SendMessage "
              "guard blocks it now). Unanswered questions stay in QUESTIONS.md; carry "
              "them yourself.")


def status():
    p = _load(PROJECT)
    r = _load(RELAY, {}) or {}
    if not p:
        print("project: NOT INITIALISED (./setup.sh init <ProjectName>)")
        return
    print(f"project:  {p['project']}")
    print(f"relay:    {'ON' if r.get('enabled') else 'OFF'}"
          + (f"  (changed {r['changed']} by {r.get('by', '?')})" if r.get("changed") else ""))
    print(f"concept:  {p['concept_session']}")
    for bid, folder in boards():
        print(f"{bid}:   {name(bid)}  ({folder}/)")
    print(f"mode:     {p.get('permission_mode', 'default')}  (both sessions must share it)")


def brief(side):
    """Printed by the SessionStart hook; lands in the session's context."""
    p = _load(PROJECT)
    if not p:
        print("[relay] This project is not initialised (kicad_project/project.json missing). "
              "The relay is unavailable; never message other sessions.")
        return
    on = enabled()
    board = os.environ.get("HW_BOARD", "")
    if side == "concept":
        me = p["concept_session"]
    elif board:
        me = p["board_session_prefix"] + board
    else:
        me = (p["board_session_prefix"] + "boardN -- this session was not started with "
              "bin/board, so it does not know its board: ask the user which one, and "
              "suggest restarting with bin/board <boardN>")
    lines = [f"[relay] Project {p['project']}. This session's name should be {me}."]
    if side == "board" and board:
        folders = [f for b, f in boards() if b == board]
        lines.append(f"[relay] You are the board session for {board}: your folder is "
                     f"kicad_project/{folders[0] if folders else board + '_?'}/ and no other.")
    lines.append(f"[relay] The relay is {'ON' if on else 'OFF'} (kicad_project/relay.json; "
                 f"the user flips it with bin/relay on|off).")
    if on:
        lines.append("[relay] Follow kicad_project/RELAY_PROTOCOL.md. "
                     + ("Arm every live board session listed by ListAgents."
                        if side == "concept" else
                        f"Now, at start: if QUESTIONS.md has something unanswered, ring "
                        f"{p['concept_session']} with it; otherwise send it "
                        f"'{board or 'boardN'} armed' (protocol sections 3 and 7). After that, "
                        "ring only for a handoff defect, after writing it in QUESTIONS.md."))
    else:
        lines.append("[relay] Do NOT message other sessions. "
                     + ("Report published revisions to the user."
                        if side == "concept" else
                        "Write questions in QUESTIONS.md and tell the user."))
    print("\n".join(lines))


def init(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="relay.py init")
    ap.add_argument("project")
    ap.add_argument("--permission-mode", default=None)
    ap.add_argument("--kicad-python", default=None)
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,30}", a.project):
        sys.exit("project name: 2 to 31 letters and digits, starting with a letter "
                 "(it becomes part of the session names)")
    old = _load(PROJECT, {}) or {}
    data = {"project": a.project,
            "concept_session": f"{a.project}_Concept",
            "board_session_prefix": f"{a.project}_Kicad_",
            "permission_mode": a.permission_mode or old.get("permission_mode", "default")}
    _save(PROJECT, data)
    local = _load(LOCAL, {}) or {}
    local["kicad_python"] = a.kicad_python or local.get("kicad_python", "/usr/bin/python3")
    _save(LOCAL, local)
    if not RELAY.exists():
        _save(RELAY, {"enabled": False, "changed": None, "by": None})
    print(json.dumps({**data, **local}, indent=2))


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]
    if cmd == "status":
        status()
    elif cmd in ("on", "off"):
        set_enabled(cmd == "on")
    elif cmd == "name" and len(rest) == 1:
        print(name(rest[0]))
    elif cmd == "brief" and rest and rest[0] in ("concept", "board"):
        brief(rest[0])
    elif cmd == "init":
        init(rest)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
