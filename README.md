# Hardware design template — a concept session and one KiCad session per board

A repository layout, agent contracts, skills and tools for designing hardware
with Claude Code as **two kinds of session with single-writer ownership**:

- **The concept session** (repo root) owns the design: specs, studies and
  models, decisions, diagrams, part selection, datasheets. For each board it
  **publishes a handoff** — a generated, hashed, versioned data package — into
  that board's folder.
- **A board session** (`kicad_project/`, one per board) owns the KiCad design:
  schematic, footprints, placement, routing, fab outputs. It reads **only its
  handoff**; anything missing is a question in its `QUESTIONS.md`, never a
  guess.

```
 concept session (repo root)                  board session (kicad_project/)
 ─────────────────────────────                ─────────────────────────────
 design/  studies/  tools/          publish   boardN_<slug>/handoff/   (read-only)
 STATUS → buses → generators  ───────────────▶  CHANGELOG, components, nets, fab rules,
                                                 datasheets/, MANIFEST hashes
 tools/handoff/answers/boardN.md  ◀───────────  boardN_<slug>/QUESTIONS.md
                     ▲                    ▲
                     └── optional relay ──┘  (SendMessage doorbells; off by default)
```

Nothing here is specific to one kind of product. The examples are there to
be read, then replaced: `./setup.sh clean-examples` removes the three studies
and their rows in one step; the example sheet (`tools/diagrams/system.py`) and
the example rows in `design/STATUS.md` give way to your first real decision.

## Quick start

```bash
./setup.sh check                      # what this machine is missing (everything listed is required)
./setup.sh install                    # project-local parts: venvs, freerouting, key file, settings
./setup.sh init MyProduct             # names the project and its sessions
./setup.sh new-board 1 main "Main board" --fab example_standard
bin/concept                           # terminal 1: the concept session
bin/board board1                      # terminal 2: the board-1 session
bin/relay on                          # optional: let the sessions ring each other
./verify.sh                           # any time: every check of the concept side
```

`SETUP.md` is the full procedure, including every requirement and where each
one comes from.

## Where to look

| you want | go to |
|---|---|
| how to set up a machine | `SETUP.md` |
| the concept session's rules | `CLAUDE.md` |
| the board sessions' rules | `kicad_project/CLAUDE.md` |
| how the sessions talk to each other | `kicad_project/RELAY_PROTOCOL.md` |
| the product: how it works / numbers / decided & open | `design/IMPLEMENTATION.md` / `SPECS.md` / `STATUS.md` |
| where the numbers come from: studies, what they are for, how to write one | `studies/README.md` |
| why anything is the way it is | `history/HISTORY.md` |
| the handoff engine, adding a board | `tools/handoff/README.md` |
| the PCB tools | `kicad_project/tools/*/README.md` |
| 3D renders of a board (KiCad or Blender, the user's pick) | `kicad_project/tools/render/README.md` |

## What makes it work

- **One fact, one home.** Every authored doc opens with a *Scope* and a *Not
  here* line; `tools/lint_docs.py` fails a heading that appears in two of them.
- **Generated is never edited.** Diagrams, the handoff, STATUS's generated
  block and the datasheet index are outputs of `tools/`. A generator declares
  `REFLECTS` (the id of the newest decision it was read against) and `OPEN_CLAIMS` (the
  open items it draws), and the lint catches a sheet that missed a decision or
  still draws a closed option.
- **A decision moves every home in one pass**: STATUS row → value buses →
  generators → handoff → HISTORY.
- **The handoff is the only channel** to a board session, and it carries the
  hashes of its sources, so a stale brief is caught in conversation, not in
  copper. A board's questions measure the handoff's quality; fixes go into the
  generator.
- **The walls are enforced** where the harness can: rendered deny rules, a hook
  that keeps each board session in its own folder, a hook that keeps the
  concept session out of generated files and the boards' files, board tools
  that refuse another session's board, and a SendMessage guard that makes the
  relay switch real.
- **One command checks everything**: `./verify.sh` — the headline numbers
  against their baseline, the doc lint, the diagram tests, handoff freshness.
- **The user's gates stay the user's**: decisions, each placement or routing
  group's review, and the fab order.

Everything the sessions use — skills, subagents, hooks, MCP servers — is
declared inside this repository (`.claude/`, `kicad_project/.claude/`,
`.mcp.json`, `kicad_project/.mcp.json`); Konnect's own skills and subagents are
generated into `kicad_project/.claude/` by `./setup.sh install` rather than
committed (Konnect is AGPL-3.0). What has to be installed on the machine is
listed in `SETUP.md`.

Publishing your own project built on this? `./setup.sh check-public` first
(SETUP.md §9). Taking template improvements into it later: SETUP.md §8.

## Licence

MIT — see `LICENSE`. Konnect, which the board sessions use, is a separate
project under AGPL-3.0; its files are generated on your machine by
`./setup.sh install` and are not part of this repository.
