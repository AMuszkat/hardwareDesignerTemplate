#!/usr/bin/env python3
"""restage.py -- re-stage a staged router spec from the router's own report, many times inside one job.

WHAT IT IS FOR.  A staged negotiation (netgroup.py: every leg has a "stage"; the legs of one stage are
negotiated together and then frozen) can end BLOCKED: a net of stage k+1 is shut in at its source or its
goal by copper that stage k froze.  The router then says what to do -- "give X's leg an earlier stage (or
theirs a later one) so the two are negotiated together" -- and a person moved one net and started the
next round.  restage.py does that move itself and runs the router again, until the group closes, the
time is up, or the report names something a stage cannot change.

    restage.py --spec SPEC --workdir DIR --deadline EPOCH [--policy pull|push] [--iter-cap S]
               [--max-iter N] [--reserve S] [--live PATH] [--continue] [--seed-log LOG --seed-exit N]
               -- <router command with {spec} and {timeout}>
    restage.py --dry --spec SPEC --log LOG [--exit N] [--policy pull|push]     the move one log asks for
    restage.py --selftest                                                      tools/route/test_restage.py

Every iteration runs the router on the current spec with {timeout} = min(--iter-cap, the time left
before --deadline minus --reserve) and keeps DIR/iterNN.spec.json, DIR/iterNN.log and one row of
DIR/RESTAGE.md (iteration, stage, state, blocked net, wallers, the move).  {spec} is DIR/iterNN.spec.json,
or --live PATH when given: the spec is then installed at PATH before each run (a router that records
where its spec lives, and a check that refuses a plan whose spec moved, both see one path), and the
command may name PATH itself and carry no {spec}.  --seed-log LOG --seed-exit N: iteration 1 is not run --
LOG is taken as the router's report on SPEC (a round before this one ran exactly this spec and its log is
kept); it costs no run and is not counted by --max-iter.  The spec must be the one that log came from.

WHAT IT READS.  The router's log only (its exit code is a cross-check): the last `--- leg N:` header (the
spec's stage N-1), every `BLOCKED SOURCE|GOAL: NET is shut in at its ..., near (x, y) mm` block with its
wall rows and its `walled by A, B's fixed copper` line (the complete list of nets that wall it), and the
closing line `BLOCKED | PLATEAU | STUCK | NO SOLUTION at this leg` or `STOPPED at the timeout`.

A WALL HAS A KIND.  A waller that is a net of the spec with a leg in an EARLIER stage is a `leg` (this
run froze it: a stage can move it).  A waller of the same or a later stage is that net's prelaid
`reserve` (the spec lays it; no stage moves it).  Everything else is the `board`.

THE POLICIES, for a blocked net that has at least one leg-waller:
  pull   the blocked leg moves INTO the earliest stage among its leg-wallers: negotiated together.
  push   the blocked leg gets a NEW stage of its own just before that stage: laid first and frozen,
         the wallers go round it.
A stage that stalls (plateau, STUCK, the timeout, no solution) under `pull`, and holds a leg an earlier
pull put there: the pull is undone (and every move after it) and that net is pushed instead, once a net.
A net of several legs keeps its leg order: only the blocked leg moves, and never before its predecessor.
Stage numbers stay 0..n-1 without a gap; the order of the nets in the spec is never changed.

EXIT CODES (the last line of RESTAGE.md says the same in words):
  0  closed: the router exited 0 on DIR/final.spec.json (iteration NN's spec; its plan is the router's)
  2  usage, or a spec it cannot read
  5  BLOCKED and no stage can open it: the net, the place and every wall with its kind are named
     (only reserves / the board wall it, or the move would put a leg before its predecessor)
  6  a stage stalled and no move is left: the stage and its contested nets are named
  7  a cycle: the move leads to a stage map this run has already tried
  8  the router did not negotiate (refused, a usage error, a crash): nothing was learnt, the row is
     null and not a `does not close`
  9  --deadline or --max-iter reached with the group open; DIR/next.spec.json is the spec not yet run
     (--continue with the same DIR goes on from it, moves and seen maps kept)

EXAMPLE
    restage.py --spec routing/G.json --workdir tmpWork/G/restage --deadline $(( $(date +%s) + 1200 )) \\
        --policy pull --iter-cap 420 -- tools/kpy tools/route/netgroup.py --board COPY {spec} \\
        --timeout {timeout} --plateau 40

A ROUTER RUN IS A RUN OF THE GROUP'S BUDGET (budget.py: RACE runs are one round), and no run resumes the
one before it: every iteration negotiates every stage again from the first.  N variants x K iterations
are ceil(N*K / RACE) rounds; --max-iter is how a job keeps to what it was granted.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

RE_LEG = re.compile(r"^--- leg (\d+): (.*)$")
RE_BLK = re.compile(r"^BLOCKED (SOURCE|GOAL): (\S+) is shut in at its (source|goal)"
                    r"(?:, near \(([-\d.]+), ([-\d.]+)\) mm)?")
RE_ROW = re.compile(r"^\s+([\d.]+) mm\s+(\S+)\s+(.*?)\s+-- (\d+) cell\(s\)"
                    r"(?:, nearest at \(([-\d.]+), ([-\d.]+)\))?\s*$")
RE_MORE = re.compile(r"^\s+\.\.\. and (\d+) more")
RE_WALLED = re.compile(r"^\s+walled by (.+?)'s fixed copper")
RE_OWN = re.compile(r"^(\S+)'s own copper")
RE_END = re.compile(r"^(BLOCKED|PLATEAU|STUCK|NO SOLUTION) at this leg|^(STOPPED) at the timeout")
RE_CONT = re.compile(r"^\s+(\S+): (\d+) contested cell\(s\)")
RE_STUCKNETS = re.compile(r"^\s+nets: (.*)$")
RE_ITER = re.compile(r"^\s+iteration (\d+): pres")
STALLS = ("plateau", "stuck", "stopped", "nosolution", "killed")
REFUSED = "BUDGET SPENT"


class Stop(Exception):
    def __init__(self, code, text):
        Exception.__init__(self, text)
        self.code, self.text = code, text


# ---------------------------------------------------------------------------------------------- the spec
def short(n):
    return n.split("/")[-1]


def stage_map(spec):
    """{(net, leg index): stage}; a leg without "stage" has its index, as the router reads it."""
    m = {}
    for n, e in spec["nets"].items():
        legs = e.get("legs") if isinstance(e, dict) else None
        if not legs:
            raise Stop(2, f"spec: net {n} has no \"legs\": restage.py moves legs between stages")
        for k, lg in enumerate(legs):
            m[(n, k)] = int(lg.get("stage", k))
    return m


def check_map(m):
    """The invariants every spec restage.py writes must keep; a list of what is broken."""
    bad = []
    st = sorted(set(m.values()))
    if st != list(range(len(st))):
        bad.append(f"stages {st} are not 0..{len(st) - 1}")
    for (n, k), s in m.items():
        if k and m[(n, k - 1)] >= s:
            bad.append(f"{n}: leg {k + 1} in stage {s} is not after leg {k} in stage {m[(n, k - 1)]}")
    return bad


def with_map(spec, m):
    """A copy of the spec with every leg's stage set from m, renumbered 0..n-1; net order kept."""
    rank = {s: i for i, s in enumerate(sorted(set(m.values())))}
    out = json.loads(json.dumps(spec))
    for n, e in out["nets"].items():
        for k, lg in enumerate(e["legs"]):
            lg["stage"] = rank[m[(n, k)]]
    return out


def frozen(m):
    return tuple(sorted((n, k, s) for (n, k), s in m.items()))


def stages_text(m):
    by = {}
    for (n, k), s in m.items():
        by[s] = by.get(s, 0) + 1
    return " ".join(f"{s}:{by[s]}" for s in sorted(by))


# ----------------------------------------------------------------------------------------------- the log
def parse_log(text, code=None):
    """The router's report as a dict: stage (the spec's, None when no leg began), state, iteration,
    blocked [{net, end, at, walls [{dist, layer, what, cells, at}], more, wallers [net]}], contested."""
    lines = text.splitlines()
    last = None
    for i, ln in enumerate(lines):
        if RE_LEG.match(ln):
            last = i
    stage = int(RE_LEG.match(lines[last]).group(1)) - 1 if last is not None else None
    tail = lines[last:] if last is not None else lines
    end, itn, contested = None, 0, []
    for ln in tail:
        m = RE_END.match(ln)
        if m:
            end = (m.group(1) or m.group(2)).lower().replace(" ", "")
        m = RE_ITER.match(ln)
        if m:
            itn = int(m.group(1))
    for ln in tail:
        m = RE_CONT.match(ln)
        if m and m.group(1) not in contested:
            contested.append(m.group(1))
        m = RE_STUCKNETS.match(ln)
        if m:
            for w in re.findall(r"(\S+) \(\d+ cell\(s\)\)", m.group(1)):
                if w not in contested:
                    contested.append(w)
    blocks, cur, section = {}, None, None
    for ln in lines:
        m = RE_BLK.match(ln)
        if m:
            cur = {"net": m.group(2), "end": m.group(3),
                   "at": [float(m.group(4)), float(m.group(5))] if m.group(4) else None,
                   "walls": [], "more": 0, "wallers": []}
            blocks[cur["net"]] = cur
            section = None
            continue
        if cur is None:
            continue
        if ln[:1] not in (" ", "\t"):                     # a line at the margin ends the block
            cur = None
            continue
        if "what closes it" in ln:
            section = "wall"
        elif "no layer change inside it" in ln:
            section = "via"
        m = RE_ROW.match(ln)
        if m and section:
            cur["walls"].append({"dist": float(m.group(1)), "layer": m.group(2), "what": m.group(3),
                                 "cells": int(m.group(4)), "section": section,
                                 "at": [float(m.group(5)), float(m.group(6))] if m.group(5) else None})
            continue
        m = RE_MORE.match(ln)
        if m and section == "wall":
            cur["more"] = int(m.group(1))
        m = RE_WALLED.match(ln)
        if m:
            cur["wallers"] = [w.strip() for w in m.group(1).split(",") if w.strip()]
    for b in blocks.values():                             # a net row of the table is a waller too
        for w in b["walls"]:
            o = RE_OWN.match(w["what"])
            if o and w["section"] == "wall" and o.group(1) not in b["wallers"]:
                b["wallers"].append(o.group(1))
    if code == 0:
        state = "closed"
    elif end == "blocked":
        state = "blocked" if blocks else "unreadable"
    elif end in ("plateau", "stuck", "stopped", "nosolution"):
        state = end
    elif REFUSED in text:
        state = "refused"
    elif stage is not None and code is not None and (code < 0 or code in (124, 137, 143)):
        state = "killed"
    else:
        state = "unreadable"
    return {"stage": stage, "state": state, "iteration": itn, "contested": contested,
            "blocked": list(blocks.values()) if state == "blocked" else []}


# --------------------------------------------------------------------------------------------- the moves
def spec_key(spec, name):
    if name in spec["nets"]:
        return name
    hit = [n for n in spec["nets"] if short(n) == short(name)]
    return hit[0] if len(hit) == 1 else None


def classify(spec, m, b, stage):
    """The walls of one blocked report, each with its kind.  -> (key of the blocked leg, [(what, kind,
    stage or None)], earliest leg-waller stage or None)."""
    net = spec_key(spec, b["net"])
    if net is None:
        raise Stop(8, f"the report names {b['net']}, which is not a net of the spec")
    legs = [k for (n, k), s in m.items() if n == net and s == stage]
    if not legs:
        raise Stop(8, f"the report blocks {b['net']} at stage {stage}, where the spec has no leg of it")
    key = (net, legs[0])
    walls, target = [], None
    for w in b["wallers"]:
        wn = spec_key(spec, w)
        if wn is None:
            walls.append((w, "board", None))
            continue
        early = sorted(s for (n, k), s in m.items() if n == wn and s < stage)
        if early:
            walls.append((w, "leg", early[0]))
            target = early[0] if target is None else min(target, early[0])
        else:
            walls.append((w, "reserve", None))
    for w in b["walls"]:
        if w["section"] == "wall" and RE_OWN.match(w["what"]):
            continue
        kind = "spec" if "the spec's" in w["what"] else "board"
        walls.append((f"{w['layer']} {w['what']}" + (" (no via)" if w["section"] == "via" else ""), kind, None))
    return key, walls, target


def walls_text(walls, full=False):
    legs = [f"{short(w)}/s{s}" for w, k, s in walls if k == "leg"]
    res = [short(w) for w, k, s in walls if k == "reserve"]
    other = [f"{w} [{k}]" for w, k, s in walls if k in ("board", "spec")]
    out = []
    if legs:
        out.append("legs " + " ".join(legs))
    if res:
        out.append("reserves " + " ".join(res))
    if other:
        out.append(("; ".join(other)) if full else f"{len(other)} board/spec wall(s)")
    return "; ".join(out) if out else "no wall named"


def apply_move(m, key, target, policy):
    """The new map (not yet renumbered) or a Stop(5) when the leg would pass its predecessor."""
    net, k = key
    new = dict(m)
    if policy == "push":
        for kk in new:
            if new[kk] >= target:
                new[kk] += 1
    new[key] = target
    if k and new[(net, k - 1)] >= target:
        raise Stop(5, f"{short(net)}: leg {k + 1} cannot go to stage {target} ({policy}): its leg {k} "
                      f"is in stage {m[(net, k - 1)]} and a net's legs keep their order")
    rank = {s: i for i, s in enumerate(sorted(set(new.values())))}
    return {kk: rank[s] for kk, s in new.items()}


def decide(spec, rep, policy, pushed, moves, load_before):
    """What the report asks for.  -> (new spec, cells of the RESTAGE.md row, the move records)
    or raises Stop(5 | 6 | 8)."""
    m = stage_map(spec)
    if rep["state"] == "blocked":
        if rep["stage"] is None:
            raise Stop(8, "BLOCKED before any leg began: the set-up stopped the run, not a stage")
        cur, recs, cells, dead = dict(m), [], [], []
        for b in rep["blocked"]:
            net = spec_key(spec, b["net"])
            legs = [k for (n, k), s in m.items() if n == net and s == rep["stage"]] if net else []
            now = cur[(net, legs[0])] if legs else rep["stage"]
            key, walls, target = classify(spec, cur, b, now)
            at = "(%.3f, %.3f)" % tuple(b["at"]) if b["at"] else "(no cell on the grid)"
            if target is None:
                dead.append(f"{short(b['net'])} is shut in at its {b['end']} near {at} and no stage opens it: "
                            + walls_text(walls, full=True))
                continue
            pol = "push" if key[0] in pushed else policy
            new = apply_move(cur, key, target, pol)
            recs.append({"net": key[0], "leg": key[1], "policy": pol, "from": now, "to": new[key],
                         "end": b["end"], "at": b["at"]})
            cells.append((short(b["net"]) + f" {b['end']} {at}", walls_text(walls),
                          f"{pol} {short(key[0])}" + (f"#{key[1] + 1}" if key[1] else "")
                          + f": stage {now} -> {new[key]}" + (" (new)" if pol == "push" else "")))
            cur = new
        if dead:
            raise Stop(5, "; ".join(dead))
        return with_map(spec, cur), cells, recs
    if rep["state"] in STALLS:
        st = rep["stage"]
        names = " ".join(short(n) for n in rep["contested"]) or "none named"
        why = f"stage {st} {rep['state']} at iteration {rep['iteration']}, contested: {names}"
        if policy == "pull":
            for i in range(len(moves) - 1, -1, -1):
                mv = moves[i]
                if mv["policy"] == "pull" and mv["net"] not in pushed and m.get((mv["net"], mv["leg"])) == st:
                    before = load_before(mv["iter"])
                    bm = stage_map(before)
                    key = (mv["net"], mv["leg"])
                    new = apply_move(bm, key, mv["to"], "push")
                    rec = {"net": key[0], "leg": key[1], "policy": "push", "from": bm[key], "to": new[key],
                           "undoes": mv["iter"], "end": mv.get("end"), "at": mv.get("at")}
                    cell = ("-", names, f"undo iteration {mv['iter']}'s pull, push {short(key[0])}: stage "
                                        f"{bm[key]} -> {new[key]} (new)")
                    return with_map(before, new), [cell], [rec]
        raise Stop(6, why + ("; no pull left to turn into a push" if policy == "pull" else ""))
    if rep["state"] == "refused":
        raise Stop(8, "the router refused the run (" + REFUSED + "): no negotiation, nothing learnt")
    raise Stop(8, "the router's log has no closing line restage.py knows: nothing learnt")


# ------------------------------------------------------------------------------------------------ the run
def jdump(obj, path):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def row(path, cells):
    with open(path, "a") as f:
        f.write("| " + " | ".join(str(c) for c in cells) + " |\n")


def run(a, cmd):
    wd = os.path.abspath(a.workdir)
    os.makedirs(wd, exist_ok=True)
    md, sj = os.path.join(wd, "RESTAGE.md"), os.path.join(wd, "restage.json")
    st = {"iter": 0, "seen": [], "moves": [], "pushed": [], "records": [], "exit": None, "last": None}
    if a.cont and os.path.exists(sj):
        st = json.load(open(sj))
        spec = json.load(open(os.path.join(wd, "next.spec.json")))
    else:
        spec = json.load(open(a.spec))
        with open(md, "w") as f:
            f.write(f"restage of {a.spec}, policy {a.policy}\n\n| iteration | stage | state | blocked | walls | move |\n"
                    "|---|---|---|---|---|---|\n")
    bad = check_map(stage_map(spec))
    if bad:
        raise Stop(2, "spec: " + "; ".join(bad))
    this_run = 0

    def load_before(i):
        return json.load(open(os.path.join(wd, "iter%02d.spec.json" % i)))

    def finish(code, text):
        st["exit"], st["last"] = code, text
        jdump(st, sj)
        with open(md, "a") as f:
            f.write(f"\nRESULT exit {code}: {text}\n")
        print(f"RESTAGE exit {code}: {text}")
        return code

    while True:
        m = stage_map(spec)
        fz = [list(x) for x in frozen(m)]
        if fz in st["seen"]:
            first = st["seen"].index(fz) + 1
            jdump(spec, os.path.join(wd, "next.spec.json"))
            loop = "; ".join(f"{r['iter']}: {r['move']}" for r in st["records"] if r["iter"] >= first)
            return finish(7, f"cycle: the next stage map is iteration {first}'s again ({loop})")
        left = a.deadline - a.reserve - time.time()
        if (a.max_iter and this_run >= a.max_iter) or left < a.min_run:
            jdump(spec, os.path.join(wd, "next.spec.json"))
            why = (f"--max-iter {a.max_iter} reached" if a.max_iter and this_run >= a.max_iter
                   else f"{int(left)} s left before the deadline (--min-run {a.min_run})")
            return finish(9, f"{why}; open after {st['iter']} iteration(s), last state: "
                             f"{st['records'][-1]['state'] if st['records'] else 'not run'}; next.spec.json is not yet run")
        seeded = bool(a.seed_log) and st["iter"] == 0
        st["iter"] += 1
        this_run += 0 if seeded else 1
        it = st["iter"]
        sp, lg = os.path.join(wd, "iter%02d.spec.json" % it), os.path.join(wd, "iter%02d.log" % it)
        jdump(spec, sp)
        use = sp
        if a.live:
            shutil.copyfile(sp, a.live)
            use = a.live
        tmo = int(min(a.iter_cap, left))
        argv = [c.replace("{spec}", use).replace("{timeout}", str(tmo)) for c in cmd]
        st["seen"].append(fz)
        jdump(st, sj)
        t0 = time.time()
        if seeded:
            shutil.copyfile(a.seed_log, lg)
            code = a.seed_exit
        else:
            with open(lg, "w") as f:
                try:
                    code = subprocess.run(argv, stdout=f, stderr=subprocess.STDOUT, timeout=tmo + a.grace).returncode
                except subprocess.TimeoutExpired:
                    code = 124
                except OSError as e:
                    f.write(f"restage.py: the router command did not start: {e}\n")
                    code = 127
        took = int(time.time() - t0)
        rep = parse_log(open(lg, errors="replace").read(), code)
        rec = {"iter": it, "exit": code, "seconds": took, "stage": rep["stage"], "state": rep["state"],
               "iteration": rep["iteration"], "stages": stages_text(m), "contested": rep["contested"],
               "blocked": [b["net"] for b in rep["blocked"]], "move": "-", "replayed": seeded}
        st["records"].append(rec)
        head = [it, "-" if rep["stage"] is None else rep["stage"], f"{rep['state']} (exit {code}, {'replayed from --seed-log' if seeded else str(took) + ' s'}; stages {rec['stages']})"]
        if rep["state"] == "closed":
            shutil.copyfile(sp, os.path.join(wd, "final.spec.json"))
            row(md, head + ["-", "-", "-"])
            return finish(0, f"closed at iteration {it} after {len(st['moves'])} move(s); final.spec.json")
        try:
            spec, cells, recs = decide(spec, rep, a.policy, st["pushed"], st["moves"], load_before)
        except Stop as e:
            row(md, head + [" ".join(short(b["net"]) for b in rep["blocked"]) or "-", "-", "none"])
            return finish(e.code, f"iteration {it}: {e.text}")
        for r in recs:
            r["iter"] = it
            if "undoes" in r:
                st["moves"] = [x for x in st["moves"] if x["iter"] < r["undoes"]]
                st["pushed"].append(r["net"])
            st["moves"].append(r)
        rec["move"] = "; ".join(c[2] for c in cells)
        row(md, head + ["; ".join(c[0] for c in cells), "; ".join(c[1] for c in cells), rec["move"]])
        bad = check_map(stage_map(spec))
        if bad:
            return finish(2, "restage.py wrote a spec that breaks its own rules: " + "; ".join(bad))
        jdump(st, sj)


def dry(a):
    spec = json.load(open(a.spec))
    rep = parse_log(open(a.log, errors="replace").read(), a.exit)
    print(f"stage {rep['stage']}, state {rep['state']}, iteration {rep['iteration']}, stages {stages_text(stage_map(spec))}")
    if rep["state"] == "closed":
        print("closed: no move")
        return 0
    try:
        new, cells, recs = decide(spec, rep, a.policy, [], [], lambda i: spec)
    except Stop as e:
        print(f"exit {e.code}: {e.text}")
        return e.code
    for c in cells:
        print(f"blocked {c[0]}\n   walls: {c[1]}\n   move: {c[2]}")
    print(f"stages after: {stages_text(stage_map(new))}")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="the docstring (pydoc, or the head of this file) has the policies and the exit codes")
    p.add_argument("--spec")
    p.add_argument("--workdir")
    p.add_argument("--deadline", type=float, help="epoch seconds")
    p.add_argument("--policy", choices=("pull", "push"), default="pull")
    p.add_argument("--iter-cap", type=int, default=420, help="seconds one router run may take ({timeout})")
    p.add_argument("--max-iter", type=int, default=0, help="router runs of THIS call (0: until the deadline)")
    p.add_argument("--reserve", type=int, default=0, help="seconds before the deadline left unspent")
    p.add_argument("--min-run", type=int, default=60, help="no router run is started with less time than this")
    p.add_argument("--grace", type=int, default=120, help="seconds past {timeout} before the router is killed")
    p.add_argument("--live", help="install each iteration's spec here; {spec} is this path")
    p.add_argument("--continue", dest="cont", action="store_true", help="go on from DIR/next.spec.json and restage.json")
    p.add_argument("--seed-log", help="iteration 1 is this log of SPEC, not a run")
    p.add_argument("--seed-exit", type=int, default=None, help="the router's exit code that came with --seed-log")
    p.add_argument("--dry", action="store_true")
    p.add_argument("--log")
    p.add_argument("--exit", type=int, default=None)
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args(argv)
    if a.selftest:
        here = os.path.dirname(os.path.abspath(__file__))
        return subprocess.call([sys.executable, os.path.join(here, "test_restage.py")])
    try:
        if a.dry:
            if not (a.spec and a.log):
                raise Stop(2, "--dry needs --spec and --log")
            return dry(a)
        if not (a.spec and a.workdir and a.deadline and cmd):
            raise Stop(2, "needs --spec, --workdir, --deadline and a router command after --")
        if a.seed_log and (a.seed_exit is None or not os.path.exists(a.seed_log)):
            raise Stop(2, "--seed-log needs a log that exists and --seed-exit, the exit code that came with it")
        if not a.live and not any("{spec}" in c for c in cmd):
            raise Stop(2, "the router command has no {spec} and there is no --live: it would never see a move")
        return run(a, cmd)
    except Stop as e:
        print(f"restage.py: {e.text}", file=sys.stderr)
        return e.code
    except (OSError, ValueError, KeyError) as e:
        print(f"restage.py: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
