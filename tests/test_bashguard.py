#!/usr/bin/env python3
"""
The Bash guard (kicad_project/tools/session/bashguard.py) against synthetic
PreToolUse events, each one piped to the hook as Claude Code pipes it.

    python3 tests/test_bashguard.py

What must hold:
  * the incident's exact loop is BLOCKED (exit 2, the message names
    `bgwait.py wait`), and so are its relatives: `until`, `pgrep` without
    -f, `ps | grep`, `pidof`, the bracket trick, the loop inside `bash -c`,
    inside a here-document fed to a shell, inside `$( )`;
  * the legitimate forms PASS (exit 0, nothing printed): a one-shot pgrep, a
    loop on a pid (`kill -0`, `ps -p`, `pgrep -P`), `wait`, `tail --pid`, a
    loop on a file, and the pattern as TEXT (a commit message, an echo, a
    grep, a here-document that writes a README, a comment);
  * the hook never blocks by failing: broken JSON, an event with no command,
    another tool's event all exit 0;
  * THE WIRING: both committed settings files (`.claude/settings.json`,
    `kicad_project/.claude/settings.json`) declare the hook once, on Bash;
    each command -- run the way Claude Code runs it, through a shell, with
    CLAUDE_PROJECT_DIR set to that session's folder (here a copy under a path
    with a space in it) -- blocks the incident's loop, passes `ls`, and exits
    0 when the script has gone missing (`python3 <missing file>` exits 2, and
    exit 2 from a PreToolUse hook would block every Bash call).

The guard is board-free and stdlib-only, so there is nothing to skip.  The
last line is PASS / FAIL with the counts and the slowest call.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "kicad_project" / "tools" / "session" / "bashguard.py"

INCIDENT = 'while pgrep -f "netgroup.py --board board1 routing/R2_emmc_"; do sleep 20; done'

BLOCKED = [
    INCIDENT,
    'while pgrep -f "netgroup.py --board board1 routing/R2_emmc_" > /dev/null; do sleep 20; done; tail -5 routing/*.log',
    'until ! pgrep -f netgroup >/dev/null 2>&1; do sleep 5; done',
    'while [ -n "$(pgrep -f \'netgroup.py\')" ]; do sleep 10; done',
    'while true; do if ! pgrep -x kicad-cli >/dev/null; then break; fi; sleep 10; done',
    'while ps aux | grep -v grep | grep -q netgroup; do sleep 30; done',
    'while [ "$(ps -ef | grep "[n]etgroup" | wc -l)" -gt 0 ]; do sleep 30; done',
    'while pidof kicad-cli >/dev/null; do sleep 2; done',
    "timeout 600 bash -c 'while pgrep -f netgroup.py; do sleep 5; done'",
    'cd kicad_project && tools/kpy tools/route/netgroup.py a.json > a.log 2>&1 &\n'
    'while pgrep -f "[n]etgroup.py"; do\n  sleep 20\ndone\ntail a.log',
    'bash <<EOF\nwhile pgrep -f foo; do sleep 1; done\nEOF',
    'while `pgrep -f foo >/dev/null`; do sleep 1; done',
    'sleep 2; while pgrep -f "route.py check" >/dev/null; do sleep 15; done; echo done',
]

PASSES = [
    'pgrep -f "netgroup.py"',
    'pgrep -fa netgroup; sleep 5; pgrep -fa netgroup',
    'ps aux | grep netgroup',
    'while kill -0 $pid 2>/dev/null; do sleep 5; done',
    'tools/kpy x.py & pid=$!; while kill -0 "$pid"; do sleep 1; done; wait $pid',
    'tail --pid=$(cat run.pid) -f /dev/null',
    'wait $pid',
    'until [ -f routing/R2_plan.json ]; do sleep 5; done',
    'while [ ! -s out.log ]; do sleep 1; done',
    'while ps -p $pid > /dev/null; do sleep 1; done',
    'while ps -p $pid | grep -q python; do sleep 1; done',
    'while pgrep -P $pid >/dev/null; do sleep 1; done',
    'while pgrep -F run.pid >/dev/null; do sleep 1; done',
    'for f in *.json; do pgrep -f "$f" || echo none; sleep 1; done',
    'git commit -m "route README: wait by pid, not while pgrep -f x; do sleep 20; done"',
    "grep -n 'while pgrep -f' README.md && sleep 1",
    'echo "while pgrep -f foo; do sleep 1; done"',
    "cat > README.md <<'EOF'\nnever: while pgrep -f \"x\"; do sleep 20; done\nEOF",
    "python3 - <<'EOF'\ncmd = 'while pgrep -f x; do sleep 20; done'\nEOF",
    'while read p; do kill $p; sleep 1; done < <(pgrep -f foo)',
    'pgrep -f foo | while read p; do echo $p; sleep 1; done',
    'while read l; do echo $l; done < a; pgrep -f foo; while [ ! -f x ]; do sleep 1; done',
    'python3 kicad_project/tools/bgwait.py wait R2_emmc_a R2_emmc_b --timeout 100',
    'python3 tools/bgwait.py run --name R2_emmc_a -- tools/kpy tools/route/netgroup.py --board board1 routing/R2_emmc_a.json',
    'while pgrep -f foo >/dev/null; do echo still; break; done',
    '# while pgrep -f foo; do sleep 1; done\nls',
    'x=1 # note\nwhile [ $x -lt 3 ]; do x=$((x+1)); sleep 1; done; pgrep foo',
]

# (label, raw stdin): the hook must exit 0 on every one -- it may not block by failing
HARMLESS = [
    ("broken JSON", "{not json"),
    ("empty stdin", ""),
    ("no command", json.dumps({"tool_name": "Bash", "tool_input": {}})),
    ("command not a string", json.dumps({"tool_name": "Bash", "tool_input": {"command": ["while", "pgrep"]}})),
    ("another tool", json.dumps({"tool_name": "Write", "tool_input": {"command": INCIDENT}})),
    ("unbalanced quotes", json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo \"while $(pgrep -f 'x"}})),
]


def hook(stdin):
    t = time.perf_counter()
    r = subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout, r.stderr, time.perf_counter() - t


def event(command):
    return json.dumps({"session_id": "test", "hook_event_name": "PreToolUse", "tool_name": "Bash",
                       "cwd": str(ROOT / "kicad_project"), "tool_input": {"command": command}})


def wiring():
    """The two committed settings files -> the list of what is wrong."""
    bad = []
    tmp = Path(tempfile.mkdtemp(prefix="test_bashguard_"))
    try:
        root = tmp / "a project"
        guard = root / "kicad_project" / "tools" / "session" / "bashguard.py"
        guard.parent.mkdir(parents=True)
        shutil.copy(HOOK, guard)
        for rel, project in ((".claude/settings.json", root), ("kicad_project/.claude/settings.json", root / "kicad_project")):
            pre = json.loads((ROOT / rel).read_text()).get("hooks", {}).get("PreToolUse", [])
            mine = [h["command"] for e in pre if e.get("matcher") == "Bash" for h in e["hooks"]
                    if "bashguard.py" in h.get("command", "")]
            if len(mine) != 1:
                bad.append(f"{rel}: {len(mine)} Bash guard hooks declared, want 1")
                continue
            cmd = mine[0]

            def fire(command):
                r = subprocess.run(["sh", "-c", cmd], input=event(command), capture_output=True, text=True,
                                   cwd=str(project), env={"PATH": os.environ.get("PATH", ""),
                                                          "CLAUDE_PROJECT_DIR": str(project)})
                return r.returncode, r.stderr

            rc, err = fire(INCIDENT)
            if rc != 2 or "bgwait.py wait" not in err:
                bad.append(f"{rel}: the hook does not block the incident's loop (exit {rc})")
            if fire("ls -la")[0] != 0:
                bad.append(f"{rel}: the hook refuses `ls -la`")
            guard.rename(guard.with_suffix(".gone"))
            rc, err = fire(INCIDENT)
            guard.with_suffix(".gone").rename(guard)
            if rc != 0:
                bad.append(f"{rel}: with the script missing the hook exits {rc}: it would block Bash (want 0)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return bad


def main():
    if not HOOK.is_file():
        print(f"FAIL: {HOOK} is missing")
        return 1
    bad, slowest = [], 0.0
    for cmd in BLOCKED:
        rc, out, err, dt = hook(event(cmd))
        slowest = max(slowest, dt)
        if rc != 2:
            bad.append(f"not blocked (exit {rc}): {cmd!r}")
        elif "bgwait.py wait" not in err or "--timeout" not in err:
            bad.append(f"blocked without naming `bgwait.py wait ... --timeout`: {cmd!r}")
        elif out:
            bad.append(f"blocked with text on stdout: {cmd!r}")
    for cmd in PASSES:
        rc, out, err, dt = hook(event(cmd))
        slowest = max(slowest, dt)
        if rc != 0 or out or err:
            bad.append(f"refused (exit {rc}): {cmd!r}")
    for label, raw in HARMLESS:
        rc, out, err, dt = hook(raw)
        slowest = max(slowest, dt)
        if rc != 0:
            bad.append(f"{label}: exit {rc}, a failing guard must exit 0")
    wired = wiring()
    bad += ["wiring: " + w for w in wired]
    rc, _, err, _ = hook(event(INCIDENT))
    print(f"the incident's loop: exit {rc}\n" + "\n".join("    " + ln for ln in err.strip().splitlines()))
    for b in bad:
        print("  BAD " + b)
    if bad:
        print(f"FAIL: {len(bad)} check(s) of {len(BLOCKED) + len(PASSES) + len(HARMLESS)} events and the wiring; "
              f"first: {bad[0]}")
        return 1
    print(f"PASS: {len(BLOCKED)} name-wait loops blocked (the incident's among them), {len(PASSES)} legitimate "
          f"commands passed, {len(HARMLESS)} malformed events exit 0; both settings.json declare the hook, "
          f"and it passes everything when the script is missing; slowest hook call {slowest * 1000:.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
