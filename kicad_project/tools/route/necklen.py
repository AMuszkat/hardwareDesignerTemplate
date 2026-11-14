"""A net's neck-down summed over BOTH outer layers (10_layout #39: 'the 4 mm is the net's whole 0.0889 mm length on the outer
layers, a D64 top stub out of the land included'; board 1's RV27.2 -- a report that counted B.Cu alone passed 4.18 mm).

    tools/kpy tools/route/necklen.py --board BOARD.kicad_pcb [--match DRAM_] [--limit 4.0] [--width 0.0889] [--except NET=MM,...]

necklen.py: every track of the matching nets on F.Cu / B.Cu at the neck width or under, summed per net over both layers.
NECKS PASS|FAIL.
"""
import collections, os, sys
import pcbnew
A = sys.argv[1:]; opt = lambda k, d=None: A[A.index(k) + 1] if k in A else d
b = pcbnew.LoadBoard(opt("--board") or sys.exit("necklen.py: --board BOARD.kicad_pcb")); mm = pcbnew.ToMM
M = opt("--match", ""); LIM = float(opt("--limit", "4.0")); NW = float(opt("--width", "0.0889")) + 0.0006
EXC = {k: float(v) for k, v in (e.split("=") for e in opt("--except", "").split(",") if e)}
tot = collections.defaultdict(lambda: collections.defaultdict(float))
for t in b.GetTracks():
    if t.Type() == pcbnew.PCB_VIA_T: continue
    n = t.GetNetname().rsplit("/", 1)[-1]; L = b.GetLayerName(t.GetLayer())
    if M in n and L in ("F.Cu", "B.Cu") and mm(t.GetWidth()) < NW:
        tot[n][L] += mm(t.GetLength())
rows = sorted(((sum(v.values()), n, dict(v)) for n, v in tot.items()), reverse=True)
bad = [r for r in rows if r[0] > EXC.get(r[1], LIM) + 1e-6]
for s, n, v in rows[:6]:
    print(f"  {n:18s} {s:.3f} mm  (F {v.get('F.Cu', 0):.3f} + B {v.get('B.Cu', 0):.3f})")
print(f"{len(rows)} nets with a 0.0889 mm neck on an outer layer; longest {rows[0][0]:.3f} mm ({rows[0][1]})" if rows else "no neck")
print("NECKS PASS" if not bad else "NECKS FAIL: " + ", ".join(f"{n} {s:.3f}" for s, n, _ in bad))
sys.exit(1 if bad else 0)
