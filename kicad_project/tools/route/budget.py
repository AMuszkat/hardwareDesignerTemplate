#!/usr/bin/env python3
"""The effort budget: how much a routing group may cost before the DESIGN has to change.

    tools/kpy tools/route/route.py budget [--board board1]              # every group: tier, rounds, time, state
    tools/kpy tools/route/route.py budget R_emmc                        # one group's record, event by event
    tools/kpy tools/route/route.py budget routing/R_emmc_spec.json      # what tier a spec or a plan would get
    tools/kpy tools/route/route.py budget R_emmc --worker "one count: the west strip's via sites"
    tools/kpy tools/route/route.py budget routing/R_emmc_spec.json --before "1: the six-variant count of 11-22, 0 of 14"
                                                                        # rounds run before the ledger existed
    python3 tools/bgwait.py run --group R_emmc --terms --name t_ceiling -- <one variant on a scratch copy>
    python3 tools/route/budget.py terms R_emmc                          # routing/terms/R_emmc.json: TERMS OK, or what it lacks
    tools/kpy tools/route/route.py wall R_emmc --blocks "..."           # the question, from the ledger and that table

WHY IT EXISTS.  A board session spent a day on counts, races and workers for four groups and
put two nets on copper: 85 background jobs and 108 worker runs in 56 hours, and nothing in the
contract, a skill or a tool said when to stop.  A group that will not close after a few honest
tries is not short of tries: something in the design is in its way -- a part, a rule, a pin, a
layer.  So every group has a budget set by ITS SIZE, the tools count against it by themselves,
and when it is spent the router, the race, the check and the job runner refuse the group
(`BUDGET SPENT`, exit 76) until a question has been asked and the design has moved.

THE BUDGET.  One ROUND is one router run with its checks (the first, and two after a hand
fix), or one race of up to six variants; router runs of one group that are in flight together
are one round, up to six.  A group drawn by hand has no router run: its first check opens
the round.  TIME is the wall-clock of the group's router runs, races, checks and `bgwait.py
--group` jobs.

    tier  what puts a group in it                                       rounds   time
    A     up to 12 nets, no BGA ball, no pair, nothing matched             2      45 min
    B     up to 40 nets, or a pair, or up to 12 balls of a BGA             3      2 h
    C     over 12 BGA balls (an escape), a matched bus, or over 40 nets    4      6 h
    and for any group ONE feasibility count of 30 min (`bgwait.py run --group G --count`,
    or its "count" in routing/groups.json, run by prelayout.py): no answer in 30 min is
    the answer "it does not fit".

The tier is read from the nets, never chosen: their number, which of them end on a ball of a
BGA (handoff/netlist.json: a part with 36+ ball-named pins), whether both halves of a pair are
among them, and `"matched": true` on the spec or on the group's routing/groups.json entry.  A
group's tier only ever rises.

WHICH GROUP A RUN BELONGS TO is decided by its NETS, not its name -- a new name for the
same nets is the same group:
  1. routing/groups.json, when the board has one: the entry whose net patterns hold at least
     half of the run's nets (the file prelayout.py's "groups" test requires);
  2. else a ledger group of that name, or one this name was already charged to;
  3. else the ledger group that holds the most of the run's nets, when that is at least half;
  4. else a new group under the run's own name.

THE LEDGER is <board>/routing/ledger.json: per group its nets, tier, every event (what, when,
how long, how it ended) and its walls.  It is written by the tools -- netgroup.py, race.py,
route.py check / apply, bgwait.py --group, prelayout.py -- under a file lock, and is the board
session's record: never edited by hand (the wall's question quotes it, and concept reads the
quote).  A run on a SCRATCH COPY of the board is charged to the real board: a copy in
the board's folder (tmpWork/...) by where it lies, a copy kept outside kicad_project/ (a cache
folder) by the board's own file name in it and the session that runs on it (HW_BOARD) -- the
copy's own routing/ledger.json is never read, and never copied back.  Any other board (a test
fixture, a review snapshot run by the concept session) has no budget.

SPENT means: the rounds are used and another would start, or the time is used.  Then the
terms count and `route.py wall <group>` are what is left.  The wall writes the question from
the ledger into QUESTIONS.md -- every round and how it ended, what blocks (yours to say:
parts, vias, nets), and the changes that would unblock it in the fixed order of what they
cost: placement, rule, swap (a pin or a part), layer -- and prints the ring.  Then the board
moves to the next group.  A wall may be declared BEFORE the budget is spent, and should be
when a round has shown the cause: the budget is a ceiling, not a target.

THE WALL CARRIES ITS COUNT.  A wall asks for a term of the design to move, and a term nobody
counted is a guess: the answer grants it, the next round shows it was not enough, and the
group is back with the next term -- one term a question, hours a term.  (board1, 2026-11-23
and 24: 28 walls on five groups in 32 hours; one group of 33 nets had 10 walls and 13.5 h of
tool time against a 6 h budget, and no net on copper.)  So the changes a wall offers are the
rows of a table that CLOSED on a scratch copy, and the table starts from the loose end:

  1. the CEILING -- one variant with EVERY term that could move, moved at once: every
     keep-out the group could ask to open, every spacing at the fab's own minimum, every
     layer the stack-up has, every part of it that could shift.  It is the loosest problem
     there is and closes fast when it closes at all.  When it does not close, no single term
     will: the group does not fit this placement and stack-up, the ceiling's own numbers are
     the question, and it is the user's at once -- not ten walls later.
  2. when the ceiling closes -- one variant per term: that term ALONE moved, or every term
     BUT it.  All of them at once (`par.cmap`, `race.py`, a board-worker each): which terms
     the group needs, and which it never did.  The cheapest row that closes is what is asked,
     and its plan is the copper once the term is granted: the reopened group re-proves and
     lays it, it does not search again.

  The table is <board>/routing/terms/<group>.json, yours to write from the variants' results:

      {"terms": {"T1": {"kind": "rule",      "text": "R1's reserve ends 0.6 mm further south (one via row)"},
                 "T2": {"kind": "placement", "text": "R215..R217 move to the east pocket x 44..48.8"},
                 "T3": {"kind": "layer",     "text": "the straps change layer west of U200 (In7)"}},
       "rows":  [{"moved": ["T1", "T2", "T3"], "closes": true,  "left": 0, "evidence": "tmpWork/R4_terms/ceiling/result.json"},
                 {"moved": ["T1"],             "closes": false, "left": 3, "evidence": "tmpWork/R4_terms/t1/result.json"},
                 {"moved": ["T2"],             "closes": true,  "left": 0, "evidence": "tmpWork/R4_terms/t2/result.json"},
                 {"moved": ["T3"],             "closes": null,  "left": null, "evidence": "tmpWork/R4_terms/t3/netgroup.log",
                  "note": "no answer in the window: 4 knots at it 170"}]}

  "kind": placement, rule, swap or layer.  "closes": true (every net of the group), false
  ("left": the nets it did not close), or null (no answer in the window).  A row that closes
  says HOW it is known, "proof": "check" (the variant's plan passed `route.py check` on the
  copy: it is the copper once granted), "router" (every net routed and none overlapping, the
  plan not yet through the gate: shape and DRC findings are still to fix) or "count" (a
  counting model says the nets fit; there is no plan).  The wall prints the level beside each
  row and calls a row that is not "check" a LEAD: granted, it still owes its gated plan, and
  whoever grants knows that before granting.  (board1, 2026-11-24: a runner scored a routed
  ceiling whose raw plan failed the gate as `LEFT 33` -- its 33 findings, read as nets, a
  DOES NOT FIT wall for a group that routes; another table marked channel counts "closes".)
  "evidence": the variant's result, a file of the board folder written in this
  epoch -- a table counted for an earlier wall is refused.  A row with "moved": [] is the
  published terms as they stand (more effort only).  `budget.py terms <group>` checks the
  table; `route.py wall` refuses to write without it -- for a tier B or C group from its
  first wall, for any group from its second -- and then takes no --change: the changes ARE
  the closing rows, cheapest first.  The table that a wall quoted is kept beside it as
  routing/terms/<group>.wall<n>.json.

  THE TERMS COUNT HAS ITS OWN WINDOW: `bgwait.py run --group G --terms --name N -- <variant>`
  runs when the budget is spent, costs no round and none of the group's time, and is on the
  ledger.  The window opens with the epoch's first such job and lasts TERMS_S (tier A 20 min,
  B 45 min, C 90 min); every variant started in it is ended at its end, and one that gave no
  answer by then is "closes": null.  Start them together -- the cores are idle while a
  question travels, and a window used one variant at a time is a window wasted.
  COPPER ENDS A WINDOW: when the group lays copper after its window (an apply no rip has
  undone), the board the table describes is gone -- every row counted before that copper is
  stale, and the window is over with it: the next --terms job opens a new one, on today's
  board (`count_base`).  (board1 R1, 2026-11-25: the one window went to a single variant
  before three applies; the table was stale and the group could neither count nor wall.)

A REOPEN IS NOT A REFILL.  An answered wall (or a part that moved) opens a new epoch, and the
epoch gets a SHARE of the tier's budget: half after the group's first wall, a quarter after
every later one (never less than one round and 15 min).  The wall's own table holds a plan
that closed on scratch, so what the new epoch has to do is lay it -- re-prove on the board as
it stands, apply -- and a group that needs a whole budget again was not answered, it was
postponed.  The full budget that every answer used to restore is what let one group spend
four budgets and reach ten walls.  The share is written into the ledger's reopen event; an
epoch opened before this rule keeps what it had.

A CLOSING ROW THE HANDOFF ALREADY ALLOWS IS LAID, NOT ASKED (kicad_project/CLAUDE.md: "a closing
row that moves only what the handoff already lets you move is not a question").  A term the
handoff already lets the group move carries the rule that lets it, in the table:

      "T2": {"kind": "placement", "text": "PDM_DATA1 re-laid on In7 through x 30.2",
             "handoff_allows": "PDM_DATA1 is the group's own net, on a layer of its own spec"}

When a spent group's table (checked, this epoch) has a row that closes with "proof" "check" or
"router" and moves only such terms, `budget.py lay <group>` puts that row on the ledger, and the
epoch gets LAY_CHECKS more checks to gate it -- its reserve gates and a hand fix of what they
find -- and nothing else: no router run, no race, no count, no more time.  Once per epoch, and
only from a table `budget.py terms` passes.  A row that needs any other term is the wall's.
(board1 NORTHGAP r192, 2026-11-25: T2, the group's own net re-laid, closed by hand on the copy
after the round and its three checks were spent; its reserve gates were refused at the door.)

THE BUDGET REOPENS ONLY WHEN THE INPUT CHANGES: the handoff answers the wall's question in a
later revision (handoff/07_answers.md carries its `## Qn`), or one of the group's own parts
has moved on the board.  Not by a new name, a new seed or a new day -- and then to a share of
the budget, not all of it (above).  A group that reaches a
SECOND wall is a design problem and its question says so: concept answers it alone only when
no offered change adds a term to the design -- more router effort, the board's own copper
re-laid on terms already published, an order between groups -- and takes every other one to
the user (RELAY_PROTOCOL.md section 5).  And a group whose nets are ALL on copper is
finished: work on it after that (a review's fix, a neighbour that needs room) starts a new
budget by itself.

A ROUND THAT MEASURED NOTHING IS NOT A ROUND.  A router run that ends before its first
negotiation -- an end shut in by copper the run cannot move (BLOCKED before leg 1), a spec the
set-up refused -- says so in its event (`free`), and the tally leaves its round out: the time
is counted, the round is not.  FREE of them an epoch; the next one is a round like any other
(a group that keeps starting runs which cannot begin is spending its budget).  Runs started
together are freed only when each of them ended that way.  (board1, 2026-11-23: R_PMIC's one
granted round ended in 41 s on two walled-in sources, and NORTHGAP's on a spec defect; each
went back to the user as a second wall with nothing measured.)

A GATE IS NOT A ROUND.  `bgwait.py run --group G --gate` is a job that checks the group's
board and searches nothing -- a DRC diff, a preflight, a dry run that stops before the race.
Its time is the group's; its round is not counted, as long as no router run or race starts
inside it and it ends within GATE_S (5 min).  A gate that runs longer, or runs the router, is
a round like any job, and its event says why.  `route.py check` inside it counts as a check, as
in any job.  A search of your own (a count script, a site solver) is never a gate: it is the
effort the budget is there to bound.  (board1, 2026-11-24: NORTHGAP's three gate jobs -- two
DRC runs of 12 s and 19 s and a 7 s dry run -- were charged three of its four rounds; the user
credited them back and asked for this flag.)

NOT COUNTED, NEVER REFUSED: `route.py apply` (copper is the point), `rip`, `audit`, `status`,
`render` -- also as a `--gate` job, whatever its length: apply re-runs the full check before it
writes, which can pass GATE_S on a big board, and it searches nothing (board1 R1, 2026-11-25: the
6-min apply of a checked plan was charged R1's last round and the next proven plan was refused).
Run an apply as a plain job (`bgwait.py run --name N -- tools/kpy tools/route/route.py apply ...`,
no --group): a --group job, a gate too, is refused at the door once the group is spent; a check that ends in a usage error; the round of a run that never negotiated (above).  HW_BUDGET_IN is set by race.py and prelayout.py
for the processes THEY start, whose cost they log themselves; setting it by hand is skipping
a gate.

Standard library only: bgwait.py imports it without KiCad.
"""
import atexit
import fcntl
import hashlib
import json
import math
import os
import re
import signal
import sys
import time
from pathlib import Path

KP = Path(__file__).resolve().parents[2]                 # kicad_project/
TIERS = {"A": dict(rounds=2, seconds=45 * 60), "B": dict(rounds=3, seconds=2 * 3600),
         "C": dict(rounds=4, seconds=6 * 3600)}
COUNT_S = 30 * 60            # the feasibility count
TERMS_S = {"A": 20 * 60, "B": 45 * 60, "C": 90 * 60}     # the terms count a wall carries: one window, variants side by side
REOPEN = (0.5, 0.25)         # the share of its tier's budget a reopened group gets: after its first wall, after any later
MIN_S = 15 * 60              # ... and never less time than this
CHECKS = 3                   # checks a round carries: its first, and two after a hand fix
LAY_CHECKS = 3               # checks a spent group gets to LAY a closing row the handoff already allows (`lay`)
RACE = 6                     # variants, or runs in flight together, that are one round
DEAD_S = 600                 # charged for a run that died without a word (killed -9)
FREE = 3                     # runs an epoch that ended before any negotiation and cost no round
UNGROUPED_S = 60             # a background job of no group shorter than this is not worth a line
GATE_S = 300                 # a --gate job that ends within this, running no router or race, costs no round
EXIT = 76                    # BUDGET SPENT
ENV_IN = "HW_BUDGET_IN"      # set by a tool that logs its children's cost itself
ENV_JOB = "HW_BUDGET_JOB"    # a job id: set by bgwait.py --group for its command; the job owns the time
CHANGES = ("placement", "rule", "swap", "layer")          # the fixed order of cost
# how a closing row of a terms table is known: (the table's word, the wall's word)
PROOFS = {"check": ("yes", "PROVED: `route.py check` PASS on the copy"),
          "router": ("routes, plan not gated", "A LEAD: every net routed, its plan has not passed `route.py check`"),
          "count": ("counts, no plan", "A LEAD: a counting model closes it, no plan exists yet")}
BALL = re.compile(r"^[A-Z]{1,2}\d{1,2}$")
PAIR = re.compile(r"^(.*?)(_P|_N|_DP|_DN|_DM|\+|-)$")
HALF = {"_P": "_N", "_N": "_P", "_DP": ("_DN", "_DM"), "_DN": "_DP", "_DM": "_DP", "+": "-", "-": "+"}


def short(net):
    return str(net).rsplit("/", 1)[-1]


def dur(s):
    s = max(0.0, float(s or 0))
    if s < 90:
        return f"{s:.0f} s"
    if s < 5400:
        return f"{s / 60:.0f} min"
    return f"{s / 3600:.1f} h"


# ---------------------------------------------------------------- the board a run is charged to

def home(board_dir):
    """The real board a run on `board_dir` is charged to, or None (a fixture: no budget)."""
    bd = Path(board_dir).resolve()
    for d in [bd] + list(bd.parents):
        if d.parent == KP and re.match(r"board\d+_", d.name):
            return d                                      # the board, or a scratch copy inside its folder
    env = os.environ.get("HW_BUDGET_BOARD")               # bgwait.py --group sets it for its command
    if env and Path(env).is_dir():
        return Path(env).resolve()
    # A copy kept OUTSIDE kicad_project/ (a cache folder, a RAM disk) still carries the board's own
    # file names, and the session running on it is that board's (HW_BOARD, or its shell's folder).
    # The concept session, which runs a review snapshot's gates, has no board: nobody's budget.
    sb = session_board()
    if sb is not None and bd.is_dir() and any((bd / (sb.name + e)).is_file() for e in (".kicad_pcb", ".kicad_pro")):
        return sb
    return None                                           # a fixture, a review snapshot: nobody's budget


def ledger_path(hb):
    return Path(hb) / "routing" / "ledger.json"


class Ledger:
    """The ledger, held under a lock from `with` to its end; saved when changed."""

    def __init__(self, hb):
        self.home, self.path, self.dirty = Path(hb), ledger_path(hb), False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = open(self.path.with_suffix(".lock"), "w")
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        try:
            self.d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.d = {}
        self.d.setdefault("groups", {})
        if self.d.get("home") != str(self.home):
            self.d["home"], self.dirty = str(self.home), self.dirty or bool(self.d["groups"])
        return self

    def __exit__(self, *exc):
        if self.dirty and exc[0] is None:
            tmp = self.path.with_suffix(f".json.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(self.d, indent=1) + "\n")
            os.replace(tmp, self.path)
        fcntl.flock(self.lock, fcntl.LOCK_UN)
        self.lock.close()
        return False

    def group(self, key):
        return self.d["groups"].get(key)


def read(hb):
    try:
        return json.loads(ledger_path(hb).read_text())
    except (OSError, ValueError):
        return {"groups": {}}


# ---------------------------------------------------------------- what the board and the handoff say

_NETLIST = {}


def netlist(hb):
    """handoff/netlist.json: {short net: [[ref, pin], ...]}; {} when the board has none."""
    hb = str(hb)
    if hb not in _NETLIST:
        try:
            N = json.loads((Path(hb) / "handoff" / "netlist.json").read_text()).get("nets", {})
        except (OSError, ValueError):
            N = {}
        _NETLIST[hb] = {short(k): v for k, v in N.items() if isinstance(v, list)}
    return _NETLIST[hb]


def bgas(hb):
    n = {}
    for pins in netlist(hb).values():
        for ref, pin in pins:
            if BALL.match(str(pin)):
                n[ref] = n.get(ref, 0) + 1
    return {r for r, k in n.items() if k >= 36}


def classify(hb, nets, matched=False):
    """(tier, the reason in words) for a set of nets."""
    nets = sorted({short(n) for n in nets})
    N, B = netlist(hb), bgas(hb)
    balls = sum(1 for n in nets if any(ref in B for ref, _ in N.get(n, [])))
    have, pairs = set(nets), 0
    for n in nets:
        m = PAIR.match(n)
        if m:
            other = HALF[m.group(2)]
            if any(m.group(1) + o in have for o in ((other,) if isinstance(other, str) else other)):
                pairs += 1
    pairs //= 2
    facts = f"{len(nets)} net(s), {balls} on a BGA ball, {pairs} pair(s)" + (", matched" if matched else "")
    if matched or balls > 12 or len(nets) > 40:
        return "C", facts
    if len(nets) > 12 or pairs or balls:
        return "B", facts
    return "A", facts


def declared(hb):
    """routing/groups.json's entries: [{"id", "nets": [regex], "matched", "count", "why"}]."""
    try:
        G = json.loads((Path(hb) / "routing" / "groups.json").read_text())
    except (OSError, ValueError):
        return []
    return [g for g in G.get("groups", []) if g.get("id")]


def declared_for(hb, nets):
    nets = [short(n) for n in nets]
    best, hit = None, 0
    for g in declared(hb):
        pats = [re.compile(p) for p in g.get("nets", [])]
        k = sum(1 for n in nets if any(p.search(n) for p in pats))
        if k > hit:
            best, hit = g, k
    return best if nets and hit * 2 >= len(nets) else None


def handoff_rev(hb):
    try:
        return int(json.loads((Path(hb) / "handoff" / "MANIFEST.json").read_text()).get("rev", 0))
    except (OSError, ValueError, TypeError):
        return 0


def answered(hb, q):
    try:
        txt = (Path(hb) / "handoff" / "07_answers.md").read_text()
    except OSError:
        return False
    return bool(q) and re.search(rf"^## {re.escape(q)}\b", txt, re.M) is not None


def parts_sha(hb, nets):
    """A hash of where the group's parts stand: the footprints with a pin on one of its nets
    (every footprint when the handoff does not say), read from the .kicad_pcb's text."""
    N = netlist(hb)
    refs = {ref for n in nets for ref, _ in N.get(short(n), [])}
    pcbs = [p for p in sorted(Path(hb).glob("*.kicad_pcb")) if not p.name.startswith(("_autosave-", "~"))]
    if len(pcbs) != 1:
        return ""
    rows, cur = [], None
    with open(pcbs[0], errors="replace") as f:
        for line in f:
            if line.startswith("\t(footprint "):
                cur = {"at": "", "layer": "", "ref": ""}
                rows.append(cur)
            elif cur is not None and line.startswith("\t\t("):
                if line.startswith("\t\t(at ") and not cur["at"]:
                    cur["at"] = line.strip()
                elif line.startswith("\t\t(layer ") and not cur["layer"]:
                    cur["layer"] = line.strip()
                elif line.startswith('\t\t(property "Reference" '):
                    m = re.match(r'\t\t\(property "Reference" "([^"]*)"', line)
                    cur["ref"] = m.group(1) if m else ""
            elif line.startswith("\t(") and not line.startswith("\t(footprint "):
                cur = None
    rows = sorted((r["ref"], r["at"], r["layer"]) for r in rows if not refs or r["ref"] in refs)
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- the count

def alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


COPPER_ONLY = ("apply", "rip", "audit", "status")


def copper_only(e):
    """A job whose one command is `route.py apply|rip|audit|status` (its label, as bgwait writes it:
    '[gate ]NAME: CMD'): it writes or reads proven copper and searches nothing.  A compound command
    or a script is not one -- what it runs is not known here."""
    lab = e.get("label") or ""
    cmd = lab.split(": ", 1)[1] if ": " in lab else lab
    if any(c in cmd for c in ("&&", "||", ";", "|", "`", "$(")):
        return False
    w = cmd.split()
    while w and (w[0].endswith("kpy") or os.path.basename(w[0]).startswith("python")):
        w = w[1:]
    return len(w) >= 2 and os.path.basename(w[0]) == "route.py" and w[1] in COPPER_ONLY


def cost(e, now):
    """Seconds an event has cost: its own, or for one with no end its age up to its limit
    (still running) or DEAD_S (its process is gone and never said how it ended)."""
    if e.get("s") is not None:
        return float(e["s"])
    lim = float(e.get("limit") or DEAD_S)
    if e.get("pid") and not alive(e["pid"]):
        lim = min(lim, DEAD_S)
    return max(0.0, min(lim, now - e["t"]))


def allowance(g):
    """What the group's current epoch may cost: its tier's budget, or the share of it that the
    reopen which began the epoch gave (REOPEN; an epoch opened before that rule has no share
    on record and keeps the whole)."""
    T = TIERS[g.get("tier", "A")]
    ep = g.get("epoch", 1)
    frac = next((float(e["frac"]) for e in g.get("events", [])
                 if e["kind"] == "reopen" and e.get("epoch", 1) == ep and e.get("frac")), 1.0)
    if frac >= 1.0:
        return dict(rounds=T["rounds"], seconds=T["seconds"], frac=1.0)
    return dict(rounds=max(1, int(T["rounds"] * frac)), seconds=max(MIN_S, T["seconds"] * frac), frac=frac)


def share(frac):
    return "all" if frac >= 1.0 else "half" if frac == 0.5 else "a quarter" if frac == 0.25 else f"{frac:g}"


def tally(g, now=None):
    """What the group's current epoch has used."""
    now = now or time.time()
    ep = g.get("epoch", 1)
    ev = [e for e in g.get("events", []) if e.get("epoch", 1) == ep]
    in_runs, in_checks, in_free, in_void = {}, {}, {}, {}    # what the route tools did inside a bgwait --group job
    for e in ev:
        j = e.get("job")
        if j and e["kind"] in ("run", "race"):
            in_runs[j] = in_runs.get(j, 0) + int(e.get("variants", 1))
            if e.get("free") and e["kind"] == "run":
                in_free[j] = in_free.get(j, 0) + 1
        elif j and e["kind"] == "check":
            d_ = in_void if e.get("free") else in_checks  # a check refused as stale checked nothing
            d_[j] = d_.get(j, 0) + 1
    gate_ok = {e.get("jid") for e in ev if e["kind"] == "job" and e.get("gate") and not in_runs.get(e.get("jid"))
               and cost(e, now) <= GATE_S}               # a gate's own checks open no round (board1 NORTHGAP r192)
    rounds = checks = free = lays = 0                    # free: runs that never negotiated, their round left out
    lay = None                                           # the `lay` event of this epoch: LAY_CHECKS checks after it
    secs = count_s = last = 0.0
    counts, terms, on_copper, t_apply = [], [], {}, 0.0
    base = last_copper(g)
    for e in g.get("events", []):                        # copper is a fact of the board, whatever the epoch
        if e["kind"] == "apply":
            on_copper[e.get("name")] = set(e.get("nets", []))
            t_apply = e["t"]
        elif e["kind"] == "rip":
            on_copper.pop(e.get("name"), None)
    for e in ev:
        k, s = e["kind"], cost(e, now)
        if k == "count":
            counts.append(e)
            count_s += s
        elif k == "terms":                               # the count a wall carries: its own window, no round, not the group's time
            if e["t"] >= base:                           # a window used before the group's last copper is over
                terms.append(e)
        elif k == "lay":
            lay = lay or e
        elif k in ("reopen", "worker", "apply", "rip"):
            pass
        elif k == "job":                                 # the job owns its time; what ran inside it says what it was
            secs, last = secs + s, e["t"]
            n = in_runs.get(e.get("jid"), 0)
            f = max(0, min(in_free.get(e.get("jid"), 0), FREE - free))
            free += f
            v = max(0, min(in_void.get(e.get("jid"), 0), FREE - free)) if n - f <= 0 and not in_checks.get(e.get("jid")) else 0
            free += v
            if n - f > 0:
                rounds, checks = rounds + math.ceil((n - f) / RACE), 0
            elif f or v:
                pass                                     # all it did ended before it began: no round
            elif e.get("gate") and (s <= GATE_S or copper_only(e)):
                pass                                     # a gate: it checked the board and searched nothing
                #                                          (an apply / rip / audit: whatever its length)
            elif not in_checks.get(e.get("jid")):
                rounds, checks = rounds + 1, 0
        else:
            last = e["t"]
            if not e.get("job"):
                secs += s
            if k == "check" and e.get("free"):
                pass                                     # refused as stale inside a job: on the record, no check
            elif k == "check" and lay:
                lays += 1                                # one of the lay's checks: no round, none of the round's
            elif k == "check" and e.get("job") in gate_ok:
                if rounds and checks < CHECKS:           # after a round: one of that round's checks
                    checks += 1
                # before any round, or past its checks: a gate checked a hand fix and searched nothing -- no
                # round. TRAP (board1 NORTHGAP r192, 2026-11-25): the hand-move gate's check opened round 1,
                # and the one round the handoff granted after it was refused at the door.
            elif k == "check":
                if rounds == 0 or checks >= CHECKS:
                    rounds, checks = rounds + 1, 1
                else:
                    checks += 1
            elif e.get("round") and not e.get("job"):
                if e.get("free") and e.get("members", 1) == 1 and free < FREE:
                    free += 1                            # it measured nothing: its time counts, its round does not
                else:
                    rounds, checks = rounds + int(e.get("rounds", 1)), 0
    T = allowance(g)
    wall = next((w for w in reversed(g.get("walls", [])) if w.get("epoch", 1) == ep), None)
    left_r, left_s = T["rounds"] - rounds, T["seconds"] - secs
    copper = set().union(*on_copper.values()) if on_copper else set()
    nets_applied = len(copper & set(g.get("nets", []))) if g.get("nets") else len(copper)
    complete = bool(copper) and set(g.get("nets", [])) <= copper
    lay_left = LAY_CHECKS - lays if lay else 0
    no_more = left_s <= 0 or left_r < 0 or (left_r <= 0 and checks >= CHECKS and lay_left <= 0)
    state = ("wall" if wall else "applied" if copper and t_apply >= last else "spent" if no_more else "open")
    return dict(rounds=rounds, seconds=secs, checks=checks, free=free, counts=counts, count_s=count_s, complete=complete,
                nets_applied=nets_applied, left_rounds=left_r, left_seconds=left_s, state=state, wall=wall,
                no_more=no_more, lay=lay, lay_left=lay_left, allow_rounds=T["rounds"], allow_seconds=T["seconds"], frac=T["frac"], terms=terms,
                terms_end=(min(e["t"] for e in terms) + TERMS_S[g.get("tier", "A")]) if terms else None)


def key_for(L, name, nets):
    hb = L.home
    nets = sorted({short(n) for n in nets or []})
    d = declared_for(hb, nets) if nets else next((g for g in declared(hb) if g["id"] == name), None)
    if d:
        return d["id"], d
    G = L.d["groups"]
    if name in G:
        return name, None
    for k, g in G.items():
        if name in g.get("names", []):
            return k, None
    best, hit = None, 0
    for k, g in G.items():
        n = len(set(nets) & set(g.get("nets", [])))
        if n > hit:
            best, hit = k, n
    if nets and best and hit * 2 >= len(nets):
        return best, None
    return name, None


def _reopen(L, g, key):
    """A walled group whose input moved gets a new epoch.  -> why it reopened, or None."""
    t = tally(g)
    w = t["wall"]
    hb = L.home
    why = None
    if not w:
        if t["state"] == "applied" and t["complete"] and (t["rounds"] or t["seconds"]):
            why = "every net of it is on copper: work after that is a new budget"
            g["epoch"] = g.get("epoch", 1) + 1
            g["events"].append(dict(t=time.time(), kind="reopen", epoch=g["epoch"], result=why))
            L.dirty = True
        return why
    if w.get("q") and answered(hb, w["q"]) and handoff_rev(hb) > int(w.get("rev", 0)):
        why = f"handoff rev {handoff_rev(hb)} answers {w['q']}"
    else:
        now = parts_sha(hb, g.get("nets", []))
        if now and w.get("parts") and now != w["parts"]:
            why = "its parts moved on the board"
    if why:                                              # a reopen is not a refill: a share of the tier's budget
        frac = REOPEN[0] if len(g.get("walls", [])) <= 1 else REOPEN[1]
        g["epoch"] = g.get("epoch", 1) + 1
        g["events"].append(dict(t=time.time(), kind="reopen", epoch=g["epoch"], result=why, frac=frac))
        L.dirty = True
    return why


def refuse(key, g, t, what):
    T = g.get("tier", "A")
    used = (f"{t['rounds']} of {t['allow_rounds']} round(s), {dur(t['seconds'])} of {dur(t['allow_seconds'])} "
            f"(tier {T}: {g.get('basis', '')})")
    if t["state"] == "wall":
        w = t["wall"]
        msg = (f"BUDGET SPENT -- {key} is at its wall ({w.get('q') or 'question not numbered'}, asked "
               f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(w['t']))}): {what} refused. It reopens when a "
               f"handoff revision after rev {w.get('rev', 0)} answers {w.get('q') or 'the question'}, or when one of "
               f"its parts moves on the board -- not before. Work on the next group.")
    else:
        msg = (f"BUDGET SPENT -- {key}: {used}; {what} refused. More runs will not close it: something in the "
               f"design is in its way. What is left, in this order:\n"
               f"  1. the terms count, on scratch copies and all at once -- the CEILING (every term that could "
               f"move, moved together) and one variant per term:\n"
               f"       python3 tools/bgwait.py run --group {key} --terms --name <variant> -- <its command>\n"
               f"     ({dur(TERMS_S[T])} from the first of them; no round, not the group's time)\n"
               f"  2. routing/terms/{key}.json, the table of what closed "
               f"(`python3 tools/route/budget.py terms {key}` checks it)\n"
               f"  3. tools/kpy tools/route/route.py wall {key} --blocks \"<the parts, vias, nets in the way>\"\n"
               f"which writes the question from the ledger and the table; then work on the next group "
               f"(tools/route/budget.py's docstring is the manual: THE WALL CARRIES ITS COUNT).")
    print(msg, file=sys.stderr)
    sys.exit(EXIT)


class Token:
    """One counted thing in flight.  `end()` closes it; an unclosed one is closed at exit."""

    def __init__(self, hb=None, key=None, eid=None, remaining=None, t0=None, pending=None):
        self.home, self.key, self.id, self.remaining = hb, key, eid, remaining
        self.t0, self.pending, self.result, self.ok, self.pid, self.done = t0 or time.time(), pending, None, None, os.getpid(), False
        self.facts, self.keep = {}, {}                    # of the result as it stands / of the run whatever its result

    def __bool__(self):
        return self.home is not None

    def note(self, result, ok=None, **facts):
        """The result as it stands, and its facts (`free`: it ended before any negotiation).  The
        next note replaces both."""
        self.result, self.ok, self.facts = result, ok, facts

    def fact(self, **facts):
        """A fact of the run itself, kept whatever its result (the layers it opened)."""
        self.keep.update(facts)

    def void(self, result):
        """A check that checked nothing (its plan refused as stale).  Alone it leaves no record, as
        before; inside a bgwait --group job it is recorded as `free`, so the job that held nothing
        else is not charged a round for it (board1, 2026-11-23: a stale check in a job was)."""
        if self.pending is not None and self.pending.get("job"):
            self.end(result, ok=False, free="nothing was checked")
        self.done = True

    def detach(self):
        """Somebody else ends it (bgwait's wrapper): -> what that process needs."""
        self.done = True
        return dict(board=str(self.home), key=self.key, event=self.id, limit=self.remaining)

    def end(self, result=None, ok=None, **facts):
        if self.done or not self.home or os.getpid() != self.pid:
            return
        self.done = True
        if result is None:                                # the noted result, with the facts noted beside it
            result, facts = self.result, {**self.facts, **facts}
        close(self.home, self.key, self.id, time.time() - self.t0, result,
              ok if ok is not None else self.ok, pending=self.pending, **{**self.keep, **facts})


CURRENT = Token()


def note(result, ok=None, **facts):
    """The running tool's result line, for the ledger (netgroup.py's end states).  `free="<why>"`
    says the run ended before any negotiation: its round is not counted (FREE an epoch)."""
    CURRENT.note(result, ok, **facts)


def fact(**facts):
    """A fact of the running tool's run for its ledger event, whatever its result: `layers` (the
    copper layers the run could use) and `closed` (the board's others) -- the wall quotes them."""
    CURRENT.fact(**facts)


def close(hb, key, eid, seconds, result=None, ok=None, pending=None, **facts):
    with Ledger(hb) as L:
        g = L.group(key)
        if not g:
            return
        if pending is not None:                           # a check: on the record only now, at its verdict
            e = dict(pending, id=len(g["events"]) + 1)
            g["events"].append(e)
        else:
            e = next((x for x in g["events"] if x.get("id") == eid), None)
            if e is None:
                return
        e.update(s=round(seconds, 1), result=result or "ended without a result (killed, or an error)", **facts)
        e.pop("pid", None)
        if ok is not None:
            e["ok"] = bool(ok)
        L.dirty = True
        if e.get("gate"):
            if e["s"] > GATE_S and not copper_only(e):
                e["gate_over"] = f"ran {dur(e['s'])}, over the gate's {dur(GATE_S)}: a round"
            print(f"budget: {key} -- gate job: " + (e.get("gate_over") or
                  f"{dur(e['s'])}, no round (a router run or race inside it is counted on its own)"))
        if e.get("free") and e["kind"] == "check":
            print(f"budget: {key} -- this check was refused as stale and checked nothing: on the record, no round for it")
        elif e.get("free"):
            t = tally(g)
            print(f"budget: {key} -- this run ended before any negotiation: {t['free']} such end(s) this epoch "
                  f"cost no round (at most {FREE}; one that ran beside others is counted with them). "
                  f"Round {t['rounds']} of {t['allow_rounds']}.")


def gate(board_dir, name, nets=None, kind="run", matched=False, limit=None, label="", variants=1, jid=None,
         is_gate=False):
    """Count one thing against its group, or refuse it (exit 76).  -> a Token (false on a board
    with no budget).  kind: run (the router), race, check, job, count, worker, before (`variants`
    rounds that were run before the ledger existed).  is_gate (a job only): it checks and searches
    nothing -- no round while it runs no router or race and ends within GATE_S."""
    global CURRENT
    if os.environ.get(ENV_IN):
        return Token()
    hb = home(board_dir)
    if hb is None:
        return Token()
    nets = sorted({short(n) for n in nets or []})
    now = time.time()
    with Ledger(hb) as L:
        key, d = key_for(L, name, nets)
        g = L.group(key)
        if g is None:
            if not nets and not d:
                print(f"budget: {name}: no such group in {ledger_path(hb).relative_to(hb)} or routing/groups.json, "
                      f"and nothing to size it by -- name its spec or plan instead (--group routing/{name}_spec.json)",
                      file=sys.stderr)
                sys.exit(2)
            g = L.d["groups"][key] = dict(names=[], nets=[], tier="A", basis="", epoch=1, events=[], walls=[])
        if name not in g["names"]:
            g["names"].append(name)
        if d:                                             # a declared group is sized by all it declares
            pats = [re.compile(x) for x in d.get("nets", [])]
            nets = set(nets) | {n for n in netlist(hb) if any(x.search(n) for x in pats)}
        g["nets"] = sorted(set(g["nets"]) | set(nets))
        tier, basis = classify(hb, g["nets"], matched or bool(d and d.get("matched")) or bool(g.get("matched")))
        g["matched"] = bool(matched or (d and d.get("matched")) or g.get("matched"))
        if tier > g["tier"] or not g["basis"]:
            g["tier"], g["basis"] = max(tier, g["tier"]), basis
        L.dirty = True
        what = {"run": "the router run", "race": "the race", "check": "the check", "job": "the job",
                "count": "the feasibility count", "worker": "the worker", "before": "the earlier work",
                "terms": "the terms count"}[kind]
        why = _reopen(L, g, key)
        t = tally(g, now)
        T = dict(rounds=t["allow_rounds"], seconds=t["allow_seconds"])
        if why:
            print(f"budget: {key} reopened ({why}): {T['rounds']} round(s) and {dur(T['seconds'])}, "
                  f"{share(t['frac'])} of tier {g['tier']}'s budget -- a reopen is not a refill: lay the plan the "
                  f"wall's table closed on scratch")
        ep = g["epoch"]
        if t["state"] == "wall":
            refuse(key, g, t, what)
        base = dict(t=now, kind=kind, name=name, epoch=ep, label=label)
        job = os.environ.get(ENV_JOB) if kind != "job" else None
        if job:
            base["job"] = job
        if kind == "count":
            sig = dict(rev=handoff_rev(hb), parts=parts_sha(hb, g["nets"]))
            if any(c.get("input") == sig for c in t["counts"]):
                print(f"BUDGET SPENT -- {key}: its one feasibility count for this placement and handoff revision "
                      f"is used ({dur(t['count_s'])}); a count that did not end in {dur(COUNT_S)} said \"it does "
                      f"not fit\". Lay copper, or `route.py wall {key}`.", file=sys.stderr)
                sys.exit(EXIT)
            e = dict(base, id=len(g["events"]) + 1, s=None, limit=COUNT_S, input=sig, pid=None if jid else os.getpid())
            if jid:
                e["jid"] = jid
            g["events"].append(e)
            return Token(hb, key, e["id"], COUNT_S, now)
        if kind == "terms":                               # the count a wall carries: runs when spent, in its own window
            span = TERMS_S[g["tier"]]
            t0 = min((x["t"] for x in t["terms"]), default=now)
            left = t0 + span - now
            if left <= 1:
                print(f"BUDGET SPENT -- {key}: its terms count is over ({dur(span)} from "
                      f"{time.strftime('%H:%M', time.localtime(t0))}, every variant side by side). A variant that "
                      f"gave no answer in it is `\"closes\": null` in routing/terms/{key}.json: write the table, "
                      f"then `route.py wall {key}`.", file=sys.stderr)
                sys.exit(EXIT)
            e = dict(base, id=len(g["events"]) + 1, s=None, limit=round(left, 1), pid=None if jid else os.getpid())
            if jid:
                e["jid"] = jid
            g["events"].append(e)
            print(f"budget: {key} -- terms count, variant {len(t['terms']) + 1}: it may take {dur(left)} "
                  f"(the window of {dur(span)} {'is open' if t['terms'] else 'opens now'}); no round, and not the "
                  f"group's time. Start every variant now, the ceiling among them.")
            return Token(hb, key, e["id"], left, now)
        if kind == "before":                              # rounds run before the ledger existed: only ever adds
            g["events"].append(dict(base, id=len(g["events"]) + 1, s=0, round=True, rounds=max(1, int(variants)),
                                    result="before the ledger: " + label, ok=False))
            return Token(hb, key, None, t["left_seconds"], now)
        if t["no_more"]:
            refuse(key, g, t, what)
        if kind == "check":                               # on the record at its verdict (close), never at a usage error
            CURRENT = Token(hb, key, None, t["left_seconds"], now, pending=base)
            return CURRENT
        if kind == "worker":
            g["events"].append(dict(base, id=len(g["events"]) + 1, s=0, result=label))
            return Token(hb, key, None, t["left_seconds"], now)
        lim = min(float(limit), t["left_seconds"]) if limit and limit > 0 else t["left_seconds"]
        e = dict(base, id=len(g["events"]) + 1, s=None, limit=round(lim, 1), pid=None if kind == "job" else os.getpid())
        if variants > 1:
            e["variants"] = variants
        joins = None
        if kind == "job":                                 # a round, unless the route tools inside it say otherwise
            e["jid"] = jid
            if is_gate:
                e["gate"] = True
            if t["left_rounds"] <= 0 and not is_gate:
                print(f"budget: {key} has no round left, "
                      f"{t['lay_left'] if t['lay'] else CHECKS - t['checks']} check(s)"
                      f"{' (the lay of a closing row)' if t['lay'] else ''}: this job may run "
                      f"`route.py check` and nothing else -- a router run in it is refused, and a job that "
                      f"runs neither spends the group")
        elif job:                                         # inside a job: the job is the round, six runs to a round
            g["events"].append(e)
            if tally(g, now)["left_rounds"] < 0:
                g["events"].pop()
                refuse(key, g, t, what)
            g["events"].pop()
        else:
            need = max(1, math.ceil(variants / RACE))
            if kind == "run":                             # runs in flight together are one round, up to six
                for x in reversed([x for x in g["events"] if x.get("epoch", 1) == ep]):
                    if x.get("round") and x["kind"] == "run" and x.get("s") is None and alive(x.get("pid")) \
                            and now - x["t"] < float(x.get("limit") or DEAD_S) and x.get("members", 1) < RACE:
                        joins = x
                        break
            if joins is not None:
                joins["members"] = joins.get("members", 1) + 1
            elif t["left_rounds"] < need:
                refuse(key, g, t, what)
            else:
                e["round"] = True
                if need > 1:
                    e["rounds"] = need
        g["events"].append(e)
        tok = Token(hb, key, e["id"], lim, now)
        t2 = tally(g, now)
    print(f"budget: {key} tier {g['tier']} -- round {t2['rounds']} of {T['rounds']}"
          + (" (joins the round in flight)" if joins is not None else "")
          + (f" (a gate: no round unless it runs past {dur(GATE_S)} or starts a router run or race)"
             if is_gate and kind == "job" else "")
          + f", {dur(max(0, t2['left_seconds']))} of {dur(T['seconds'])} left"
          + (f"; this {kind} may take {dur(lim)}" if lim < (limit or 9e9) or kind in ("job", "race") else ""))
    if kind != "job":
        CURRENT = tok
        atexit.register(tok.end)
        try:                                              # a TERM ends the record too (a KILL costs DEAD_S)
            if signal.getsignal(signal.SIGTERM) is signal.SIG_DFL:
                signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
        except ValueError:
            pass
    return tok


def adopt(hb, key, eid, pid):
    """bgwait's wrapper takes a job's event: its pid says whether the job is still alive."""
    with Ledger(hb) as L:
        e = next((x for x in (L.group(key) or {}).get("events", []) if x.get("id") == eid), None)
        if e is not None and e.get("s") is None:
            e["pid"], L.dirty = pid, True


def session_board():
    """The board this session works on (HW_BOARD, else the board folder the shell is in), or None."""
    b = os.environ.get("HW_BOARD", "")
    if re.fullmatch(r"board\d+", b):
        hit = sorted(KP.glob(b + "_*"))
        if hit:
            return hit[0].resolve()
    cwd = Path.cwd().resolve()
    for d in [cwd] + list(cwd.parents):
        if d.parent == KP and re.match(r"board\d+_", d.name):
            return d
    return None


def board_of(path):
    """The board folder (or the copy of one) a spec or plan file lies in."""
    for d in Path(path).resolve().parents:
        if any(d.glob("*.kicad_pcb")):
            return d
    return None


def ungrouped(name, cmd, seconds):
    """A background job charged to no group: on the record, so the time outside every budget shows."""
    hb = session_board()
    if hb is None or not ledger_path(hb).is_file() or seconds < UNGROUPED_S or os.environ.get(ENV_IN):
        return
    with Ledger(hb) as L:
        L.d.setdefault("ungrouped", []).append(dict(t=time.time(), name=name, s=round(seconds, 1),
                                                    cmd=" ".join(map(str, cmd))[:200]))
        L.dirty = True


def plan_nets(path):
    """(group name, nets, matched) of a plan or a spec file."""
    P_ = json.loads(Path(path).read_text())
    name = P_.get("group") or Path(path).stem
    if isinstance(P_.get("nets"), dict) and "window" in P_:           # a netgroup spec
        return name, list(P_["nets"]), bool(P_.get("matched"))
    nets = {x.get("net") for k in ("tracks", "vias", "zones", "polys") for x in P_.get(k, []) if isinstance(x, dict)}
    return name, sorted(short(n) for n in nets if n), bool(P_.get("matched"))


def applied(board_dir, plan_path):
    """route.py apply put a plan on the board: on the record (never refused)."""
    hb = home(board_dir)
    if hb is None or os.environ.get(ENV_IN) or Path(board_dir).resolve() != hb:
        return                                            # a rehearsal on a copy is not copper
    name, nets, _ = plan_nets(plan_path)
    with Ledger(hb) as L:
        key, _d = key_for(L, name, nets)
        g = L.group(key)
        if g is None:                                     # applied with nothing counted before it
            tier, basis = classify(hb, nets)
            g = L.d["groups"][key] = dict(names=[name], nets=sorted(nets), tier=tier, basis=basis, epoch=1,
                                          events=[], walls=[])
        g["events"].append(dict(id=len(g["events"]) + 1, t=time.time(), kind="apply", name=name,
                                epoch=g.get("epoch", 1), nets=sorted(nets), s=0,
                                result=f"{len(nets)} net(s) on copper"))
        L.dirty = True


def ripped(board_dir, name):
    """route.py rip took a group's copper off the board."""
    hb = home(board_dir)
    if hb is None or os.environ.get(ENV_IN) or Path(board_dir).resolve() != hb:
        return
    with Ledger(hb) as L:
        for g in L.d["groups"].values():
            if any(e["kind"] == "apply" and e.get("name") == name and e.get("epoch", 1) == g.get("epoch", 1)
                   for e in g["events"]):
                g["events"].append(dict(id=len(g["events"]) + 1, t=time.time(), kind="rip", name=name,
                                        epoch=g.get("epoch", 1), s=0, result="ripped out"))
                L.dirty = True


# ---------------------------------------------------------------- the terms table (what a wall carries)

def terms_path(hb, key):
    return Path(hb) / "routing" / "terms" / (re.sub(r"[^A-Za-z0-9_.-]", "_", key) + ".json")


def epoch_start(g):
    ep = g.get("epoch", 1)
    return min((e["t"] for e in g.get("events", []) if e.get("epoch", 1) == ep), default=0.0)


def last_copper(g):
    """When the group last laid copper in this epoch and kept it (an apply no rip has undone), or 0."""
    ep, kept = g.get("epoch", 1), {}
    for e in g.get("events", []):
        if e.get("epoch", 1) != ep:
            continue
        if e["kind"] == "apply":
            kept[e.get("name")] = e["t"]
        elif e["kind"] == "rip":
            kept.pop(e.get("name"), None)
    return max(kept.values(), default=0.0)


def count_base(g):
    """Where the board a terms count describes begins: the epoch's start, or the group's last kept
    copper if later. A row counted before it was counted on a board that is gone, and the window
    used before it is used no more: one new window, on today's board.  TRAP (board1 R1, 2026-11-25):
    its one window went at 23:20 to a single variant; three applies followed, the old table was
    stale, and the group could neither count nor write its wall."""
    return max(epoch_start(g), last_copper(g))


def read_terms(hb, key, since=0.0):
    """The group's terms table, checked (the docstring's THE WALL CARRIES ITS COUNT).
    -> (the table, []) or (None, what is wrong with it).  The table gains "path", "sha",
    "ceiling" (its row) and "closing" (the rows that closed, cheapest first)."""
    hb = Path(hb)
    p = terms_path(hb, key)
    rel = p.relative_to(hb)
    try:
        raw = p.read_bytes()
        D = json.loads(raw)
    except OSError:
        return None, [f"there is no {rel}"]
    except ValueError as e:
        return None, [f"{rel} is not JSON: {e}"]
    if not isinstance(D, dict):
        return None, [f"{rel}: an object with \"terms\" and \"rows\""]
    errs = []
    terms = D.get("terms") if isinstance(D.get("terms"), dict) else {}
    if not terms:
        errs.append("\"terms\" is empty: name every term that could move -- a keep-out, a spacing, a layer's use, "
                    "a part's place, a pin")
    for tid, x in terms.items():
        if not isinstance(x, dict) or x.get("kind") not in CHANGES or not str(x.get("text") or "").strip():
            errs.append(f"term {tid}: {{\"kind\": one of {', '.join(CHANGES)}, \"text\": \"the change, with its numbers\"}}")
        elif "handoff_allows" in x and not (isinstance(x["handoff_allows"], str) and x["handoff_allows"].strip()):
            errs.append(f"term {tid}: \"handoff_allows\" names the rule that already lets it move, or is left out")
    if errs:
        return None, errs
    allt = set(terms)

    def rank(mv):
        k = [CHANGES.index(terms[m]["kind"]) for m in mv]
        return (max(k) if k else -1, len(k), sum(k))

    rows = []
    for i, r in enumerate(D.get("rows") if isinstance(D.get("rows"), list) else []):
        if not isinstance(r, dict) or not isinstance(r.get("moved"), list):
            errs.append(f"row {i + 1}: {{\"moved\": [term ids], \"closes\": true | false | null, \"left\": the nets "
                        f"not closed, \"evidence\": \"a file of the board folder\"}}")
            continue
        mv = [str(m) for m in r["moved"]]
        name = f"row {i + 1} ({' + '.join(mv) or 'no term'})"
        if set(mv) - allt:
            errs.append(f"{name}: {', '.join(sorted(set(mv) - allt))} is not in \"terms\"")
            continue
        c, left = r.get("closes", "?"), r.get("left")
        if not (c is True or c is False or c is None):
            errs.append(f"{name}: \"closes\" is true, false, or null (no answer in the window)")
        elif c is True and not (left == 0 and left is not False):
            errs.append(f"{name}: it closes with \"left\": {left!r}? closed is every net of the group: 0 left")
        elif c is True and r.get("proof") not in PROOFS:
            errs.append(f"{name}: it closes -- known how? \"proof\": \"check\" (its plan passed route.py check on "
                        f"the copy), \"router\" (every net routed, the plan not through the gate yet) or \"count\" "
                        f"(a counting model, no plan). The wall prints it: a row that is not \"check\" is a lead")
        elif c is False and not (isinstance(left, int) and not isinstance(left, bool) and left > 0):
            errs.append(f"{name}: \"left\": how many nets it did not close -- the wall quotes the number")
        ev = str(r.get("evidence") or "")
        f = hb / ev
        if not ev or Path(ev).is_absolute() or ".." in Path(ev).parts or not f.is_file():
            errs.append(f"{name}: \"evidence\": the variant's result, a file inside the board folder (tmpWork/..., "
                        f"routing/...) -- {('no such file: ' + ev) if ev else 'none given'}")
        elif f.stat().st_mtime < since:
            errs.append(f"{name}: its evidence {ev} is older than the board it describes (this epoch, or the "
                        f"group's last copper, began {time.strftime('%m-%d %H:%M', time.localtime(since))}): it was "
                        f"counted for an earlier wall, on an earlier board -- count it again")
        rows.append(dict(r, moved=mv, closes=c))
    ceiling = next((r for r in rows if set(r["moved"]) == allt), None)
    if ceiling is None:
        errs.append("no CEILING row: one variant with every term moved at once (\"moved\": "
                    + json.dumps(sorted(allt)) + "). If that does not close, no single term will, and the "
                    "question is a different one")
    elif ceiling["closes"] is True and len(allt) > 1:
        for tid in sorted(allt):
            if not any(set(r["moved"]) in ({tid}, allt - {tid}) for r in rows):
                errs.append(f"term {tid} has no row of its own: the ceiling closes, so count {tid} ALONE or every "
                            f"term BUT {tid} -- which terms the group needs is the question")
    if errs:
        return None, errs
    rows.sort(key=lambda r: r is not ceiling)             # the ceiling first, the rest as written
    closing = sorted((r for r in rows if r["closes"] is True), key=lambda r: rank(r["moved"]))
    return dict(D, terms=terms, rows=rows, ceiling=ceiling, closing=closing, path=str(rel),
                sha=hashlib.sha256(raw).hexdigest()[:12]), []


def terms_lines(TT):
    """The table as the wall quotes it."""
    allt, bs = set(TT["terms"]), chr(92)
    out = [f"**The count** (`{TT['path']}`: each row one variant on a scratch copy, run side by side; the first is "
           "the CEILING, every term below moved at once):", "",
           "| moved | closes | nets left | evidence |", "|---|---|---|---|"]
    for r in TT["rows"]:
        mv, c = r["moved"], r["closes"]
        what = (f"every term ({', '.join(mv)}): the ceiling" if set(mv) == allt and len(allt) > 1 else
                " + ".join(mv) if mv else "no term: the published terms as they stand")
        out.append(f"| {what} | {PROOFS[r['proof']][0] if c is True else 'NO' if c is False else 'no answer in the window'} | "
                   f"{r['left'] if r.get('left') is not None else '?'} | `{r['evidence']}`"
                   + (f": {str(r['note']).replace('|', bs + '|')}" if r.get("note") else "") + " |")
    out += ["", "**The terms:**", ""]
    out += [f"- **{tid}** ({x['kind']}) — {str(x['text']).strip()}" for tid, x in TT["terms"].items()]
    return out


def closing_lines(TT, key):
    """What the wall offers: the rows that closed, cheapest first -- or that nothing did."""
    if not TT["closing"]:
        c = TT["ceiling"]
        return ["", "**No change the board can name closes it.** With every term above moved at once the count "
                + (f"left {c['left']} net(s) open: {key} does not fit this placement and stack-up as they stand"
                   if c["closes"] is False else
                   f"gave no answer in its window: as loose as the board can make it, {key} still does not close "
                   "in the time a group may cost")
                + ". No routing term is left to ask for: what moves next is the design's -- a part out of the "
                "group's field, a layer, a net -- and the user's, with the ceiling's evidence above "
                "(RELAY_PROTOCOL.md section 5)."]
    out = ["", "**The changes that close it, cheapest first** -- each is a row above that CLOSED on a scratch copy "
           "(placement, rule, swap, layer is the order of cost; grant one, or say the last rung allowed). PROVED: "
           "its plan passed `route.py check` and is the copper once granted. A LEAD: it closed for the router or "
           "for a count only, and the reopened group owes its gated plan before any copper:", ""]
    for i, r in enumerate(TT["closing"]):
        mv = r["moved"]
        kinds = sorted({TT["terms"][m]["kind"] for m in mv}, key=CHANGES.index)
        out.append(f"{i + 1}. **{' + '.join(kinds) if kinds else 'no term'}** — "
                   + (f"{' + '.join(mv)}: " + "; ".join(str(TT["terms"][m]["text"]).strip() for m in mv) if mv else
                      "more router effort only: the count closed on the published terms, and the budget ran out first")
                   + f" (`{r['evidence']}`) -- {PROOFS[r['proof']][1]}")
    return out


def cmd_terms(board_dir, key):
    """`budget.py terms <group>`: the table, checked as the wall will check it."""
    hb = home(board_dir)
    if hb is None:
        print(f"{board_dir}: not a session's board: no budget, no terms table", file=sys.stderr)
        return 2
    L = read(hb)
    g = L["groups"].get(key) or next((x for k, x in L["groups"].items() if key in x.get("names", [])), None)
    if g is None:
        print(f"terms: {key} is not in the ledger ({', '.join(sorted(L['groups'])) or 'empty'})", file=sys.stderr)
        return 2
    key = next(k for k, x in L["groups"].items() if x is g)
    TT, errs = read_terms(hb, key, count_base(g))
    if errs:
        print(f"TERMS FAIL -- {terms_path(hb, key).relative_to(hb)}:\n  " + "\n  ".join(errs))
        return 1
    print("\n".join(terms_lines(TT) + closing_lines(TT, key)))
    print(f"\nTERMS OK -- {len(TT['rows'])} row(s), {len(TT['closing'])} close(s): `route.py wall {key} --blocks \"...\"` "
          f"writes the question from it")
    return 0


def lay_rows(TT):
    """The closing rows of a checked table that move only what the handoff already allows (every
    moved term carries "handoff_allows"), proved by a gated plan or a routed one -- cheapest first."""
    return [r for r in TT["closing"] if r.get("proof") in ("check", "router")
            and all(str(TT["terms"][m].get("handoff_allows") or "").strip() for m in r["moved"])]


def cmd_lay(board_dir, key):
    """`budget.py lay <group>`: a spent group lays the closing row the handoff already allows."""
    hb = home(board_dir)
    if hb is None:
        print(f"{board_dir}: not a session's board: no budget", file=sys.stderr)
        return 2
    with Ledger(hb) as L:
        g = L.group(key) or next((x for x in L.d["groups"].values() if key in x.get("names", [])), None)
        if g is None:
            print(f"lay: {key} is not in the ledger", file=sys.stderr)
            return 2
        key = next(k for k, x in L.d["groups"].items() if x is g)
        t = tally(g)
        if t["lay"]:
            print(f"LAY REFUSED -- {key}: this epoch's lay is on the ledger already ({t['lay_left']} of its "
                  f"{LAY_CHECKS} check(s) left); a second is the wall's", file=sys.stderr)
            return EXIT
        if t["state"] != "spent":
            print(f"LAY REFUSED -- {key} is {t['state']}, not spent: gate the row with the round's own checks",
                  file=sys.stderr)
            return 2
        TT, errs = read_terms(hb, key, count_base(g))
        if errs:
            print(f"LAY REFUSED -- {terms_path(hb, key).relative_to(hb)}:\n  " + "\n  ".join(errs), file=sys.stderr)
            return 1
        ok = lay_rows(TT)
        if not ok:
            print(f"LAY REFUSED -- {key}: no closing row moves only terms that carry \"handoff_allows\" "
                  f"(proof check or router): every closing row needs a term granted -- `route.py wall {key}`",
                  file=sys.stderr)
            return 1
        r = ok[0]
        ep = g.get("epoch", 1)
        g["events"].append(dict(t=time.time(), kind="lay", name=key, epoch=ep, id=len(g["events"]) + 1, s=0,
                                label="lay " + (" + ".join(r["moved"]) or "no term"), table=TT["path"], sha=TT["sha"],
                                moved=r["moved"], proof=r["proof"], evidence=r.get("evidence"),
                                allows={m: TT["terms"][m]["handoff_allows"] for m in r["moved"]}))
        L.dirty = True
    print(f"budget: {key} -- LAY {' + '.join(r['moved']) or 'no term'} ({r['proof']}, {r.get('evidence')}): "
          f"{LAY_CHECKS} check(s) to gate it, its reserve gates among them (`bgwait.py run --group {key} --gate`); "
          f"no router run, no race. Then apply as a plain job.")
    for m in r["moved"]:
        print(f"  {m}: {TT['terms'][m]['handoff_allows']}")
    return 0


# ---------------------------------------------------------------- the commands (route.py budget / wall)

def table(hb, now=None):
    """Rows for every group: what `route.py budget` prints and tools/measure/sessions.py reports."""
    rows = []
    for key, g in sorted(read(hb)["groups"].items()):
        t = tally(g, now)
        rows.append(dict(group=key, tier=g.get("tier", "A"), rounds=t["rounds"], allow_rounds=t["allow_rounds"],
                         seconds=round(t["seconds"]), allow_seconds=t["allow_seconds"],
                         count_s=round(t["count_s"]), nets=len(g.get("nets", [])), nets_applied=t["nets_applied"],
                         state=t["state"] + (f" (no round left, {CHECKS - t['checks']} check(s))"
                                             if t["state"] == "open" and t["left_rounds"] <= 0 else ""),
                         walls=len(g.get("walls", [])), epoch=g.get("epoch", 1),
                         total_seconds=round(sum((e.get("s") or 0) for e in g.get("events", [])
                                                 if not e.get("job") or e["kind"] == "job"))))
    return rows


def print_table(hb):
    rows = table(hb)
    if not rows:
        print(f"no group has been counted yet ({ledger_path(hb).relative_to(hb)}); the first router run, race, "
              "check or `bgwait.py run --group` opens one")
        return 0
    print(f"{'group':18s} tier  rounds   time            count    nets  on copper  state")
    for r in rows:
        print(f"{r['group'][:18]:18s}  {r['tier']}    {r['rounds']} / {r['allow_rounds']}   "
              f"{dur(r['seconds']):>7s} / {dur(r['allow_seconds']):<6s} {dur(r['count_s']) if r['count_s'] else '-':>6s}  "
              f"{r['nets']:5d}  {r['nets_applied']:9d}  {r['state']}"
              + (f" (wall {r['walls']}, {dur(r['total_seconds'])} in all)" if r["walls"] else ""))
    ug = read(hb).get("ungrouped", [])
    if ug:
        print(f"\noutside every group: {len(ug)} background job(s), {dur(sum(u['s'] for u in ug))} "
              f"(`bgwait.py run` without --group; the longest: "
              + ", ".join(f"{u['name']} {dur(u['s'])}" for u in sorted(ug, key=lambda u: -u["s"])[:4]) + ")")
    spent = [r["group"] for r in rows if r["state"] == "spent"]
    if spent:
        print(f"\nSPENT, and no question asked yet: {', '.join(spent)} -- for each the terms count (`bgwait.py run "
              f"--group G --terms`, the ceiling first), its table, `route.py wall <group> ...`; then the next group")
    return 0


def print_group(hb, key):
    L = read(hb)
    g = L["groups"].get(key) or next((x for k, x in L["groups"].items() if key in x.get("names", [])), None)
    if g is None:
        print(f"{key}: not in the ledger ({', '.join(sorted(L['groups'])) or 'empty'})", file=sys.stderr)
        return 2
    t = tally(g)
    print(f"{key}: tier {g['tier']} ({g.get('basis', '')}); epoch {g.get('epoch', 1)}; {t['state']}; "
          f"{t['rounds']} of {t['allow_rounds']} round(s), {dur(t['seconds'])} of {dur(t['allow_seconds'])}"
          + (f" ({share(t['frac'])} of the tier's: a reopen)" if t["frac"] < 1 else "")
          + (f"; terms count open until {time.strftime('%H:%M', time.localtime(t['terms_end']))}"
             if t["terms_end"] and t["terms_end"] > time.time() else "; terms count used" if t["terms"] else "")
          + f"; names: {', '.join(g.get('names', []))}")
    for e in g.get("events", []):
        print("  " + event_line(e))
    for w in g.get("walls", []):
        print(f"  WALL {w.get('n')}: {w.get('q')}, rev {w.get('rev')}, "
              f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(w['t']))}")
    return 0


def event_line(e):
    when = time.strftime("%m-%d %H:%M", time.localtime(e["t"]))
    what = e["kind"] + (f" x{e['variants']}" if e.get("variants") else "") + (
        " [never began]" if e.get("free") else " [round]" if e.get("round") else "")
    return (f"{when}  {what:16s} {dur(e['s']) if e.get('s') is not None else 'running':>7s}  "
            f"{e.get('label') or e.get('name', '')}: {e.get('result', '')}")


def cmd_budget(board_dir, arg=None, worker=None, before=None):
    hb = home(board_dir)
    if hb is None:
        print(f"{board_dir}: not a session's board and not a copy of one: no budget", file=sys.stderr)
        return 2
    if before:                                            # work done before the ledger existed, entered once
        m = re.match(r"\s*(\d+)\s*:\s*(\S.*)$", before)
        if not m or not int(m.group(1)) or not (arg and Path(arg).is_file()):
            print("budget --before: `route.py budget <the group's spec or plan> --before \"N: what the N rounds "
                  "were and how they ended\"` (N >= 1)", file=sys.stderr)
            return 2
        name, nets, matched = plan_nets(arg)
        tok = gate(board_dir, name, nets, kind="before", matched=matched, label=m.group(2).strip(),
                   variants=int(m.group(1)))
        return print_group(hb, tok.key)
    if arg and Path(arg).is_file():
        name, nets, matched = plan_nets(arg)
        d = declared_for(hb, nets)
        tier, basis = classify(hb, nets, matched or bool(d and d.get("matched")))
        T = TIERS[tier]
        print(f"{Path(arg).name}: group {d['id'] if d else name} -- tier {tier} ({basis}): {T['rounds']} round(s), "
              f"{dur(T['seconds'])}, one feasibility count of {dur(COUNT_S)}, and before a wall a terms count of "
              f"{dur(TERMS_S[tier])}; reopened, it gets {share(REOPEN[0])} of that, then {share(REOPEN[1])}")
        return 0
    if arg and worker:
        tok = gate(board_dir, arg, kind="worker", label=worker)
        g = read(hb)["groups"][tok.key]
        t = tally(g)
        print(f"budget: {tok.key} -- worker logged. Put these lines in its brief:\n"
              f"  GROUP {tok.key} (tier {g['tier']}): {max(0, t['left_rounds'])} round(s) and "
              f"{dur(max(0, t['left_seconds']))} of recorded run time are left (the jobs, router runs and checks "
              f"the ledger counts -- not your wall-clock), and the tools count every run you start -- on your "
              f"scratch copy too."
              + (f" A LAY is open: {t['lay_left']} check(s) to gate its row, no router run, no race." if t.get('lay') else "")
              + f"\n  STOP at the first `BUDGET SPENT`, or when one round has shown what blocks: "
              f"hand back what blocks (parts, vias, nets) and EVERY term that could move, each with the edit that "
              f"moves it on a scratch copy -- the terms count's variants (budget.py, THE WALL CARRIES ITS COUNT); "
              f"do not start another run.")
        return 0
    if arg:
        return print_group(hb, arg)
    return print_table(hb)


ORDINALS = ("SECOND THIRD FOURTH FIFTH SIXTH SEVENTH EIGHTH NINTH TENTH ELEVENTH TWELFTH THIRTEENTH "
            "FOURTEENTH FIFTEENTH SIXTEENTH SEVENTEENTH EIGHTEENTH NINETEENTH TWENTIETH").split()


def ordinal(n):
    """A wall's number in its title.  TRAP (board1 NORTHGAP, 2026-11-25): the 14th wall came out "the THIRD"."""
    if 2 <= n <= len(ORDINALS) + 1:
        return ORDINALS[n - 2]
    return f"{n}" + ("th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


def cmd_wall(board_dir, key, blocks, changes, blocking=False):
    hb = home(board_dir)
    if hb is None:
        print(f"{board_dir}: not a session's board: no budget, no wall", file=sys.stderr)
        return 2
    if not blocks or not blocks.strip():
        print("wall: --blocks \"<what is in the way: the parts, vias, nets, with the numbers>\" -- the ledger knows "
              "how the rounds ended, not why", file=sys.stderr)
        return 2
    rows = []
    for c in changes or []:
        kind, _, text = c.partition(":")
        kind = kind.strip().lower()
        if kind not in CHANGES or not text.strip():
            print(f"wall: --change {c!r}: write `<kind>: <the change, and what it gains>`, kind one of "
                  f"{', '.join(CHANGES)} (that order is their cost)", file=sys.stderr)
            return 2
        rows.append((CHANGES.index(kind), kind, text.strip()))
    rows.sort()
    qf = hb / "QUESTIONS.md"
    if not qf.is_file():
        print(f"wall: no {qf.name} in {hb.name}/", file=sys.stderr)
        return 2
    m = re.match(r"(board\d+)", hb.name)
    bname = m.group(1) if m else hb.name
    with Ledger(hb) as L:
        g = L.group(key) or next((x for k, x in L.d["groups"].items() if key in x.get("names", [])), None)
        if g is None:
            print(f"wall: {key} is not in the ledger ({', '.join(sorted(L.d['groups'])) or 'empty'}): a wall is "
                  f"what a counted round found", file=sys.stderr)
            return 2
        key = next(k for k, x in L.d["groups"].items() if x is g)
        t = tally(g)
        if t["state"] == "wall":
            print(f"wall: {key} is at its wall already ({t['wall'].get('q')}); it reopens when that is answered "
                  f"or its parts move", file=sys.stderr)
            return 2
        if t["rounds"] == 0 and not t["counts"]:
            print(f"wall: {key} has no round and no count on record: run the feasibility count or one round first "
                  f"-- a wall is what a measurement found, not a guess", file=sys.stderr)
            return 2
        n_wall = len(g.get("walls", [])) + 1
        # the wall carries its count: the changes it offers are rows that closed on scratch
        TT, terrs = None, []
        if n_wall >= 2 or g.get("tier", "A") != "A" or terms_path(hb, key).is_file():
            TT, terrs = read_terms(hb, key, count_base(g))
        if terrs:
            print(f"WALL REFUSED -- {key}'s wall number {n_wall} (tier {g.get('tier', 'A')}) carries its count, and "
                  f"{terms_path(hb, key).relative_to(hb)} is not it yet:\n  " + "\n  ".join(terrs) + "\n"
                  f"Count on scratch copies, all at once: the CEILING (every term that could move, moved together) "
                  f"and one variant per term --\n    python3 tools/bgwait.py run --group {key} --terms --name "
                  f"<variant> -- <its command>\nthen write the table (`python3 tools/route/budget.py terms {key}` "
                  f"checks it) and run this again. tools/route/budget.py's docstring has the format: THE WALL "
                  f"CARRIES ITS COUNT.", file=sys.stderr)
            return 2
        if TT and rows:
            print(f"wall: no --change with a terms table: the changes this wall offers are the rows of "
                  f"{TT['path']} that closed. A change that is not there has not been counted: add its term and "
                  f"its row.", file=sys.stderr)
            return 2
        if not TT and not rows:
            print("wall: at least one --change \"placement|rule|swap|layer: ...\": a wall with no way through it is "
                  "not a question concept can answer", file=sys.stderr)
            return 2
        txt = qf.read_text()
        qn = max([int(x) for x in re.findall(r"^## Q(\d+)\b", txt, re.M)] or [0]) + 1
        ep = g.get("epoch", 1)
        ev = [e for e in g["events"] if e.get("epoch", 1) == ep and e["kind"] not in ("reopen",)]
        rev = handoff_rev(hb)
        head = blocks.strip().splitlines()[0][:110]
        second = n_wall >= 2
        title = (f"WALL {key}" + (f", the {ordinal(n_wall)}" if second else "") + f": {head}")
        if TT and not TT["closing"]:
            title = title.replace(": ", " -- DOES NOT FIT: ", 1)
        # the layers the epoch's rounds could use, and the board's others: a layer no round opened
        # is the change nobody priced (board1 R_PMIC, 2026-11-23: two walls on three layers, with
        # four more the handoff's own terms allowed)
        opened = sorted({l for e in ev for l in e.get("layers", [])})
        shut = sorted({l for e in ev for l in e.get("closed", [])} - set(opened))
        out = [f"## Q{qn} — {title}",
               f"**Status:** open · {'BLOCKING' if blocking else 'not blocking'}",
               f"**Need:** a change to the design. {key} (tier {g['tier']}: {g.get('basis', '')}) "
               + (f"has used its budget -- {t['rounds']} of {t['allow_rounds']} round(s), {dur(t['seconds'])} of "
                  f"{dur(t['allow_seconds'])}" if t["state"] == "spent" else
                  f"is stopped at {t['rounds']} of {t['allow_rounds']} round(s), {dur(t['seconds'])} of "
                  f"{dur(t['allow_seconds'])}: the cause is known and more runs would not move it")
               + f"; {t['nets_applied']} of its {len(g.get('nets', []))} net(s) are on copper. The tools refuse the "
               f"group until a handoff revision after rev {rev} answers this, or its parts move."
               + (" **This is the group's wall number " + str(n_wall) + ", a design problem: a change below that "
                  "adds a term to the design (an allowance, a rule, a part, a net, a layer's use) is the user's; one "
                  "that adds none (more router effort, your own copper re-laid on terms already published, an order "
                  "between groups) concept answers alone (RELAY_PROTOCOL.md section 5), with the earlier wall's "
                  "answer beside it.**" if second else ""),
               f"**What blocks:** {blocks.strip()}",
               *([f"**Layers:** the rounds could use {', '.join(opened)}; no round opened "
                  f"{', '.join(shut) or 'none -- every copper layer was open'}."
                  + (" For each layer not opened: the handoff row that closes it to this group -- or it is a change "
                     "below, and the cheapest one to count." if shut else "")] if opened else []),
               "**The rounds** (`routing/ledger.json`, written by the tools):", "",
               "| when | what | time | how it ended |", "|---|---|---|---|"]
        for e in ev:
            what = e["kind"] + (f" x{e['variants']}" if e.get("variants") else "") + (
                ", ended before negotiating" if e.get("free") else ", round" if e.get("round") else "")
            res = str(e.get("result", "")).replace("|", "\\|")
            out.append(f"| {time.strftime('%m-%d %H:%M', time.localtime(e['t']))} | {what}: "
                       f"{str(e.get('label') or e.get('name', '')).replace('|', chr(92) + '|')} | "
                       f"{dur(e['s']) if e.get('s') is not None else 'no end'} | {res} |")
        if TT:
            out += [""] + terms_lines(TT) + closing_lines(TT, key)
        else:
            out += ["", "**The changes that would unblock it, cheapest first** (placement, rule, swap, layer -- "
                    "a ladder: say the last rung allowed):", ""]
            out += [f"{i + 1}. **{kind}** — {text}" for i, (_, kind, text) in enumerate(rows)]
        nxt = allowance(dict(g, epoch=ep + 1, events=[dict(kind="reopen", epoch=ep + 1,
                                                           frac=REOPEN[0] if n_wall <= 1 else REOPEN[1])]))
        out += ["", f"**Without an answer I would:** leave {key} unrouted and work on the next group; no value is chosen.",
                f"**When it reopens:** {nxt['rounds']} round(s) and {dur(nxt['seconds'])}, {share(nxt['frac'])} of its "
                f"tier's budget -- enough to re-prove and lay a plan that closed above, not to search again.",
                f"**Working from:** rev {rev}."]
        qf.write_text(txt.rstrip("\n") + "\n\n" + "\n".join(out) + "\n")
        w = dict(t=time.time(), epoch=ep, n=n_wall, q=f"Q{qn}", rev=rev,
                 parts=parts_sha(hb, g.get("nets", [])), blocks=blocks.strip(),
                 changes=[f"{k}: {x}" for _, k, x in rows])
        if TT:                                            # the table as the wall quoted it, kept beside it
            kept = terms_path(hb, key).with_name(f"{terms_path(hb, key).stem}.wall{n_wall}.json")
            kept.write_bytes(terms_path(hb, key).read_bytes())
            w["terms"] = dict(path=str(kept.relative_to(hb)), sha=TT["sha"], ceiling=TT["ceiling"]["closes"],
                              closing=[" + ".join(r["moved"]) or "no term" for r in TT["closing"]])
            w["changes"] = w["terms"]["closing"]
        g.setdefault("walls", []).append(w)
        L.dirty = True
    print(f"wrote Q{qn} into {qf.name} from the ledger ({len(ev)} event(s)). {key} is closed until rev > {rev} "
          f"answers Q{qn} or its parts move.")
    print(f"ring: {bname} Q{qn} {'BLOCKING' if blocking else 'not blocking'} — {title[:150]}")
    print("then: the next group. `route.py budget` shows every group's state.")
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="the budget table, without KiCad (route.py budget is the same)")
    ap.add_argument("--board", default=".", help="the board folder, or boardN (default: here)")
    ap.add_argument("group", nargs="*", metavar="[terms] GROUP",
                    help="a group: its record. `terms GROUP`: check routing/terms/GROUP.json as the wall will. "
                         "`lay GROUP`: a spent group lays its closing row the handoff already allows")
    a_ = ap.parse_args()
    bd_ = a_.board
    if re.fullmatch(r"board\d+", bd_) and sorted(KP.glob(bd_ + "_*")):
        bd_ = str(sorted(KP.glob(bd_ + "_*"))[0])
    elif bd_ == "." and home(".") is None and session_board() is not None:
        bd_ = str(session_board())
    if a_.group[:1] == ["terms"]:
        if len(a_.group) != 2:
            ap.error("terms GROUP")
        sys.exit(cmd_terms(bd_, a_.group[1]))
    if a_.group[:1] == ["lay"]:
        if len(a_.group) != 2:
            ap.error("lay GROUP")
        sys.exit(cmd_lay(bd_, a_.group[1]))
    sys.exit(cmd_budget(bd_, a_.group[0] if a_.group else None))
