# SELF_IMPROVEMENT — how a session improves the template

**Scope:** when a lesson learned during a session is owed to the template,
where each kind of lesson goes, and how the user sees it. Both sessions read
this file; **neither edits it** (it is the user's, like `RELAY_PROTOCOL.md`).
**Not here:** the lessons themselves — they live in the READMEs, the skills
and the tools this file points at.

## The rule

**Anything that failed on the first attempt and worked on the second with a
different method is written where the next session will read it before its
first attempt — the moment the second attempt works, not at the end of the
task.**

This is the ordinary engineering retrospective (the blameless postmortem,
"lessons learned"), scoped to one agent session and with one hard trigger
instead of a meeting. Next time is a fresh context: a lesson "kept in mind"
is lost with the transcript, and the next session pays for it again.

What is NOT a lesson: a thing that worked the first time; a board-specific
number (it belongs in the board folder); a workaround for a bug that has
since been fixed.

## The trigger

A session keeps a running table from its first tool call, two columns: *what
failed first* / *what worked*. The hard rule is the second column: **when a
second method works, the row is captured within the same turn** — a README
bullet, a skill line, a script, a gate moved earlier. Three softer triggers
join it:

- the same fact had to be looked up twice → README or skill;
- the same multi-call sequence was typed twice → a script with an exit code;
- a gate fired after more than one unit of work had been done → the gate
  moves earlier (one sheet, one group, one symbol at a time).

At every stop for the user's review — a board session: a schematic, a
placement or routing group; concept: a publish — **the report ends with the
table, one line per row: *failed first / worked second / captured in*.** The
user sees the template move and can veto a change. A row captured nowhere is
reported as such, and the user says where it goes.

## Where each kind of lesson goes — board session

| what was learned | where it goes |
|---|---|
| a fact about a tool ("X cannot do Y; do Z") | the tool's README, one bullet: the trap, the way around it, how to prove it |
| a procedure (an order of steps that works) | the skill, in the step where it is needed — a board session cannot edit the project's `.claude/skills/`; it edits the template's copy (Giving it back) and the merge brings it home |
| a mechanical sequence done twice by hand | a script with an exit code, in `kicad_project/tools/`, generic: the board's numbers stay in the board folder |
| a check that caught a defect late | the same check, earlier — after each sheet, each group, each symbol |
| a guess the user had to settle | `AskUserQuestion` with a recommendation; the answer in memory |
| a defect in what concept produced | `QUESTIONS.md` — never a local patch of the handoff |

## Where each kind of lesson goes — concept session

Concept's product is the handoff; its failures arrive as board questions and
as `invalidates:` lines. **The trigger is the board's `QUESTIONS.md`**: every
question answered is a retro row — *which page should have carried this
before it was asked* — and the generator change comes before the answer.

| what was learned | where it goes |
|---|---|
| a page lacked a fact the board needed | the generator — `tools/handoff/boards/boardN.py` when only this board needs it, `board_template.py` / `publish.py` when every board does — not only the answer |
| prose the board session had to transcribe by hand | the same thing as data, with a parity tool on the board side (`netlist.json` is the model; it is complete from the first publish of a board whose pin tables exist) |
| an answer that invalidated drawn work | the `RELEASE_NOTE` names the board gate that shows the effect (`sch.py parity --handoff`, `place.py sync`, `place.py drc`), beside the work |
| the same question from two boards | a `00_READ_FIRST` rule or a generator default |
| a datasheet read wrongly on the concept side | the page cites page and table, so the board's `verify` re-reads the same spot |
| a fact about a concept tool, a procedure, a sequence typed twice | README bullet, skill step, script — as for the board session |

## Giving it back — the template first, the project by merge

A lesson captured in a template-owned file — a tool under
`kicad_project/tools/`, a skill, either `CLAUDE.md`, this file, the handoff
publisher — belongs to every project started from the template, not to this
one. **It is committed to the template remote and pushed, and the project
takes it back as a merge**, the same way it takes any template update
(`SETUP.md` §8). A project is a different remote from the template
(`https://github.com/AMuszkat/hardwareDesignerTemplate`, named `template` in
the project's remotes); the project's own facts never travel, and no project
keeps a private copy of a template file.

At each stop for the user's review, the turn's template-owned lessons go as
one commit, from a worktree outside the project tree:

    git fetch template
    git worktree add <scratch>/tpl template/main          # a checkout of the template
    cp <changed template-owned files> <scratch>/tpl/<same paths>
    git -C <scratch>/tpl add -A
    git -C <scratch>/tpl commit -F <message file>         # what failed, what works, where it is written
    git -C <scratch>/tpl push template HEAD:main
    git worktree remove <scratch>/tpl
    git fetch template && git merge template/main         # the project: "Merge template: <subject>"

The report's retro rows name the template commit. The worktree is outside the
project's folders, so the project's walls do not apply there: this is also how
a board session's lesson reaches a skill — it edits the template's copy under
`kicad_project/.claude/skills/`, and the merge brings the skill home. A fix is
made generic before it travels; the board's numbers stay in the board folder.
A project whose working tree holds uncommitted edits to the same files puts
them aside first (`git stash push -- <paths>`), merges, and checks that the
merged tree equals them before dropping the stash.

## Worked example — a schematic redraw, the first project on this template (2026-10)

Eight sheets, 311 parts, redrawn to the schematic standard after the
handoff's `netlist.json` arrived. Every row happened once the slow way.

| failed first | worked second | captured in |
|---|---|---|
| Konnect cannot position a symbol's fields; the check needs them at each unit's corner | `symfields.py` (parsed `.kicad_sym` tree, `kicad-cli` resave proof, backup) | `tools/sch/symfields.py`, README |
| `create_symbol` grows and centres the body; widths probed with throwaway symbols | two-pass: create, read the resolved pin x, re-create shifted | `schematic` skill, Symbols |
| placed parts kept the sheet's stale copy of a rebuilt symbol (old pins) | `update_symbols_from_library` + `reset_schematic_field_positions` after placement | `emit.py` emits them; README |
| `batch_edit_schematic_components` silently skipped MPN / DNP ("Field not found") | `add_component_annotation` for a field the symbol does not have | `emit.py`; `sch.py drawn` checks fields; README |
| root-sheet pins all facing right → 24 left-edge pins shorted to right-edge signals; found by `parity` after 787 calls | `import_sheet_pins(side=left)` + `edit_sheet_pin`; a gate after each sheet | `emit.py`; `sch.py drawn`; skill §3 |
| `plan` ignored the board's waivers, so a waived finding read as PLAN FAILS | the preview check applies `schematic.json` | `emit.py` |
| `Device:R`'s Value inside the body ran over its wires | a derived `<board>:R` with the Value beside; the user chose it | `symfields.py --derive`; skill |
| a stock multi-unit symbol's fields sat on a unit's pin numbers | a board copy, same pins, origin boxes | board lib; skill |
| a board build script named `selectors.py` shadowed the stdlib module `tools/sch` imports | the build dir stripped from `sys.path`, then the script renamed | the naming rule in `kicad_project/CLAUDE.md` |
| eight sheets drawn one after another would have taken hours | one agent per sheet, each gated by `drawn`; the root last | skill §3, `draw_sheet.md` |
