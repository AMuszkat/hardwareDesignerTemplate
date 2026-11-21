#!/usr/bin/env python3
"""tmpclean.py -- free /tmp of the Claude Code session folders nobody needs any more.

    python3 tools/tmpclean.py                      what would go, with sizes (nothing is deleted)
    python3 tools/tmpclean.py --go                 delete it
    python3 tools/tmpclean.py --live NAME ... --go also empty a LIVE session's scratchpad (see below)

Every session keeps a folder /tmp/claude-<uid>/<project>/<session id>/ (scratchpad/, tasks/).  /tmp is a
tmpfs here: what lies in it is RAM, under a user quota, and a board session's scratch copies are 45-290 MB each
(board1: 10.8 GB, and `route.py apply` died half way on `Disk quota exceeded`).

What goes:
  * the whole folder of a session with NO live process (its id is in no ~/.claude/sessions/<pid>.json whose
    pid is running) -- a session that ended, or one that was resumed under a new folder;
  * with --live NAME (a session's name or id, as often as needed): the entries of that live session's
    scratchpad/ whose newest file is older than --keep-min minutes (default 30).  Its tasks/ folder, and
    anything it wrote in the last half hour, stay.  Name only a session whose scratch you know is spent --
    a board session keeps its evidence in <board>/tmpWork/, so its scratch copies are.
What never goes: a live session not named with --live; anything outside /tmp/claude-<uid>/; a symlink's
target (the link itself is removed, never followed).

Exit 0 done or dry run, 2 it cannot tell which sessions are live (no session file found): nothing is touched.
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path


def size_mb(p):
    n = 0
    for d, _, files in os.walk(p, followlinks=False):
        for f in files:
            try:
                n += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    return n / 1e6


def newest(p):
    t = os.lstat(p).st_mtime
    for d, _, files in os.walk(p, followlinks=False):
        for f in files:
            try:
                t = max(t, os.lstat(os.path.join(d, f)).st_mtime)
            except OSError:
                pass
    return t


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--go", action="store_true", help="delete (without it: list only)")
    ap.add_argument("--live", action="append", default=[], metavar="NAME", help="a live session (name or id) whose scratchpad is emptied too")
    ap.add_argument("--keep-min", type=float, default=30, help="with --live: keep scratch entries written in the last N minutes (default 30)")
    ap.add_argument("--root", default=f"/tmp/claude-{os.getuid()}", help=argparse.SUPPRESS)
    ap.add_argument("--sessions", default=str(Path.home() / ".claude" / "sessions"), help=argparse.SUPPRESS)
    a = ap.parse_args()
    root = Path(a.root).resolve()

    live = {}                                                   # session id -> name
    files = sorted(Path(a.sessions).glob("*.json"))
    for f in files:
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if f.stem.isdigit() and Path("/proc", f.stem).exists() and d.get("sessionId"):
            live[d["sessionId"]] = d.get("name") or d.get("cwd") or "?"
    if not live:
        print(f"tmpclean: no live session found in {a.sessions} -- cannot tell which folders are in use; nothing touched")
        return 2
    wanted = {i for i, n in live.items() if i in a.live or n in a.live}
    unknown = [x for x in a.live if x not in live and x not in live.values()]
    if unknown:
        print(f"tmpclean: --live {unknown}: not a live session (live: {sorted(live.values())}); nothing touched")
        return 2

    targets, kept = [], []                                      # (path, MB, why)
    for proj in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()):
        for s in sorted(p for p in proj.iterdir() if p.is_dir() and not p.is_symlink()):
            if not (s / "scratchpad").exists() and not (s / "tasks").exists():
                continue                                        # not a session folder (bash-edit-diff, probes)
            if s.name not in live:
                targets.append((s, size_mb(s), "no live process"))
            elif s.name in wanted:
                sp = s / "scratchpad"
                for e in sorted(sp.iterdir()) if sp.is_dir() else []:
                    age = (time.time() - newest(e)) / 60 if not e.is_symlink() else 1e9
                    if age >= a.keep_min:
                        targets.append((e, 0 if e.is_symlink() else size_mb(e), f"scratch of {live[s.name]}"))
                    else:
                        kept.append((e, size_mb(e), f"written {age:.0f} min ago"))
            else:
                kept.append((s, size_mb(s), f"LIVE: {live[s.name]} (name it with --live to empty its scratchpad)"))

    total = sum(t[1] for t in targets)
    for p, mb, why in targets:
        print(f"{'delete' if a.go else 'would delete'} {mb:8.0f} MB  {p}  [{why}]")
    for p, mb, why in kept:
        print(f"keep         {mb:8.0f} MB  {p}  [{why}]")
    if not a.go:
        print(f"\nDRY RUN: {len(targets)} entries, {total:.0f} MB would go. Add --go to delete.")
        return 0
    freed = 0
    for p, mb, _ in targets:
        rp = p if p.is_symlink() else p.resolve()
        if root not in rp.parents:                              # never outside the root
            print(f"SKIPPED (not under {root}): {p}")
            continue
        try:
            if p.is_symlink() or p.is_file():
                p.unlink()
            else:
                shutil.rmtree(p)
            freed += mb
        except OSError as e:
            print(f"FAILED {p}: {e}")
    print(f"\ndeleted {freed:.0f} of {total:.0f} MB in {len(targets)} entries under {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
