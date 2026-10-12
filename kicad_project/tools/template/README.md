# `tools/template` — a template-owned fix goes to the template first

Both sessions use it; it is plain `python3` and git. The practice it serves
is `kicad_project/SELF_IMPROVEMENT.md` ("Giving it back"): a lesson captured
in a template-owned file — a tool, a skill, a contract, the handoff publisher —
is committed to the template remote and pushed, and the project takes it back
as a merge. No pull request, no private copy of a template file.

    python3 kicad_project/tools/template/giveback.py status            # from the repo root (concept)
    python3 tools/template/giveback.py status                          # from kicad_project/ (a board session)

| command | does |
|---|---|
| `status` | fetches `template/main` and lists every mechanism file that differs from it, and every new file under a mechanism dir the template lacks. Exit 1 when there is something to give back. Run it at every stop for review |
| `open` | a detached worktree of `template/main` in the system temp dir, outside the project tree, and prints its path. Edit the template's copy there of anything that carries project fills (`CLAUDE.md`, `README.md`): copying those would carry the project's text |
| `push PATH... -m MSG` | `open`, copy those project files or directories into the worktree at the same paths, then `finish` |
| `finish -m MSG` | commit in the worktree (the repo's last author when git has no identity), push to `template/main`, remove the worktree, fetch, and merge `template/main` into the project as `Merge template: <subject>` |
| `abort` | remove the worktree; nothing pushed |

**Mechanism vs data.** Mechanism is every path in the template's tree except
the data prefixes in the script's header (`design/`, `history/`, `reference/`,
the studies, the diagram generators, `tools/handoff/boards|answers|fabs/`),
plus a new file under `kicad_project/tools/`, either `.claude/`,
`tools/handoff/`, `tools/parts/`, `tools/setup/` or `bin/`. Data never travels;
`push` refuses it. A fix is made generic before it travels — the board's
numbers stay in the board folder.

**The merge is proven, not assumed.** `finish` stashes the project's
uncommitted edits to the files the merge touches, merges, and drops the stash
only when the merged tree equals them; an untracked copy identical to the
template's is removed first, a different one stops the merge. A conflict
aborts the merge, restores the stash and reports: the template commit is
pushed, the project merge is yours to do by hand.

The worktree is outside the project's folders, so the project's walls do not
apply there: a board session, which cannot edit the project's
`kicad_project/.claude/skills/`, edits the template's copy in the worktree
and the merge brings the skill home.
