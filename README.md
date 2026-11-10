# Hardware Designer Template

Design circuit boards with [Claude Code](https://docs.claude.com/en/docs/claude-code/setup)
and [KiCad](https://www.kicad.org). One AI session works out **what** to build.
Another **draws** it. **You** make the decisions.

## How it works

```
 CONCEPT SESSION ────── handoff ──────▶ BOARD SESSION ──────▶ fab files
 decides what to build                  draws it in KiCad
        ▲                                     │
        └───────────── questions ─────────────┘
```

- **Concept session.** Specs, calculations, part choices. It packs everything
  a board needs into one folder: the **handoff**.
- **Board session.** One per board. It reads only the handoff, then draws the
  schematic and the layout in KiCad.
- **Questions.** If the handoff is missing something, the board session asks
  instead of guessing. The answer arrives in the next handoff.
- **Reviews.** Every schematic and layout change is checked before work goes on.
- **You.** You make the decisions and you place the fab order. No session does
  either for you.

## Get started

You need Claude Code, KiCad 10, Konnect, Python 3.10+ and git.
`./setup.sh check` lists anything missing and how to install it;
[`SETUP.md`](SETUP.md) is the full guide.

```sh
./setup.sh check                           # what is missing?
./setup.sh install                         # install the project's own parts
./setup.sh init MyProduct                  # name your project
./setup.sh new-board 1 main "Main board"   # create your first board
```

Then open two terminals:

```sh
bin/concept         # terminal 1: decide what to build
bin/board board1    # terminal 2: draw it in KiCad
```

Start in the first one: tell the concept session what you want to build.

## Check your work

```sh
./verify.sh
```

One command runs every check: numbers, documents, diagrams and handoffs.

## What is in the box

| Folder | What it holds |
|---|---|
| `design/` | What the product is: its numbers, how it works, what is decided and what is open |
| `studies/` | The calculations and simulations behind every number |
| `history/` | Why each decision was made, and every design review |
| `reference/` | Datasheets and papers |
| `tools/` | The concept session's tools: diagrams, parts, the handoff |
| `kicad_project/` | One folder per board, and the tools that draw them |

## Learn more

| If you want to | Read |
|---|---|
| Install everything, add API keys, fix a problem | [`SETUP.md`](SETUP.md) |
| Know the concept session's rules | [`CLAUDE.md`](CLAUDE.md) |
| Know the board sessions' rules | [`kicad_project/CLAUDE.md`](kicad_project/CLAUDE.md) |
| Let the sessions message each other (off by default) | [`kicad_project/RELAY_PROTOCOL.md`](kicad_project/RELAY_PROTOCOL.md) |
| Write a calculation or a simulation | [`studies/README.md`](studies/README.md) |
| See how the handoff is built | [`tools/handoff/README.md`](tools/handoff/README.md) |
| See what is planned | [`ROADMAP.md`](ROADMAP.md) |
| Publish your own project built on this | `./setup.sh check-public` first ([`SETUP.md`](SETUP.md), section 9) |

## Licence

MIT, see [`LICENSE`](LICENSE). Konnect, which the board sessions use to drive
KiCad, is a separate AGPL-3.0 project. It is installed on your machine and is
not part of this repository.
