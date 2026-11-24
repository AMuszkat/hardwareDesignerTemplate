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

**"Come back" is counted, not remembered.** Before writing, find the
question's topic among the `Topic:` lines already in
`tools/handoff/answers/boardN.md` — the problem, not the question number (`U1
escape`, not `Q31`). If that topic has two answers, this one is the user's:
escalate first. The publisher refuses a third answer on a topic that has no
`Escalated: <date, what the user decided>` line, and a narrower allowance on
the same problem is the same topic, whatever the question calls it (seven
answers in five hours on one escape were each a new allowance).

**"It does not close" is answered with a ladder, never with one term.** When
the question is a count that falls short (a bus, an escape, a corner), put
to the user every term they might accept, cheapest first, each with what it
costs (a study where a number decides it) and whether the board has already
counted it -- and ask for the last rung allowed, once. Publish the whole
ladder in one revision (`10_layout`: the terms in order, "lay the cheapest
that closes; count every rung at once"), with one `Decided` row for it; the
board's report of the rung used completes the row. A second answer on the
same problem that adds one more allowance is the failure this prevents:
nine terms on one bus went out one at a time, each with its own two-hour
count and its own decision, over five days.

**A `WALL` question is one the board's tools wrote** (`route.py wall`, when
a routing group's effort budget was spent or a round showed what blocks it:
`kicad_project/tools/route/budget.py`). It carries the ledger -- every round
and how it ended -- what blocks, and the changes that would unblock it in
the order of their cost: placement, rule, swap, layer. Answer it as a
ladder, in ONE revision, under the wall's own `Qn` (the board's tools reopen
the group only when a handoff revision after the wall carries that heading
in `07_answers.md`, or one of the group's parts moves): grant alone the
rungs the facts decide (a placement the handoff already allows, a fact that
was missing), take the rest to the user. **A second wall of one group** (its
title says so) is a design problem -- two budgets spent on one group. Answer
it alone only when no offered change adds a term to the design: more router
effort, the board's own copper re-laid on terms the handoff already
carries, an order between groups; no `Decided` row then. A change that adds
a term (an allowance, a rule, a part, a net, a layer's use, a keep-out
opened) is the user's, and a ladder that holds one goes to the user whole.
**Read the wall's `Layers:` line first**: a layer no round opened is either
closed by a handoff row -- name it in the answer -- or the cheapest rung, and
the board counts it before anything is granted. Never answer a wall with
"try again with …": an answer that changes no input reopens nothing, by
design.

**A new answer is its own revision.** The publisher never amends the head
revision with a publish that adds a `## Qn`: a board's tools reopen a walled
group only on a revision after the one its wall was written at.

**A wall at a part's own exit** (a BGA's fan, a connector's rows) is read
against the part's pin table before the ladder is written: balls the vendor
calls not connected may carry a track -- some datasheets say so in the
signal table -- and the handoff's pin accounting lists them only as
unconnected. That is a rung with no term, and the board cannot see it. One
eMMC's third wall asked for layers and part moves while its datasheet let
signals run through 100 unused balls.

**A race that did not close is read before a term is offered for it.**
`python3 tools/review/knots.py <variant>/partial.json` prints the router's last
state as knots: layer, place, the nets in each. A knot of two tracks on one
layer is a crossing with no second layer there; the term to look for is what
shuts the other layers at that place. A static count (crossing pairs, lanes)
ranks a rung only after it has predicted a race: two ladders once went out on
a crossing count that fell from 64 pairs to 50 while the race went from 82
cells to 97. Two lines it prints settle a wall without a term: `PAIR TWICE` (one
pair knotted an even number of times on a layer crosses and crosses back --
the two paths are exchanged by hand between the knots, no via) and `VIA PAIR`
(contested vias nearer than the separation, which a knot table of two-net
clusters drops). A router that plateaus on either is answered with hand-laid
fixed copper and another race, not with an allowance.

**A question about a route** (pair order, which side a bus leaves, a
crossing, an escape) is never answered from pin order alone. Before an
option goes into the handoff, measure it yourself on a snapshot of the board
(`tools/review/snapshot.py --board boardN --kind pcb --id Qn --out
out/questions/boardN/Qn-pcb`: no agent; never under `out/reviews/`, where
the next review's `changed.md` would diff against it): an escape test
for every net the route encloses, and the track capacity of each exit
against the nets that must use it — `tools/review/geom.py <snapshot>`
`lanes`, `box`, `coupled`, `conn`, `probe`, or the board's route tool on a
scratch copy (`route.py check`, its `reach()` flood;
`kicad_project/tools/route/README.md`). **Never hand it to the
`pcb-reviewer`**: the reviewers review, they are not a measuring service
(`review-board`, step 3) — a what-if handed to one ran 13–55 minutes each
time. A measurement neither tool makes is a script in the snapshot's
`tmpWork/` and a retro row (the `geom.py` query it should have been). Two
pair routes once argued from pin order each failed their next review (a
pocket, then an exit four tracks short); the measurement settled the
third.

**A board's saved count model** (an escape or lane model its tools wrote,
re-solved here with one term changed) says nothing about the board "as
placed" until you know what the model already left out: read the log or
the script that built it for the obstacle sets it dropped, and quote the
result with them ("with those 17 sets out, 20 of 20"), or ask the board for
the count on the board itself. One "closes as placed" published from such a
model was 6 of 20 on the board, and cost a corrected revision.

**A custom DRC rule** that an answer adds to or changes in a board's `FAB`
(a `rules` row, or an area net class, which derives one) is proven on a copy
of the board before the publish: `python3 tools/review/rulecheck.py --board
boardN`, until its last line is `RULECHECK PASS`. On a board that has a PCB
the publisher refuses the rule without it. `FAILS … matches nothing on the
board` is a name the rule tests that no net, class or footprint carries (a
net name carries its sheet path: match `'*NAME'`); fix the rule, never the
proof. A rule scoped to a BGA's courtyard also needs that BGA's
`FAB["escape"]` rows — `escape_bound.py`'s arguments per escape layer — and
fails on a bound that is PROVEN SHORT: that rule cannot be routed, and
relaxing it is a decision, not an answer. Commit
`tools/handoff/rulechecks/boardN.json` with the publish. Twice a rule that
read well was wrong on the board (it missed the DRAM's pair names; it
outranked the BGA area classes).

**A clearance, width or layer allowance an answer or a decision grants is a
custom DRC rule in the same publish**, not only a sentence in the layout
page: DRC and the board's planner read the net class, so an allowance left
in prose is invisible to both and is asked for again. Write the `rules` row
with the sentence, prove it as above, and name the rule in the sentence. (One
bus's own-line spacing, granted in text, came back as a routing wall: the
class clearance was all the tools saw.)

**A geometric rule or a drawing** an answer publishes — a keep-out, a guard,
"no other net inside", an escape "as drawn by concept" — is tried on the
board's NEWEST PCB snapshot before the publish, with the board's own copper
in place: every pad, via and track of another net the rule's area holds, and
the board's gates on a scratch copy with the drawing applied. A drawing
carries the proof as `"checked": {"snapshot": "<id>-pcb", "gates": "PASS"}`;
the publisher refuses a new or changed one without it, or on an older
snapshot. Three such publishes came back within 5-24 minutes: a guard over
a connector's fixed pads, a drawing that broke hole-to-edge, a part that lost
another's start-up job.

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
  pointing at where the fact now lives, with a `Topic: <the problem>` line
  under the heading (reuse the topic when the problem is the same) and, for a
  third answer on it, the `Escalated:` line from step 2. **Never** answer a
  footprint question with pad dimensions: publish the datasheet; the numbers
  are theirs to read.

## 4. Publish

Set `RELEASE_NOTE` — which questions this answers, **which of their drawn
work is now wrong** (or "nothing") and the board gate that shows it
(`sch.py parity --handoff`, `place.py sync`, `place.py drc`) — then

    .venv/bin/python tools/handoff/publish.py --board boardN
    .venv/bin/python tools/handoff/publish.py --check --all

A question that showed a whole kind of fact missing (not one value) earns a
`--selftest` too: what else of that kind is missing is cheaper found now.

## 5. Tell them

- **Relay off:** tell the user, one line: `board1 rev 12 answers Q7, Q8;
  invalidates: nothing`.
- **Relay on:** send that board session
  `boardN handoff rev R — answers Qn[,Qm]; invalidates: <…>` with
  `notify_when_idle: true`. On the one idle notice that comes back: an
  unanswered entry in `QUESTIONS.md` → handle it; none → do nothing (they
  stopped for the user, by design).
