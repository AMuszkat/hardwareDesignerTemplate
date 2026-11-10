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
  * THE WIRING: `tools/setup/render_settings.py`, run in a skeleton copy of
    the repository (a path with a space in it), writes the hook into both
    `settings.local.json` files as the same command; `--check` then passes,
    fails again once the hook is taken out, and the rendered command -- run
    the way Claude Code runs it, through a shell -- blocks the incident's
    loop, passes `ls`, and exits 0 when the script has gone missing.

The guard is board-free and stdlib-only, so there is nothing to skip.  The
last line is PASS / FAIL with the counts and the slowest call.
"""
import json
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
    """render_settings.py in a skeleton copy -> the list of what is wrong."""
    bad = []
    tmp = Path(tempfile.mkdtemp(prefix="test_bashguard_"))
    try:
        root = tmp / "a project"
        (root / "tools" / "setup").mkdir(parents=True)
        (root / "kicad_project" / "tools" / "session").mkdir(parents=True)
        (root / "design").mkdir()
        rs = root / "tools" / "setup" / "render_settings.py"
        shutil.copy(ROOT / "tools" / "setup" / "render_settings.py", rs)
        guard = root / "kicad_project" / "tools" / "session" / "bashguard.py"
        shutil.copy(HOOK, guard)

        def render(*args):
            r = subprocess.run([sys.executable, str(rs), *args], capture_output=True, text=True, cwd=str(root))
            return r.returncode, r.stdout + r.stderr

        if render("--check")[0] != 1:
            bad.append("render_settings --check passes before anything is rendered")
        rc, out = render()
        if rc != 0:
            return bad + [f"render_settings failed: {out.strip()[-200:]}"]
        rc, out = render("--check")
        if rc != 0:
            bad.append(f"render_settings --check fails right after a render: {out.strip()}")
        cmds = []
        for f in (root / ".claude" / "settings.local.json", root / "kicad_project" / ".claude" / "settings.local.json"):
            pre = json.loads(f.read_text()).get("hooks", {}).get("PreToolUse", [])
            mine = [h["command"] for e in pre if e.get("matcher") == "Bash" for h in e["hooks"]]
            if len(mine) != 1:
                bad.append(f"{f.relative_to(root)}: {len(mine)} Bash hooks rendered, want 1")
            cmds += mine
        if len(set(cmds)) != 1:
            return bad + ["the two files do not carry the same hook command (a board session would run it twice)"]
        cmd = cmds[0]

        def fire(command):
            r = subprocess.run(["sh", "-c", cmd], input=event(command), capture_output=True, text=True, cwd=str(root))
            return r.returncode, r.stderr

        rc, err = fire(INCIDENT)
        if rc != 2 or "bgwait.py wait" not in err:
            bad.append(f"the rendered hook does not block the incident's loop (exit {rc})")
        if fire("ls -la")[0] != 0:
            bad.append("the rendered hook refuses `ls -la`")
        # a second render keeps the user's own hooks and leaves one guard
        f = root / ".claude" / "settings.local.json"
        d = json.loads(f.read_text())
        d["hooks"]["PreToolUse"].insert(0, {"matcher": "Bash", "hooks": [
            {"type": "command", "command": "echo mine"},
            {"type": "command", "command": "python3 /old/place/kicad_project/tools/session/bashguard.py"}]})
        f.write_text(json.dumps(d))
        if render("--check")[0] != 1:
            bad.append("--check passes with a guard hook of another location in the file")
        render()
        d = json.loads(f.read_text())
        got = [h["command"] for e in d["hooks"]["PreToolUse"] for h in e["hooks"]]
        if got != ["echo mine", cmd]:
            bad.append(f"a re-render did not keep the user's hook and one guard: {got}")
        d["hooks"]["PreToolUse"] = [e for e in d["hooks"]["PreToolUse"] if e["hooks"][0]["command"] != cmd]
        f.write_text(json.dumps(d))
        rc, out = render("--check")
        if rc != 1 or "Bash guard" not in out:
            bad.append("--check passes with the guard hook taken out")
        guard.unlink()
        rc, err = fire(INCIDENT)
        if rc != 0:
            bad.append(f"with the script missing the rendered hook exits {rc}: it would block Bash (want 0)")
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
          f"commands passed, {len(HARMLESS)} malformed events exit 0; render_settings wires the same hook into both "
          f"settings.local.json and its --check sees it missing; slowest hook call {slowest * 1000:.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
