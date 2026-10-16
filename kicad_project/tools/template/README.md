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
| `finish -m MSG` | commit in the worktree, run `tools/setup/check_public.py --rev HEAD` on it (a FAIL undoes the commit; nothing pushed), push to `template/main`, remove the worktree, fetch, and `merge` |
| `merge [-m MSG]` | fetch, and merge `template/main` into the project as `Merge template: <subject>` (the template's last subject by default): how a project takes template updates |
| `abort` | remove the worktree; nothing pushed |

**Identity.** The template is public. Unless the repository's `user.email`
is a GitHub noreply address, a commit is made as the newest noreply identity
in the template's history, never as the template's last commit: one pushed by
an older giveback carried a private address, and copying "the last commit's"
identity would have spread it to every later one. No noreply commit at all →
it stops and says how to set yours.

**Mechanism vs data.** Mechanism is every path in the template's tree except
the data prefixes in the script's header (`design/`, `history/`, `reference/`,
the studies, the diagram generators, `tools/handoff/boards|answers|fabs/`),
plus a new file under `kicad_project/tools/`, either `.claude/`,
`tools/handoff/`, `tools/parts/`, `tools/setup/` or `bin/`. Data never travels;
`push` refuses it. A fix is made generic before it travels — the board's
numbers stay in the board folder.

**The merge is proven, not assumed.** The merged tree is computed first
(`git merge-tree`, git 2.40+), so nothing moves until it is known. A conflict
is reported and changes nothing; the template commit stays pushed and the
project merge is yours to do by hand. An uncommitted copy of a file the merge
changes must already equal the merged content (it does after a `push`):
equal, it is set aside, the merge comes in, and the copy is dropped only when
it equals the merged file; different, the merge stops before it starts.

**A rewritten template history.** A privacy re-commit (a private address
replaced by the noreply one) keeps every tree and changes every id. Git's
merge base then falls back to the last commit both histories still share,
days old, and every template file changed since conflicts: a plain
`git merge template/main` once conflicted in nine files. `merge` finds the
twin: the template commit whose tree equals the newest template commit the
project merged and the template no longer holds, and merges on it.

The worktree is outside the project's folders, so the project's walls do not
apply there: a board session, which cannot edit the project's
`kicad_project/.claude/skills/`, edits the template's copy in the worktree
and the merge brings the skill home.

**One worktree per session**: `<tmp>/<repo>.template-worktree.<HW_BOARD or
concept>`. With one shared path, a board session's `push` once reused the
concept session's open worktree, committed its half-done files under the
board's message, pushed them and removed the worktree. `push` now refuses
while your own worktree holds edits from an earlier `open` (finish or abort
it), and `status` lists every session's open worktree.
