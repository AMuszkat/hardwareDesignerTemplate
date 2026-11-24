#!/usr/bin/env python3
"""exitlane.py --spec-blocks against exitlane's own gate, on a scratch copy of board1.

    tools/kpy tools/route/test_exitlane_blocks.py [--copy DIR] [--nets SPEC] [--mod exitlane]

DIR is a scratch copy of board1 OUTSIDE kicad_project/ (default ~/.cache/board1_tmp/exl_base: the
board's .kicad_pcb/.kicad_pro/.kicad_dru, general_routing_guide.md and routing/*.json, copied as they are);
SPEC the net list (default NORTHGAP r9's spec, tmpWork/NORTHGAP/r9/v_r9a_gr/r9a_gr.json).

  1. the blocks for the spec's nets hold U1's north ring-1 lanes the r10 worker found by hand (r10 STATE.md);
  2. identity on the board: a real via of a group net added to the copy (in memory) and exitlane's judge run
     again, as route.py check runs it -- on the edge of each of those blocks (lane +- need + r -+ 0.002) the
     balls outside the group that newly fail are exactly the ones whose kept blocks hold the probe (every
     ring-1 one; a ring-2 one only when its matching breaks), the lane's own ball failing inside and passing
     beside; the same for a track (lane +- need + W/2 -+ 0.002);
  3. soundness over the whole band 1 mm either side of U1's courtyard line (step 0.05, exitlane.blocked_by +
     shuts, the group's balls counted as routed as the plan leaves them): no via centre outside every block
     shuts a ball outside the group;
  4. without --spec-blocks, exitlane.py prints and writes byte for byte what the old tool (--old, default
     exitlane.pre_specblocks.py; skipped when absent) does.
Exit 0: all pass; 1: a failure (each named); 2: no copy.
"""
import argparse
import importlib
import json
import math
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KP = HERE.parent.parent                       # kicad_project/
NORTH = [(27.0, "BOOT_CFG4"), (27.5, "BOOT_CFG2"), (28.5, "BOOT_CFG0"), (29.0, "PHY_INT_N"),
         (30.0, "PHY_RESET_N"), (30.5, "ENET_MDC"), (32.5, "WL_HOST_WAKE"), (33.5, "DRAM_DATA8_B"),
         (34.0, "DRAM_DMI1_B")]
EPS = 0.002
FAILS = []


def ok(cond, what):
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        FAILS.append(what)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--copy", default=str(Path("~/.cache/board1_tmp/exl_base").expanduser()))
    ap.add_argument("--nets", default=str(KP / "board1_module/tmpWork/NORTHGAP/r9/v_r9a_gr/r9a_gr.json"))
    ap.add_argument("--mod", default="exitlane", help="the module under test (tools/route/<mod>.py)")
    ap.add_argument("--ref", default="U1")
    ap.add_argument("--old", default=str(HERE / "exitlane.pre_specblocks.py"),
                    help="the tool before --spec-blocks, for 4 (skipped when absent)")
    a = ap.parse_args()
    copy = Path(a.copy).resolve()
    if "/kicad_project/" in str(copy) + "/" or not next(copy.glob("*.kicad_pcb"), None):
        print(f"test_exitlane_blocks: {copy} is not a scratch copy outside kicad_project/", file=sys.stderr)
        return 2
    sys.path[:0] = [str(HERE)]
    import pcbnew
    import route as R
    EL = importlib.import_module(a.mod)
    B = R.RBoard(copy)
    entry = next(e for e in EL.load_spec(B.dir) if e["ref"] == a.ref)
    S = json.loads(Path(a.nets).read_text())
    group = EL.board_nets(B, list(S["nets"]))
    tw = EL.spec_width(S)
    vr = float(S.get("via", [0.6, 0.3])[0]) / 2
    gnet = group[0]
    r0 = EL.judge_entry(B, entry)
    res = EL.spec_blocks(r0, group, tw, entry.get("why"))
    print(f"{a.ref}: {len(res['kept'])} lanes kept, {len(res['block'])} rects, {len(res['shut'])} shut; group "
          f"{len(group)} nets, track {tw}, via r {vr}, test net {EL.short(gnet)}")

    # 1 -- the lanes the r10 worker counted by hand
    print("1. the north ring-1 lanes of balls outside the group")
    north = {round(k["key"][1], 3): k for k in res["kept"] if k["key"][0] == "N" and k["ring"] == 1}
    for x, net in NORTH:
        k = north.get(x)
        ok(k is not None and any(n.endswith(net) or net.endswith(n) for n in k["nets"]),
           f"N lane x {x:.3f} {net}: " + (f"{k['balls']} {k['nets']} need {k['need']}" if k else "missing"))

    # 2 -- real copper on the copy, exitlane's judge as route.py check runs it
    print("2. a via / a track of a group net on the copy, the gate re-run")
    pn = pcbnew
    fm = pn.FromMM

    def with_item(kind, x, y, extra=None):
        if kind == "via":
            it = pn.PCB_VIA(B.b)
            it.SetPosition(pn.VECTOR2I(fm(x), fm(y)))
            it.SetLayerPair(pn.F_Cu, pn.B_Cu)
            try:
                it.SetWidth(fm(2 * vr))
            except TypeError:
                it.SetWidth(fm(2 * vr), pn.F_Cu)
            it.SetDrill(fm(vr))
        else:
            it = pn.PCB_TRACK(B.b)
            it.SetStart(pn.VECTOR2I(fm(x), fm(y)))
            it.SetEnd(pn.VECTOR2I(fm(x), fm(extra)))
            it.SetWidth(fm(tw))
            it.SetLayer(pn.F_Cu)
        it.SetNet(B.b.FindNet(gnet))
        B.b.Add(it)
        try:
            r1 = EL.judge_entry(B, entry)
        finally:
            B.b.Remove(it)
        g = r1["gate"]
        return sorted(set(r1["gate_balls"][g]) - set(r0["gate_balls"][g]))

    gball = {L["ball"] for L in r0["lanes"] if L["net"] in set(group)}

    def zone(x, y0, y1, kind):
        """The balls whose kept lane holds this probe (a via at (x, y0), or a track x, y0..y1): the
        lane's segment +- need, and + the via radius (a via) or + track/2 (a track) -- what the blocks say."""
        z = set()
        for k in res["kept"]:
            sx0, sy0, sx1, sy1 = k["seg"]
            lx0, lx1, ly0, ly1 = min(sx0, sx1), max(sx0, sx1), min(sy0, sy1), max(sy0, sy1)
            if kind == "via":
                dx, dy = max(lx0 - x, 0.0, x - lx1), max(ly0 - y0, 0.0, y0 - ly1)
                if math.hypot(dx, dy) <= k["need"] + vr:
                    z |= set(k["balls"])
            elif lx0 - k["need"] - tw / 2 <= x <= lx1 + k["need"] + tw / 2 and y0 <= ly1 and y1 >= ly0:
                z |= set(k["balls"])
        return z
    ring = {L["ball"]: L["ring"] for L in r0["lanes"]}

    def probe(kind, x, y0, y1, ball, on):
        new = set(with_item(kind, x, y0, y1)) - gball
        z = zone(x, y0, y1, kind)
        z1 = {b for b in z if ring.get(b) == 1}
        ok((ball in z) == on and new <= z and z1 <= new and ((ball in new) == on),
           f"{kind:5s} {'on' if on else 'beside':6s} x {x:.4f}: fail {sorted(new)} == the kept blocks holding it "
           f"{sorted(z)} (ring-1 {sorted(z1)}), {ball} {'fails' if on else 'passes'}")

    for x, net in NORTH:
        k = north.get(x)
        if not k:
            continue
        ball = k["balls"][0]
        y = k["seg"][1] + 0.05                       # inside the lane's span: the distance is the lateral offset
        dv, dt = k["need"] + vr, k["need"] + tw / 2
        probe("via", x + dv - EPS, y, None, ball, True)
        probe("via", x - dv + EPS, y, None, ball, True)
        for sgn in (-1, 1):
            probe("via", x + sgn * (dv + EPS), y, None, ball, False)
        y0, y1 = k["seg"][1] + 0.05, k["seg"][3] - 0.15
        probe("track", x - dt + EPS, y0, y1, ball, True)
        probe("track", x - dt - EPS, y0, y1, ball, False)

    # 3 -- soundness: every via centre outside every block, in the band round the courtyard line
    print("3. the band round the courtyard line: a via outside every block shuts no ball outside the group")
    vb = [b["rect"] for b in res["block"] if b["vias"]]

    def in_block(x, y):
        for x0, y0, x1, y1 in vb:
            dx, dy = max(x0 - x, 0.0, x - x1), max(y0 - y, 0.0, y - y1)
            if dx * dx + dy * dy <= vr * vr + 1e-9:
                return True
        return False
    cy = r0["courtyard"]
    routed = set(r0["routed"]) | gball
    base = set(EL.evaluate(r0["lanes"], r0["blocked"], routed)["gate_balls"][r0["gate"]])
    n_out = n_in = n_in_free = 0
    bad = []
    st = 0.05
    nx, ny = int((cy[2] - cy[0] + 2) / st) + 1, int((cy[3] - cy[1] + 2) / st) + 1
    for i in range(nx):
        x = round(cy[0] - 1 + i * st, 4)
        for j in range(ny):
            y = round(cy[1] - 1 + j * st, 4)
            if min(abs(x - cy[0]), abs(x - cy[2]), abs(y - cy[1]), abs(y - cy[3])) > 1.0:
                continue
            ext = EL.blocked_by(r0, dict(k="via", net=gnet, x=x, y=y, r=vr))
            new = sorted(set(EL.evaluate(r0["lanes"], r0["blocked"] | ext, routed)["gate_balls"][r0["gate"]]) - base)
            if in_block(x, y):
                n_in += 1
                n_in_free += not new
            else:
                n_out += 1
                if new:
                    bad.append(((x, y), new))
    ok(not bad, f"{n_out} via centres outside every block: {len(bad)} shut a ball outside the group"
       + (f": {bad[:6]}" if bad else ""))
    print(f"       ({n_in} centres inside a block; {n_in_free} of them shut nothing: the square ends, and "
          f"ring-2 balls that had another lane under --gate {r0['gate']})")

    # 4 -- the old output, byte for byte, without the new flag
    print(f"4. without --spec-blocks: byte for byte against {Path(a.old).name}")
    kpy = str(KP / "tools/kpy")
    tmp = Path(a.copy) / "_exl_test"
    tmp.mkdir(exist_ok=True)
    old = Path(a.old)
    if not old.exists():
        print(f"  skip {old} absent (keep a copy of exitlane.py from before a change to test it here)")
    for args in (([] if not old.exists() else [["--board", str(copy)],
                  ["--board", str(copy), "--ref", a.ref, "--gate", "strict", "--max", "5"]])):
        outs = []
        for n, path in (("old", old), ("new", HERE / f"{a.mod}.py")):
            o = tmp / f"{n}.json"
            o.unlink(missing_ok=True)
            p = subprocess.run([kpy, str(path)] + args + ["--out", str(o)], capture_output=True, text=True)
            outs.append((p.returncode, p.stdout, p.stderr, o.read_bytes() if o.exists() else b""))
        ok(outs[0] == outs[1] and outs[0][1], f"exitlane {' '.join(args[2:]) or '(every prelayout entry)'}: exit "
           f"{outs[0][0]} / {outs[1][0]}, stdout {len(outs[0][1])} / {len(outs[1][1])} bytes, --out "
           f"{len(outs[0][3])} / {len(outs[1][3])} bytes" + ("" if outs[0] == outs[1] else " -- DIFFER"))
    print(f"TEST_EXITLANE_BLOCKS {'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failure(s)")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
