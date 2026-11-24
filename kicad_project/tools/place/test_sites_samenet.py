"""Tests for sites.World.via_ok's same-net rules (no board needed: a World is built by hand).

    tools/kpy tools/place/test_sites_samenet.py

A via of net N: a pad of N at OWN from its centre, a track of N may touch, a via of N at Board Setup's hole to
hole; every other net at CLR.  Without a net the old net-blind test holds (every item foreign).  TRAP (board1
R1d134b, 2026-11-24): the net-blind test refused GND vias 0.48 mm c-c from other GND vias, sites DRC passes."""
import math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sites as S  # noqa: E402

S.VIA, S.DRILL, S.CLR, S.OWN, S.H2H = 0.4, 0.2, 0.2032, 0.3016, 0.28


def world(pads=(), tracks=(), vias=()):
    W = S.World.__new__(S.World)
    W.short = lambda n: n.split("/")[-1]
    W.pads, W.tracks, W.vias, W.court, W.side, W._memo = list(pads), list(tracks), list(vias), {}, {}, {}
    W.lanes = W.lane_drop = None
    W.outline, W.edge = (-10, -10, 10, 10), 0.4
    return W


def via(x, y, net, drill=0.2, d=0.4):
    return S._Item.of((x, y, d / 2), net, drill)


def trk(a, b, w, net):
    return S._Item.of((a, b, w), net)


FAILS = []


def check(name, got, want):
    print(("ok  " if got == want else "FAIL") + f"  {name}: {got} (want {want})")
    if got != want:
        FAILS.append(name)


# a GND via at 0.48 c-c (hole edges 0.28 apart = H2H): legal for GND, not for a foreign net
W = world(vias=[via(0.48, 0, "GND")])
check("same-net via at 0.48 c-c, net GND", W.via_ok(0, 0, net="GND"), True)
check("same-net via at 0.48 c-c, full net name", W.via_ok(0, 0, net="/LPDDR4/GND"), True)
check("same via, net-blind", W.via_ok(0, 0), False)
check("same via, foreign net VDD", W.via_ok(0, 0, net="VDD"), False)
W = world(vias=[via(0.47, 0, "GND")])
W = world(vias=[via(0.53, 0, "GND", drill=0.3, d=0.5)])
check("same-net 0.3-drill via at 0.53 c-c: hole to hole exactly 0.28", W.via_ok(0, 0, net="GND"), True)
W = world(vias=[via(0.47, 0, "GND")])
check("same-net via at 0.47 c-c breaks hole to hole", W.via_ok(0, 0, net="GND"), False)
W = world(vias=[via(0.52, 0, "GND", drill=0.3, d=0.5)])
check("same-net 0.3-drill via at 0.52 c-c breaks hole to hole (0.28 + 0.25)", W.via_ok(0, 0, net="GND"), False)
W = world(vias=[via(0.81, 0, "VDD")])
check("foreign via at 0.81 c-c (0.41 copper gap > CLR)", W.via_ok(0, 0, net="GND"), True)
W = world(vias=[(0.48, 0, 0.2)])
check("a caller's plain tuple via is foreign", W.via_ok(0, 0, net="GND"), False)

# a track of its net may touch; a foreign one keeps CLR
W = world(tracks=[trk((0.25, -1), (0.25, 1), 0.1, "GND")])
check("same-net track touching", W.via_ok(0, 0, net="GND"), True)
check("same track, net-blind", W.via_ok(0, 0), False)
check("same track, foreign net", W.via_ok(0, 0, net="SIG"), False)

# a pad of its net at OWN from the centre; a foreign pad at VIA/2 + CLR
pad = ("U1", "A1", "GND", 0.31, -0.1, 0.51, 0.1)     # 0.31 from the via's centre
W = world(pads=[pad])
check("same-net pad at 0.31 >= OWN", W.via_ok(0, 0, net="GND"), True)
check("same pad, net-blind (0.31 < 0.2 + 0.2032)", W.via_ok(0, 0), False)
W = world(pads=[("U1", "A1", "GND", 0.29, -0.1, 0.49, 0.1)])
check("same-net pad at 0.29 < OWN (via-in-pad gap)", W.via_ok(0, 0, net="GND"), False)
W = world(pads=[("U1", "A1", "", 0.31, -0.1, 0.51, 0.1)])
check("an unconnected pad is never the via's net", W.via_ok(0, 0, net=""), False)

# via_free memoises per net
W = world(vias=[via(0.48, 0, "GND")])
check("via_free GND", W.via_free(0, 0, "GND"), True)
check("via_free net-blind after GND (separate memo)", W.via_free(0, 0), False)

print("TESTS PASS" if not FAILS else f"TESTS FAIL: {len(FAILS)}")
sys.exit(1 if FAILS else 0)
