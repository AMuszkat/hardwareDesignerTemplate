#!/usr/bin/env python3
"""giveback.py -- a template-owned fix goes to the template first, then merges back.

Both sessions run it, from anywhere inside the repository (plain python3, git
only). It is the mechanism behind "Giving it back" in SELF_IMPROVEMENT.md:

    giveback.py status                 mechanism files that differ between this
                                       project and template/main (fetches first)
    giveback.py open                   a worktree of template/main, outside the
                                       project tree; prints its path. Edit there
                                       what carries project fills (CLAUDE.md,
                                       README.md) instead of copying it.
                                       One worktree PER SESSION (named after
                                       HW_BOARD, or "concept"): two sessions
                                       giving back at once never share one.
    giveback.py push PATH... -m MSG    open, copy those project files into the
                                       worktree at the same paths, finish
    giveback.py finish -m MSG          commit in the worktree, push to the
                                       template remote, remove the worktree,
                                       fetch, merge template/main into the project
    giveback.py abort                  remove the worktree, nothing pushed

Mechanism = every path in the template's tree outside the DATA prefixes below,
plus a new file under a MECHANISM_DIR (a tool, a skill) the template does not
have yet. Data (a board module, a study, a design page, a fab profile) never
travels. A FILLED file carries project text and is refused by `push`: open the
worktree and edit the template's copy.

`finish` refuses to merge over uncommitted edits it cannot prove harmless: it
stashes the project's dirty copies of the files the merge touches, merges, and
drops the stash only when the merged tree equals them. A conflict aborts the
merge and is reported; nothing is pushed twice.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

REMOTE = "template"
BRANCH = "main"
DATA = ("design/", "history/", "reference/", "studies/baselines/", "studies/reports/",
        "studies/designpoint.py", "studies/components_", "studies/s0", "studies/s1", "studies/s2",
        "tools/diagrams/", "tools/handoff/boards/", "tools/handoff/answers/", "tools/handoff/reviews/",
        "tools/handoff/fabs/",
        "kicad_project/project.json", "kicad_project/relay.json", ".gitignore", "LICENSE")
DATA_KEEP = ("tools/diagrams/sheet.py", "tools/diagrams/check_diagrams.py",
             "tools/diagrams/test_diagrams.py", "tools/handoff/fabs/_blank.py")
FILLED = ("CLAUDE.md", "README.md", "studies/README.md", "design/DIAGRAMS.md")
MECHANISM_DIRS = ("kicad_project/tools/", "kicad_project/.claude/", ".claude/", "tools/handoff/",
                  "tools/parts/", "tools/setup/", "tools/review/", "bin/")
NOT_MECHANISM = ("kicad_project/.claude/konnect", "kicad_project/.claude/agents/",
                 "kicad_project/.claude/skills/kicad-")   # Konnect's generated files


def git(*args, cwd=None, check=True, env=None):
    r = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, env=env)
    if check and r.returncode:
        sys.exit(f"git {' '.join(args)} failed:\n{r.stderr.strip() or r.stdout.strip()}")
    return r


def root():
    return git("rev-parse", "--show-toplevel").stdout.strip()


def is_mechanism(path):
    if path.startswith(NOT_MECHANISM):
        return False
    if path in DATA_KEEP:
        return True
    return not path.startswith(DATA)


def template_tree(repo):
    return set(git("ls-tree", "-r", "--name-only", f"{REMOTE}/{BRANCH}", cwd=repo).stdout.split())


def identity(repo):
    """The environment a template commit is made in.  The template is public:
    unless this repository's git identity already uses a GitHub noreply
    address, the commit carries the identity of the template's own last commit
    -- a project's global user.email (a private address) must never reach it."""
    r = subprocess.run(["git", "config", "user.email"], cwd=repo, text=True, capture_output=True)
    mail = r.stdout.strip()
    env = dict(os.environ)
    if mail.lower().endswith("@users.noreply.github.com"):
        return env
    name, email = git("log", "-1", "--format=%an%n%ae", f"{REMOTE}/{BRANCH}", cwd=repo).stdout.split("\n")[:2]
    env.update(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
    print(f"committing as {name} <{email}> (the template's identity; this repository's user.email is "
          f"{mail or 'unset'})")
    return env


def owner():
    """Whose worktree: a board session's (bin/board exports HW_BOARD) or concept's.
    One fixed path for every session once let a board session's `push` commit the
    concept session's half-done edits under its own message, and delete them."""
    return os.environ.get("HW_BOARD") or "concept"


def worktree_path(repo, who=None):
    return os.path.join(tempfile.gettempdir(),
                        f"{os.path.basename(repo)}.template-worktree.{who or owner()}")


def open_worktrees(repo):
    """Every giveback worktree of this repository, any session's."""
    pre = os.path.join(tempfile.gettempdir(), os.path.basename(repo) + ".template-worktree")
    listing = git("worktree", "list", "--porcelain", cwd=repo).stdout
    return [ln[len("worktree "):] for ln in listing.splitlines()
            if ln.startswith("worktree " + pre)]


def current_worktree(repo):
    wt = worktree_path(repo)
    listing = git("worktree", "list", "--porcelain", cwd=repo).stdout
    return wt if f"worktree {wt}\n" in listing else None


def fetch(repo):
    git("fetch", "--quiet", REMOTE, cwd=repo)


def cmd_status(repo, _a):
    fetch(repo)
    tree = template_tree(repo)
    drift = []
    for p in sorted(tree):
        if not is_mechanism(p) or not os.path.exists(os.path.join(repo, p)):
            continue
        blob = git("show", f"{REMOTE}/{BRANCH}:{p}", cwd=repo).stdout
        with open(os.path.join(repo, p), errors="replace") as f:
            if f.read() != blob:
                drift.append((p, "project fills -- edit the template's copy (open)" if p in FILLED
                              else "differs -- a lesson not given back, or a fill that belongs in a data file"))
    new = []
    for d in MECHANISM_DIRS:
        out = git("ls-files", "--others", "--cached", "--exclude-standard", d, cwd=repo).stdout.split()
        new += [p for p in out if p not in tree and is_mechanism(p)]
    for p, why in drift:
        print(f"  {p:<52} {why}")
    for p in sorted(set(new)):
        print(f"  {p:<52} not in the template -- new; `push` it or it stays private")
    for wt in open_worktrees(repo):
        mine = wt == worktree_path(repo)
        print(f"  worktree open at {wt} -- " + ("yours: `finish -m MSG` or `abort`" if mine
                                               else "another session's: leave it alone"))
    if not drift and not new:
        print("TEMPLATE: nothing to give back")
    return 1 if drift or new else 0


def cmd_open(repo, _a, say=True):
    wt = current_worktree(repo)
    if wt is None:
        fetch(repo)
        wt = worktree_path(repo)
        if os.path.exists(wt):
            shutil.rmtree(wt)
        git("worktree", "add", "--quiet", "--detach", wt, f"{REMOTE}/{BRANCH}", cwd=repo)
    if say:
        print(wt)
        return 0          # a string return would be sys.exit's message, and exit 1
    return wt


def cmd_push(repo, a):
    if not a.message:
        sys.exit("push needs -m MSG")
    wt = current_worktree(repo)
    if wt and git("status", "--porcelain", cwd=wt).stdout.strip():
        sys.exit(f"your worktree at {wt} holds edits from an earlier `open`: `finish -m MSG` "
                 "or `abort` it first -- `push` would commit them under this message")
    wt = cmd_open(repo, a, say=False)
    tree = template_tree(repo)
    for p in a.paths:
        rel = os.path.relpath(os.path.abspath(p), repo)
        if rel.startswith(".."):
            sys.exit(f"{p}: outside the repository")
        if rel in FILLED:
            sys.exit(f"{rel} carries project fills: `open` and edit the template's copy at {wt}/{rel}")
        if not is_mechanism(rel):
            sys.exit(f"{rel}: data, not mechanism -- it stays in the project")
        if rel not in tree and not rel.startswith(MECHANISM_DIRS):
            sys.exit(f"{rel}: not in the template and not under a mechanism dir")
        src, dst = os.path.join(repo, rel), os.path.join(wt, rel)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", ".venv", "*.pyc"))
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        print(f"copied  {rel}")
    return cmd_finish(repo, a)


def cmd_finish(repo, a):
    if not a.message:
        sys.exit("finish needs -m MSG")
    wt = current_worktree(repo)
    if wt is None:
        sys.exit("no worktree open: `open` or `push` first")
    git("add", "-A", cwd=wt)
    if not git("status", "--porcelain", cwd=wt).stdout.strip():
        sys.exit("nothing to give back: the worktree equals template/main")
    env = identity(repo)
    git("commit", "--quiet", "-m", a.message, cwd=wt, env=env)
    sha = git("rev-parse", "--short", "HEAD", cwd=wt).stdout.strip()
    files = git("diff", "--name-only", "HEAD~1", "HEAD", cwd=wt).stdout.split()
    # the template's own privacy check, on what this commit would publish: the
    # worktree's files and the commit's identity -- a FAIL leaves the commit
    # undone, the files staged, and nothing pushed
    chk = os.path.join(wt, "tools", "setup", "check_public.py")
    r = subprocess.run([sys.executable, chk, "--rev", "HEAD"], cwd=wt, env=env, text=True, capture_output=True)
    if r.returncode:
        git("reset", "--quiet", "--soft", "HEAD~1", cwd=wt)
        sys.exit(f"check-public refuses this commit; nothing pushed, the files stay staged in {wt}:\n"
                 + "\n".join(ln for ln in r.stdout.splitlines() if ln.startswith(("FAIL", "WARN")) or "FAIL" in ln))
    git("push", "--quiet", REMOTE, f"HEAD:{BRANCH}", cwd=wt)
    print(f"template {sha} pushed to {REMOTE}/{BRANCH}: {len(files)} file(s)")
    git("worktree", "remove", "--force", wt, cwd=repo)
    fetch(repo)

    # the project takes it back as a merge, over nothing it cannot prove harmless.
    # Touched = what the template changed since the last merge -- NOT every file
    # that differs from the template, which is the project's own data too
    base = git("merge-base", "HEAD", f"{REMOTE}/{BRANCH}", cwd=repo).stdout.strip()
    touched = set(git("diff", "--name-only", base, f"{REMOTE}/{BRANCH}", cwd=repo).stdout.split())
    dirty = {l[3:] for l in git("status", "--porcelain", "--untracked-files=all", cwd=repo).stdout.splitlines()}
    tracked_dirty = [p for p in touched & dirty
                     if git("ls-files", "--error-unmatch", p, cwd=repo, check=False).returncode == 0]
    for p in touched & dirty:
        if p in tracked_dirty:
            continue
        with open(os.path.join(repo, p), errors="replace") as f:
            same = f.read() == git("show", f"{REMOTE}/{BRANCH}:{p}", cwd=repo).stdout
        if not same:
            sys.exit(f"{p}: untracked in the project and different from the template's -- move it aside, "
                     f"then `git merge {REMOTE}/{BRANCH}` yourself")
        os.remove(os.path.join(repo, p))
    stashed = False
    if tracked_dirty:
        git("stash", "push", "--quiet", "-m", "giveback: before the template merge", "--", *tracked_dirty, cwd=repo)
        stashed = True
    subject = a.message.splitlines()[0]
    r = git("merge", "--no-ff", "--quiet", "-m", f"Merge template: {subject}",
            f"{REMOTE}/{BRANCH}", cwd=repo, check=False, env=env)
    if r.returncode:
        git("merge", "--abort", cwd=repo, check=False)
        if stashed:
            git("stash", "pop", "--quiet", cwd=repo, check=False)
        sys.exit(f"the merge conflicts; template {sha} is pushed. Merge by hand:\n{r.stdout}{r.stderr}")
    if stashed:
        if git("diff", "--quiet", "stash@{0}", "HEAD", "--", *tracked_dirty, cwd=repo, check=False).returncode:
            sys.exit("merged, but the merged tree differs from your uncommitted edits to\n  "
                     + "\n  ".join(tracked_dirty) + "\nthey are in `git stash list`; reconcile by hand")
        git("stash", "drop", "--quiet", cwd=repo)
    print(f"project {git('rev-parse', '--short', 'HEAD', cwd=repo).stdout.strip()}: merged {REMOTE}/{BRANCH}")
    return 0


def cmd_abort(repo, _a):
    wt = current_worktree(repo)
    if wt is None:
        sys.exit("no worktree open")
    git("worktree", "remove", "--force", wt, cwd=repo)
    print("worktree removed; nothing pushed")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("open")
    p = sub.add_parser("push"); p.add_argument("paths", nargs="+"); p.add_argument("-m", "--message")
    p = sub.add_parser("finish"); p.add_argument("-m", "--message")
    sub.add_parser("abort")
    a = ap.parse_args()
    repo = root()
    if not git("remote", "get-url", REMOTE, cwd=repo, check=False).stdout.strip():
        sys.exit(f"no `{REMOTE}` remote: git remote add {REMOTE} <the template's URL> (SETUP.md §8)")
    return {"status": cmd_status, "open": cmd_open, "push": cmd_push,
            "finish": cmd_finish, "abort": cmd_abort}[a.cmd](repo, a)


if __name__ == "__main__":
    sys.exit(main())
