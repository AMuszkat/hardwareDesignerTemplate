#!/usr/bin/env python3
"""
Write the machine-specific halves of the two sessions' settings:

    .claude/settings.local.json                 (concept session)
    kicad_project/.claude/settings.local.json   (board sessions)

    python3 tools/setup/render_settings.py [--check]

Both are gitignored, because they hold this clone's ABSOLUTE path.  The
committed `settings.json` beside each carries everything portable (hooks, MCP
servers, allow rules).  Re-run after moving or re-cloning the repo, and after
merging a template version that renders something new; `--check` exits 1 when
either file is missing, names another location, or lacks something below.

The board file carries the board sessions' wall (deny rules) and keeps the root
CLAUDE.md out of their context.  The concept file carries no rules on purpose:
a session started in kicad_project/ loads the ROOT's settings.local.json deny
rules too, so the concept wall is a hook instead (kicad_project/tools/session/
wall.py concept, declared in .claude/settings.json).

ONE HOOK IS RENDERED HERE, into both files: the Bash guard
(kicad_project/tools/session/bashguard.py), which refuses a loop that waits
for a process by NAME (`while pgrep -f ...; do sleep N; done` matches its own
shell and never ends) and names `tools/bgwait.py wait`.  It is the same for
both session types, so it is written once, with this clone's absolute path,
as the SAME command string in both files: a board session loads both, and
Claude Code runs identical hook commands once.  The command tests that the
script exists first -- `python3 <missing file>` exits 2, and exit 2 from a
PreToolUse hook blocks the call, so a renamed script would otherwise block
every Bash command of every session.  An existing project gets the hook by
re-rendering (`./setup.sh render-settings`) and restarting its sessions.

⚠ ABSOLUTE PATHS IN PERMISSION RULES TAKE TWO SLASHES.  `Read(//home/u/x/**)`
is absolute; `Read(/home/u/x/**)` is resolved RELATIVE TO THE SETTINGS FILE
and silently matches nothing -- a wall that is not there, and nothing reports
it.  Every path below is written `//` + the absolute path.

Merging, not overwriting: entries this script manages are recognised and
replaced -- those naming this clone, and those left behind by a clone that has
since moved (same rule, a root that no longer holds this project; any hook
command that runs a `tools/session/bashguard.py`); anything else in the file
(your own local allows, your own hooks) is kept.

The concept-only list is read off the repo root each time: every top-level
entry except kicad_project/ and .git.  A folder you add later (firmware/,
mechanical/) is therefore covered at the next render, and `--check` says when
one is not.
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KP = ROOT / "kicad_project"
A = "//" + str(ROOT).lstrip("/")          # permission-rule absolute prefix

def concept_only():
    """Repo-root items a board session may not read or edit: EVERYTHING at the
    root except kicad_project/.  It works from its handoff; anything it needs
    from here is a QUESTIONS.md entry."""
    always = ["CLAUDE.md", "README.md", "SETUP.md", "setup.sh", "verify.sh", "requirements.txt",
              ".mcp.json", "design/**", "history/**", "studies/**", "tools/**",
              "reference/**", "out/**", "bin/**", ".claude/**", ".venv/**"]
    found = [e.name + ("/**" if e.is_dir() else "") for e in sorted(ROOT.iterdir())
             if e.name not in ("kicad_project", ".git")]
    return list(dict.fromkeys(always + found))


# kicad_project items a board session reads but never edits: its handoff, the
# switch and the names, the protocol and its contract, and everything that
# defines the session itself (hooks, skills, MCP servers, the interpreter).
BOARD_READ_ONLY = ["kicad_project/*/handoff/**", "kicad_project/project.json",
                   "kicad_project/relay.json", "kicad_project/local.json",
                   "kicad_project/local.sessions.json",
                   "kicad_project/RELAY_PROTOCOL.md", "kicad_project/CLAUDE.md",
                   "kicad_project/.mcp.json", "kicad_project/.claude/**",
                   "kicad_project/tools/session/**", "kicad_project/tools/boardsel.py"]


GUARD = KP / "tools" / "session" / "bashguard.py"
GUARD_MARK = "tools/session/bashguard.py"      # how a rendered guard hook is recognised, whatever its root


def bash_guard():
    """The PreToolUse entry for the Bash guard: absolute, the same string in
    both files (run once by a session that loads both), and harmless when the
    script is not there."""
    g = shlex.quote(str(GUARD))
    return {"matcher": "Bash",
            "hooks": [{"type": "command", "command": f"[ -f {g} ] || exit 0; exec python3 {g}"}]}


def is_guard(hook):
    return isinstance(hook, dict) and GUARD_MARK in str(hook.get("command", ""))


def rules(kinds, rels):
    return [f"{k}({A}/{r})" for r in rels for k in kinds]


def concept_local():
    # Deliberately EMPTY: a session started in kicad_project/ also loads the
    # root's settings.local.json deny rules, so a concept wall written here
    # would wall the board sessions out of their own folders.  The concept
    # side's wall is a hook in .claude/settings.json (tools/session/wall.py
    # concept), which board sessions do not load.
    return {"deny": [], "claudeMdExcludes": []}


def board_local():
    # Edit(...) covers every file-writing tool (Edit, Write, NotebookEdit);
    # Claude Code ignores Write(...) path rules.
    return {"deny": rules(("Read", "Edit"), concept_only()) + rules(("Edit",), BOARD_READ_ONLY),
            # Claude Code loads CLAUDE.md from every parent directory; the root
            # one is the CONCEPT contract and must not reach a board session.
            "claudeMdExcludes": [str(ROOT / "CLAUDE.md")]}


_SUFFIXES = None


def managed(entry):
    """Ours to replace: an entry naming this clone, or one a previous location
    of it left behind (one of our rules under a root that is not this project
    any more).  A rule the user wrote for some other tree is left alone."""
    global _SUFFIXES
    if str(ROOT) in entry:
        return True
    if _SUFFIXES is None:
        _SUFFIXES = sorted(set(concept_only() + BOARD_READ_ONLY), key=len, reverse=True)
    m = re.fullmatch(r"(?:Read|Edit)\(//(.*)\)", entry)
    path = "/" + m.group(1) if m else entry
    if not m and path.endswith("/CLAUDE.md"):
        return not os.path.exists(path)                    # a stale CLAUDE.md exclude
    for suf in _SUFFIXES:
        if path.endswith("/" + suf):
            base = path[: -len(suf) - 1]
            return not os.path.isdir(os.path.join(base, "kicad_project", "tools", "session"))
    return False


def merge(path, want):
    try:
        with open(path) as f:
            cur = json.load(f)
    except FileNotFoundError:
        cur = {}
    perms = cur.setdefault("permissions", {})
    perms["deny"] = [e for e in perms.get("deny", []) if not managed(e)] + want["deny"]
    ex = [e for e in cur.get("claudeMdExcludes", []) if not managed(e)] + want["claudeMdExcludes"]
    if ex:
        cur["claudeMdExcludes"] = ex
    else:
        cur.pop("claudeMdExcludes", None)
    # the Bash guard: drop every rendered copy of it (this location's or an
    # older one's), keep every other hook of the file, append the current one
    hooks = cur.setdefault("hooks", {})
    pre = []
    for entry in hooks.get("PreToolUse", []):
        if isinstance(entry, dict) and any(is_guard(h) for h in entry.get("hooks", [])):
            rest = [h for h in entry["hooks"] if not is_guard(h)]
            if not rest:
                continue
            entry = dict(entry, hooks=rest)
        pre.append(entry)
    hooks["PreToolUse"] = pre + [bash_guard()]
    return cur


def guard_state(cur):
    """None when the file runs exactly the current Bash guard, once; else what is wrong."""
    want = bash_guard()["hooks"][0]["command"]
    found = [(e.get("matcher"), h.get("command")) for e in (cur.get("hooks") or {}).get("PreToolUse", [])
             if isinstance(e, dict) for h in e.get("hooks", []) if is_guard(h)]
    if not found:
        return "the Bash guard hook (tools/session/bashguard.py) is not declared"
    if any(c != want for _m, c in found):
        return "the Bash guard hook names another location (or an older form of the command)"
    if any(m != "Bash" for m, _c in found):
        return "the Bash guard hook is not matched on Bash"
    return None


TARGETS = [(ROOT / ".claude" / "settings.local.json", concept_local),
           (KP / ".claude" / "settings.local.json", board_local)]


def check():
    bad = []
    for path, fn in TARGETS:
        try:
            with open(path) as f:
                cur = json.load(f)
        except FileNotFoundError:
            bad.append(f"{path.relative_to(ROOT)}: missing")
            continue
        have = set(cur.get("permissions", {}).get("deny", []))
        want = fn()
        miss = [e for e in want["deny"] if e not in have]
        if miss:
            bad.append(f"{path.relative_to(ROOT)}: {len(miss)} rule(s) missing or for another "
                       f"location (e.g. {miss[0]})")
        ex = [e for e in want["claudeMdExcludes"] if e not in cur.get("claudeMdExcludes", [])]
        if ex:
            bad.append(f"{path.relative_to(ROOT)}: the root CLAUDE.md is not excluded -- board "
                       "sessions would load the concept contract")
        stale = [e for e in list(have) + cur.get("claudeMdExcludes", [])
                 if managed(e) and str(ROOT) not in e]
        if stale:
            bad.append(f"{path.relative_to(ROOT)}: {len(stale)} entr(ies) left from a previous "
                       "location of this repo")
        g = guard_state(cur)
        if g:
            bad.append(f"{path.relative_to(ROOT)}: {g} -- a loop waiting on `pgrep -f` would not be refused")
    if not GUARD.is_file():
        bad.append(f"{GUARD.relative_to(ROOT)}: missing (the rendered hook passes everything without it)")
    return bad


def main(argv):
    if "--check" in argv:
        bad = check()
        for b in bad:
            print(b)
        return 1 if bad else 0
    for path, fn in TARGETS:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = merge(path, fn())
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        print(f"wrote {path.relative_to(ROOT)} ({len(data['permissions']['deny'])} deny rules, the Bash guard hook)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
