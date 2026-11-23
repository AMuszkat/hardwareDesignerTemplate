#!/usr/bin/env python3
"""The effort budget: how much a routing group may cost before the DESIGN has to change.

    tools/kpy tools/route/route.py budget [--board board1]              # every group: tier, rounds, time, state
    tools/kpy tools/route/route.py budget R_emmc                        # one group's record, event by event
    tools/kpy tools/route/route.py budget routing/R_emmc_spec.json      # what tier a spec or a plan would get
    tools/kpy tools/route/route.py budget R_emmc --worker "one count: the west strip's via sites"
    tools/kpy tools/route/route.py budget routing/R_emmc_spec.json --before "1: the six-variant count of 11-22, 0 of 14"
                                                                        # rounds run before the ledger existed
    tools/kpy tools/route/route.py wall R_emmc --blocks "..." --change "placement: ..." [--change "rule: ..."]

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

SPENT means: the rounds are used and another would start, or the time is used.  Then
`route.py wall <group>` is the one command left.  It writes the question from the ledger into
QUESTIONS.md -- every round and how it ended, what blocks (yours to say: parts, vias, nets),
and the changes that would unblock it in the fixed order of what they cost: placement, rule,
swap (a pin or a part), layer -- and prints the ring.  Then the board moves to the next group.
A wall may be declared BEFORE the budget is spent, and should be when a round has shown the
cause: the budget is a ceiling, not a target.

THE BUDGET REOPENS ONLY WHEN THE INPUT CHANGES: the handoff answers the wall's question in a
later revision (handoff/07_answers.md carries its `## Qn`), or one of the group's own parts
has moved on the board.  Not by a new name, a new seed or a new day.  A group that reaches a
SECOND wall goes to the user: its question says so, and concept does not answer it alone
(RELAY_PROTOCOL.md section 5).  And a group whose nets are ALL on copper is finished: work on
it after that (a review's fix, a neighbour that needs room) starts a new budget by itself.

NOT COUNTED, NEVER REFUSED: `route.py apply` (copper is the point), `rip`, `audit`, `status`,
`render`; a check that ends in a usage error.  HW_BUDGET_IN is set by race.py and prelayout.py
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
CHECKS = 3                   # checks a round carries: its first, and two after a hand fix
RACE = 6                     # variants, or runs in flight together, that are one round
DEAD_S = 600                 # charged for a run that died without a word (killed -9)
UNGROUPED_S = 60             # a background job of no group shorter than this is not worth a line
EXIT = 76                    # BUDGET SPENT
ENV_IN = "HW_BUDGET_IN"      # set by a tool that logs its children's cost itself
ENV_JOB = "HW_BUDGET_JOB"    # a job id: set by bgwait.py --group for its command; the job owns the time
CHANGES = ("placement", "rule", "swap", "layer")          # the fixed order of cost
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


def cost(e, now):
    """Seconds an event has cost: its own, or for one with no end its age up to its limit
    (still running) or DEAD_S (its process is gone and never said how it ended)."""
    if e.get("s") is not None:
        return float(e["s"])
    lim = float(e.get("limit") or DEAD_S)
    if e.get("pid") and not alive(e["pid"]):
        lim = min(lim, DEAD_S)
    return max(0.0, min(lim, now - e["t"]))


def tally(g, now=None):
    """What the group's current epoch has used."""
    now = now or time.time()
    ep = g.get("epoch", 1)
    ev = [e for e in g.get("events", []) if e.get("epoch", 1) == ep]
    in_runs, in_checks = {}, {}                          # what the route tools did inside a bgwait --group job
    for e in ev:
        j = e.get("job")
        if j and e["kind"] in ("run", "race"):
            in_runs[j] = in_runs.get(j, 0) + int(e.get("variants", 1))
        elif j and e["kind"] == "check":
            in_checks[j] = in_checks.get(j, 0) + 1
    rounds = checks = 0
    secs = count_s = last = 0.0
    counts, on_copper, t_apply = [], {}, 0.0
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
        elif k in ("reopen", "worker", "apply", "rip"):
            pass
        elif k == "job":                                 # the job owns its time; what ran inside it says what it was
            secs, last = secs + s, e["t"]
            n = in_runs.get(e.get("jid"), 0)
            if n:
                rounds, checks = rounds + math.ceil(n / RACE), 0
            elif not in_checks.get(e.get("jid")):
                rounds, checks = rounds + 1, 0
        else:
            last = e["t"]
            if not e.get("job"):
                secs += s
            if k == "check":
                if rounds == 0 or checks >= CHECKS:
                    rounds, checks = rounds + 1, 1
                else:
                    checks += 1
            elif e.get("round") and not e.get("job"):
                rounds, checks = rounds + int(e.get("rounds", 1)), 0
    T = TIERS[g.get("tier", "A")]
    wall = next((w for w in reversed(g.get("walls", [])) if w.get("epoch", 1) == ep), None)
    left_r, left_s = T["rounds"] - rounds, T["seconds"] - secs
    copper = set().union(*on_copper.values()) if on_copper else set()
    nets_applied = len(copper & set(g.get("nets", []))) if g.get("nets") else len(copper)
    complete = bool(copper) and set(g.get("nets", [])) <= copper
    no_more = left_s <= 0 or left_r < 0 or (left_r <= 0 and checks >= CHECKS)
    state = ("wall" if wall else "applied" if copper and t_apply >= last else "spent" if no_more else "open")
    return dict(rounds=rounds, seconds=secs, checks=checks, counts=counts, count_s=count_s, complete=complete,
                nets_applied=nets_applied, left_rounds=left_r, left_seconds=left_s, state=state, wall=wall,
                no_more=no_more, allow_rounds=T["rounds"], allow_seconds=T["seconds"])


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
    if why:
        g["epoch"] = g.get("epoch", 1) + 1
        g["events"].append(dict(t=time.time(), kind="reopen", epoch=g["epoch"], result=why))
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
               f"design is in its way. The one command left is\n    tools/kpy tools/route/route.py wall {key} "
               f"--blocks \"<the parts, vias, nets in the way>\" --change \"placement: ...\" "
               f"[--change \"rule: ...\"] [--change \"swap: ...\"] [--change \"layer: ...\"]\n"
               f"which writes the question from the ledger; then work on the next group "
               f"(tools/route/budget.py's docstring is the manual).")
    print(msg, file=sys.stderr)
    sys.exit(EXIT)


class Token:
    """One counted thing in flight.  `end()` closes it; an unclosed one is closed at exit."""

    def __init__(self, hb=None, key=None, eid=None, remaining=None, t0=None, pending=None):
        self.home, self.key, self.id, self.remaining = hb, key, eid, remaining
        self.t0, self.pending, self.result, self.ok, self.pid, self.done = t0 or time.time(), pending, None, None, os.getpid(), False

    def __bool__(self):
        return self.home is not None

    def note(self, result, ok=None):
        self.result, self.ok = result, ok

    def detach(self):
        """Somebody else ends it (bgwait's wrapper): -> what that process needs."""
        self.done = True
        return dict(board=str(self.home), key=self.key, event=self.id, limit=self.remaining)

    def end(self, result=None, ok=None, **facts):
        if self.done or not self.home or os.getpid() != self.pid:
            return
        self.done = True
        close(self.home, self.key, self.id, time.time() - self.t0, result if result is not None else self.result,
              ok if ok is not None else self.ok, pending=self.pending, **facts)


CURRENT = Token()


def note(result, ok=None):
    """The running tool's result line, for the ledger (netgroup.py's end states)."""
    CURRENT.note(result, ok)


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


def gate(board_dir, name, nets=None, kind="run", matched=False, limit=None, label="", variants=1, jid=None):
    """Count one thing against its group, or refuse it (exit 76).  -> a Token (false on a board
    with no budget).  kind: run (the router), race, check, job, count, worker, before (`variants`
    rounds that were run before the ledger existed)."""
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
                "count": "the feasibility count", "worker": "the worker", "before": "the earlier work"}[kind]
        why = _reopen(L, g, key)
        if why:
            print(f"budget: {key} reopened ({why}): a new budget, tier {g['tier']}")
        t = tally(g, now)
        T = TIERS[g["tier"]]
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
            if t["left_rounds"] <= 0:
                print(f"budget: {key} has no round left, {CHECKS - t['checks']} check(s): this job may run "
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
              + (f" (wall {r['walls']})" if r["walls"] else ""))
    ug = read(hb).get("ungrouped", [])
    if ug:
        print(f"\noutside every group: {len(ug)} background job(s), {dur(sum(u['s'] for u in ug))} "
              f"(`bgwait.py run` without --group; the longest: "
              + ", ".join(f"{u['name']} {dur(u['s'])}" for u in sorted(ug, key=lambda u: -u["s"])[:4]) + ")")
    spent = [r["group"] for r in rows if r["state"] == "spent"]
    if spent:
        print(f"\nSPENT, and no question asked yet: {', '.join(spent)} -- `route.py wall <group> ...` each, then the next group")
    return 0


def print_group(hb, key):
    L = read(hb)
    g = L["groups"].get(key) or next((x for k, x in L["groups"].items() if key in x.get("names", [])), None)
    if g is None:
        print(f"{key}: not in the ledger ({', '.join(sorted(L['groups'])) or 'empty'})", file=sys.stderr)
        return 2
    t = tally(g)
    print(f"{key}: tier {g['tier']} ({g.get('basis', '')}); epoch {g.get('epoch', 1)}; {t['state']}; "
          f"{t['rounds']} of {t['allow_rounds']} round(s), {dur(t['seconds'])} of {dur(t['allow_seconds'])}; "
          f"names: {', '.join(g.get('names', []))}")
    for e in g.get("events", []):
        print("  " + event_line(e))
    for w in g.get("walls", []):
        print(f"  WALL {w.get('n')}: {w.get('q')}, rev {w.get('rev')}, "
              f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(w['t']))}")
    return 0


def event_line(e):
    when = time.strftime("%m-%d %H:%M", time.localtime(e["t"]))
    what = e["kind"] + (f" x{e['variants']}" if e.get("variants") else "") + (" [round]" if e.get("round") else "")
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
              f"{dur(T['seconds'])}, and one feasibility count of {dur(COUNT_S)}")
        return 0
    if arg and worker:
        tok = gate(board_dir, arg, kind="worker", label=worker)
        g = read(hb)["groups"][tok.key]
        t = tally(g)
        print(f"budget: {tok.key} -- worker logged. Put these lines in its brief:\n"
              f"  GROUP {tok.key} (tier {g['tier']}): {max(0, t['left_rounds'])} round(s) and "
              f"{dur(max(0, t['left_seconds']))} are left, and the tools count every run you start -- on your "
              f"scratch copy too.\n  STOP at the first `BUDGET SPENT`, or when one round has shown what blocks: "
              f"hand back what blocks (parts, vias, nets) and the change that would unblock it; do not start another run.")
        return 0
    if arg:
        return print_group(hb, arg)
    return print_table(hb)


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
    if not rows:
        print("wall: at least one --change \"placement|rule|swap|layer: ...\": a wall with no way through it is "
              "not a question concept can answer", file=sys.stderr)
        return 2
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
        txt = qf.read_text()
        qn = max([int(x) for x in re.findall(r"^## Q(\d+)\b", txt, re.M)] or [0]) + 1
        n_wall = len(g.get("walls", [])) + 1
        ep = g.get("epoch", 1)
        ev = [e for e in g["events"] if e.get("epoch", 1) == ep and e["kind"] not in ("reopen",)]
        rev = handoff_rev(hb)
        head = blocks.strip().splitlines()[0][:110]
        second = n_wall >= 2
        title = (f"WALL {key}" + (f", the {['', '', 'SECOND', 'THIRD'][min(n_wall, 3)]} -- for the user" if second else "")
                 + f": {head}")
        out = [f"## Q{qn} — {title}",
               f"**Status:** open · {'BLOCKING' if blocking else 'not blocking'}",
               f"**Need:** a change to the design. {key} (tier {g['tier']}: {g.get('basis', '')}) "
               + (f"has used its budget -- {t['rounds']} of {t['allow_rounds']} round(s), {dur(t['seconds'])} of "
                  f"{dur(t['allow_seconds'])}" if t["state"] == "spent" else
                  f"is stopped at {t['rounds']} of {t['allow_rounds']} round(s), {dur(t['seconds'])} of "
                  f"{dur(t['allow_seconds'])}: the cause is known and more runs would not move it")
               + f"; {t['nets_applied']} of its {len(g.get('nets', []))} net(s) are on copper. The tools refuse the "
               f"group until a handoff revision after rev {rev} answers this, or its parts move."
               + (" **This is the group's wall number " + str(n_wall) + ": it goes to the user "
                  "(RELAY_PROTOCOL.md section 5), with the earlier wall's answer beside it.**" if second else ""),
               f"**What blocks:** {blocks.strip()}",
               "**The rounds** (`routing/ledger.json`, written by the tools):", "",
               "| when | what | time | how it ended |", "|---|---|---|---|"]
        for e in ev:
            what = e["kind"] + (f" x{e['variants']}" if e.get("variants") else "") + (", round" if e.get("round") else "")
            res = str(e.get("result", "")).replace("|", "\\|")
            out.append(f"| {time.strftime('%m-%d %H:%M', time.localtime(e['t']))} | {what}: "
                       f"{str(e.get('label') or e.get('name', '')).replace('|', chr(92) + '|')} | "
                       f"{dur(e['s']) if e.get('s') is not None else 'no end'} | {res} |")
        out += ["", "**The changes that would unblock it, cheapest first** (placement, rule, swap, layer -- "
                "a ladder: say the last rung allowed):", ""]
        out += [f"{i + 1}. **{kind}** — {text}" for i, (_, kind, text) in enumerate(rows)]
        out += ["", f"**Without an answer I would:** leave {key} unrouted and work on the next group; no value is chosen.",
                f"**Working from:** rev {rev}."]
        qf.write_text(txt.rstrip("\n") + "\n\n" + "\n".join(out) + "\n")
        g.setdefault("walls", []).append(dict(t=time.time(), epoch=ep, n=n_wall, q=f"Q{qn}", rev=rev,
                                              parts=parts_sha(hb, g.get("nets", [])), blocks=blocks.strip(),
                                              changes=[f"{k}: {x}" for _, k, x in rows]))
        L.dirty = True
    print(f"wrote Q{qn} into {qf.name} from the ledger ({len(ev)} event(s)). {key} is closed until rev > {rev} "
          f"answers Q{qn} or its parts move.")
    print(f"ring: {bname} Q{qn} {'BLOCKING' if blocking else 'not blocking'} — {title[:150]}")
    print("then: the next group. `route.py budget` shows every group's state.")
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="the budget table, without KiCad (route.py budget is the same)")
    ap.add_argument("--board", default=".")
    ap.add_argument("group", nargs="?")
    a_ = ap.parse_args()
    sys.exit(cmd_budget(a_.board, a_.group))
