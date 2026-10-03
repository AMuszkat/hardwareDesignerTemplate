---
name: decision
description: >
  Record a design decision and propagate it to every home in one pass: STATUS
  Decided row, SPECS, value buses, diagram generators (REFLECTS, OPEN_CLAIMS),
  the handoff, HISTORY. Triggers on "we decided", "go with option", "change the
  part", "rename the net", "close the open item", "sign off", "accept the
  deviation", "update the spec", "the user chose".
---

# A decision, propagated

A decision that changes the architecture, a spec value, a net name, a part or
a hard line is **not finished until every home moves in the same pass**. Do
the steps in this order; each one feeds the next.

## 0. Is it yours to make?

Only the user makes a new decision. If it came from a board session's
question, from a study result that would commit the design, or from your own
recommendation, put the options and your recommendation in front of the user
and stop until they choose (relay on: `RELAY_PROTOCOL.md` §5).

## 1. `design/STATUS.md`

- Add a row to the **Decided** table: `| D<n> | YYYY-MM-DD | **THE DECISION
  IN CAPITALS** (who decided, on what). The reasoning in two sentences; the
  rest goes to HISTORY. |` — newest first, `D<n>` one more than the highest
  id so far and never reused. Lint check 6 compares every generator's
  `REFLECTS` to the newest id.
- If it closes an open item: **delete the item from the Open section, tag
  and all.** A struck-through item that keeps its `{open:tag}` still counts
  as open.
- If it opens one: add it to Open with a new `{open:<tag>}`.

## 2. `design/SPECS.md` and `design/IMPLEMENTATION.md`

Numbers go in SPECS (value, limit, source, **H** for a hard line); how it
works goes in IMPLEMENTATION. Each fact in one place — the lint fails a
heading that appears in two authored docs.

## 3. The value buses

`studies/designpoint.py` (model parameters, each with its source) and
`studies/components_boardN.py` (the physical parts: refdes, function, value,
status `set`/`proposed`/`tbd`, note). A part decision is a row moving to
`set` with the study or datasheet page that set it.

If a model parameter moved: `.venv/bin/python studies/run_all.py --check`.
A drift you intended is `--check --bless` — after you have looked at it.

## 4. The diagram generators — EDIT them, then run them

For **every** generator in `tools/diagrams/`:

1. Open it and read it against the decision. Does it draw the old part, the
   old net name, the deleted block, the killed option?
2. Edit what is wrong. A deleted part loses its box and gains the wire that
   replaced it; an absence is a line of text, never a box.
3. Bump `REFLECTS` to the new Decided row's id (`"D<n>"`) — **only after step 1**.
   Bumping without reading is the one failure no check can see.
4. Drop any `OPEN_CLAIMS` tag the decision closed; add one it opened.
5. Run it.

## 5. The handoff

For each board the decision touches: edit `tools/handoff/boards/boardN.py`
(nets, connectors, layout rules, open/deliberate text), set `RELEASE_NOTE`
saying **which drawn work is now wrong**, then

    .venv/bin/python tools/handoff/publish.py --board boardN
    .venv/bin/python tools/handoff/publish.py --check --all

## 6. `history/HISTORY.md`

Append `### Decision D<n>, YYYY-MM-DD — <title>`: what was decided, the options that
lost and why, the numbers it rested on with their sources. Nothing in HISTORY
is ever edited afterwards.

## 7. Prove it

    .venv/bin/python tools/status.py
    ./verify.sh          # headline regression, doc lint, diagram tests and text check

All clean, then tell the user — and, if the relay is on and a board's drawn
work moved, ring that board with the revision.
