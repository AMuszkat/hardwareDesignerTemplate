#!/usr/bin/env python3
"""Prove a board's 14_fab custom DRC rules on a scratch copy of its PCB, before
they are published: concept-side, read-only on the board.

    python3 tools/review/rulecheck.py --board board1                 # every rule the generator has now
    python3 tools/review/rulecheck.py --board board1 --rule "pair"   # list only violations of rules matching

It is the proof `tools/handoff/publish.py` asks for: a custom DRC rule that is
new or changed, on a board that already has a PCB, does not publish until
this has run on exactly that rule text. The result is written to
`tools/handoff/rulechecks/<board>.json` (generated -- never typed; commit it
with the publish) and its hash goes into the handoff MANIFEST.

What it does. It copies the board's .kicad_pcb / .kicad_pro into
out/rulecheck/<board>/, gives the copy the page's net classes (so a rule on
`A.NetClass == 'X'` is judged as it will be after `board_setup.py apply`),
writes a .kicad_dru holding the board's own non-generated rules plus EVERY
custom rule the publisher would put in 14_fab now (derived area rules, the fab
profile's and the board's own), runs `kicad-cli pcb drc` once, and judges each
rule:

  loads     KiCad drops the WHOLE .kicad_dru, silently, when one rule has an
            unknown property, an unknown function or a syntax error -- every
            rule then reads "0 violations". A canary rule that must fire says
            whether the file was read; when nothing fired, each rule is run
            alone to name the one that kills it.
  matches   every name a condition tests must be on the board: a net
            (`A.NetName == '*CK_T'`), a net class, a footprint's courtyard
            (`A.intersectsCourtyard('U1')`), a rule area, a group, a diff pair.
            Each is run as a probe (an assertion rule that fires once per
            item its condition matches), and a name that matches nothing fails
            the rule. That is the defect this exists for: a wildcard that
            matched no net published twice on a board and cost a review round
            each time. For a rule on one item (no `B.`) the whole condition is
            probed too and a zero is a WARNING, not a failure: a rule about
            vias is right on a board that has no via yet.
            KiCad reports at most 199 violations of one type a run, so on a
            real board the first probes that fire starve the rest: a run that
            hit the limit proves only the probes that fired, and the ones
            still at zero are run again, without the others, until a run
            stays under it. A count from a run that hit the limit is a lower
            bound and is printed with a `+`.
  escapes   a rule that sets a clearance or a width inside a BGA's courtyard is
            a statement about what can escape its ball field. It carries the
            bound `kicad_project/tools/route/escape_bound.py` computes: the
            board module's FAB["escape"] gives that tool's arguments per layer
            (or a `waive` with the reason), it is run here, and a bound that is
            PROVEN SHORT -- or no row at all -- fails the rule. The bounds are
            rendered into 14_fab.md from the result file.

The violations of the real rules are still listed, grouped by rule name: on a
routed board they are the other half of the proof (a relaxation that did not
relax, a rule that outranked the area classes).

A rule the board module names in FAB["unproven"] is judged and recorded like
the others, printed as WAIVED when it fails, and does not fail the run: the
publisher lets it through with the reason given there.

Exit 0 every rule proven or waived, 1 a rule is not, 2 nothing could be
checked (no PCB with a footprint yet, no kicad-cli, an unreadable board
module).
"""
import argparse
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "handoff"))
import publish                                       # noqa: E402

CANARY = "probe: canary"
# DRC_ENGINE's ERROR_LIMIT: KiCad reports at most this many violations of ONE type in a run, and
# every probe is the same type (assertion_failure). `--all-track-errors` lifts it for clearance
# and unconnected items only.
CAP = 199
# an assertion nothing satisfies: the rule fires once for every item its condition matches
NEVER = "(constraint assertion \"A.Type == '__none__'\")"
# constraints that decide what fits between the balls of a BGA
ESCAPE_KINDS = {"clearance", "track_width", "hole_clearance", "hole_to_hole", "via_diameter", "hole_size",
                "annular_width", "physical_clearance", "physical_hole_clearance", "diff_pair_gap"}
PROP = re.compile(r"\b[AB]\.(NetName|NetClass)\s*(?:==|!=)\s*'([^'\"]+)'")
FUNC = re.compile(r"(!\s*)?\b[AB]\.(\w+)\(\s*'([^'\"]*)'\s*\)")
NET_FUNCS = {"inDiffPair", "hasNetclass", "hasExactNetclass"}
BALL = re.compile(r"[A-Z]{1,2}\d{1,2}")


def condition(sx):
    m = re.search(r'\(condition\s+"((?:[^"\\]|\\.)*)"\s*\)', sx)
    return m.group(1) if m else ""


def atoms(sx):
    """The names one rule's condition tests, as [(label, probe condition)].

    A probe has only an A item (an assertion is unary), so a `B.` term is
    asked of A: the question is whether the NAME is on the board, not which
    side of the pair it is on. `!=` is probed as `==` -- a net exempted by a
    name that is not on the board is as dead as one selected by it. Net tests
    are probed on pads only (every net has one), which keeps the report small
    on a routed board; area and courtyard tests on every item."""
    cond, out = condition(sx), {}
    for m in PROP.finditer(cond):
        out[f"{m.group(1)} '{m.group(2)}'"] = f"A.{m.group(1)} == '{m.group(2)}' && A.Type == 'Pad'"
    for m in FUNC.finditer(cond):
        fn, arg = m.group(2), m.group(3)
        if fn == "existsOnLayer" or not arg:
            continue
        out[f"{fn}('{arg}')"] = f"A.{fn}('{arg}')" + (" && A.Type == 'Pad'" if fn in NET_FUNCS else "")
    if "isCoupledDiffPair()" in cond:
        # unary, isCoupledDiffPair() is always false; inDiffPair('*') is the same question of one item
        out["isCoupledDiffPair()"] = "A.inDiffPair('*') && A.Type == 'Pad'"
    return list(out.items())


def escape_refs(sx, bgas):
    """The BGAs whose escape this rule is about: it sets a spacing or a width
    and its condition is scoped (not negated) to that part's courtyard."""
    if not set(re.findall(r"\(constraint\s+(\w+)", sx)) & ESCAPE_KINDS:
        return []
    return sorted({m.group(3) for m in FUNC.finditer(condition(sx))
                   if not m.group(1) and "courtyard" in m.group(2).lower() and m.group(3) in bgas})


def footprints(text):
    """{reference: [pad names]} read from the PCB's text (no pcbnew here)."""
    out = {}
    for chunk in re.split(r'\n\s*\(footprint "', text)[1:]:
        m = (re.search(r'\(property "Reference" "([^"]*)"', chunk)
             or re.search(r'\(fp_text reference "?([^"\s)]*)', chunk))
        if m:
            out[m.group(1)] = re.findall(r'\(pad "([^"]*)"', chunk)
    return out


def board_nets(text):
    return set(re.findall(r'\(net (?:\d+ )?"([^"]+)"\)', text))


def inject_classes(pro, classes, nets):
    """Give the scratch project the page's net classes and their nets, the
    way `board_setup.py apply` will: a rule on `A.NetClass == 'X'` must be
    judged against the classes this handoff states, not the ones the board
    happens to have applied. Returns the page nets that are not on the board."""
    try:
        d = json.load(open(pro))
    except (OSError, ValueError):
        d = {}
    ns = d.setdefault("net_settings", {})
    have = ns.setdefault("classes", [])
    base = next((c for c in have if c.get("name") == "Default"), None)
    if base is None:
        base = {"name": "Default", "clearance": 0.2, "track_width": 0.2, "via_diameter": 0.6, "via_drill": 0.3}
        have.append(base)
    pats = ns.get("netclass_patterns") or []
    absent = []
    for i, (cls, cell, trk, clr, _via, _note) in enumerate(classes):
        if cls == "Default":
            continue
        c = next((x for x in have if x.get("name") == cls), None)
        if c is None:
            c = dict(base, name=cls, priority=i)
            have.append(c)
        for key, v in (("track_width", trk), ("clearance", clr)):
            m = re.search(r"\d+(?:\.\d+)?", str(v))
            if m:
                c[key] = float(m.group(0))
        pats = [p for p in pats if p.get("netclass") != cls]
        if re.search(r"inside [A-Za-z0-9_]+'s", str(cell)):
            continue                        # an area class: no nets, its rule is a custom rule
        for n in [t.strip() for t in str(cell).split(",") if t.strip()]:
            hits = [b for b in nets if b == n or b.endswith("/" + n)]
            if not hits:
                absent.append(f"{cls}: {n}")
            for b in hits:
                pat = re.escape(b).replace("/", "/") if re.search(r"[][*?.+()|^$\\{}]", b) else b
                pats.append({"netclass": cls, "pattern": pat})
    ns["netclass_patterns"] = pats
    # a board that ignores assertion failures would silence every probe
    d.setdefault("board", {}).setdefault("design_settings", {}).setdefault(
        "rule_severities", {})["assertion_failure"] = "error"
    with open(pro, "w") as f:
        json.dump(d, f, indent=2)
    return absent


def drc(out, stem, own, rules, probes):
    """One kicad-cli DRC on the scratch copy with this rule file.
    -> (Counter of violations by rule name, the violations, error text)."""
    lines = [own.rstrip()] + [sx for _, sx in rules]
    lines += [f'(rule "{name}" {layer} (condition "{cond}") {NEVER})' for name, cond, layer in probes]
    with open(os.path.join(out, stem + ".kicad_dru"), "w") as f:
        f.write("\n".join(lines) + "\n")
    rep = os.path.join(out, "drc.json")
    if os.path.exists(rep):
        os.remove(rep)
    r = subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "-o", rep,
                        os.path.join(out, stem + ".kicad_pcb")], capture_output=True, text=True)
    try:
        vio = json.load(open(rep)).get("violations", [])
    except (OSError, ValueError):
        return collections.Counter(), [], (r.stderr or r.stdout or "kicad-cli wrote no report").strip()[-300:]
    by = collections.Counter()
    for v in vio:
        m = re.search(r"rule '([^']+)'", v.get("description", ""))
        if m:
            by[m.group(1)] += 1
    return by, vio, ""


def probe(out, stem, own, rules, probes, canary):
    """The rules and every probe, judged in as many DRC runs as KiCad's limit
    of CAP violations per type needs.

    The first run holds the board's own rules, the handoff rules and all the
    probes. Items are visited in board order and every assertion failure
    counts toward the one limit, so on a real board the tracks under the first
    BGA use it up and a probe further down reads 0 without having been asked.
    A run is complete when it stayed under the limit AND the canary (one
    footprint, so exactly one failure) was seen; otherwise the probes that
    fired are settled -- their count is a lower bound -- and the ones at zero
    run again, alone with the canary. Each such run settles at least one
    probe, so it ends.

    -> (by, violations, error, loaded, {probe: count}, {probes whose count is
    a lower bound}, runs). `loaded` is False when nothing at all fired in the
    first run: KiCad dropped the file."""
    by, vio, err = drc(out, stem, own, rules, [canary] + probes)
    counts = {p[0]: by[p[0]] for p in probes}
    if err:
        return by, vio, err, False, counts, set(), 1
    total, seen, asked, lower, runs = by[CANARY] + sum(counts.values()), bool(by[CANARY]), probes, set(), 1
    loaded = total > 0
    while loaded and (total >= CAP or not seen):
        lower |= {p[0] for p in asked if counts[p[0]]}
        asked = [p for p in asked if not counts[p[0]]]
        if not asked:
            break
        got, _, err = drc(out, stem, "(version 1)\n", [], [canary] + asked)
        runs += 1
        if err:
            return by, vio, err, loaded, counts, lower, runs
        total, seen = got[CANARY] + sum(got[p[0]] for p in asked), bool(got[CANARY])
        if not total:
            return by, vio, ("the probes that loaded with the rules fired nothing alone, the canary "
                             "included"), loaded, counts, lower, runs
        counts.update({p[0]: got[p[0]] for p in asked})
    return by, vio, "", loaded, counts, lower, runs


def run_escape(row, out, folder):
    """One FAB["escape"] row through escape_bound.py, on the scratch copy,
    run from the board folder (the row's --spec / --vias paths are relative
    to it)."""
    res = {"ref": row["ref"], "layer": row["layer"], "args": list(row.get("args", [])),
           "bound": None, "of": None, "verdict": "not computed"}
    tools = os.path.join(ROOT, "kicad_project", "tools")
    try:
        r = subprocess.run([os.path.join(tools, "kpy"), os.path.join(tools, "route", "escape_bound.py"),
                            "--board", out, "--ref", row["ref"], "--layer", row["layer"], *res["args"]],
                           cwd=folder, capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as e:
        res["verdict"] = f"not computed: {e}"[:200]
        return res
    m = re.search(r"^BOUND (\d+) of (\d+): (.+)$", r.stdout, re.M)
    if m:
        res.update(bound=int(m.group(1)), of=int(m.group(2)), verdict=m.group(3).strip())
    else:
        last = [ln for ln in (r.stderr + "\n" + r.stdout).splitlines() if ln.strip()]
        res["verdict"] = "not computed: " + (last[-1].strip()[:160] if last else f"exit {r.returncode}")
    return res


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--board", required=True, help="board1, board2, ...")
    ap.add_argument("--rule", default="", help="only list violations whose rule name contains this "
                    "(every rule is still judged and recorded)")
    a = ap.parse_args(argv)
    m = re.match(r"board\d+", a.board)
    bid = m.group(0) if m else a.board
    try:
        b = publish.Board(bid)
        if not b.fab:
            print(f"rulecheck: {bid} has no FAB: no custom DRC rules to prove", file=sys.stderr)
            return 2
        _o, _c, _s, classes, rows = b.fab_tables()
    except (publish.PublishError, OSError) as e:
        print(f"rulecheck: {bid}: {e}", file=sys.stderr)
        return 2
    rules = [(name, sx) for row in rows for name, sx in publish.split_rules(row[1])]
    pcb = b.pcb_path()
    if not pcb:
        print(f"rulecheck: kicad_project/{b.board}/ has no PCB with a footprint on it yet: there is nothing "
              f"to prove a rule on, and a publish asks for no proof until there is", file=sys.stderr)
        return 2
    if not shutil.which("kicad-cli"):
        print("rulecheck: kicad-cli is not on PATH (./setup.sh check)", file=sys.stderr)
        return 2
    t0 = time.time()
    stem = os.path.splitext(os.path.basename(pcb))[0]
    out = os.path.join(ROOT, "out", "rulecheck", bid)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    shutil.copy(pcb, out)
    pro = os.path.join(b.out, stem + ".kicad_pro")
    if os.path.exists(pro):
        shutil.copy(pro, out)
    text = open(pcb, errors="replace").read()
    absent = inject_classes(os.path.join(out, stem + ".kicad_pro"), classes, board_nets(text))
    dru = os.path.join(b.out, stem + ".kicad_dru")
    own = open(dru).read() if os.path.exists(dru) else "(version 1)\n"
    # drop the generated block (board_setup's), keep the board's own rules
    own = re.sub(r"# >>> board_setup.*?# <<< board_setup\n?", "", own, flags=re.S)
    if "(version" not in own:
        own = "(version 1)\n" + own

    fps = footprints(text)

    # -- the rules, a probe per name each tests, the canary ------------------------------------
    ref = next((r for r in fps if re.fullmatch(r"[A-Za-z0-9_#.+-]+", r)), None)
    canary = (CANARY, "A.Type == 'Footprint'" + (f" && A.Reference == '{ref}'" if ref else ""), "")
    uniq, plan = {}, {}

    def named(cond, layer=""):
        # one probe per distinct question: nine rules that name U1's courtyard ask it once
        return uniq.setdefault((cond, layer), f"probe: {len(uniq)}")

    for name, sx in rules:
        at = [(lab, named(cond)) for lab, cond in atoms(sx)]
        whole = None
        cond = condition(sx)
        # (isCoupledDiffPair() asks about a pair of items: of one item alone it is never true)
        if cond and not re.search(r"\bB\.", cond) and '"' not in cond and "isCoupledDiffPair" not in cond:
            lay = re.search(r"\(layer\s+[^()]*\)", sx)
            whole = named(cond, lay.group(0) if lay else "")
        plan[name] = (at, whole)
    probes = [(pn, cond, layer) for (cond, layer), pn in uniq.items()]
    by, vio, err, loaded, counts, lower, runs = probe(out, stem, own, rules, probes, canary)
    dead, own_dead = {}, False
    if not err and not loaded:
        # KiCad dropped the file: find the rule(s) that kill it, one run each
        print(f"rulecheck: KiCad read none of the {len(rules)} rules (it drops the whole file on one bad "
              f"rule, silently) -- running each alone to name it", file=sys.stderr)
        one, _, _ = drc(out, stem, own, [], [canary])
        own_dead = not one[CANARY]
        for name, sx in rules:
            one, _, _ = drc(out, stem, "(version 1)\n", [(name, sx)], [canary])
            if not one[CANARY]:
                dead[name] = "KiCad cannot read this rule (an unknown property or function, or a syntax " \
                             "error), and one such rule makes it drop every rule in the .kicad_dru"
        runs += 1 + len(rules)
        live = [(n, sx) for n, sx in rules if n not in dead]
        used = {pn for n, _ in live for pn in [p for _, p in plan[n][0]] + [plan[n][1]] if pn}
        by, vio, err, loaded, counts, lower, more = probe(
            out, stem, "(version 1)\n" if own_dead else own, live, [p for p in probes if p[0] in used], canary)
        runs += more
    if err:
        print(f"rulecheck: kicad-cli pcb drc failed on the copy in {out}: {err}", file=sys.stderr)
        return 2

    # -- the escape bounds ---------------------------------------------------------------------
    bgas = {r for r, pads in fps.items() if sum(1 for p in pads if BALL.fullmatch(p)) >= 16}
    esc_rows = b.B.FAB.get("escape", [])
    escape = [run_escape(e, out, b.out) for e in esc_rows if not e.get("waive")]
    escape += [{"ref": e["ref"], "waive": e["waive"]} for e in esc_rows if e.get("waive")]

    # -- the verdicts --------------------------------------------------------------------------
    res, warns, marks = {}, [], {}
    waived = b.B.FAB.get("unproven", {})
    for name, sx in rules:
        at, whole = plan[name]
        seen = {lab: counts.get(pn, 0) for lab, pn in at}
        why = dead.get(name, "")
        if not why and not loaded:
            why = "the rule file did not load with its probes: no rule could be judged (see stderr)"
        if not why:
            none = [lab for lab, n in seen.items() if not n]
            if none:
                why = "matches nothing on the board: " + ", ".join(none)
        if not why:
            for ref in escape_refs(sx, bgas):
                mine = [e for e in escape if e["ref"] == ref]
                short = [e for e in mine if "waive" not in e and "not excluded" not in e["verdict"]]
                if not mine:
                    why = (f"is about the escape of {ref} (a BGA) and FAB[\"escape\"] has no row for "
                           f"{ref}: give escape_bound.py's arguments for each escape layer, or a waive "
                           f"with the reason")
                elif short:
                    e = short[0]
                    why = (f"escape bound {ref} {e['layer']}: " +
                           (f"{e['bound']} of {e['of']}, " if e["bound"] is not None else "") + e["verdict"])
                if why:
                    break
        n_all = counts.get(whole, 0) if whole and not why else None
        if n_all == 0:
            warns.append(f"'{name}': every name it tests is on the board, but its whole condition "
                         f"matches no item now -- right for a rule about copper that is not drawn "
                         f"yet, wrong if the names cannot be true of one item together")
        # the file records what was decided, not the counts: above KiCad's limit those depend on
        # the order its threads visit the items, and a proof that differs run to run is noise in git
        res[name] = {"sha": publish._hb(sx.encode()), "ok": not why, "why": why,
                     "names": {lab: bool(n) for lab, n in seen.items()},
                     "matches": None if n_all is None else bool(n_all), "violations": by[name]}
        marks[name] = {lab: f" x{seen[lab]}{'+' if pn in lower else ''}" for lab, pn in at}

    ver = subprocess.run(["kicad-cli", "version"], capture_output=True, text=True).stdout.strip()
    proof = {"board": b.board, "pcb_file": os.path.basename(pcb),
             "pcb": publish._hb(open(pcb, "rb").read()), "kicad": ver, "rules": res, "escape": escape}
    proof["hash"] = publish._hb(json.dumps(proof, sort_keys=True).encode())
    dst = os.path.join(ROOT, b.proof_rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w") as f:
        json.dump(proof, f, indent=1, sort_keys=True)
        f.write("\n")

    # -- the report ----------------------------------------------------------------------------
    print(f"{bid}: {len(rules)} handoff rules on a copy of {os.path.basename(pcb)} "
          f"({os.path.relpath(out, ROOT)}), KiCad {ver}, {runs} DRC run(s), {time.time() - t0:.1f} s")
    for name, r in res.items():
        seen = ", ".join(lab + marks[name][lab] for lab in r["names"])
        tag = "PROVEN" if r["ok"] else "WAIVED" if name in waived else "FAILS "
        print(f"  {tag:6s}  {name}: " +
              (r["why"] + (f" -- FAB[\"unproven\"]: {waived[name]}" if name in waived else "")
               if not r["ok"] else (seen or "tests no name") + f"; {r['violations']} violation(s)"))
    for e in escape:
        print(f"  ESCAPE  {e['ref']}" + (f": waived -- {e['waive']}" if "waive" in e else
              f" {e['layer']}: " + (f"{e['bound']} of {e['of']}, " if e["bound"] is not None else "") + e["verdict"]))
    for w in warns:
        print(f"  WARNING {w}")
    if own_dead:
        print("  WARNING the board's own rules in its .kicad_dru (outside board_setup's block) do not "
              "load: KiCad drops the whole file, the generated rules with them -- a review finding")
    if absent:
        print(f"  note: {len(absent)} net(s) the Net classes table names are not on the board yet "
              f"({', '.join(absent[:4])}{', ..' if len(absent) > 4 else ''})")
    shown, ex = collections.Counter(), {}
    for v in vio:
        m = re.search(r"rule '([^']+)'", v.get("description", ""))
        if m and m.group(1).startswith("probe: "):
            continue
        key = f"{v['type']}: {m.group(1) if m else '(class / board rule)'}"
        if a.rule and a.rule not in key:
            continue
        shown[key] += 1
        ex.setdefault(key, [i["description"][:70] for i in v.get("items", [])])
    print("violations on the copy" + (f" matching '{a.rule}'" if a.rule else "") + ":")
    for k, n in shown.most_common():
        print(f"  {n:4d}  {k}\n        e.g. {ex[k]}")
    if not shown:
        print("  none")
    if lower:
        print(f"  note: a count with a + is a lower bound (KiCad reports at most {CAP} assertion "
              f"failures a run; the probes still at zero were run again without the others)")
    bad = [n for n, r in res.items() if not r["ok"] and n not in waived]
    nw = sum(1 for n, r in res.items() if not r["ok"] and n in waived)
    rel = os.path.relpath(dst, ROOT)
    if bad:
        print(f"RULECHECK FAIL: {len(bad)} of {len(rules)} rule(s) not proven ({', '.join(bad[:3])}"
              f"{', ..' if len(bad) > 3 else ''}) -> {rel} (result {proof['hash']}); a new or changed one "
              f"of them will not publish")
        return 1
    print(f"RULECHECK PASS: {len(rules) - nw} rule(s) proven" + (f", {nw} waived" if nw else "") +
          f" -> {rel} (result {proof['hash']}); publish, and commit that file with it")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
