"""Two nets of one negotiation whose ENDS stand closer than a track and its clearance: no router separates them.

    tools/kpy tools/route/endpairs.py --board <board dir> routing/SPEC.json [SPEC.json ...]     (python3 works for specs
                                                                                               whose fields all have a box)

A pre-check that runs every net ALONE cannot see this: each end is free against the board, and two of them together are not.
For every pair of nets in a netgroup spec and every pair of their ends (`src`, each leg's `to_xy`) that share a layer, the
centre distance is held against width + clearance of the copper they must run in THERE: a field's own numbers only when both
ends lie inside the same field by more than that distance from its edge -- an end ON a field's edge leaves it into the
spec's base width and clearance, and that is where fan-ins end.  The distance is rounded up to the spec's pitch, as the
router's cells do.  Also refused: a `via_sep` below via pad + base clearance (two new vias the router would stand closer
than DRC allows).  Exit 1 and one line a pair (`INHERENT`), exit 0 and `ENDPAIRS PASS` otherwise.  Read-only.
"""
import itertools, json, math, os, sys

A = sys.argv[1:]
board = A[A.index("--board") + 1] if "--board" in A else None
specs = [a for i, a in enumerate(A) if a.endswith(".json") and (i == 0 or A[i - 1] != "--board")]
if not specs:
    sys.exit(__doc__)
_cy = {}


def courtyard(ref):
    """the courtyard's bounding box of a footprint, or None when no board was named"""
    if ref in _cy or not board:
        return _cy.get(ref)
    import pcbnew
    pcb = next(f for f in os.listdir(board) if f.endswith(".kicad_pcb"))
    b = pcbnew.LoadBoard(os.path.join(board, pcb)); mm = pcbnew.ToMM
    for f in b.GetFootprints():
        s = f.GetCourtyard(pcbnew.F_CrtYd if not f.IsFlipped() else pcbnew.B_CrtYd).BBox()
        _cy[f.GetReference()] = [mm(s.GetLeft()), mm(s.GetTop()), mm(s.GetRight()), mm(s.GetBottom())]
    return _cy.get(ref)


def ends(S, n):
    """[(x, y, layers or None for a via / every layer, what)] -- only ends given as coordinates"""
    d = S["nets"][n]; out = []
    lay = d.get("layer"); lay = None if isinstance(lay, list) else lay
    if isinstance(d.get("src"), list) and len(d["src"]) == 2 and all(isinstance(v, (int, float)) for v in d["src"]):
        out.append((d["src"][0], d["src"][1], lay, "src"))
    for leg in d.get("legs", []):
        t = leg.get("to_xy")
        if t:
            out.append((t[0], t[1], leg.get("goal_layer"), "end"))
    return out


def terms(S, p, q, layer):
    """(width, clearance, where) for two ends: a field's when both are well inside it, else the spec's base"""
    W, C = S["width"], S["clearance"]
    for f in S.get("fields", []):
        if layer and f.get("open") and layer not in f["open"]:
            continue
        r = f.get("box") or courtyard(f.get("ref"))
        if not r:
            continue
        m = f.get("width", W) + f.get("clr", C)
        if all(r[0] + m <= x <= r[2] - m and r[1] + m <= y <= r[3] - m for x, y in (p, q)):
            return f.get("width", W), f.get("clr", C), "field " + str(f.get("ref") or f.get("box"))
    return W, C, "the spec's base"


bad = 0
for sp in specs:
    S = json.load(open(sp)); P = S.get("pitch", 0.025); found = []
    for a, b in itertools.combinations(sorted(S["nets"]), 2):
        for (x1, y1, l1, k1), (x2, y2, l2, k2) in itertools.product(ends(S, a), ends(S, b)):
            if k1 == k2 == "src" and l1 is None and l2 is None:
                continue                                        # two standing vias: the board's DRC judged them, not this
            d = math.hypot(x1 - x2, y1 - y2)
            if l1 is None and l2 is None:                       # two vias
                need, why = S.get("via_sep", S["via"][0] + S["clearance"]), "via_sep"
            elif l1 is None or l2 is None or l1 == l2:
                w, c, why = terms(S, (x1, y1), (x2, y2), l1 or l2)
                need = (S["via"][0] / 2 + w / 2 + c) if (l1 is None or l2 is None) else w + c
            else:
                continue
            cells = math.ceil(need / P - 1e-9) * P
            if d < cells - 1e-9:
                found.append((d, f"INHERENT {a} {k1} ({x1:.3f}, {y1:.3f}) / {b} {k2} ({x2:.3f}, {y2:.3f}) on {l1 or l2 or 'vias'}: "
                                 f"{d:.3f} mm apart, {need:.4f} needed ({why}; {cells:.3f} on the {P} grid)"))
    for _, line in sorted(found):
        print(line)
    vs, vneed = S.get("via_sep", 0.8), S["via"][0] + S["clearance"]
    if len(S["nets"]) > 1 and vs < vneed - 1e-9:     # the router spaces two NEW vias by via_sep alone; DRC by pad + clearance
        print(f"VIA_SEP {vs} is below via pad {S['via'][0]} + clearance {S['clearance']} = {vneed:.4f}: two new vias of "
              f"different nets may stand where DRC refuses them, and the plan fails its check after the round")
        found.append((0, "via_sep"))
    print(f"{os.path.basename(sp)}: {len(S['nets'])} nets, {len(found)} finding(s): ends too close to be routed together, or via_sep")
    bad += len(found)
print("ENDPAIRS PASS" if not bad else f"ENDPAIRS FAIL: {bad} -- spread the ends (or move the field's edge past where they spread) before any round")
sys.exit(1 if bad else 0)
