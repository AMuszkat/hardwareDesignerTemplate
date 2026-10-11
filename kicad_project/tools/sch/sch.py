#!/usr/bin/env python3
"""Schematic drawing standard: the gate, and the plan that meets it.

    tools/.venv/bin/python tools/sch/sch.py check  --board board1 [--json]
    tools/.venv/bin/python tools/sch/sch.py plan   --board board1 [--from-sch | --netlist n.json]
                                                    [--only SHEET] [--out DIR]
    tools/.venv/bin/python tools/sch/sch.py frame  --board board1 [--plan DIR] [--remove]
    tools/.venv/bin/python tools/sch/sch.py parity --board board1 [--plan DIR | --handoff]
    tools/.venv/bin/python tools/sch/sch.py rules

  check   the saved schematic against the standard (tools/sch/README.md, the
          `schematic` skill): paper, frame, title block, text collisions
          (from kicad-cli's own SVG), wires through text and bodies, rails
          drawn as labels, global labels below the root, unconnected root
          sheet pins, label-stitched pieces, small parts far from the IC pin
          they serve, pins per side. Exit 1 on any finding not waived in
          <board>/schematic.json. KiCad's ERC is reported beside it.
  plan    lay every sheet out to the standard from a netlist and write
          <out>/<sheet>.ops.json -- the Konnect calls, in order -- plus a
          scratch preview project (<out>/preview/) that kicad-cli renders,
          checks and nets up: the plan is proven against the source netlist
          and this gate before any call is made. --from-sch reads the drawn
          schematic (a redraw); --netlist reads tools/sch/README.md's format;
          --handoff reads handoff/netlist.json when the handoff marks it
          complete, with the board's symbols from <board>/schematic.json.
  parity  after drawing: the board's netlist, as KiCad exports it now, against
          the netlist the plan was made from (<plan>/netlist.json) -- the same
          connections, pin for pin, whatever the net names. Exit 1 on a
          difference. --handoff instead checks the drawing against the
          handoff's own netlist.json: every part there and no placeholder,
          every pin the handoff fixes on its net BY NAME, every no-connect
          and OPEN pin unconnected.
  rules   the rules and their numbers.

Read-only on the board's KiCad files except `frame`: plan writes under <out>
only (default <board>/sch_plan/). Konnect draws.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import boardsel                                     # noqa: E402
import model                                        # noqa: E402
import check                                        # noqa: E402


def die(msg, code=2):
    print(f"sch: {msg}", file=sys.stderr)
    sys.exit(code)


def waivers(board):
    f = Path(board) / "schematic.json"
    if not f.exists():
        return []
    w = json.loads(f.read_text()).get("waive", [])
    bad = [x for x in w if not str(x.get("why", "")).strip()]
    if bad:
        die(f"schematic.json: a waiver without a 'why': {bad[0]}")
    return w


def report(design, findings, waived, as_json=False):
    if as_json:
        print(json.dumps({"findings": [dict(rule=r, sheet=s, key=k, msg=m) for r, s, k, m in findings],
                          "waived": [dict(rule=f[0], sheet=f[1], key=f[2], why=w["why"]) for f, w in waived]},
                         indent=1))
        return
    by = {}
    for r, s, k, m in findings:
        by.setdefault(s, []).append((r, m))
    print(f"{'sheet':40} {'paper':5} {'parts':>5} {'wires':>5} {'labels':>6} {'findings':>8}")
    for sh in design.sheets:
        print(f"{sh.name[:40]:40} {sh.paper:5} {len(sh.parts):5} {len(sh.wires):5} "
              f"{len(sh.labels):6} {len(by.get(sh.name, [])):8}")
    for sh in design.sheets:
        fs = by.get(sh.name)
        if not fs:
            continue
        print(f"\n== {sh.name} ({sh.path.name})")
        rules = {}
        for r, m in fs:
            rules.setdefault(r, []).append(m)
        for r, ms in rules.items():
            for m in ms[:6]:
                print(f"  {r:10} {m}")
            if len(ms) > 6:
                print(f"  {r:10} ... and {len(ms) - 6} more")
    for f, w in waived:
        print(f"  waived {f[0]} {f[2]} -- {w['why']}")


def cmd_check(a):
    board = boardsel.resolve(a.board)
    design = model.Design(board)
    findings, waived = check.run(design, waivers(board))
    report(design, findings, waived, a.json)
    if not a.json:
        e, w = model.erc(design.root)
        print(f"\nERC: {e} errors, {w} warnings (kicad-cli sch erc)" if e is not None else "\nERC: did not run")
        print("CHECKS PASS" if not findings else f"CHECKS FAIL: {len(findings)} finding(s)")
    sys.exit(1 if findings else 0)


def cmd_plan(a):
    import plan
    board = boardsel.resolve(a.board)
    out = Path(a.out) if a.out else Path(board) / "sch_plan"
    if a.handoff:
        src = plan.Source.from_handoff(board)
    elif a.netlist:
        src = plan.Source.from_json(board, boardsel.in_board(board, a.netlist))
    else:
        src = plan.Source.from_sch(board)
    sys.exit(plan.run(src, out, only=a.only))


def cmd_frame(a):
    import emit
    board = boardsel.resolve(a.board)
    plan_dir = Path(a.plan) if a.plan else Path(board) / "sch_plan"
    n = emit.frame(board, plan_dir, a.remove)
    print(f"{n} section frame(s) and the title block written -- netlist unchanged" if not a.remove
          else "frames removed; title block kept")


def handoff_parity(board):
    """The drawing against handoff/netlist.json. Returns the findings."""
    f = Path(board) / "handoff" / "netlist.json"
    if not f.exists():
        die(f"{f} not found -- the handoff has no netlist yet (a question for concept)")
    nl = json.loads(f.read_text())
    nets, comps = model.netlist(model.Design(board).root)
    drawn, size = {}, {}
    for n, nodes in nets.items():
        for r, p in nodes:
            drawn[(r, p)] = n
        size[n] = len(set(nodes))

    def base(n):
        return n.rsplit("/", 1)[-1]

    def loose(k):
        n = drawn.get(k)
        return n is None or n.startswith("unconnected-") or size.get(n, 0) < 2
    out = []
    parts = nl["parts"]
    real = {r for r in comps if not r.startswith("#")}
    for r, p in sorted(parts.items()):
        if p["placeholder"] and r in real:
            out.append(("placeholder", r, f"{r} is drawn, and the handoff marks it PLACEHOLDER: no part"))
        elif not p["placeholder"] and r not in real:
            out.append(("missing", r, f"{r} ({p['function']}) is in the handoff and not drawn"))
    for r in sorted(real - set(parts)):
        out.append(("extra", r, f"{r} is drawn and has no line in the handoff's components"))
    checked = 0
    hn = {(r, p): n for n, pins in nl["nets"].items() for r, p in pins}
    swapped, via_swap = set(), {}
    for r, groups in nl.get("swap", {}).items():
        if r not in real:
            continue
        for g in groups:
            want = [hn.get((r, p), "NC") for p in g]
            got = ["NC" if loose((r, p)) else base(drawn[(r, p)]) for p in g]
            rem, ok = list(got), True
            for w in want:
                if not w.startswith("_"):
                    if w in rem:
                        rem.remove(w)
                    else:
                        ok = False
            ok = ok and all(x != "NC" for x in rem[:sum(w.startswith("_") for w in want)])
            if ok:
                # the pins left after the named nets matched are the unnamed nets' ends
                left = [p for p in g if not loose((r, p))]
                for w in want:
                    if not w.startswith("_"):
                        q = next((p for p in left if base(drawn[(r, p)]) == w), None)
                        if q:
                            left.remove(q)
                for w, q in zip([w for w in want if w.startswith("_")], left):
                    via_swap.setdefault(w, set()).add(drawn[(r, q)])
            if not ok:
                out.append(("net", f"{r}.{'/'.join(g)}", f"{r} pins {', '.join(g)} are on {', '.join(got)}; "
                            f"the handoff puts them on {', '.join(want)} (either way round)"))
            swapped |= {(r, p) for p in g}
    for n, pins in sorted(nl["nets"].items()):
        seen = set()
        for r, p in pins:
            if r not in real:
                continue
            checked += 1
            if (r, p) in swapped:
                if not loose((r, p)) and base(drawn[(r, p)]) == n:
                    seen.add(drawn[(r, p)])
                continue
            d = drawn.get((r, p))
            if loose((r, p)):
                out.append(("net", f"{r}.{p}", f"{r}.{p} is unconnected; the handoff puts it on {n}"))
            elif base(d) != n and not n.startswith("_"):
                out.append(("net", f"{r}.{p}", f"{r}.{p} is on {base(d)}; the handoff puts it on {n}"))
            else:
                seen.add(d)
        seen |= via_swap.get(n, set())
        if len(seen) > 1:
            out.append(("split", n, f"{n} is drawn as {len(seen)} separate nets: {', '.join(sorted(seen))}"))
    for kind, rows in (("nc", nl["nc"]), ("open", nl["open"])):
        for r, p in rows:
            if r in real:
                checked += 1
                if not loose((r, p)):
                    what = "no-connect" if kind == "nc" else "OPEN (leave unconnected, no flag)"
                    out.append((kind, f"{r}.{p}", f"{r}.{p} is on {base(drawn[(r, p)])}; the handoff says {what}"))
    return out, checked, nl


def cmd_parity(a):
    import emit
    board = boardsel.resolve(a.board)
    if a.handoff:
        out, checked, nl = handoff_parity(board)
        for kind, k, msg in out[:60]:
            print(f"  {kind:11} {msg}")
        if len(out) > 60:
            print(f"  ... and {len(out) - 60} more")
        prose = nl.get("prose_only", [])
        print(f"{checked} pins of {len(nl['parts']) - len(prose)} parts checked against the handoff; "
              f"{len(prose)} parts have their connections only in prose there (not checked)")
        print("HANDOFF PARITY OK" if not out else f"HANDOFF PARITY FAIL: {len(out)} difference(s)")
        sys.exit(1 if out else 0)
    plan_dir = Path(a.plan) if a.plan else Path(board) / "sch_plan"
    f = plan_dir / "netlist.json"
    if not f.exists():
        die(f"{f} not found -- run sch.py plan first")
    ref = json.loads(f.read_text())
    nets, _ = model.netlist(model.Design(board).root)
    missing, extra = emit.parity(ref["nets"], nets, ref.get("nc", []))
    for g in sorted(missing, key=sorted)[:20]:
        print("  in the plan, not drawn:", sorted(g)[:8])
    for g in sorted(extra, key=sorted)[:20]:
        print("  drawn, not in the plan:", sorted(g)[:8])
    print("PARITY OK" if not missing and not extra else
          f"PARITY FAIL: {len(missing)} net(s) not drawn as planned, {len(extra)} drawn otherwise")
    sys.exit(0 if not missing and not extra else 1)


def cmd_rules(_):
    for k, v in check.RULES.items():
        print(f"{k:10} {v}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--board")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_check)
    p = sub.add_parser("plan")
    p.add_argument("--board")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--from-sch", action="store_true", help="redraw: read the drawn schematic (default)")
    g.add_argument("--netlist", help="a netlist JSON (README's format)")
    g.add_argument("--handoff", action="store_true", help="handoff/netlist.json (complete) + schematic.json symbols")
    p.add_argument("--only", help="one sheet, by name")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_plan)
    fr = sub.add_parser("frame")
    fr.add_argument("--board")
    fr.add_argument("--plan")
    fr.add_argument("--remove", action="store_true")
    fr.set_defaults(fn=cmd_frame)
    q = sub.add_parser("parity")
    q.add_argument("--board")
    qg = q.add_mutually_exclusive_group()
    qg.add_argument("--plan")
    qg.add_argument("--handoff", action="store_true", help="against handoff/netlist.json")
    q.set_defaults(fn=cmd_parity)
    r = sub.add_parser("rules")
    r.set_defaults(fn=cmd_rules)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
