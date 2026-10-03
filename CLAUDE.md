# CLAUDE.md

Rules for working in this repository. **Scope:** how the sessions work,
what owns what, how to run things, and the working rules that keep the
docs and the design honest. **Not here:** what the product is
(`design/IMPLEMENTATION.md`), its numbers (`design/SPECS.md`), what is
open (`design/STATUS.md`), why decisions were made (`history/HISTORY.md`),
how the sessions talk to each other (`kicad_project/RELAY_PROTOCOL.md`).

## The project, in two sentences

**(Fill in: what this product is and the one idea that makes it work.)**
Start with `design/IMPLEMENTATION.md` and the sheets in `design/diagrams/`.

## Repo map — one fact, one home

| path | holds | owner |
|---|---|---|
| `design/SPECS.md` | numbers only, each with its source; hard lines flagged **H** | concept |
| `design/IMPLEMENTATION.md` | how it works, as it is now | concept |
| `design/STATUS.md` | decided / open / blocking / deliberate / roadmap — the only file that says these words | concept |
| `design/diagrams/` | generated sheets | `tools/diagrams/` |
| `history/HISTORY.md` | append-only research log, every negative result included; nothing in it is current | concept |
| `history/archive/` | superseded artefacts, moved verbatim, never referenced from `design/` | concept |
| `history/reviews/` | dated design reviews | concept |
| `studies/` | runnable experiments `sNN_*.py` — the evidence behind every number (`studies/README.md`) — plus `designpoint.py` (model-parameter bus), `components_boardN.py` (each board's value bus), `baselines/`, `reports/` | concept |
| `tools/diagrams/` | sheet generators + `sheet.py` | concept |
| `tools/handoff/` | the handoff publisher, one data module per board in `boards/`, answers in `answers/`, fab profiles in `fabs/` | concept |
| `tools/parts/` | distributor market data and the datasheet fetcher | concept |
| `tools/lint_docs.py`, `tools/status.py`, `tools/setup/`, `verify.sh` | doc lint, STATUS's generated block, machine setup, every check in one command | concept |
| `reference/` | `papers/`, `vendor/` (the datasheet store), `market/` (distributor cache), `sandbox/` — source material, never authority | — |
| `out/` | study outputs; generated, disposable, gitignored | studies |
| `kicad_project/boardN_*/` | KiCad files, `QUESTIONS.md`, `lib/`, plans — theirs; `handoff/` — ours, generated | board session |
| `kicad_project/tools/` | the board sessions' own tools (footprints, board setup, placement, routing, freerouting) | board session |
| `kicad_project/tools/session/`, `kicad_project/.claude/`, `kicad_project/.mcp.json`, `kicad_project/CLAUDE.md` | the hooks that enforce the walls, the board sessions' skills and MCP servers, their contract | the user (edit only at their request) |
| `kicad_project/RELAY_PROTOCOL.md`, `project.json`, `relay.json` | the communication protocol, the session names, the relay switch | the user |

## A decision is not finished until every home moves

**A decision that changes the architecture, a spec value, a net name, a part
or a hard line moves every home in the same pass**, in order: the
`design/STATUS.md` and `design/SPECS.md` rows; the value buses
(`studies/designpoint.py`, `studies/components_boardN.py`); **the affected
diagram GENERATORS**; the handoff (`publish.py`, with a `RELEASE_NOTE`); and a
dated entry in `history/HISTORY.md` with the reasoning. A doc that lags a
decision is worse than none. The `decision` skill is the checklist.

- ⚠ **"Re-run the generator" is not the work — EDITING it is.** An unedited
  generator reproduces the stale sheet byte-for-byte and the lint passes,
  because lint check 4 compares a sheet to its own generator, never the
  generator to the design. Never hand-edit an SVG.
- ⚠ **A box is a thing that exists on the board. An absence gets a line of
  text.** Deleting a part means deleting its block and drawing the wire that
  replaced it; a box reading "no regulator" is how a dead part survives.
- **Every Decided row has an id (`D1`, `D2`, …) and every generator declares
  `REFLECTS = "D<n>"`**: the newest `STATUS.md` *Decided* row it has been read
  against. Check 6 fails a sheet that names an older row (or one that does
  not exist), so **a new decision marks every sheet stale** — a second one on
  the same day too — until someone opens each generator, checks it, and bumps
  the id. Bumping without looking is the one failure the check cannot catch.
- **And `OPEN_CLAIMS`**: the `{open:<tag>}` items the sheet draws as open.
  Check 7 fails a tag STATUS no longer lists, so **closing an item breaks
  every sheet still drawing it**. 6 catches a sheet that missed a new
  decision; 7 catches a sheet still offering a killed option.
- ⚠ **Sign-off is a decision and owes a `Decided` row.** When an artefact
  outside `design/` commits the design — a simulation that closes, a pinout
  that ships, a connector pin order that goes into a handoff — ask *which open
  item did that just close*, and write the row before calling it done. Work
  that commits the design without a STATUS row is invisible to every check.

`tools/lint_docs.py` (`studies/run_all.py --docs`) enforces scope headers,
unique headings, no refdes on system sheets, regenerable diagrams, handoff
freshness, `REFLECTS` and `OPEN_CLAIMS`, that every study and headline key
`SPECS.md` cites exists in the baseline, and that STATUS's generated block is
current. `./verify.sh` runs it with everything else (the headline regression,
the diagram unit tests and rendered-text check).

## Sessions and ownership

Two session types share the repo with **single-writer file ownership**:

- **Concept** (this one, repo root, `bin/concept`): owns everything except
  the board sessions' files. Publishes each board's `handoff/` through
  `tools/handoff/publish.py`; reads their `QUESTIONS.md`; answers ONLY in
  `tools/handoff/answers/boardN.md`, never in their files.
- **Board** (one per board, `kicad_project/`, `bin/board boardN`): owns its
  board folder minus `handoff/`, plus `kicad_project/tools/`; reads nothing
  outside `kicad_project/`. `kicad_project/CLAUDE.md` is its contract.

The walls are enforced where the harness can enforce them: rendered deny
rules (`tools/setup/render_settings.py`), a hook that keeps each board session
in its own folder, a hook on THIS side that blocks edits to generated files
and to the board sessions' files, the board tools refusing another session's
board, and the SendMessage guard. **Bash bypasses the hooks and rules**, so
the contract carries the boundary too: never write into a board folder or
`kicad_project/tools/` from here except through `publish.py` and
`new_board.py`.

## The handoff

**Their questions are the measurement of handoff quality.** Every question is
a located defect in how the handoff *specifies*; the fix goes in the
GENERATOR (`tools/handoff/boards/boardN.py`, the value bus), never only in an
answer. The `answer-question` skill is the procedure.

- Set or clear `RELEASE_NOTE` every publish; an amended revision (republish
  within 20 minutes) describes every change since the previous one.
- A durable fact goes into the generated tables (nets, pin treatment,
  connectors), not only into an answer.
- **Placeholders get no part at all** — a plausible generic part is worse than
  a hole, because it stops looking like an open question.
- Classify pins by PIN NAME, never by a datasheet's Function column.
- **Footprints are theirs.** `publish.py` copies every datasheet the handoff
  names into `handoff/datasheets/` and hashes it into the MANIFEST, so their
  footprint tool verifies a land against the vendor's own page. Never answer a
  footprint question by quoting pad dimensions: publish the document; the
  numbers are theirs to read.
- ⚠ **Chip R, C and ferrite beads (0201–2512) get no datasheet on purpose**:
  they use KiCad's stock footprints. A passive datasheet needed for something
  else (a DC-bias curve, an ESR limit) goes in the board's `DATASHEETS_EXTRA`.

## Talking to the board sessions

`kicad_project/RELAY_PROTOCOL.md` is the whole protocol; read it before the
first exchange. In short:

- **The relay is OFF by default** (`kicad_project/relay.json`; a hook prints
  its state at session start). Off: never message a board session — publish,
  then tell the user which revision answers which question. On: ring per the
  protocol; the files stay the record and the message is only the doorbell.
- **Only the user flips the switch** (`bin/relay on|off`), or you at their
  explicit request. "Arm the relay" means: send the arming line to every live
  board session.
- **An answer that needs a NEW decision is not yours to give alone** — anything
  that would owe a `Decided` row, spends money, or comes back after two
  answers. Escalate (protocol §5) and stop.
- A message never waives one of the user's gates (a routing review, a fab
  order), whoever sends it.

## Generated files — never hand-edit

`design/diagrams/*.svg` (`tools/diagrams/`), `design/STATUS.md` between its
markers (`tools/status.py`), `kicad_project/*/handoff/**`
(`tools/handoff/publish.py`), `reference/vendor/datasheets.json`
(`tools/parts/datasheet.py`), `studies/baselines/*.json`
(`run_all.py --check --bless`). **(Add your own: any file that must match
another — a pinout, a constraints file — is generated from it, never typed:
a hand-kept copy goes stale without failing.)**

## Environment and running things

The concept venv is `.venv` (`./setup.sh install`); matplotlib uses `Agg`.

    .venv/bin/python studies/run_all.py --list          # what each study answers
    .venv/bin/python studies/run_all.py                 # every study
    .venv/bin/python studies/run_all.py --only s02_divider_tolerance
    .venv/bin/python studies/run_all.py --check         # headline numbers vs baseline
    .venv/bin/python studies/run_all.py --docs          # doc lint
    ./verify.sh                                         # every check, before calling anything done
    .venv/bin/python tools/diagrams/<generator>.py      # one sheet
    .venv/bin/python tools/handoff/publish.py --board board1   # publish (RELEASE_NOTE first)
    .venv/bin/python tools/handoff/publish.py --check --all    # fresh = the folder is what the sources render to now
    .venv/bin/python tools/status.py                    # STATUS generated block
    .venv/bin/python tools/parts/fetch.py <MPN>         # price, stock, lead time
    .venv/bin/python tools/parts/search.py --help       # discover candidates in a category
    .venv/bin/python tools/parts/datasheet.py <MPN>     # the datasheet PDF, filed
    ./setup.sh check | new-board N slug "Title"        # machine, boards
    ./setup.sh check-public                            # before anything is pushed to a public remote
    bin/relay status                                   # relay switch and names

Sweeps run on all cores through `studies/par.py` (fork, results in job order);
`HW_JOBS=1` forces serial — the first thing to try when a parallel number
disagrees with a published one.

## Working rules that have earned their place

- **Every published number has a source: a datasheet page, a standard, or a
  study.** `studies/README.md` says when to write one and how; the `study`
  skill is the procedure.
- **Model numbers go through `studies/designpoint.py`.** `kw()` raises on an
  unknown name, so a default can never silently set a headline number.
- **A published number owes a baseline.** `run_all.py --check` diffs every
  `headline()` against `studies/baselines/`; `--bless` is a claim that somebody
  looked. Run `--check` after ANY model edit. A `SPECS.md` row sourced from a
  study names it and its headline key in backticks (`` `s02_x` — `tag.key_unit` ``):
  the lint fails a cited key the baseline does not hold.
- **Simulate before hardware**, and a number from the model is quoted with the
  study that produced it.
- **Parts go through `tools/parts/` first** — `fetch.py <MPN>` for price,
  stock and lead time, `search.py` to discover candidates — then web search
  only if they have not delivered.
- **Never quote a component price or spec from a search snippet** — fetch the
  distributor page or the datasheet.
- **A datasheet is a FILE, never a web page.** `datasheet.py` fetches and
  files it under `reference/vendor/<mfr>/<MPN>.pdf` with its provenance; read
  it with the Read tool's `pages`. Nothing else may put a datasheet there. The
  `datasheet` skill covers the failure paths (headless Chrome, `--url`,
  `--file`, Konnect's catalogue).
- ⚠ **With more than one Chrome connected to the account**, anything using the
  Chrome MCP first calls `list_connected_browsers` and selects the one with
  `isLocal: true`; a file saved on another machine is not here.
- **Konnect, from this session, is for its `integration` toolset only**
  (datasheet URLs, the JLCPCB catalogue). KiCad files are the board sessions'.
- Datasheet facts get a page/table citation when first used; forum or FAE
  answers are recorded verbatim with source and date.

## Measurement conventions

**(Fill in: how this project measures — units, windows, test levels, what is
always reported next to a headline. Carry the same conventions to the bench.)**

## Answering the user

**As short as possible.** Answer what was asked and stop: no restating the
question, no recap of how you got there, no context the user does not need to
act on. **Bugs and fixes found on the way are the exception** — a defect, a
stale number or a wrong assumption found while answering is the most valuable
line in the reply.
