#!/usr/bin/env python3
"""Routing for a board, one reviewed group at a time (see <board>/ROUTING_PLAN.md).

    tools/kpy tools/route/route.py status --board board1 [--nets]
    tools/kpy tools/route/route.py check  --board board1 routing/R1_power.json
    tools/kpy tools/route/route.py apply  --board board1 routing/R1_power.json
    tools/kpy tools/route/route.py rip    --board board1 R1
    tools/kpy tools/route/route.py render --board board1 [--out DIR]
    tools/kpy tools/route/route.py angles --board board1 [--nets]
    tools/kpy tools/route/route.py edge   --board board1
    tools/kpy tools/route/route.py audit  --board board1              # every rule of the guide, on the board as it is
    tools/kpy tools/route/route.py rules  --board board1              # what the guide's table switches on

  status  what is routed and what is open: per net class, per group, and the
          groups already applied (from their manifests).  Read-only.
  check   write the plan into a SCRATCH COPY of the board and judge it with
          KiCad's own DRC (schematic parity on).  Any violation not already in
          the baseline fails the plan.  Then every rule of the board's
          general_routing_guide.md that DRC cannot express.  Read-only.
  apply   check, then write to the real board (KiCad closed, backup first),
          read back, and record every UUID in <board>/routing/<group>.applied.json
          so the group can be ripped out again.
  rip     delete exactly the copper a group applied, by UUID.  KiCad closed.
  render  colour PNGs of the whole board (top, bottom, inner layers).
  angles  audit the copper already on the board: every joint 0 or 45 degrees.
  edge    audit the copper already on the board against the copper-to-edge
          minimum (the guide's "copper to edge", else Board Setup's).
  audit   every rule of the guide on the copper already laid: DRC, angles,
          edge, copper shape, via links, ground beside, pour islands.  One
          PASS/FAIL line each; exit 1 on a failure.  The group report quotes it.
  rules   print the rules the guide's table switches on, each with its source.

THE GATE IS KICAD'S DRC, NOT ARITHMETIC OF MINE.  A hand-written clearance
check that uses the track's own net class misses that KiCad enforces the
STRICTER of the two nets' clearances (a Default track beside a Power pad needs
the Power clearance).  So `check` writes the plan into a copy and asks KiCad.

THE RULES are in <board>/general_routing_guide.md: prose for the reader and
one table the tools read (tools/route/guide.py).  A rule absent from the table
is not run; `route.py rules` prints what is.  In brief, what the table can
switch on: joints 0/45, copper to edge, via to pad gap and the 45-degree link,
copper shape, class width, thermals, plane layers, no-via classes, class
layers, keep-outs, a via field (a BGA), ground beside, frozen copper.

A plan may carry, besides tracks/vias/zones:
  rips      [{"net", "layer", "pts": [[x, y], [x, y]], "width"?}]   segments to remove first
  ripvias   [{"net", "at": [x, y]}]                                  vias to remove first
  polyrips  [{"net", "layer", "at": [x0, y0]}]                      net-tied polygons to remove (first vertex)
  polys     [{"net", "layer", "pts": [[x, y], ...]}]                 net-tied copper polygons to add
  thermals  true                                                     type the guide's thermal policy, then refill
`apply` lays them in that order, in one save, and the manifest records what
was added (by UUID) and what was removed (a rip is undone from the backup).
"""

import argparse
from collections import defaultdict
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "place"))
import place as P  # noqa: E402  (die, quiet_stderr, Board, board_dir_arg)

die, quiet_stderr, Board = P.die, P.quiet_stderr, P.Board
boardsel = P.boardsel
sys.path.insert(0, str(ROOT / "tools" / "route"))
sys.path.insert(0, str(ROOT / "tools" / "copper"))
import guide                                                # noqa: E402


def group_of(plan_path, plan):
    g = plan.get("group")
    if not g:
        die(f"{plan_path}: the plan has no \"group\"")
    return g


def manifest_path(board_dir, group):
    return board_dir / "routing" / f"{group}.applied.json"


# ---------------------------------------------------------------------------
# the board, with the routing bits place.Board does not model
# ---------------------------------------------------------------------------

class RBoard(Board):
    def __init__(self, board_dir):
        super().__init__(board_dir)
        pn = self.pn
        self.layer_id = {}
        self.netnames = sorted(str(n) for n in self.b.GetNetsByName().keys() if str(n))
        self._classes = None
        self.gates = load_gates(board_dir)

    def net(self, name):
        n = self.b.FindNet(str(name))
        if n is None:
            die(f"net not on the board: {name!r} (route.py status --nets lists them)")
        return n

    def netclass(self, name):
        return self.net(name).GetNetClassName() or "Default"

    def netclasses(self, name):
        """The net's classes as a list: KiCad reports a net assigned to two
        classes as "Power,HV", which is the name of neither."""
        return [c.strip() for c in self.netclass(name).split(",") if c.strip()] or ["Default"]

    def _class(self, name, key):
        """A class number from the .kicad_pro (pcbnew's NETCLASS object is opaque
        through SWIG, so the classes are read from the project file)."""
        if self._classes is None:
            pro = next(self.dir.glob("*.kicad_pro"), None)
            j = json.loads(pro.read_text()) if pro else {}
            self._classes = {c.get("name"): c for c in j.get("net_settings", {}).get("classes", [])}
        c = next((self._classes[n] for n in self.netclasses(name) if n in self._classes),
                 None) or self._classes.get("Default") or {}
        return c.get(key)

    def class_width(self, name):
        return self._class(name, "track_width")

    def class_clearance(self, name):
        return self._class(name, "clearance")

    def layer(self, name):
        """A layer id by name (KiCad's canonical name or the board's own)."""
        if name not in self.layer_id:
            lid = self.b.GetLayerID(name)
            if lid < 0 or not self.b.IsLayerEnabled(lid):
                die(f"layer {name!r} is not on this board")
            self.layer_id[name] = lid
        return self.layer_id[name]

    def copper_layers(self):
        """Canonical names (F.Cu, In1.Cu, ... B.Cu) -- a board may rename its layers."""
        return [self.pn.BOARD.GetStandardLayerName(l) for l in self.b.GetEnabledLayers().CuStack()]

    def canon(self, name):
        """A layer named either way (canonical or the board's own) -> canonical."""
        return self.pn.BOARD.GetStandardLayerName(self.layer(name))

    def field(self, ref):
        """(x0, y0, x1, y1) of a footprint's pad field (pad centres), and the
        lattice of diagonal centres of its pad grid -- where a dog-bone via sits."""
        f = self.fps.get(ref)
        if not f:
            return None, []
        pads = [(self.mm(p.GetPosition().x), self.mm(p.GetPosition().y)) for p in f.Pads()]
        xs, ys = sorted({round(x, 3) for x, _ in pads}), sorted({round(y, 3) for _, y in pads})
        box = (min(xs), min(ys), max(xs), max(ys))
        lattice = [((xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2)
                   for i in range(len(xs) - 1) for j in range(len(ys) - 1)]
        return box, lattice

    def keepout_rect(self, k):
        """A guide keep-out as absolute board mm.  "rect" is board-relative
        (the outline's top-left is 0, 0; null = open on that side), like
        placement.json; "from_placement" names a placement.json keep-out."""
        r = k.get("rect")
        if r is None and k.get("from_placement"):
            r = next((q["rect"] for q in self.cfg.get("keepouts", []) if q["name"] == k["from_placement"]), None)
            if r is None:
                die(f"guide keep-out {k.get('name')!r}: no placement.json keep-out named "
                    f"{k['from_placement']!r}")
        if r is None:
            die(f"guide keep-out {k.get('name')!r} has neither \"rect\" nor \"from_placement\"")
        o = self.outline
        return (o[0] + r[0] if r[0] is not None else -1e9, o[1] + r[1] if r[1] is not None else -1e9,
                o[0] + r[2] if r[2] is not None else 1e9, o[1] + r[3] if r[3] is not None else 1e9)

    # -- writing ----------------------------------------------------------
    def add_track(self, net, layer, width, a, b):
        pn = self.pn
        t = pn.PCB_TRACK(self.b)
        t.SetStart(pn.VECTOR2I(pn.FromMM(a[0]), pn.FromMM(a[1])))
        t.SetEnd(pn.VECTOR2I(pn.FromMM(b[0]), pn.FromMM(b[1])))
        t.SetWidth(pn.FromMM(width))
        t.SetLayer(self.layer(layer))
        t.SetNet(self.net(net))
        self.b.Add(t)
        return t

    def add_via(self, net, at, pad, drill, top="F.Cu", bottom="B.Cu"):
        pn = self.pn
        v = pn.PCB_VIA(self.b)
        v.SetPosition(pn.VECTOR2I(pn.FromMM(at[0]), pn.FromMM(at[1])))
        v.SetWidth(pn.FromMM(pad))
        v.SetDrill(pn.FromMM(drill))
        v.SetLayerPair(self.layer(top), self.layer(bottom))
        v.SetNet(self.net(net))
        self.b.Add(v)
        return v

    def add_zone(self, net, layer, poly, pad_connection=None, min_width=None, priority=None, clearance=None,
                 holes=None, thermal_gap=None, spoke_width=None):
        pn = self.pn
        z = pn.ZONE(self.b)
        z.SetLayer(self.layer(layer))
        z.SetNet(self.net(net))
        if min_width is not None:
            # KiCad's default minimum fill width (0.25 mm) is wider than the bridges
            # a fine-pitch BGA field leaves between antipads, so a pour under one
            # fragments unless this comes down to the fab's minimum.
            z.SetMinThickness(pn.FromMM(min_width))
        # A zone's OWN clearance, or KiCad's default 0.5 mm applies: antipads that
        # wide round every via leave no fill between neighbouring vias and strand
        # caps on islands of their own.  Default: the net's class clearance.
        if clearance is None:
            clearance = self.class_clearance(net) or 0.2
        z.SetLocalClearance(pn.FromMM(clearance))
        if priority is not None:
            z.SetAssignedPriority(int(priority))    # higher wins where two pours overlap
        if pad_connection:
            # "tht_thermal": reliefs on through-hole pads, solid on SMD
            z.SetPadConnection({"solid": pn.ZONE_CONNECTION_FULL,
                                "thermal": pn.ZONE_CONNECTION_THERMAL,
                                "tht_thermal": pn.ZONE_CONNECTION_THT_THERMAL,
                                "none": pn.ZONE_CONNECTION_NONE}[pad_connection])
        if thermal_gap is not None:
            z.SetThermalReliefGap(pn.FromMM(thermal_gap))
        if spoke_width is not None:
            z.SetThermalReliefSpokeWidth(pn.FromMM(spoke_width))
        pts = pn.VECTOR_VECTOR2I()
        for x, y in poly:
            pts.append(pn.VECTOR2I(pn.FromMM(x), pn.FromMM(y)))
        z.AddPolygon(pts)
        # holes: polygons cut out of the outline (a BGA's ball field, standoff keep-outs)
        for hole in holes or []:
            ol = z.Outline()
            h = ol.NewHole(0)
            for x, y in hole:
                ol.Append(pn.FromMM(x), pn.FromMM(y), 0, h)
        z.SetIsFilled(False)
        self.b.Add(z)
        return z

    def fill_zones(self):
        """Fill every zone on this board.  Without it a pour is an outline: DRC
        sees no copper, the open count does not move, and a GUI review shows a
        board that is not the one that was checked."""
        with P.quiet_stderr():
            filler = self.pn.ZONE_FILLER(self.b)
            filler.Fill(self.b.Zones())

    def rip_plan(self, plan):
        """The plan's rips, ripvias and polyrips, removed from the in-memory board.
        Returns {"rips": n, "ripvias": n, "polyrips": n}; a listed item that is
        not on the board stops the plan (it was written against another board)."""
        import poly as POLY
        mm = self.pn.ToMM
        n = {"rips": 0, "ripvias": 0, "polyrips": 0}
        for r in plan.get("rips", []):
            (x1, y1), (x2, y2) = r["pts"]
            want = sorted([(x1, y1), (x2, y2)])
            hit = None
            for t in self.b.GetTracks():
                if t.Type() == self.pn.PCB_VIA_T or t.GetLayerName() != r["layer"]:
                    continue
                if t.GetNetname() != r["net"] and short(t.GetNetname()) != r["net"]:
                    continue
                if "width" in r and abs(mm(t.GetWidth()) - r["width"]) > 0.001:
                    continue
                a_, e_ = t.GetStart(), t.GetEnd()
                have = sorted([(mm(a_.x), mm(a_.y)), (mm(e_.x), mm(e_.y))])
                if all(abs(have[i][j] - want[i][j]) < 0.01 for i in range(2) for j in range(2)):
                    hit = t
                    break
            if hit is None:
                die(f"plan rips a {r['net']} segment on {r['layer']} at {r['pts']} that is not on the board")
            self.b.RemoveNative(hit)
            n["rips"] += 1
        for r in plan.get("ripvias", []):
            hit = next((t for t in self.b.GetTracks() if t.Type() == self.pn.PCB_VIA_T
                        and (t.GetNetname() == r["net"] or short(t.GetNetname()) == r["net"])
                        and abs(mm(t.GetPosition().x) - r["at"][0]) < 0.01 and abs(mm(t.GetPosition().y) - r["at"][1]) < 0.01), None)
            if hit is None:
                die(f"plan rips a {r['net']} via at {r['at']} that is not on the board")
            self.b.RemoveNative(hit)
            n["ripvias"] += 1
        for r in plan.get("polyrips", []):
            hit = POLY.match(self.b, self.pn, r)
            if hit is None:
                die(f"plan rips a {r['net']} polygon on {r['layer']} at {r['at']} that is not on the board")
            self.b.RemoveNative(hit)
            n["polyrips"] += 1
        return n

    def write_plan(self, plan):
        """Everything the plan declares, onto this in-memory board, in order:
        rips, then polygons, tracks, vias and zones, then the thermal policy,
        then a refill.  Returns the items added (and the removal counts)."""
        import poly as POLY
        made = {"tracks": [], "vias": [], "zones": [], "polys": []}
        made["removed"] = self.rip_plan(plan)
        for q in plan.get("polys", []):
            made["polys"].append(POLY.add(self.b, self.pn, q))
        for t in plan.get("tracks", []):
            pts = t["pts"]
            for a, b in zip(pts, pts[1:]):
                made["tracks"].append(self.add_track(t["net"], t.get("layer", "F.Cu"),
                                                     t["width"], a, b))
        for v in plan.get("vias", []):
            made["vias"].append(self.add_via(v["net"], v["at"], v.get("pad", 0.6),
                                             v.get("drill", 0.3),
                                             v.get("top", "F.Cu"), v.get("bottom", "B.Cu")))
        for z in plan.get("zones", []):
            made["zones"].append(self.add_zone(z["net"], z["layer"], z["poly"],
                                               pad_connection=z.get("pad_connection"),
                                               min_width=z.get("min_width"), priority=z.get("priority"),
                                               clearance=z.get("clearance"), holes=z.get("holes"),
                                               thermal_gap=z.get("thermal_gap"), spoke_width=z.get("spoke_width")))
        if plan.get("thermals"):
            import thermals as TH
            if not self.gates.get("thermals"):
                die("the plan says \"thermals\": true but the guide has no 'thermals' row")
            TH.apply(self.b, self.pn, self.gates)
        if made["zones"] or made["polys"] or made["removed"]["rips"] or plan.get("thermals") or any(True for _ in self.b.Zones()):
            self.fill_zones()
        return made

    def save(self, path=None):
        """Save, then READ THE FILE BACK and compare every track's and via's net
        with what this board was told.  Returns the differences, as text.

        ⚠ pcbnew re-nets copper on the way to disk: a track or via that touches
        a pad of ANOTHER net, with no pad of its own net in the same piece of
        copper, is silently given the pad's net.  The short is then not a short
        any more -- DRC sees one net, the open count looks right, and the plan's
        GND stub is a +3V3 stub.  The only place it shows is here."""
        want = {t.m_Uuid.AsString(): (t.GetNetname(), t.GetLayerName(),
                                      self.mm(t.GetPosition().x), self.mm(t.GetPosition().y),
                                      "via" if t.Type() == self.pn.PCB_VIA_T else "track")
                for t in self.b.GetTracks()}
        with quiet_stderr():
            ok = self.pn.SaveBoard(str(path or self.pcb), self.b)
        if not ok:
            die("SaveBoard failed", 4)
        with quiet_stderr():
            chk = self.pn.LoadBoard(str(path or self.pcb))
        moved = []
        for t in chk.GetTracks():
            w = want.get(t.m_Uuid.AsString())
            if w and w[0] != t.GetNetname():
                moved.append(f"{w[4]} on {w[1]} at ({w[2]:.3f}, {w[3]:.3f}) was {short(w[0]) or 'no net'} "
                             f"and is saved as {short(t.GetNetname()) or 'no net'}: it touches copper "
                             f"of that net (a SHORT that KiCad hides by renaming)")
        return moved

    def edge_segs(self):
        """The board outline as segments in mm, cut-outs included -- from KiCad's
        own outline builder (arcs and circles sampled, polygons closed)."""
        rings = self.rings
        if not rings:
            o = self.outline
            rings = [[(o[0], o[1]), (o[2], o[1]), (o[2], o[3]), (o[0], o[3])]]
        return [(a, b) for pts in rings for a, b in zip(pts, pts[1:] + pts[:1])]


# ---------------------------------------------------------------------------
# DRC, through place.py's gate (schematic parity on)
# ---------------------------------------------------------------------------

def drc_report(pcb):
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "drc.json"
        r = subprocess.run(["kicad-cli", "pcb", "drc", "--schematic-parity", "--format", "json",
                            "-o", str(out), str(pcb)], capture_output=True, text=True)
        if not out.exists():
            die(f"kicad-cli pcb drc failed: {r.stderr.strip()[-300:]}", 4)
        return json.loads(out.read_text())


def viol_keys(rep):
    keys = []
    for v in rep.get("violations", []):
        items = tuple(sorted(i.get("description", "") for i in v.get("items", [])))
        keys.append((v["type"], v.get("description", ""), items))
    return keys


def open_count(rep):
    return len(rep.get("unconnected_items", []))


# ---------------------------------------------------------------------------
# rule gates
# ---------------------------------------------------------------------------

def load_gates(board_dir):
    """The rules of <board>/general_routing_guide.md (tools/route/guide.py).
    No guide, or an empty table: KiCad's DRC alone judges a plan."""
    return guide.load(board_dir)


def short(net):
    """The leaf of a hierarchical net name: '/Power/+3V3' -> '+3V3'."""
    return net.rsplit("/", 1)[-1]


def _seg_hits_rect(a, b, r):
    """Does segment a-b touch the rectangle r = (x0, y0, x1, y1)?"""
    if any(r[0] <= p[0] <= r[2] and r[1] <= p[1] <= r[3] for p in (a, b)):
        return True
    n = max(2, int(math.dist(a, b) / 0.05))
    return any(r[0] <= a[0] + (b[0] - a[0]) * k / n <= r[2] and r[1] <= a[1] + (b[1] - a[1]) * k / n <= r[3]
               for k in range(n + 1))


def rule_gates(B, plan):
    """The rules KiCad's DRC cannot express, from the guide. Returns failures."""
    G = B.gates
    bad = []
    necks = {(n["net"], round(float(n["width"]), 4)) for n in plan.get("necks", [])}
    planes = {B.canon(q["layer"]): q for q in G.get("plane_layers", [])}
    cvias = {q["class"]: q for q in G.get("class_vias", [])}
    clays = {q["class"]: dict(q, layers=[B.canon(n) for n in q["layers"]]) for q in G.get("class_layers", [])}
    keeps = [(k, B.keepout_rect(k)) for k in G.get("keepouts", [])]
    fields = []
    for fv in G.get("field_vias", []):
        box, lattice = B.field(fv["ref"])
        if box is None:
            bad.append(f"field-via: {fv['ref']} is not on the board ({fv['source']})")
            continue
        fields.append((fv, box, lattice))

    def field_at(x, y, margin=None):
        for fv, box, lat in fields:
            m = float(fv.get("margin", 0.4)) if margin is None else margin
            if box[0] - m <= x <= box[2] + m and box[1] - m <= y <= box[3] + m:
                return fv, box, lat
        return None

    for t in plan.get("tracks", []):
        net, lay, w = t["net"], B.canon(t.get("layer", "F.Cu")), round(float(t["width"]), 4)
        clss = B.netclasses(net)
        cls = ", ".join(clss)
        if lay in planes:
            bad.append(f"plane: {net} has a track on {lay}, the {planes[lay]['net']} plane "
                       f"({planes[lay]['source']})")
        cl = next((clays[c] for c in clss if c in clays), None)
        if cl and short(net) not in cl.get("except", []) and lay not in cl["layers"]:
            bad.append(f"class-layer: {net} is {cls} class and stays on {', '.join(cl['layers'])}, "
                       f"not {lay} ({cl['source']})")
        segs = list(zip(t["pts"], t["pts"][1:])) or [(t["pts"][0], t["pts"][0])]
        for k, rect in keeps:
            if not set(clss) & set(k.get("forbid_classes", [])) or \
                    (k.get("layers") and lay not in [B.canon(n) for n in k["layers"]]):
                continue
            if any(_seg_hits_rect(tuple(a), tuple(b), rect) for a, b in segs):
                bad.append(f"keepout: {cls} net {net} enters {k['name']!r} on {lay} ({k['source']})")
        inside = next((f for f in (field_at(x, y) for x, y in t["pts"]) if f), None)
        if inside and inside[0].get("widths"):
            # inside a field the field's own range governs: a track that starts in
            # the field and runs out keeps its width to the end of the lane rather
            # than widening at an arbitrary point
            lo, hi = (float(v) for v in inside[0]["widths"])
            if not (lo - 1e-6 <= w <= hi + 1e-6) and (net, w) not in necks:
                bad.append(f"width: {net} track {w} mm is inside {inside[0]['ref']}'s field, which "
                           f"takes {lo}-{hi} mm ({inside[0]['source']}; declare it in \"necks\" "
                           f"with a reason if deliberate)")
        elif "class_width" in G:
            want = B.class_width(net)
            if want and abs(w - want) > 1e-6 and (net, w) not in necks:
                bad.append(f"width: {net} track {w} mm, class {cls} wants {want} mm "
                           f"({G['class_width']['source']}; declare it in \"necks\" with a reason "
                           f"if deliberate)")

    # vias per net for the capped classes: the board's own plus the plan's
    if cvias:
        count = defaultdict(int)
        for t in B.b.GetTracks():
            if t.Type() == B.pn.PCB_VIA_T and t.GetNetname():
                count[t.GetNetname()] += 1
        on_board = dict(count)
        for v in plan.get("vias", []):
            count[v["net"]] += 1
        for net in sorted({v["net"] for v in plan.get("vias", [])}):
            rule = next((cvias[c] for c in B.netclasses(net) if c in cvias), None)
            if not rule:
                continue
            cap = int(rule.get("except", {}).get(short(net), rule.get("max_vias", 0)))
            if count[net] > cap:
                bad.append(f"class-via: {net} ({rule['class']} class) may have "
                           f"{'no via' if cap == 0 else f'at most {cap} via(s)'} on the board; this "
                           f"plan makes {count[net]} ({on_board.get(net, 0)} already placed) "
                           f"({rule['source']})")

    for v in plan.get("vias", []):
        net = v["net"]
        x, y = v["at"]
        pad, drill = round(float(v.get("pad", 0.6)), 4), round(float(v.get("drill", 0.3)), 4)
        f = field_at(x, y, 0.0)
        if f:
            fv, _box, lattice = f
            want = (round(float(fv["pad"]), 4), round(float(fv["drill"]), 4))
            if (pad, drill) != want:
                bad.append(f"field-via: via on {net} at ({x}, {y}) is inside {fv['ref']}'s field and "
                           f"is {pad}/{drill}, must be {want[0]}/{want[1]} ({fv['source']})")
            if fv.get("on_lattice") and lattice and min(math.dist((x, y), q) for q in lattice) > 0.05:
                near = min(lattice, key=lambda q: math.dist((x, y), q))
                bad.append(f"field-via: via on {net} at ({x}, {y}) is not on a diagonal centre of "
                           f"{fv['ref']}'s pad grid (nearest {near[0]:.3f}, {near[1]:.3f}) ({fv['source']})")
        else:
            for fv, _box, _lat in fields:
                if fv.get("outside_refused", True) and \
                        (pad, drill) == (round(float(fv["pad"]), 4), round(float(fv["drill"]), 4)):
                    bad.append(f"field-via: a {pad}/{drill} via on {net} at ({x}, {y}) is OUTSIDE "
                               f"{fv['ref']}'s field, the only place that via is allowed ({fv['source']})")

    for z in plan.get("zones", []):
        q = planes.get(B.canon(z["layer"]))
        if q and short(z["net"]) != short(q["net"]):
            bad.append(f"plane: a zone on {z['layer']} must be {q['net']}, not {z['net']} ({q['source']})")
    return bad



# ---------------------------------------------------------------------------
# copper to board edge
# ---------------------------------------------------------------------------

def edge_min(B):
    """(minimum, where it came from): the guide's "copper to edge", else Board Setup."""
    e = B.gates.get("edge")
    if e:
        return float(e["min"]), e["source"]
    return B.edge_clr, "Board Setup copper-to-edge (the guide has no 'copper to edge' row)"


def _pt_seg(p, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def _seg_seg(a, b, c, d):
    """Distance between two segments that do not cross (copper never crosses the outline)."""
    return min(_pt_seg(a, c, d), _pt_seg(b, c, d), _pt_seg(c, a, b), _pt_seg(d, a, b))


def edge_clearance(edges, a, b=None, half=0.0):
    """Copper-to-edge of a segment a-b (or a point) of half-width `half`."""
    b = b or a
    return min(_seg_seg(a, b, e0, e1) for e0, e1 in edges) - half


def edge_gate(B, plan):
    """New copper closer than the "edge" gate's minimum to the outline, unless the
    plan declares it in "edge_ok".  Not run without an "edge" gate: KiCad's DRC
    already enforces Board Setup's copper-to-edge, and the gate exists for a
    stricter number (the fab's minimum plus its outline tolerance)."""
    if "edge" not in B.gates:
        return []
    EDGE_MIN, src = edge_min(B)
    edges = B.edge_segs()
    ok = {(e["net"], e.get("layer")): float(e["min"]) for e in plan.get("edge_ok", [])}
    bad = []

    def allowed(net, layer):
        return ok.get((net, layer), ok.get((net, None), EDGE_MIN))

    for t in plan.get("tracks", []):
        hw = float(t["width"]) / 2
        pts = t["pts"] if len(t["pts"]) > 1 else t["pts"] * 2
        worst = min(edge_clearance(edges, tuple(p), tuple(q), hw) for p, q in zip(pts, pts[1:]))
        if worst < allowed(t["net"], t.get("layer", "F.Cu")) - 1e-6:
            bad.append(f"edge: {short(t['net'])} on {t.get('layer', 'F.Cu')} comes {worst:.3f} mm from the "
                       f"board edge (>= {EDGE_MIN}: {src})")
    for v in plan.get("vias", []):
        c = edge_clearance(edges, tuple(v["at"]), None, float(v.get("pad", 0.6)) / 2)
        if c < allowed(v["net"], "via") - 1e-6:
            bad.append(f"edge: via on {short(v['net'])} at {tuple(v['at'])} is {c:.3f} mm from the board edge")
    for z in plan.get("zones", []):
        poly = [tuple(p) for p in z["poly"]]
        c = min(edge_clearance(edges, p) for p in poly)
        if c < allowed(z["net"], z["layer"]) - 1e-6:
            bad.append(f"edge: {short(z['net'])} zone on {z['layer']} has its outline {c:.3f} mm from the board edge")
    return bad


# ---------------------------------------------------------------------------
# the angle gate -- tracks turn by 45 degrees only (the guide's "joints" row)
# ---------------------------------------------------------------------------

ANGLE_TOL = 0.5          # degrees


def angle_gate(board, pn, collect=None):
    """Every joint between two track segments is 0 or 45 degrees; a right-angle
    turn is two 45-degree joints with a diagonal between them at least one track
    width long; no arcs.  Entries into a pad or a via are free (the pad edge is
    not a joint); pours are polygons and exempt.  Every layer, every class.

    A JOINT is a point where two or more segments of one net on one layer share
    an end, or where one segment's end lands on another's interior (a tee), and
    which is neither inside a pad of that net nor on one of its vias.  At a
    junction of three or more the AXES must be 0 or 45 degrees apart: a 45-degree
    branch is the compliant tee, and its other pair is then necessarily 135.

    Returns (joints_checked, [violation dict]).  Runs on a pcbnew BOARD, so the
    same code judges a plan on its scratch copy and the copper already laid.
    `collect`, a list, receives the segment uuids of every joint checked."""
    mm = pn.ToMM
    segs, viol = [], []
    for t in board.GetTracks():
        if t.Type() == pn.PCB_VIA_T:
            continue
        if t.Type() == pn.PCB_ARC_T:
            viol.append(dict(kind="arc", net=t.GetNetname(), layer=t.GetLayerName(),
                             at=(mm(t.GetStart().x), mm(t.GetStart().y)), uuids=[t.m_Uuid.AsString()],
                             why="an arc: the angle gate allows none"))
            continue
        a, b = t.GetStart(), t.GetEnd()
        if (a.x, a.y) == (b.x, b.y):
            continue
        segs.append(dict(net=t.GetNetCode(), name=t.GetNetname(), layer=t.GetLayer(),
                         lname=t.GetLayerName(), a=(a.x, a.y), b=(b.x, b.y), w=t.GetWidth(),
                         uuid=t.m_Uuid.AsString()))
    pads, vias = defaultdict(list), defaultdict(list)
    for f in board.GetFootprints():
        for p in f.Pads():
            pads[p.GetNetCode()].append(p)
    for t in board.GetTracks():
        if t.Type() == pn.PCB_VIA_T:
            vias[t.GetNetCode()].append((t.GetPosition().x, t.GetPosition().y,
                                         t.GetBoundingBox().GetWidth() // 2))

    def free(net, layer, pt):
        for vx, vy, r in vias[net]:
            if (vx - pt[0]) ** 2 + (vy - pt[1]) ** 2 <= r * r:
                return True
        v = pn.VECTOR2I(int(pt[0]), int(pt[1]))
        return any(p.IsOnLayer(layer) and p.HitTest(v) for p in pads[net])

    SNAP = 2000                                    # 2 um: two ends this close are one node
    nodes = defaultdict(list)
    for i, s_ in enumerate(segs):
        for end in ("a", "b"):
            x, y = s_[end]
            nodes[(s_["net"], s_["layer"], round(x / SNAP), round(y / SNAP))].append((i, end))

    def out(i, end):                               # unit vector leaving the node along segment i
        s_ = segs[i]
        p, q = (s_["a"], s_["b"]) if end == "a" else (s_["b"], s_["a"])
        d = math.hypot(q[0] - p[0], q[1] - p[1])
        return ((q[0] - p[0]) / d, (q[1] - p[1]) / d)

    def turn(u, v):                                # 0 = straight on, 90 = right angle
        dot = max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))
        return 180.0 - math.degrees(math.acos(dot))

    def near(x, ok):
        return any(abs(x - k) <= ANGLE_TOL for k in ok)

    checked = 0
    turn_at = {}                                   # (seg, end) -> signed 45-degree turn, for the diagonal rule
    for key, ends in nodes.items():
        net, layer = key[0], key[1]
        pt = segs[ends[0][0]][ends[0][1]]
        if len(ends) >= 2:
            if free(net, layer, pt):
                continue
            checked += 1
            if collect is not None:
                collect.append([segs[i]["uuid"] for i, _e in ends])
            ok = (0.0, 45.0) if len(ends) == 2 else (0.0, 45.0, 135.0)
            for m in range(len(ends)):
                for n in range(m + 1, len(ends)):
                    u, v = out(*ends[m]), out(*ends[n])
                    tn = turn(u, v)
                    if not near(tn, ok):
                        viol.append(dict(kind="corner" if len(ends) == 2 else "junction",
                                         net=segs[ends[m][0]]["name"], layer=segs[ends[m][0]]["lname"],
                                         at=(mm(pt[0]), mm(pt[1])), angle=round(tn, 1),
                                         uuids=[segs[ends[m][0]]["uuid"], segs[ends[n][0]]["uuid"]],
                                         why=f"a {tn:.0f}-degree joint"))
                    elif len(ends) == 2 and near(tn, (45.0,)):
                        # each segment remembers which way its NEIGHBOUR leaves the joint
                        turn_at[ends[m]], turn_at[ends[n]] = v, u
        else:                                       # a lone end: does it land on another segment?
            i, end = ends[0]
            for j, o in enumerate(segs):
                if j == i or o["net"] != net or o["layer"] != layer:
                    continue
                ax, ay = o["a"]
                bx, by = o["b"]
                L2 = (bx - ax) ** 2 + (by - ay) ** 2
                tt = ((pt[0] - ax) * (bx - ax) + (pt[1] - ay) * (by - ay)) / L2
                if not 0.0 < tt < 1.0:
                    continue
                d = math.hypot(ax + tt * (bx - ax) - pt[0], ay + tt * (by - ay) - pt[1])
                if d > SNAP or math.hypot(pt[0] - ax, pt[1] - ay) < SNAP or math.hypot(pt[0] - bx, pt[1] - by) < SNAP:
                    continue
                if free(net, layer, pt):
                    break
                checked += 1
                if collect is not None:
                    collect.append([segs[i]["uuid"], o["uuid"]])
                u = out(i, end)
                ov = ((bx - ax) / math.sqrt(L2), (by - ay) / math.sqrt(L2))
                tn = turn(u, ov)
                if not near(tn, (0.0, 45.0, 135.0, 180.0)):
                    viol.append(dict(kind="tee", net=segs[i]["name"], layer=segs[i]["lname"],
                                     at=(mm(pt[0]), mm(pt[1])), angle=round(tn, 1),
                                     uuids=[segs[i]["uuid"], o["uuid"]],
                                     why=f"a tee at {min(tn, 180 - tn):.0f} degrees to the track it joins"))
                break
    # the diagonal of a right-angle turn is at least one track width long
    for i, s_ in enumerate(segs):
        na, nb = turn_at.get((i, "a")), turn_at.get((i, "b"))
        if na is None or nb is None:
            continue
        # Walk a -> b along this segment: the copper arrives at `a` along -na,
        # runs along d, and leaves `b` along nb.  Two turns of the SAME sense make
        # a right angle (and this is its diagonal); opposite senses are an S-jog.
        # Computed in the segment's own direction, so the answer does not depend
        # on the order the segments happen to be stored in.
        d = out(i, "a")
        c1 = (-na[0]) * d[1] - (-na[1]) * d[0]
        c2 = d[0] * nb[1] - d[1] * nb[0]
        if c1 * c2 > 0:
            ln = math.hypot(s_["b"][0] - s_["a"][0], s_["b"][1] - s_["a"][1])
            if ln < s_["w"] - 1000:
                viol.append(dict(kind="short", net=s_["name"], layer=s_["lname"],
                                 at=(mm(s_["a"][0]), mm(s_["a"][1])), angle=90.0, uuids=[s_["uuid"]],
                                 why=f"the diagonal of a right-angle turn is {mm(ln):.3f} mm, "
                                     f"under one track width ({mm(s_['w']):.3f})"))
    return checked, viol


def group_of_uuid(board_dir):
    own = {}
    for m in sorted((Path(board_dir) / "routing").glob("*.applied.json")):
        j = json.loads(m.read_text())
        for u in j.get("tracks", []) + j.get("vias", []) + j.get("zones", []):
            own[u] = j["group"]
    return own


def cmd_angles(a):
    """Audit the copper already on the board against the angle rule, group by group."""
    B = RBoard(a.board)
    seen = []
    checked, viol = angle_gate(B.b, B.pn, collect=seen)
    own = group_of_uuid(a.board)
    njoint = defaultdict(int)
    for us in seen:
        njoint["+".join(sorted({own.get(u, "unowned") for u in us}))] += 1
    per = defaultdict(list)
    for v in viol:
        gs = sorted({own.get(u, "unowned") for u in v["uuids"]})
        per["+".join(gs)].append(v)
    nseg = defaultdict(int)
    for t in B.b.GetTracks():
        if t.Type() != B.pn.PCB_VIA_T:
            nseg[own.get(t.m_Uuid.AsString(), "unowned")] += 1
    print(f"angles: {checked} joints checked on {sum(nseg.values())} segments; {len(viol)} not at 0 or 45 degrees")
    for g in sorted(set(nseg) | set(njoint)):
        print(f"   {g:12s} {nseg.get(g, 0):4d} segments {njoint.get(g, 0):4d} joints "
              f"{len(per.get(g, [])):4d} not at 0 / 45")
    for g, vs in sorted(per.items()):
        kinds = defaultdict(int)
        for v in vs:
            kinds[v["kind"]] += 1
        print(f"   {g}: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))
        if a.nets:                                 # --nets: list them
            for v in vs:
                print(f"        {v['kind']:8s} {short(v['net']):22s} {v['layer']:7s} "
                      f"({v['at'][0]:.3f}, {v['at'][1]:.3f})  {v['why']}")
    if "angles" not in B.gates:
        print("ANGLES not gated: the guide has no 'joints' row, so this is a report, "
              "not a rule (a rule comes from the handoff, never from a habit)")
        return 0
    print("ANGLES", "PASS" if not viol else "FAIL")
    return 0 if not viol else 1

# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_edge(a):
    """Every track, via and zone outline on the board closer than the minimum to Edge.Cuts."""
    B = RBoard(a.board)
    EDGE_MIN, src = edge_min(B)
    edges = B.edge_segs()
    own = group_of_uuid(B.dir)
    rows = []
    for t in B.b.GetTracks():
        g = own.get(t.m_Uuid.AsString(), "unowned")
        if t.Type() == B.pn.PCB_VIA_T:
            p = (B.pn.ToMM(t.GetPosition().x), B.pn.ToMM(t.GetPosition().y))
            c = edge_clearance(edges, p, None, B.pn.ToMM(t.GetWidth(B.pn.F_Cu)) / 2)
            if c < EDGE_MIN - 1e-6:
                rows.append((c, g, short(t.GetNetname()), "via", f"at ({p[0]:.2f}, {p[1]:.2f})"))
        else:
            p = (B.pn.ToMM(t.GetStart().x), B.pn.ToMM(t.GetStart().y))
            q = (B.pn.ToMM(t.GetEnd().x), B.pn.ToMM(t.GetEnd().y))
            c = edge_clearance(edges, p, q, B.pn.ToMM(t.GetWidth()) / 2)
            if c < EDGE_MIN - 1e-6:
                rows.append((c, g, short(t.GetNetname()), t.GetLayerName(),
                             f"({p[0]:.2f}, {p[1]:.2f})-({q[0]:.2f}, {q[1]:.2f}), {math.dist(p, q):.1f} mm"))
    for z in B.b.Zones():
        if z.GetIsRuleArea():
            continue                               # a keep-out is not copper
        ol = z.Outline()
        pts = [(B.pn.ToMM(ol.Outline(k).CPoint(i).x), B.pn.ToMM(ol.Outline(k).CPoint(i).y))
               for k in range(ol.OutlineCount()) for i in range(ol.Outline(k).PointCount())]
        c = min(edge_clearance(edges, p) for p in pts)
        if c < EDGE_MIN - 1e-6:
            rows.append((c, own.get(z.m_Uuid.AsString(), "unowned"), short(z.GetNetname()),
                         z.GetLayerName(), "zone outline"))
    rows.sort()
    print(f"copper to board edge: {len(rows)} item(s) under {EDGE_MIN} mm ({src})")
    for c, g, n, lay, where in rows:
        print(f"   {c:6.3f} mm  {g:8s} {n:12s} {lay:7s} {where}")
    print("EDGE PASS" if not rows else "EDGE FAIL")
    return 0 if not rows else 1


def cmd_status(a):
    B = RBoard(a.board)
    if a.nets:
        for n in B.netnames:
            print(f"  {B.netclass(n):10s} {n}")
        return 0
    rep = drc_report(B.pcb)
    tracks = [t for t in B.b.GetTracks()]
    vias = [t for t in tracks if t.GetClass() == "PCB_VIA"]
    seg = [t for t in tracks if t.GetClass() != "PCB_VIA"]
    print(f"copper: {len(seg)} track segments, {len(vias)} vias, {len(list(B.b.Zones()))} zones, "
          f"{sum(t.GetLength() for t in seg) / 1e6:.1f} mm")
    print(f"open connections: {open_count(rep)}")
    d = {}
    for t in seg:
        d.setdefault(B.netclass(t.GetNetname()) if t.GetNetname() else "?", [0, 0.0])
        e = d[B.netclass(t.GetNetname()) if t.GetNetname() else "?"]
        e[0] += 1
        e[1] += t.GetLength() / 1e6
    for cls, (n, mm) in sorted(d.items()):
        print(f"   {cls:10s} {n:4d} seg {mm:7.1f} mm")
    man = sorted((a.board / "routing").glob("*.applied.json"))
    print(f"groups applied: {len(man)}")
    for m in man:
        j = json.loads(m.read_text())
        print(f"   {j['group']:6s} {j['applied']}  {len(j['tracks'])} seg, {len(j['vias'])} vias, "
              f"{len(j.get('zones', []))} zones   open {j.get('open_before')} -> {j.get('open_after')}")
    return 0


def cmd_check(a, quiet=False):
    B = RBoard(a.board)
    plan = json.loads(Path(a.plan).read_text())
    group = group_of(a.plan, plan)
    for k in plan:
        if k not in ("group", "why", "tracks", "vias", "zones", "necks", "accept", "edge_ok",
                     "rips", "ripvias", "polyrips", "polys", "thermals", "marks", "guides"):
            die(f"plan: unknown key {k!r}")
    bad = rule_gates(B, plan) + edge_gate(B, plan)
    base = drc_report(B.pcb)
    base_keys = dict()
    for k in viol_keys(base):
        base_keys[k] = base_keys.get(k, 0) + 1

    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / B.pcb.name
        shutil.copy2(B.pcb, scratch)
        for extra in ("*.kicad_pro", "*.kicad_sch", "*.kicad_dru", "fp-lib-table", "sym-lib-table"):
            for f in B.dir.glob(extra):
                shutil.copy2(f, Path(td) / f.name)
        if (B.dir / "lib").exists():
            shutil.copytree(B.dir / "lib", Path(td) / "lib", dirs_exist_ok=True)
        S = RBoard(B.dir)
        man = manifest_path(a.board, group)
        if getattr(a, "force", False) and man.exists():
            # judged as `apply --force` will lay it: in place of the group's own copper
            old = json.loads(man.read_text())
            gone = set(old.get("tracks", []) + old.get("vias", []) + old.get("zones", []) + old.get("polys", []))
            for item in list(S.b.GetTracks()) + list(S.b.Zones()) + list(S.b.GetDrawings()):
                if str(item.m_Uuid.AsString()) in gone:
                    S.b.RemoveNative(item)
        made = S.write_plan(plan)
        bad += ["short: " + m for m in S.save(scratch)]
        copper_notes, cbad = copper_gates(B, S)
        bad += cbad
        rep = drc_report(scratch)
        angle_note = "angles: the guide has no 'joints' row, not checked"
        if "angles" in B.gates:
            # whatever this plan adds to the corners already on the board
            src = B.gates["angles"]["source"]
            _c0, a_base = angle_gate(B.b, B.pn)
            a_checked, a_now = angle_gate(S.b, S.pn)
            old_pts = defaultdict(int)
            for v in a_base:
                old_pts[(v["kind"], v["net"], v["layer"], v["at"])] += 1
            for v in a_now:
                k = (v["kind"], v["net"], v["layer"], v["at"])
                if old_pts.get(k):
                    old_pts[k] -= 1
                else:
                    bad.append(f"angle: {short(v['net'])} on {v['layer']} at ({v['at'][0]:.3f}, "
                               f"{v['at'][1]:.3f}): {v['why']} (0 or 45 only: {src})")
            angle_note = (f"angles: {a_checked} joints on the board with this plan, "
                          f"{len(a_now)} not at 0 or 45 degrees ({len(a_base)} before it)")

    new = []
    seen = dict(base_keys)
    for k in viol_keys(rep):
        if seen.get(k):
            seen[k] -= 1
        else:
            new.append(k)
    nseg = len(made["tracks"])
    rm = made["removed"]
    if not quiet:
        print(f"{group}: {nseg} segments, {len(made['vias'])} vias, {len(made['zones'])} zones, "
              f"{len(made['polys'])} polygons" + (f"; removes {rm['rips']} segments, {rm['ripvias']} vias, "
                                                  f"{rm['polyrips']} polygons" if any(rm.values()) else "")
              + ("; thermals re-typed" if plan.get("thermals") else ""))
        print(f"open connections {open_count(base)} -> {open_count(rep)} "
              f"({open_count(base) - open_count(rep)} closed)")
        for n in plan.get("necks", []):
            print(f"   neck {n['net']} at {n['width']} mm: {n['why']}")
        for e in plan.get("edge_ok", []):
            print(f"   edge_ok {e['net']} to {e['min']} mm: {e.get('why') or 'NO REASON GIVEN'}")
        print(f"   {angle_note}")
        for n_ in copper_notes:
            print(f"   {n_}")
    # A plan may ACCEPT a violation type, with a written reason -- a fanout
    # group ends every escape in free board on purpose, and "track has an
    # unconnected end" is what that looks like to DRC.  It is never a way to wave away a real
    # error: the reason is printed every run and reviewed with the group.
    accepted = {a["type"]: a["why"] for a in plan.get("accept", [])}
    taken = defaultdict(int)
    real = []
    for k in new:
        if k[0] in accepted:
            taken[k[0]] += 1
        else:
            real.append(k)
    for k in real:
        print(f"  DRC {k[0]}: {k[1][:96]} {list(k[2])[:2]}")
    for t, n in sorted(taken.items()):
        print(f"  ACCEPTED {n} x {t}: {accepted[t]}")
    for t in accepted:
        if not taken.get(t):
            print(f"  ACCEPTED {t}: declared but none occurred -- delete it from the plan")
    for b in bad:
        print(f"  RULE {b}")
    new = real
    ok = not new and not bad
    if not quiet:
        print("CHECK PASS" if ok else f"CHECK FAIL ({len(new)} new DRC, {len(bad)} rule)")
    return 0 if ok else 1


def cmd_apply(a):
    B = RBoard(a.board)
    if P.locks(B.pcb):
        die("KiCad has the board open; close it first", 3)
    if cmd_check(a, quiet=False) != 0:
        die("plan does not pass check; nothing applied", 1)
    plan = json.loads(Path(a.plan).read_text())
    group = group_of(a.plan, plan)
    man = manifest_path(a.board, group)
    if man.exists() and not a.force:
        die(f"{group} is already applied ({man}); rip it first, or --force", 3)
    before = open_count(drc_report(B.pcb))
    backup = B.pcb.with_suffix(f".kicad_pcb.{group}-bak")
    if man.exists():
        # --force REPLACES the group: laying the plan on top of its own copper
        # doubles every segment, and DRC does not flag same-net duplicates.
        old = json.loads(man.read_text())
        gone = set(old.get("tracks", []) + old.get("vias", []) + old.get("zones", []) + old.get("polys", []))
        backup = B.pcb.with_suffix(f".kicad_pcb.{group}-force-bak")
        n = 0
        for item in list(B.b.GetTracks()) + list(B.b.Zones()) + list(B.b.GetDrawings()):
            if str(item.m_Uuid.AsString()) in gone:
                B.b.RemoveNative(item)
                n += 1
        print(f"--force: removed the {n} items of the previous {group} first")
    shutil.copy2(B.pcb, backup)
    made = B.write_plan(plan)
    removed = made.pop("removed")
    uuids = {k: [str(i.m_Uuid.AsString()) for i in v] for k, v in made.items()}
    moved = B.save()
    if moved:
        shutil.copy2(backup, B.pcb)
        die("copper changed net when saved; board restored:\n  " + "\n  ".join(moved), 4)
    # read back: every uuid must be on the saved board
    C = RBoard(a.board)
    have = {str(i.m_Uuid.AsString()) for i in list(C.b.GetTracks()) + list(C.b.Zones()) + list(C.b.GetDrawings())}
    missing = [u for v in uuids.values() for u in v if u not in have]
    if missing:
        shutil.copy2(backup, B.pcb)
        die(f"read-back failed: {len(missing)} items are not on the saved board; restored", 4)
    man.parent.mkdir(exist_ok=True)
    rec = {"group": group, "why": plan.get("why", ""),
           "applied": time.strftime("%Y-%m-%d %H:%M"),
           "plan": str(Path(a.plan).resolve().relative_to(ROOT)
                       if str(Path(a.plan).resolve()).startswith(str(ROOT)) else Path(a.plan).name),
           "open_before": before, "open_after": None, "removed": removed, **uuids}
    # The manifest FIRST: copper without one cannot be ripped, and the DRC
    # below can fail for reasons that have nothing to do with the copper.
    man.write_text(json.dumps(rec, indent=1) + "\n")
    after_rep = drc_report(B.pcb)
    rec["open_after"] = open_count(after_rep)
    man.write_text(json.dumps(rec, indent=1) + "\n")
    print(f"applied {group}: {len(uuids['tracks'])} segments, {len(uuids['vias'])} vias, "
          f"{len(uuids['zones'])} zones, {len(uuids['polys'])} polygons (read back and matched)"
          + (f"; removed {removed['rips']} segments, {removed['ripvias']} vias, {removed['polyrips']} polygons"
             if any(removed.values()) else ""))
    print(f"open connections {before} -> {open_count(after_rep)}")
    print(f"manifest {man.relative_to(a.board)}; backup {backup.name}")
    return 0


def cmd_rip(a):
    B = RBoard(a.board)
    if P.locks(B.pcb):
        die("KiCad has the board open; close it first", 3)
    man = manifest_path(a.board, a.plan)
    if not man.exists():
        die(f"no manifest for {a.plan} ({man})")
    j = json.loads(man.read_text())
    want = set(j["tracks"] + j["vias"] + j.get("zones", []) + j.get("polys", []))
    rm = j.get("removed") or {}
    if any(rm.values()):
        print(f"note: {a.plan} also REMOVED {rm.get('rips', 0)} segments, {rm.get('ripvias', 0)} vias and "
              f"{rm.get('polyrips', 0)} polygons when it was applied; rip takes back only what it added -- "
              f"the removed copper is in the backup {B.pcb.name}.{a.plan}-bak")
    shutil.copy2(B.pcb, B.pcb.with_suffix(f".kicad_pcb.{a.plan}-rip-bak"))
    n = 0
    for item in list(B.b.GetTracks()) + list(B.b.Zones()) + list(B.b.GetDrawings()):
        if str(item.m_Uuid.AsString()) in want:
            B.b.RemoveNative(item)
            n += 1
    if any(not z.GetIsRuleArea() for z in B.b.Zones()):
        B.fill_zones()                             # the pours close over what was removed
    moved = B.save()
    if moved:
        print("note: copper changed net when saved (it was already wrong before this rip):\n  "
              + "\n  ".join(moved))
    man.rename(man.with_suffix(".json.ripped"))
    print(f"ripped {n} of {len(want)} items from {a.plan}; manifest renamed to .ripped")
    return 0


def cmd_render(a):
    B = RBoard(a.board)
    out = Path(a.out or (Path(a.board) / "routing" / "renders"))
    out.mkdir(parents=True, exist_ok=True)
    o = B.outline
    views = [("top", "F.Cu,F.SilkS,Edge.Cuts", o),
             ("bottom", "B.Cu,B.SilkS,Edge.Cuts", o)]
    inner = [n for n in B.copper_layers() if n not in ("F.Cu", "B.Cu")]
    if inner:
        views.append(("inner", ",".join(inner + ["Edge.Cuts"]), o))
    made = []
    for name, layers, box in views:
        pdf = out / f"{name}.pdf"
        r = subprocess.run(["kicad-cli", "pcb", "export", "pdf", "--layers", layers,
                            "--mode-single", "-o", str(pdf), str(B.pcb)],
                           capture_output=True, text=True)
        if not pdf.exists():
            print(f"  render {name} failed: {r.stderr.strip()[-160:]}")
            continue
        png = out / f"{name}.png"
        _raster(pdf, png, box, a.dpi_scale)
        made.append(png)
    for m in made:
        print(f"  {m}")
    if not [m for m in made if m.exists()]:
        print("render: no PNG was written (see above)")
        return 1
    return 0


_RASTER = r"""
import sys
try:
    import pymupdf as fitz
except ImportError:
    import fitz
pdf, png, scale = sys.argv[1], sys.argv[2], float(sys.argv[3])
p = fitz.open(pdf)[0]
ink = [v[1] for v in p.get_bboxlog()]
if ink:
    x0 = min(r[0] for r in ink) - 4; y0 = min(r[1] for r in ink) - 4
    x1 = max(r[2] for r in ink) + 4; y1 = max(r[3] for r in ink) + 4
    p.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=fitz.Rect(x0, y0, x1, y1)).save(png)
"""


VENV_PY = ROOT / "tools" / ".venv" / "bin" / "python"


def _raster(pdf, png, box, scale):
    """PyMuPDF crops the PDF to its ink.  pcbnew pins this script to KiCad's own
    interpreter, and PyMuPDF lives in kicad_project/tools/.venv (tools/setup.sh),
    so the crop runs out of process."""
    try:
        try:
            import pymupdf as fitz                     # noqa: F401  (if it is here, use it)
        except ImportError:
            import fitz                                # noqa: F401  (older PyMuPDF)
    except ImportError:
        for exe in (str(VENV_PY), "python3"):
            if exe != "python3" and not Path(exe).exists():
                continue
            r = subprocess.run([exe, "-c", _RASTER, str(pdf), str(png), str(scale)],
                               capture_output=True, text=True)
            if r.returncode == 0 and Path(png).exists():
                return
        print(f"  raster {png.name}: no interpreter with PyMuPDF -- run tools/setup.sh "
              f"(the PDF is beside it)")
        return
    p = fitz.open(pdf)[0]
    ink = [v[1] for v in p.get_bboxlog()]
    if not ink:
        return
    x0 = min(r[0] for r in ink) - 4
    y0 = min(r[1] for r in ink) - 4
    x1 = max(r[2] for r in ink) + 4
    y1 = max(r[3] for r in ink) + 4
    p.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=fitz.Rect(x0, y0, x1, y1)).save(png)


def copper_gates(B, S):
    """The guide's copper rules on a plan: (notes, failures).  B is the board
    as it is, S the scratch copy with the plan laid.  A plan may not add a
    copper-shape site, and may not add a live via-link site or raise the count."""
    notes, bad = [], []
    G = B.gates
    if not (G.get("shape") or G.get("via_link")):
        return notes, bad
    import common as CC
    import shape as SHAPE
    import vialink as VL
    CC.settings(B.dir, B.dir)
    CC.G = G
    fields = guide.field_boxes(B.b, G, B.pn)
    if G.get("shape"):
        s0 = SHAPE.audit(B.b, B.pn, FIELDS=fields)
        s1 = SHAPE.audit(S.b, S.pn, FIELDS=fields)
        near = lambda x, L: any(t["kind"] == x["kind"] and t["net"] == x["net"] and t["layer"] == x["layer"]
                                and abs(t["at"][0] - x["at"][0]) <= 0.3 and abs(t["at"][1] - x["at"][1]) <= 0.3 for t in L)
        new = [x for x in s1 if not near(x, s0)]
        gone = [x for x in s0 if not near(x, s1)]
        notes.append(f"shape: {len(s0)} sites before, {len(s1)} with this plan ({len(new)} new, {len(gone)} gone) "
                     f"({G['shape']['source']})")
        bad += [f"shape: new {x['kind']} on {x['net']} {x['layer']} at {x['at']} size {x['size']}" for x in new]
    if G.get("via_link"):
        _, l0, _ = VL.run(B.b, G, B.dir)
        _, l1, _ = VL.run(S.b, G, B.dir)
        key = lambda x: (x["kind"], x["net"], round(x["via"][0], 2), round(x["via"][1], 2))
        k0 = {key(x) for x in l0}
        new = [x for x in l1 if key(x) not in k0]
        notes.append(f"vialink: {len(l0)} live sites before, {len(l1)} with this plan ({len(new)} new) "
                     f"({G['via_link']['source']})")
        bad += [f"vialink: new {x['kind']} on {x['net']} via {x['via']} pad {x['pad']}" for x in new]
    return notes, bad


def cmd_rules(a):
    """What the guide's table switches on, each with its source."""
    B = RBoard(a.board)
    p = Path(a.board) / guide.FILE
    if not p.exists():
        print(f"no {guide.FILE} in {Path(a.board).name}/: `check` runs KiCad's DRC only")
        return 0
    print("\n".join(guide.describe(B.gates)))
    lo, src = edge_min(B)
    print(f"  copper-to-edge used by `edge`: {lo} mm ({src})")
    return 0


def cmd_audit(a):
    """Every rule of the guide on the board as it is: one line per gate."""
    B = RBoard(a.board)
    G = B.gates
    rows = []

    def gate(name, ok, detail):
        rows.append((name, ok, detail))
        print(f"  {name:13s} {'PASS' if ok else 'FAIL'}  {detail}")

    d = subprocess.run([sys.executable, str(ROOT / "tools" / "place" / "place.py"), "drc", "--board", str(a.board)],
                       capture_output=True, text=True)
    lines = [ln for ln in d.stdout.splitlines() if ln.strip()]
    viol = next((ln for ln in lines if ln.startswith("violations:")), "violations: ?")
    unc = next((ln for ln in lines if ln.startswith("unconnected:")), "unconnected: ?")
    par = next((ln for ln in lines if ln.startswith("schematic parity:")), "")
    gate("drc", d.returncode == 0 and viol.strip() == "violations: none" and unc.strip() == "unconnected: 0",
         f"{viol}; {unc}; {par}" if d.returncode != 4 else (d.stderr.strip().splitlines() or ["drc did not run"])[-1])
    checked, viol_a = angle_gate(B.b, B.pn)
    if "angles" in G:
        gate("angles", not viol_a, f"{checked} joints, {len(viol_a)} not at 0/45 ({G['angles']['source']})")
    else:
        print(f"  {'angles':13s} ----  not in the guide ({len(viol_a)} joints not at 0/45, for information)")
    lo, src = edge_min(B)
    edges = B.edge_segs()
    n_edge = 0
    for t in B.b.GetTracks():
        if t.Type() == B.pn.PCB_VIA_T:
            c = edge_clearance(edges, (B.pn.ToMM(t.GetPosition().x), B.pn.ToMM(t.GetPosition().y)), None, B.pn.ToMM(t.GetWidth(B.pn.F_Cu)) / 2)
        else:
            c = edge_clearance(edges, (B.pn.ToMM(t.GetStart().x), B.pn.ToMM(t.GetStart().y)),
                               (B.pn.ToMM(t.GetEnd().x), B.pn.ToMM(t.GetEnd().y)), B.pn.ToMM(t.GetWidth()) / 2)
        if c < lo - 1e-6:
            n_edge += 1
    gate("edge", n_edge == 0, f"{n_edge} item(s) under {lo} mm ({src})")
    if G.get("shape") or G.get("via_link") or G.get("ground_beside"):
        import common as CC
        CC.settings(B.dir, B.dir); CC.G = G
        fields = guide.field_boxes(B.b, G, B.pn)
    if G.get("shape"):
        import shape as SHAPE
        sites = SHAPE.audit(B.b, B.pn, FIELDS=fields)
        out = Path(a.board) / "routing" / "shape_sites.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(sites, indent=1))
        kinds = defaultdict(int)
        for x in sites:
            kinds[x["kind"]] += 1
        gate("shape", not sites, f"{len(sites)} site(s)" + (": " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) if sites else "")
             + f" -> {out.relative_to(a.board)} ({G['shape']['source']})")
    if G.get("via_link"):
        import vialink as VL
        sites, lv, st = VL.run(B.b, G, B.dir)
        out = Path(a.board) / "routing" / "vialink_sites.json"
        out.write_text(json.dumps(sites, indent=1))
        kinds = defaultdict(int)
        for x in lv:
            kinds[x["kind"]] += 1
        ndecl = sum(1 for x in sites if "declared" in x)
        gate("vialink", not lv, f"{len(lv)} live site(s)" + (": " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) if lv else "")
             + f", {ndecl} declared; {st['nvia']} vias judged -> {out.relative_to(a.board)} ({G['via_link']['source']})")
    if G.get("ground_beside"):
        import fastside as FS
        rule = G["ground_beside"]
        fast, fill = FS.load(B.pcb, B.pn, G)
        t = FS.tally(fast, fill, rule["within"], B.pn)
        bare = sum(v[2] for v in t.values()); one = sum(v[1] for v in t.values()); two = sum(v[0] for v in t.values())
        gate("ground beside", bare < 0.05, f"{rule['class']} mm with {rule['net']} on 2/1/0 sides: {two:.1f} / {one:.1f} / {bare:.1f} "
             f"({rule['source']})")
    pours = [z for z in B.b.Zones() if not z.GetIsRuleArea() and z.GetNetCode()]
    if pours:
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "route" / "pour.py"), "--board", str(a.board)],
                           capture_output=True, text=True)
        isl = [ln.strip() for ln in r.stdout.splitlines() if "island" in ln]
        many = [ln for ln in isl if not re.search(r"\b1 island", ln)]
        gate("pours", not many, f"{len(isl)} pour(s)" + (f"; {len(many)} in more than one island -- every island must be tied (pour.py)" if many else ", each one island"))
    bad = [n for n, ok, _ in rows if not ok]
    print("AUDIT " + ("PASS" if not bad else f"FAIL ({', '.join(bad)})"))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "check", "apply", "rip", "render", "angles", "edge", "audit", "rules"])
    ap.add_argument("plan", nargs="?", help="check/apply: plan JSON; rip/render: group name")
    ap.add_argument("--board", help="boardN, the folder name or a path (default: the folder you "
                    "are in, else this session's board)")
    ap.add_argument("--nets", action="store_true", help="status: list every net with its class")
    ap.add_argument("--force", action="store_true", help="apply: re-apply a group that has a manifest")
    ap.add_argument("--out", help="render: output directory (default <board>/routing/renders)")
    ap.add_argument("--dpi-scale", type=float, default=6.0, help="render: PyMuPDF matrix scale")
    a = ap.parse_args()
    a.board = P.board_dir_arg(a.board)
    if a.command in ("check", "apply") and not a.plan:
        die(f"{a.command} needs a plan file")
    if a.command in ("check", "apply"):
        a.plan = str(boardsel.in_board(a.board, a.plan))     # as typed, else inside the board folder
        if not Path(a.plan).exists():
            die(f"no such plan: {a.plan}")
    if a.command == "rip" and not a.plan:
        die("rip needs a group name")
    sys.exit({"status": cmd_status, "check": cmd_check, "apply": cmd_apply,
              "rip": cmd_rip, "render": cmd_render, "angles": cmd_angles,
              "edge": cmd_edge, "audit": cmd_audit, "rules": cmd_rules}[a.command](a))


if __name__ == "__main__":
    main()
