"""Every dangling via on a board, uncapped, and the diff of two such lists.

KiCad's DRC lists at most 199 violations of a type, so a plan that rips and adds vias reorders WHICH
199 via_dangling items show, and route.py check reports one as "new" although it dangled before.
This counts them all with pcbnew's own test (CONNECTIVITY_DATA.TestTrackEndpointDangling, after a
zone refill) and diffs two lists by UUID.

    tools/kpy tools/route/viadangle.py list BOARD.kicad_pcb [OUT.json]   -> "dangling vias N"
    python3   tools/route/viadangle.py diff BASE.json NEW.json           -> DANGLE PASS | DANGLE FAIL (exit 1)

A list is [[net, x, y, uuid], ...]. diff: a via in NEW whose UUID is not in BASE is newly dangling
(FAIL); a UUID only in BASE is gone. Run `list` on the copy before the plan and after it.
"""
import sys
import json


def list_board(path, out=None):
    import pcbnew
    b = pcbnew.LoadBoard(path)
    mm = pcbnew.ToMM
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    b.BuildConnectivity()
    c = b.GetConnectivity()
    rows = []
    for t in b.GetTracks():
        if t.GetClass() == "PCB_VIA" and c.TestTrackEndpointDangling(t, True):
            p = t.GetPosition()
            rows.append([t.GetNetname(), round(mm(p.x), 4), round(mm(p.y), 4), t.m_Uuid.AsString()])
    rows.sort()
    print("dangling vias", len(rows))
    if out:
        with open(out, "w") as f:
            json.dump(rows, f, indent=0)
    return 0


def diff(base, new):
    A, B = (json.load(open(p)) for p in (base, new))
    ua, ub = {r[3]: r for r in A}, {r[3]: r for r in B}
    added = [ub[u] for u in ub if u not in ua]
    gone = [ua[u] for u in ua if u not in ub]
    for r in added:
        print("NEW ", r)
    for r in gone:
        print("GONE", r)
    print(f"dangling vias {len(A)} -> {len(B)}")
    print("DANGLE PASS" if not added else "DANGLE FAIL")
    return 0 if not added else 1


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) >= 2 and a[0] == "list":
        sys.exit(list_board(a[1], a[2] if len(a) > 2 else None))
    if len(a) == 3 and a[0] == "diff":
        sys.exit(diff(a[1], a[2]))
    print(__doc__)
    sys.exit(2)
