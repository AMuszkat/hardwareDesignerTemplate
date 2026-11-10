#!/usr/bin/env python3
"""
The board wall never parks a session on a permission prompt (ROADMAP W1).

    python3 tests/test_wall.py

A scratch kicad_project/ with two board folders and the real hook scripts
(tools/session/wall.py, relay.py, tools/boardsel.py); synthetic hook events
are piped in, as Claude Code does.  Asserted:

  * {own folder, another board, handoff/} x {HW_BOARD set; unset with this
    session's claim; unset, no claim, two boards; unset, one board}: the exit
    code of each (0 = allowed, 2 = blocked), and that NO board-side output
    carries "permissionDecision": "ask" -- the prompt two live board sessions
    sat on for 20 hours each;
  * `relay.py brief board` writes the claim when HW_BOARD is set, names the
    board from the claim when it is not (and exports HW_BOARD into the
    session's shell through CLAUDE_ENV_FILE), and prints the refusal line
    when there is neither;
  * the tools' own wall (boardsel.resolve) follows the exported HW_BOARD.

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
SRC = ROOT / "kicad_project" / "tools"
BAD = []


def hook(kp, script, args, event, **env):
    e = {k: v for k, v in os.environ.items() if k not in ("HW_BOARD", "CLAUDE_ENV_FILE")}
    e.update(env)
    r = subprocess.run([sys.executable, str(kp / "tools" / "session" / script)] + args, input=json.dumps(event),
                       capture_output=True, text=True, env=e, cwd=str(kp), timeout=30)
    return r.returncode, r.stdout, r.stderr


def expect(what, got, want):
    if got != want:
        BAD.append(f"{what}: got {got!r}, want {want!r}")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="wall-"))
    try:
        kp = tmp / "kicad_project"
        (kp / "tools" / "session").mkdir(parents=True)
        shutil.copy(SRC / "boardsel.py", kp / "tools")
        for f in ("wall.py", "relay.py"):
            shutil.copy(SRC / "session" / f, kp / "tools" / "session")
        (kp / "project.json").write_text(json.dumps({"project": "T", "concept_session": "T_Concept",
                                                     "board_session_prefix": "T_Kicad_"}))
        for b in ("board1_main", "board2_power"):
            (kp / b / "handoff").mkdir(parents=True)
        targets = {"own": kp / "board1_main" / "x.kicad_sch", "other": kp / "board2_power" / "x.kicad_sch",
                   "handoff": kp / "board1_main" / "handoff" / "01.md"}

        def wall(which, sid, **env):
            ev = {"session_id": sid, "cwd": str(kp), "tool_name": "Edit", "tool_input": {"file_path": str(targets[which])}}
            rc, out, err = hook(kp, "wall.py", ["board"], ev, **env)
            if "permissionDecision" in out:
                BAD.append(f"wall {which} ({env or 'HW_BOARD unset'}, session {sid}) ASKS: {out.strip()[:120]}")
            return rc, err

        n = 0
        # 1. HW_BOARD set
        for which, want in (("own", 0), ("other", 2), ("handoff", 2)):
            expect(f"HW_BOARD set / {which}", wall(which, "S-env", HW_BOARD="board1")[0], want); n += 1
        # 2. the brief with HW_BOARD writes the claim; the same session without HW_BOARD keeps its board
        rc, out, _ = hook(kp, "relay.py", ["brief", "board"], {"session_id": "S-claim", "source": "startup"}, HW_BOARD="board1")
        claims = json.loads((kp / "local.sessions.json").read_text())
        expect("claim written", (claims.get("S-claim") or {}).get("board"), "board1")
        for which, want in (("own", 0), ("other", 2), ("handoff", 2)):
            expect(f"unset, claim / {which}", wall(which, "S-claim")[0], want); n += 1
        envf = tmp / "env.sh"
        rc, out, _ = hook(kp, "relay.py", ["brief", "board"], {"session_id": "S-claim", "source": "resume"},
                          CLAUDE_ENV_FILE=str(envf))
        expect("brief on resume names the board", "board session for board1" in out and "claim" in out, True)
        expect("brief on resume exports HW_BOARD", envf.read_text().strip() if envf.exists() else "", "export HW_BOARD=board1")
        # 3. unset, no claim, two boards: blocked with the line to run, never asked
        for which in ("own", "other", "handoff"):
            rc, err = wall(which, "S-stranger")
            expect(f"unset, no claim, two boards / {which}", rc, 2); n += 1
            if which == "own":
                expect("the refusal names the fix", "bin/board boardN --resume" in err, True)
        rc, out, _ = hook(kp, "relay.py", ["brief", "board"], {"session_id": "S-stranger", "source": "resume"})
        expect("brief with no board says REFUSED", "REFUSED" in out and "bin/board boardN --resume" in out, True)
        # a claim for a board that no longer exists is no claim
        (kp / "local.sessions.json").write_text(json.dumps({"S-gone": {"board": "board9", "at": "2026-01-01T00:00:00"}}))
        expect("claim for a missing board", wall("own", "S-gone")[0], 2)
        # a broken claim file never crashes the hook: it blocks
        (kp / "local.sessions.json").write_text("{ not json")
        expect("broken claim file", wall("own", "S-claim")[0], 2)
        # the tools' wall follows HW_BOARD (what the brief exports on resume)
        code = "import sys; sys.path.insert(0, 'tools'); import boardsel; print(boardsel.resolve(None).name)"
        e = {k: v for k, v in os.environ.items() if k != "HW_BOARD"}
        r = subprocess.run([sys.executable, "-c", code], cwd=str(kp), capture_output=True, text=True, env=e)
        expect("boardsel, two boards, HW_BOARD unset", r.returncode, 2)
        r = subprocess.run([sys.executable, "-c", code], cwd=str(kp), capture_output=True, text=True, env={**e, "HW_BOARD": "board2"})
        expect("boardsel with HW_BOARD", r.stdout.strip(), "board2_power")
        # 4. one board only: it is that board's session
        shutil.rmtree(kp / "board2_power")
        (kp / "local.sessions.json").unlink()
        for which, want in (("own", 0), ("handoff", 2)):
            expect(f"unset, one board / {which}", wall(which, "S-only")[0], want); n += 1
        # the concept side still asks for the two files that are the user's (no stall was measured there)
        ev = {"session_id": "C", "cwd": str(tmp), "tool_input": {"file_path": str(kp / "RELAY_PROTOCOL.md")}}
        rc, out, _ = hook(kp, "wall.py", ["concept"], ev)
        expect("concept side, RELAY_PROTOCOL.md", (rc, "permissionDecision" in out), (0, True))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if BAD:
        print("\n".join(BAD))
        print(f"FAIL: {len(BAD)} wall case(s): {BAD[0]}")
        return 1
    print(f"PASS: {n} wall cases (own / other board / handoff x HW_BOARD set, claim, no claim, one board): "
          "exit codes right and no permission prompt; the brief writes the claim, names the board on a resume "
          "and exports HW_BOARD; no claim -> refused with the line to run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
