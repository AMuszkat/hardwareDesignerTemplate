---
name: answer-question
description: >
  Handle a board session's question from its QUESTIONS.md: find the defect in
  the handoff generator, fix it there, publish, answer, and tell the board
  (relay on) or the user (relay off). Triggers on "board1 Q", "QUESTIONS.md",
  "the board session asks", "answer their question", a cross-session message
  from a board session, "rev", "handoff is stale".
---

# A board question, answered at the source

A question is a **located defect in how the handoff specifies**. The answer
is the least of it; the fix goes in the generator so the next reader never
has to ask.

## 1. Read it properly

- `kicad_project/boardN_*/QUESTIONS.md`: every entry whose status is `open`,
  `reopened` or that has no status. Note the MANIFEST hashes it quotes.
- `.venv/bin/python tools/handoff/publish.py --check --board boardN` — if the
  handoff is stale, publish first: the question may be about a source that has
  already moved.

## 2. Is it the facts' to decide, or the user's?

Answer alone only what the facts already decide: a value that exists in
`design/` or a datasheet and never reached the handoff; two generated pages
that disagree; a datasheet not yet published.

**Escalate** when the answer would owe a STATUS `Decided` row (architecture,
spec value, net name, part, hard line, accepted deviation), spends money, or
the question has come back after two answers: options and a recommendation in
the terminal, a one-line `PushNotification`, and — relay on —
`boardN Qn waits on the user — work on what is independent; choose no value`.
Then stop. When the user decides, run the `decision` skill and come back to
step 3.

## 3. Fix the generator, then answer

- The fact goes where the handoff should have carried it: the value bus, a
  NETS / CONNECTORS / LAYOUT / pin-treatment row in
  `tools/handoff/boards/boardN.py`, or the board's `DATASHEETS_EXTRA`.
- A missing datasheet: `.venv/bin/python tools/parts/datasheet.py <MPN>` (the
  `datasheet` skill), then publish — it is copied and hashed automatically.
- A question is design work, not a template lesson: the value, net, pin or
  part goes into this board's data module and the answer, and nowhere else.
  The exception is a *kind* of fact the publisher could not carry at all —
  prose where the board needed data, a connector shape with no row type —
  which is a mechanism fix in `board_template.py` / `publish.py` and goes to
  the template (`kicad_project/SELF_IMPROVEMENT.md`).
- The answer goes in `tools/handoff/answers/boardN.md` under the same `Qn`,
  pointing at where the fact now lives. **Never** answer a footprint question
  with pad dimensions: publish the datasheet; the numbers are theirs to read.

## 4. Publish

Set `RELEASE_NOTE` — which questions this answers, **which of their drawn
work is now wrong** (or "nothing") and the board gate that shows it
(`sch.py parity --handoff`, `place.py sync`, `place.py drc`) — then

    .venv/bin/python tools/handoff/publish.py --board boardN
    .venv/bin/python tools/handoff/publish.py --check --all

## 5. Tell them

- **Relay off:** tell the user, one line: `board1 rev 12 answers Q7, Q8;
  invalidates: nothing`.
- **Relay on:** send that board session
  `boardN handoff rev R — answers Qn[,Qm]; invalidates: <…>` with
  `notify_when_idle: true`. On the one idle notice that comes back: an
  unanswered entry in `QUESTIONS.md` → handle it; none → do nothing (they
  stopped for the user, by design).
