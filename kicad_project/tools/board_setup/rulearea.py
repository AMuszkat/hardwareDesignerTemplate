"""Draw (or check) a named rule area: the area a custom DRC rule tests with intersectsArea('NAME').

    tools/kpy tools/board_setup/rulearea.py draw  --board B --name NAME --rect X0,Y0,X1,Y1 [--layers copper|F.Cu,In2.Cu,..] [--keepout tracks,vias,pads,pour,footprints]
    tools/kpy tools/board_setup/rulearea.py check --board B --name NAME --rect X0,Y0,X1,Y1 [same options]
    tools/kpy tools/board_setup/rulearea.py remove --board B --name NAME

A handoff page that writes a rule over an area ("the rule area U1_FIRST_MM_NORTH, x 20.725-37.275,
y 13.225-21.5, every copper layer, no keep-outs") leaves the area to the board; this draws it
exactly so.  `draw` is idempotent: every rule area of that name is removed first, then ONE is
added, the board saved by pcbnew (KiCad closed: refused while a lock file exists), a backup made
in <board>/.board_setup_backups/<ts>-rulearea/, and the result read back by `check` in a fresh
load.  Keep-outs default to NONE (every DoNotAllow flag cleared; this KiCad's ZONE has
SetDoNotAllowZoneFills, not ...CopperPour).  `check` exits 1 on any difference: missing,
duplicated, not a rule area, outline, layers or flags.  Board mm, rectangle axis-aligned.
"""
import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "place"))
import boardsel                                       # noqa: E402
from place import board_dir_arg                        # noqa: E402  refuses a copy under tmpWork/
import pcbnew                                          # noqa: E402

FLAGS = {"tracks": "Tracks", "vias": "Vias", "pads": "Pads", "pour": "ZoneFills",
         "footprints": "Footprints"}
EPS = 1e-4  # mm


def die(m):
    print(f"rulearea: {m}", file=sys.stderr)
    sys.exit(2)


def rect(s):
    v = [float(t) for t in s.split(",")]
    if len(v) != 4 or v[0] >= v[2] or v[1] >= v[3]:
        die(f"--rect wants X0,Y0,X1,Y1 with X0<X1, Y0<Y1, got {s!r}")
    return v


def layerset(b, spec):
    ls = pcbnew.LSET()
    if spec == "copper":
        return pcbnew.LSET.AllCuMask(b.GetCopperLayerCount())
    for n in spec.split(","):
        lid = b.GetLayerID(n.strip())
        if lid < 0 or not pcbnew.IsCopperLayer(lid) or not b.IsLayerEnabled(lid):
            die(f"layer {n!r} is not an enabled copper layer of this board")
        ls.AddLayer(lid)
    return ls


def named(b, name):
    return [z for z in b.Zones() if z.GetIsRuleArea() and z.GetZoneName() == name]


def keepouts(spec):
    ks = [k.strip() for k in spec.split(",") if k.strip()] if spec else []
    bad = [k for k in ks if k not in FLAGS]
    if bad:
        die(f"--keepout: unknown {bad} (have {sorted(FLAGS)})")
    return set(ks)


def compare(b, a):
    zs = named(b, a.name)
    if len(zs) != 1:
        return [f"{len(zs)} rule areas named {a.name!r}, want 1"]
    z, diffs = zs[0], []
    o = z.Outline()
    pts = sorted({(round(pcbnew.ToMM(p.x), 4), round(pcbnew.ToMM(p.y), 4))
                  for p in (o.CVertex(i) for i in range(o.TotalVertices()))})
    x0, y0, x1, y1 = a.rect
    want = sorted({(x0, y0), (x1, y0), (x1, y1), (x0, y1)})
    if o.OutlineCount() != 1 or len(pts) != 4 or any(
            abs(p[0] - q[0]) > EPS or abs(p[1] - q[1]) > EPS for p, q in zip(pts, want)):
        diffs.append(f"outline {pts}, want {want}")
    have_l = sorted(b.GetLayerName(l) for l in z.GetLayerSet().CuStack())
    want_l = sorted(b.GetLayerName(l) for l in layerset(b, a.layers).CuStack())
    if have_l != want_l:
        diffs.append(f"layers {have_l}, want {want_l}")
    ko = keepouts(a.keepout)
    for k, f in FLAGS.items():
        if bool(getattr(z, f"GetDoNotAllow{f}")()) != (k in ko):
            diffs.append(f"keep-out {k}: {getattr(z, f'GetDoNotAllow{f}')()}, want {k in ko}")
    return diffs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["draw", "check", "remove"])
    ap.add_argument("--board")
    ap.add_argument("--name", required=True)
    ap.add_argument("--rect", type=rect)
    ap.add_argument("--layers", default="copper", help="'copper' (every copper layer) or a comma list")
    ap.add_argument("--keepout", default="", help="comma list of tracks,vias,pads,pour,footprints (default none)")
    a = ap.parse_args()
    board_dir = board_dir_arg(a.board)
    pcb = boardsel.one_pcb(board_dir)
    if a.command != "remove" and a.rect is None:
        die("--rect is required")

    if a.command == "check":
        d = compare(pcbnew.LoadBoard(str(pcb)), a)
        print(f"rulearea check {a.name} on {pcb}: " + ("PASS" if not d else "FAIL\n  " + "\n  ".join(d)))
        sys.exit(1 if d else 0)

    lk = [p for p in (pcb.parent / f"~{pcb.name}.lck", pcb.parent / f"~{pcb.with_suffix('.kicad_pro').name}.lck")
          if p.exists()]
    if lk:
        die(f"KiCad lock file {lk[0].name}: close KiCad first")
    b = pcbnew.LoadBoard(str(pcb))
    old = named(b, a.name)
    bk = board_dir / ".board_setup_backups" / (time.strftime("%Y%m%d-%H%M%S") + "-rulearea")
    bk.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pcb, bk / pcb.name)
    for z in old:
        b.Remove(z)
    if a.command == "draw":
        z = pcbnew.ZONE(b)
        z.SetIsRuleArea(True)
        z.SetZoneName(a.name)
        ko = keepouts(a.keepout)
        for k, f in FLAGS.items():
            getattr(z, f"SetDoNotAllow{f}")(k in ko)
        z.SetLayerSet(layerset(b, a.layers))
        o = z.Outline()
        o.NewOutline()
        x0, y0, x1, y1 = a.rect
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            o.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
        b.Add(z)
    pcbnew.SaveBoard(str(pcb), b)
    print(f"rulearea {a.command} {a.name}: removed {len(old)}, "
          f"{'added 1' if a.command == 'draw' else 'added 0'}; backup {bk}")
    if a.command == "draw":
        d = compare(pcbnew.LoadBoard(str(pcb)), a)
        print("read back: " + ("PASS" if not d else "FAIL\n  " + "\n  ".join(d)))
        sys.exit(1 if d else 0)


if __name__ == "__main__":
    main()
