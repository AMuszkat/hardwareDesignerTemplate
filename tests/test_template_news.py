#!/usr/bin/env python3
"""
`relay.py brief` names what the template merges of the last days brought, so a resumed session
learns that a tool or a rule moved under it.

    python3 tests/test_template_news.py

A temp repository: a "template" history with three commits (a route tool, a root-side tool, a
skill), merged into a "project" the way giveback.py does it (`commit-tree -p HEAD -p template`,
subject `Merge template: ...`).  What must hold:

  * the concept brief names all three template commits, newest first;
  * the board brief names only the two that touch kicad_project/;
  * a project commit of its own is never named;
  * with no merge in the window, and outside a git repository, the brief says nothing of it
    and still exits 0 (a hook must never stop a session).

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
SRC = ROOT / "kicad_project" / "tools" / "session" / "relay.py"
BAD = []


def expect(what, ok):
    if not ok:
        BAD.append(what)


def git(repo, *a, **env):
    e = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
             GIT_COMMITTER_EMAIL="t@example.com", **env)
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True, env=e).stdout.strip()


def brief(kp, side):
    e = {k: v for k, v in os.environ.items() if k not in ("HW_BOARD", "CLAUDE_ENV_FILE")}
    r = subprocess.run([sys.executable, str(kp / "tools" / "session" / "relay.py"), "brief", side],
                       input=json.dumps({"session_id": "S", "source": "startup"}), capture_output=True, text=True,
                       env=dict(e, HW_BOARD="board1"), cwd=str(kp), timeout=30)
    return r.returncode, r.stdout


def commit(repo, path, text, subject):
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", subject)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="news-"))
    try:
        repo = tmp / "proj"
        kp = repo / "kicad_project"
        (kp / "tools" / "session").mkdir(parents=True)
        shutil.copy(SRC, kp / "tools" / "session")
        shutil.copy(SRC.parents[1] / "boardsel.py", kp / "tools")
        (kp / "project.json").write_text(json.dumps({"project": "T", "concept_session": "T_Concept",
                                                     "board_session_prefix": "T_Kicad_"}))
        (kp / "board1_main").mkdir()
        (kp / "board1_main" / "x").write_text("x")

        rc, out = brief(kp, "board")
        expect("outside a git repository the brief still runs", rc == 0 and "board session for board1" in out)
        expect("outside a git repository: no template line", "[template]" not in out)

        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base")
        rc, out = brief(kp, "concept")
        expect("no merge in the window: no template line", rc == 0 and "[template]" not in out)

        git(repo, "checkout", "-q", "-b", "tpl")
        commit(repo, "kicad_project/tools/route/README.md", "a", "route README: ALPHA lesson")
        commit(repo, "tools/measure/x.py", "b", "measure: BETA lesson")
        commit(repo, "kicad_project/.claude/skills/routing/SKILL.md", "c", "routing skill: GAMMA lesson")
        git(repo, "checkout", "-q", "main")
        commit(repo, "design/x.md", "d", "a project commit: DELTA")
        tree = git(repo, "merge-tree", "--write-tree", "HEAD", "tpl").splitlines()[0]
        m = git(repo, "commit-tree", tree, "-p", "HEAD", "-p", "tpl", "-m", "Merge template: routing skill: GAMMA lesson")
        git(repo, "merge", "--ff-only", "-q", m)

        rc, out = brief(kp, "concept")
        news = [l for l in out.splitlines() if l.startswith("[template]")]
        expect(f"concept: three template commits named, newest first: {news}",
               rc == 0 and len(news) == 4 and "3 change(s)" in news[0] and "GAMMA" in news[1] and "BETA" in news[2]
               and "ALPHA" in news[3])
        expect("a project commit is never named", "DELTA" not in out)
        rc, out = brief(kp, "board")
        news = [l for l in out.splitlines() if l.startswith("[template]")]
        expect(f"board: only the two that touch kicad_project/: {news}",
               rc == 0 and len(news) == 3 and "GAMMA" in news[1] and "ALPHA" in news[2] and "BETA" not in out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for b in BAD:
        print("  " + b)
    print("FAIL: " + BAD[0] if BAD else "PASS: the brief names the template's merged changes, per side, and stays silent without them")
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
