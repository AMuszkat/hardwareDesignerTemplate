---
name: study
description: >
  Answer a design question with a runnable study in studies/: size or select a
  value, prove robustness (worst case, Monte Carlo, temperature, supply range),
  check a budget against a hard line, compare options, or model behaviour --
  then move the result into SPECS, the baseline, the value bus and HISTORY.
  Triggers on "run a study", "simulate", "sweep", "worst case", "tolerance",
  "Monte Carlo", "how sensitive", "budget", "thermal", "is there margin",
  "which value", "compare options", "trade study", "derive", "headline",
  "--check drifted".
---

# A study, from question to published number

`studies/README.md` is the full guide; this is the procedure.

## 1. Frame it

- Write the question so a number or a yes/no answers it, and say what the
  answer will **decide**: a SPECS row, a value-bus line, a STATUS decision, a
  handoff layout rule.
- Search `history/HISTORY.md` for it first. If it was done, say so and quote
  the entry rather than re-running it blind.
- If a datasheet page already states the number, cite the page. No study is
  needed.

## 2. Inputs before code

Every input the study needs goes into `studies/designpoint.py` **first**, as a
`PARAMS` row with its source: the SPECS section, a datasheet page, or the
study that set it. A part that already exists on a board is read from that
board's value bus with `common.part(board, ref)`, never copied. No local
constants: `kw()` raising on an unknown name is what stops a forgotten
default deciding a headline.

## 3. Write it

Copy `studies/_template.py` to `studies/sNN_<name>.py`, using the next free
number. Fill the header (Question / Decides / Inputs / Headline), with
`analyse()` shared by `main()` and `headline()`.

- Deterministic: fixed seeds, one generator per parallel job (`par.pmap`).
- Against a hard line, compute the **worst case**; add statistics for "how
  often". Evaluate budgets at the corner, never at the typical point.
- Report the **achieved** value after snapping to buyable parts.
- A method that sizes or checks a *kind* of part — above all one found after
  a first method failed — is a generic function in `studies/common.py` (or a
  module beside it) with every project number passed in; the study calls it.
  That function is the template's: give it back
  (`kicad_project/SELF_IMPROVEMENT.md`). The study, with its numbers, stays here.
- Plots go to `out/` via `common.out()`; `main()` ends with one
  `-> conclusion` line.

## 4. Run and read

    .venv/bin/python studies/run_all.py --only sNN_<name>

Read the result before believing it. Does it pass a sanity hand-calculation?
Does the margin rule hold, not just the line? A surprising number is a bug
until shown otherwise.

## 5. Move the result into its homes

The study prints; **you** move the homes, in one pass:

1. `design/SPECS.md`: the achieved number, source `sNN_<name>`, **H** if it
   is a hard line.
2. `headline()` keys `<tag>.<metric>_<unit>`, then
   `run_all.py --check` (expect NEW keys), then `--check --bless`. Blessing
   is a claim you looked.
3. A sized or selected part: the board's value bus, status `proposed`, the
   study in the note, the numbers in the sixth field. Delete the design-point
   row it replaces.
4. A result that commits the design (a part, a topology, a margin accepted):
   that is a **decision**. Put it to the user, then follow the `decision`
   skill (STATUS row, generators, handoff).
5. A result that constrains layout: a `LAYOUT` row in the board's data module,
   citing the study, then publish.
6. `history/HISTORY.md`: `### Study N — <title>` — the question, the method,
   the numbers, what it rules out. **Negative results included**: "no class
   passes" is the entry that saves the next week.

## 6. When `--check` drifts

Every DRIFT, NEW or GONE line means either the model moved and SPECS must
follow, or something broke. Find out which (`HW_JOBS=1` first, if a parallel
number disagrees with a serial one). Then fix the code, or update SPECS and
bless. Never bless to make the check pass.
