# SELF_IMPROVEMENT — how a session improves the template

**Scope:** when a lesson learned during a session is owed to the template,
where each kind of lesson goes, and how the user sees it. Both sessions read
this file; **neither edits it in the project** — it is the user's, like
`RELAY_PROTOCOL.md`, and changes through the template (Giving it back) at
their request. **Not here:** the lessons themselves — they live in the tools,
the skills and the READMEs this file points at.

## The rule

**A method that failed on the first attempt and worked on the second with a
different approach — or a tool, a script or a skill built because none
existed for the job — that the next design, with other parts and other
numbers, would need too, is written where the next session reads it before
its first attempt, the moment it works, and goes to the template.**

Two ways in, and one test every candidate must pass:

1. **Second attempt.** Something was tried, failed, and a different method
   worked. The second method is the lesson.
2. **Missing capability.** Nothing did the job, so the session built it — a
   script, a check, a skill, a generic function — and it worked, perhaps the
   first time. The thing built is the lesson; no failure is needed.
3. **Reuse — the test.** The next project — other parts, other numbers,
   other boards — would need the same method. That makes it *process*: a
   tool, a script, a skill step, a check, a generic calculation, a publisher
   feature. A *design fact* fails this test however hard it was to find.

This is the ordinary engineering retrospective (the blameless postmortem,
"lessons learned"), scoped to one agent session, with one hard trigger
instead of a meeting. Next time is a fresh context: a lesson "kept in mind"
is lost with the transcript, and the next session pays for it again.

## What is not a lesson

- **A design fact**: a capacitor value, a net, a pin, a part, a layout
  distance, a fab number. It belongs in the design pages, the value bus, the
  generator and the handoff. A board session's question about it is handoff
  work, answered through the generator, and never a template change — a
  question from the board to concept about a regulator's capacitor is the
  canonical non-example.
- A job done the first time with what already existed — an existing tool, a
  skill step as written. It is already where it belongs.
- A board-specific number (it belongs in the board folder).
- A workaround for a bug that has since been fixed.

## The trigger

A session keeps a running table from its first tool call, two columns: *what
failed first, or was missing* / *what worked*. The hard rule is the second
column: **when a second method works, or something built because nothing
existed does the job, and it passes the reuse test, the row is captured
within the same turn** — a README bullet, a skill line, a script, a generic
function, a gate moved earlier. A thing built new goes to the template as it
is built, not once it has failed somewhere: the schematic planner, the
placement checker and `giveback.py` itself entered that way. Three softer
triggers join it:

- the same fact about a tool had to be looked up twice → README or skill;
- the same multi-call sequence was typed twice → a script with an exit code;
- a gate fired after more than one unit of work had been done → the gate
  moves earlier (one sheet, one group, one symbol at a time).

At every stop for the user's review — a board session: a schematic, a
placement or routing group; concept: a study's result, a decision, a publish
— **the report ends with the table, one line per row: *failed first or missing /
worked / captured in*.** The user sees the template move and can veto a
change. A row captured nowhere is reported as such, and the user says where
it goes.

## Where each kind of lesson goes — board session

| what was learned | where it goes |
|---|---|
| a fact about a tool ("X cannot do Y; do Z") | the tool's README, one bullet: the trap, the way around it, how to prove it |
| a procedure (an order of steps that works) | the skill, in the step where it is needed — a board session cannot edit the project's `.claude/skills/`; it edits the template's copy (Giving it back) and the merge brings it home |
| a mechanical sequence done twice by hand | a script with an exit code, in `kicad_project/tools/`, generic: the board's numbers stay in the board folder |
| a check that caught a defect late | the same check, earlier — after each sheet, each group, each symbol |
| a guess the user had to settle | `AskUserQuestion` with a recommendation; the answer in memory |
| a defect in the handoff (a missing value, a contradiction) | `QUESTIONS.md` — design work, not a template lesson; never a local patch |

## Where each kind of lesson goes — concept session

The same two tests. Concept's work is design (studies, parts, decisions,
diagrams) and handoff (the publisher); both produce process lessons. The
board sessions' questions almost never do: they ask for design facts.

| what was learned | where it goes |
|---|---|
| a calculation method that sizes or checks a *kind* of part, found after a first method failed | a generic function in `studies/common.py` (or a module beside it) with every project number passed in; the study that calls it stays in the project |
| a way to find a part or fetch a datasheet that worked where the first did not | `tools/parts/` and the `datasheet` skill |
| a diagram technique (a layout, a notation, a check) | `tools/diagrams/sheet.py` and `check_diagrams.py`, never one generator |
| a kind of fact the publisher could not carry — prose where the board needed data, a connector shape with no row type | `publish.py` / `board_template.py`, so every board's page carries it (`netlist.json` is the model). The value itself stays in the board's data module |
| a rule both boards needed about how to read the handoff | the publisher's `00_READ_FIRST` text, not two answers |
| a fact about a concept tool, a procedure, a sequence typed twice | README bullet, skill step, script — as for the board session |
| a board's question about a value, a net, a pin, a part | **not here**: the data module, the value bus, the answer (`answer-question` skill) |

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
one commit, through `kicad_project/tools/template/giveback.py` (its README has
the details; a board session runs it as `tools/template/giveback.py`):

    giveback.py status                  # mechanism files that differ from template/main, and new ones
    giveback.py push PATH... -m MSG     # copy these project files into a worktree of template/main,
                                        # commit, push to the template, merge template/main back
    giveback.py open                    # the worktree alone, to edit the template's copy of a file
    giveback.py finish -m MSG           # that carries project fills (CLAUDE.md, README.md); then finish

The report's retro rows name the template commit. The worktree is outside the
project's folders, so the project's walls do not apply there: this is also how
a board session's lesson reaches a skill — it edits the template's copy under
`kicad_project/.claude/skills/`, and the merge brings the skill home. A fix is
made generic before it travels; the board's numbers stay in the board folder.
`finish` proves the merge: the project's uncommitted edits to the files the
merge touches are stashed, merged over and compared before the stash is
dropped; a conflict aborts and is reported.

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
