#!/usr/bin/env python3
"""Prove a board's 14_fab custom DRC rules on a scratch copy of its PCB, before
(or after) they are published: concept-side, read-only on the board.

    python3 tools/review/rulecheck.py --board board1            # rules as the generator has them now
    python3 tools/review/rulecheck.py --board board1 --rule "pair"   # only list violations of rules matching

It copies the board's .kicad_pcb / .kicad_pro into out/rulecheck/<board>/, writes
a .kicad_dru holding the board's own non-generated rules plus EVERY custom
rule the publisher would put in 14_fab now (derived area rules, the fab
profile's and the board's own), runs `kicad-cli pcb drc`, and prints the violations grouped by rule
name. Exit 0 always; read the counts. Why it exists: a rule written against
the board's net names failed twice on a board (a wildcard path, a rule that
outranked the BGA area classes); KiCad's DRC on the real board is the only
proof, and it costs one command.
"""
import argparse
import collections
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--board", required=True, help="board1, board2, ...")
    ap.add_argument("--rule", default="", help="only show violations whose rule name contains this")
    a = ap.parse_args(argv)
    spec = importlib.util.spec_from_file_location(
        a.board, os.path.join(ROOT, "tools", "handoff", "boards", f"{a.board}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    folder = os.path.join(ROOT, "kicad_project", mod.FOLDER if hasattr(mod, "FOLDER") else "")
    if not hasattr(mod, "FOLDER"):
        cands = [d for d in os.listdir(os.path.join(ROOT, "kicad_project")) if d.startswith(a.board + "_")]
        if not cands:
            print(f"rulecheck: no kicad_project/{a.board}_* folder", file=sys.stderr)
            return 2
        folder = os.path.join(ROOT, "kicad_project", cands[0])
    name = os.path.basename(folder)
    out = os.path.join(ROOT, "out", "rulecheck", a.board)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    for ext in (".kicad_pcb", ".kicad_pro"):
        shutil.copy(os.path.join(folder, name + ext), out)
    dru = os.path.join(folder, name + ".kicad_dru")
    own = open(dru).read() if os.path.exists(dru) else "(version 1)\n"
    # drop the generated block (board_setup's), keep the board's own rules
    own = re.sub(r"# >>> board_setup.*?# <<< board_setup\n?", "", own, flags=re.S)
    # every custom rule the publisher would put in 14_fab NOW: the derived area
    # rules, the fab profile's and the board's own (publish.Board.fab_tables)
    sys.path.insert(0, os.path.join(ROOT, "tools", "handoff"))
    import publish
    rules = [r[1] for r in publish.Board(a.board).fab_tables()[4] if len(r) > 1]
    with open(os.path.join(out, name + ".kicad_dru"), "w") as f:
        f.write(own.rstrip() + "\n" + "\n".join(rules) + "\n")
    rep = os.path.join(out, "drc.json")
    subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "-o", rep,
                    os.path.join(out, name + ".kicad_pcb")], capture_output=True)
    d = json.load(open(rep))
    by = collections.Counter()
    ex = {}
    for v in d.get("violations", []):
        m = re.search(r"rule '([^']+)'", v.get("description", ""))
        key = f"{v['type']}: {m.group(1) if m else '(class / board rule)'}"
        if a.rule and a.rule not in key:
            continue
        by[key] += 1
        ex.setdefault(key, [i["description"][:70] for i in v.get("items", [])])
    print(f"{a.board}: {len(rules)} handoff rules on a copy of {name}.kicad_pcb ({out})")
    for k, n in by.most_common():
        print(f"  {n:4d}  {k}\n        e.g. {ex[k]}")
    if not by:
        print("  no violations" + (f" matching '{a.rule}'" if a.rule else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
