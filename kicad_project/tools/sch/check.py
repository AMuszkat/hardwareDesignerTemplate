"""
The schematic drawing standard as a gate. Every rule reads the SAVED files
(and kicad-cli's own SVG for text extents); nothing is written. The rules and
their numbers are the `schematic` skill's; tools/sch/README.md says why each
one exists.

A finding is (rule, sheet, key, message). `key` is what a waiver in
<board>/schematic.json names: {"waive": [{"rule": ..., "key": ..., "why": ...}]}.
"""
import itertools
import math
import re
import tempfile
from collections import defaultdict, Counter

import model
from model import ISO, FRAME, TITLE_BLOCK, GROUND_RE

WIRE_NEAR = 12.7       # two pieces of one net closer than this are drawn as one wire
HOME_NEAR = 101.6      # a small part sits within this of the IC it serves (its body and pins)
HOME_CHAIN = 25.4      # with sections: a home's sections stand together, each within this of another
PINS_PER_SIDE = 40     # pin rows on one side of one unit
MARGIN = 5.0           # content keeps this clear of the frame
SMALL_PINS = 4         # a part with at most this many pins is a "small part"
FIELD_NEAR = 12.7      # a visible Reference / Value this close to its own body
TITLE_SIZE = 2.0       # a section title's text size (bold)
TITLE_BAND = 8.0       # a rectangle's title is a text this far inside its top-left corner

RULES = {
    "paper": "the paper is the smallest ISO size the content fits, and never above A2",
    "frame": "nothing outside the frame or under the title block",
    "title": "the title block names the board, its revision and the date",
    "text": "no text overlaps another text",
    "text-wire": "no wire runs through a text",
    "text-graphic": "no text runs into a symbol's drawn lines (a body outline, a power glyph)",
    "body": "no wire crosses a symbol body, no two bodies overlap",
    "rail-label": "a power net is drawn with power symbols, never a label",
    "global": "below the root, a signal leaves its sheet through a hierarchical label",
    "sheet-pin": "every sheet pin on the root is connected",
    "stitch": f"pieces of one net closer than {WIRE_NEAR} mm are one wire, not two labels",
    "home": f"a small part sits within {HOME_NEAR} mm of the IC it serves; with sections, its section stands "
            f"with the IC's (a cluster of sections no more than {HOME_CHAIN} mm apart)",
    "pins-side": f"at most {PINS_PER_SIDE} pin rows on one side of one unit",
    "field-far": f"a symbol's Reference and Value sit within {FIELD_NEAR} mm of its body",
    "section": "below the root, everything sits inside one titled section frame; frames never overlap",
    "section-title": "every section frame has a title in its top-left corner, and no two on a sheet share one",
    "section-wire": "no wire crosses a section frame: sections connect through labels and power symbols",
}


def key(x, y):
    return (round(x * 100), round(y * 100))


def seg_hits_box(a, b, box):
    """A horizontal / vertical / general segment intersecting the open box."""
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return False
    (ax, ay), (bx, by) = a, b
    # Liang-Barsky clip
    dx, dy = bx - ax, by - ay
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if p == 0:
            if q <= 0:
                return False
        else:
            t = q / p
            if p < 0:
                if t > t1:
                    return False
                t0 = max(t0, t)
            else:
                if t < t0:
                    return False
                t1 = min(t1, t)
    return t1 - t0 > 1e-9


def shrink(b, d):
    return (b[0] + d, b[1] + d, b[2] - d, b[3] - d)


def overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def pairs_overlapping(boxes):
    """Indices (i, j) of overlapping boxes, by a sweep over x."""
    order = sorted(range(len(boxes)), key=lambda i: boxes[i][0])
    active, out = [], []
    for i in order:
        b = boxes[i]
        active = [j for j in active if boxes[j][2] > b[0]]
        for j in active:
            if overlap(b, boxes[j]):
                out.append((j, i))
        active.append(i)
    return out


def on_segment(p, a, b, tol=0.005):
    (px, py), (ax, ay), (bx, by) = p, a, b
    if min(ax, bx) - tol <= px <= max(ax, bx) + tol and min(ay, by) - tol <= py <= max(ay, by) + tol:
        return abs((bx - ax) * (py - ay) - (by - ay) * (px - ax)) <= tol * max(1.0, math.hypot(bx - ax, by - ay))
    return False


class UF:
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def pieces(sh):
    """Geometric connectivity of one sheet, labels NOT joined by name:
    {point key: piece id}. Points: wire ends, pin ends, label anchors, power
    pins, sheet pins; a point on a wire's span joins that wire."""
    uf = UF()
    pts = []
    for a, b, _ in sh.wires:
        uf.union(key(*a), key(*b))
        pts += [a, b]
    for s in sh.symbols:
        for _, e, _ in s.pins():
            pts.append(e)
            uf.find(key(*e))
    for l in sh.labels:
        pts.append((l.x, l.y))
        uf.find(key(l.x, l.y))
    for ss in sh.sheets:
        for p in ss.pins:
            pts.append((p["x"], p["y"]))
            uf.find(key(p["x"], p["y"]))
    # points lying on a wire's span
    for p in pts:
        for a, b, _ in sh.wires:
            if on_segment(p, a, b):
                uf.union(key(*p), key(*a))
    return uf


def content_box(sh, texts):
    xs, ys = [], []

    def add(b):
        xs.extend((b[0], b[2]))
        ys.extend((b[1], b[3]))
    for s in sh.symbols:
        b = s.body()
        if b:
            add(b)
        for _, e, _ in s.pins():
            add((e[0], e[1], e[0], e[1]))
    for a, b, _ in sh.wires:
        add((min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])))
    for ss in sh.sheets:
        add((ss.x, ss.y, ss.x + ss.w, ss.y + ss.h))
    for _, b in texts:
        add(b)
    for _, b, _, _ in sh.sections():
        add(b)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def fits(w, h, paper):
    pw, ph = model.paper_size(paper)
    aw, ah = pw - 2 * FRAME - 2 * MARGIN, ph - 2 * FRAME - 2 * MARGIN
    if w > aw or h > ah:
        return False
    tw, th = TITLE_BLOCK
    return not (w > aw - tw and h > ah - th)


def required_paper(w, h):
    for p in ISO:
        if fits(w, h, p):
            return p
    return None


def drawing_texts(sh, texts):
    """kicad-cli's texts minus the drawing sheet's own (frame, title block)."""
    pw, ph = sh.size()
    tw, th = TITLE_BLOCK
    tb = (pw - FRAME - tw, ph - FRAME - th, pw - FRAME, ph - FRAME)
    out = []
    for t, b in texts:
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if cx < FRAME or cy < FRAME or cx > pw - FRAME or cy > ph - FRAME:
            continue
        if tb[0] <= cx <= tb[2] and tb[1] <= cy <= tb[3]:
            continue
        out.append((t, b))
    return out


def run(design, waivers=(), with_svg=True):
    findings = []

    def find(rule, sh, k, msg):
        findings.append((rule, sh.name, k, msg))

    rails = design.rails() - {"PWR_FLAG"}
    nets, comps = model.netlist(design.root)
    net_of = {}
    for n, nodes in nets.items():
        for r, p in nodes:
            net_of[(r, p)] = n

    def base(n):
        return n.rsplit("/", 1)[-1] if n else n

    svg = {}
    if with_svg:
        with tempfile.TemporaryDirectory() as d:
            svg = model.svg_texts(design.root, d)

    for sh in design.sheets:
        texts = drawing_texts(sh, svg.get(sh.name, []))
        pw, ph = sh.size()

        # paper, frame
        cb = content_box(sh, texts)
        if cb:
            w, h = cb[2] - cb[0], cb[3] - cb[1]
            need = required_paper(w, h)
            if need is None or ISO.index(need) > ISO.index("A2"):
                find("paper", sh, "size", f"content {w:.0f} x {h:.0f} mm needs more than A2: split the sheet")
            elif sh.paper in ISO and ISO.index(sh.paper) > ISO.index(need):
                find("paper", sh, "size", f"{sh.paper} holds {w:.0f} x {h:.0f} mm of content, which fits {need}")
            elif sh.paper not in ISO:
                find("paper", sh, "size", f"paper {sh.paper}: use an ISO size ({need})")
            tw, th = TITLE_BLOCK
            tb = (pw - FRAME - tw, ph - FRAME - th, pw - FRAME, ph - FRAME)
            if cb[0] < FRAME or cb[1] < FRAME or cb[2] > pw - FRAME or cb[3] > ph - FRAME:
                find("frame", sh, "outside", f"content {tuple(round(v) for v in cb)} runs outside the frame")
            hit = [s.ref for s in sh.parts if s.body() and overlap(s.body(), tb)]
            if hit:
                find("frame", sh, "title-block", f"under the title block: {', '.join(hit[:6])}")

        # title block
        miss = [f for f in ("title", "rev", "date") if not sh.title_block.get(f, "").strip()]
        if miss:
            find("title", sh, "fields", f"title block has no {', '.join(miss)}")

        # text on text
        boxes = [shrink(b, 0.05) for _, b in texts]
        for i, j in pairs_overlapping(boxes):
            a, b = texts[i], texts[j]
            if a[0] == b[0] and a[1] == b[1]:
                continue
            find("text", sh, f"{a[0]}|{b[0]}",
                 f"'{a[0]}' overlaps '{b[0]}' at ({a[1][0]:.1f}, {a[1][1]:.1f})")

        # wire through text, wire through body, body on body
        tboxes = [(t, shrink(b, 0.2)) for t, b in texts]
        for a, b, _ in sh.wires:
            for t, bx in tboxes:
                if seg_hits_box(a, b, bx):
                    find("text-wire", sh, f"{t}@{bx[0]:.1f},{bx[1]:.1f}",
                         f"a wire runs through '{t}' at ({bx[0]:.1f}, {bx[1]:.1f})")
        # text into a symbol's own lines: a ground glyph's triangle in the lane of the label beside
        # it, a pin number wider than its pin crossing the body outline -- text-on-text cannot see
        # either, and neither can a box test (a pin name sits inside its body, clear of the lines)
        cell, grid = 10.0, defaultdict(list)
        for t, b in texts:
            bx = shrink(b, 0.1)
            for gx in range(int(bx[0] // cell), int(bx[2] // cell) + 1):
                for gy in range(int(bx[1] // cell), int(bx[3] // cell) + 1):
                    grid[(gx, gy)].append((t, bx))
        hits = set()
        for s in sh.symbols:
            for a, b in s.strokes():
                near = set()
                for gx in range(int(min(a[0], b[0]) // cell), int(max(a[0], b[0]) // cell) + 1):
                    for gy in range(int(min(a[1], b[1]) // cell), int(max(a[1], b[1]) // cell) + 1):
                        near.update(grid.get((gx, gy), ()))
                for t, bx in near:
                    if seg_hits_box(a, b, bx):
                        who = s.value if s.power else s.ref
                        k = f"{t}|{who}@{bx[0]:.1f},{bx[1]:.1f}"
                        if k not in hits:
                            hits.add(k)
                            find("text-graphic", sh, k, f"'{t}' runs into {who}'s "
                                 f"{'glyph' if s.power else 'body'} at ({bx[0]:.1f}, {bx[1]:.1f})")
        bodies = [(s, shrink(s.body(), 0.1), {key(*e) for _, e, _ in s.pins()})
                  for s in sh.symbols if s.body() and not s.power]
        for a, b, _ in sh.wires:
            for s, bx, mine in bodies:
                if key(*a) in mine or key(*b) in mine:
                    continue                      # a wire leaving the part's own pin
                if seg_hits_box(a, b, bx):
                    find("body", sh, f"wire:{s.ref}", f"a wire crosses {s.ref}'s body")
        for i, j in pairs_overlapping([bx for _, bx, _ in bodies]):
            find("body", sh, f"{bodies[i][0].ref}|{bodies[j][0].ref}",
                 f"{bodies[i][0].ref} and {bodies[j][0].ref} overlap")

        # rails by label; global labels below the root
        for l in sh.labels:
            if l.text in rails:
                find("rail-label", sh, l.text, f"power net {l.text} drawn as a {l.kind.replace('_', ' ')}")
            elif l.kind == "global_label" and design.hierarchical and sh.hpath != "/":
                find("global", sh, l.text, f"global label {l.text}: use a hierarchical label and a sheet pin")

        # pieces: stitched by labels, sheet pins unconnected
        uf = pieces(sh)
        if sh.hpath == "/":
            used = defaultdict(int)
            for a, b, _ in sh.wires:
                used[uf.find(key(*a))] += 1
            for l in sh.labels:
                used[uf.find(key(l.x, l.y))] += 1
            for ss in sh.sheets:
                for p in ss.pins:
                    if not used.get(uf.find(key(p["x"], p["y"]))):
                        find("sheet-pin", sh, f"{ss.name}:{p['name']}",
                             f"sheet pin {p['name']} on '{ss.name}' goes nowhere")
        sec_frames = [b for _, b, _, _ in sh.sections()]

        def sec_of(x, y):
            return next((i for i, f in enumerate(sec_frames) if f[0] <= x <= f[2] and f[1] <= y <= f[3]), None)
        by_text = defaultdict(lambda: defaultdict(list))
        piece_sec = {}
        for l in sh.labels:
            if l.kind == "label" and l.text not in rails:
                by_text[l.text][uf.find(key(l.x, l.y))].append((l.x, l.y))
                piece_sec.setdefault(uf.find(key(l.x, l.y)), sec_of(l.x, l.y))
        pin_pts = defaultdict(list)
        piece_big = defaultdict(set)                   # piece -> big parts with a pin on it
        for s in sh.symbols:
            for _, e, _ in s.pins():
                pin_pts[uf.find(key(*e))].append(e)
                if len(s.pins()) > SMALL_PINS:
                    piece_big[uf.find(key(*e))].add(s.ref)
        for text, groups in by_text.items():
            if len(groups) < 2:
                continue
            best = None
            for (ga, pa), (gb, pb) in itertools.combinations(groups.items(), 2):
                if sec_frames and piece_sec.get(ga) != piece_sec.get(gb):
                    continue                          # two sections: a label is how they connect
                if piece_big[ga] & piece_big[gb]:
                    continue                          # two pins of one IC: a label each, not a wire round it
                for p in pa + pin_pts.get(ga, []):
                    for q in pb + pin_pts.get(gb, []):
                        d = math.hypot(p[0] - q[0], p[1] - q[1])
                        if best is None or d < best[0]:
                            best = (d, p)
            if best and best[0] < WIRE_NEAR:
                find("stitch", sh, text,
                     f"{text}: two labelled pieces {best[0]:.1f} mm apart near ({best[1][0]:.1f}, "
                     f"{best[1][1]:.1f}) -- draw the wire")

        # small parts near the IC they serve -- their sections side by side
        # where the sheet has sections, the parts themselves where it has none
        frames_here = [b for _, b, _, _ in sh.sections()]

        def frame_of(s):
            return next((f for f in frames_here if f[0] <= s.x <= f[2] and f[1] <= s.y <= f[3]), None)
        anchors = [s for s in sh.parts if len(s.pins()) > SMALL_PINS]
        apins = defaultdict(list)                       # net -> [(ref, box of that unit)]
        for s in anchors:
            ends = [e for _, e, _ in s.pins()]
            ab = s.body() or (ends[0][0], ends[0][1], ends[0][0], ends[0][1])
            ab = (min([ab[0]] + [e[0] for e in ends]), min([ab[1]] + [e[1] for e in ends]),
                  max([ab[2]] + [e[0] for e in ends]), max([ab[3]] + [e[1] for e in ends]))
            ab = frame_of(s) or ab
            for p, e, _ in s.pins():
                n = net_of.get((s.ref, p.number))
                if n:
                    apins[base(n)].append((s.ref, ab))
        def gap(a, b):
            return math.hypot(max(b[0] - a[2], 0, a[0] - b[2]), max(b[1] - a[3], 0, a[1] - b[3]))
        home_secs = defaultdict(set)                    # anchor ref -> frames of the small parts on its nets
        home_frame = {}
        part_homes = {}                                  # small part -> (its frame, {anchor: net})
        for s in sh.parts:
            ps = s.pins()
            if len(ps) > SMALL_PINS or len(ps) < (1 if frames_here else 2) or s in anchors \
                    or (s.lib and len(s.lib.units()) > 1) or re.match(r"(J|P|CN)[A-Z_]*\d", s.ref):
                continue                # a connector is an interface, not a satellite, whatever its pin count
            mine = [(base(net_of.get((s.ref, p.number))), e) for p, e, _ in ps]
            sig = [(n, e) for n, e in mine if n and n not in rails and not n.startswith("unconnected")]
            # a part whose only nets are rails serves the IC on that rail if it is decoupling (C, L, a
            # bead); a header, a diode or a switch between two rails is a group of its own, anywhere
            cands = sig or ([(n, e) for n, e in mine if n in rails and not GROUND_RE.match(n)]
                            if re.match(r"(C|L|FB)\d", s.ref) else [])
            sf = frame_of(s)
            best, mine_anchors = None, {}
            for n, e in cands:
                for r, q in apins.get(n, []):
                    d = gap(sf, q) if sf else math.hypot(max(q[0] - e[0], 0, e[0] - q[2]), max(q[1] - e[1], 0, e[1] - q[3]))
                    if best is None or d < best[0]:
                        best = (d, r, n)
                    mine_anchors.setdefault(r, n)
                    home_frame.setdefault(r, set()).add(q)         # every placed unit of the IC
                    if sf:
                        home_secs[r].add(sf)
            if not best:
                continue
            if sf:
                part_homes[s.ref] = (sf, mine_anchors)
            elif best[0] > HOME_NEAR:
                find("home", sh, s.ref, f"{s.ref} sits {best[0]:.0f} mm from {best[1]} ({best[2]})")
        # a home's sections: one cluster, grown from the anchor's units (a
        # frame holding the anchor is 0 mm from it); a part is at home when
        # it is in the cluster of any IC on its nets
        apart = {}
        for r, secs in home_secs.items():
            have = list(home_frame[r])
            left = set(secs) - set(have)
            grown = True
            while grown:
                grown = False
                for f in list(left):
                    if any(gap(f, g) <= HOME_CHAIN for g in have):
                        have.append(f)
                        left.discard(f)
                        grown = True
            apart[r] = (left, have)
        for ref, (sf, mine_anchors) in sorted(part_homes.items()):
            if all(sf in apart[r][0] for r in mine_anchors):
                r = min(mine_anchors, key=lambda r: min(gap(sf, g) for g in apart[r][1]))
                d = min(gap(sf, g) for g in apart[r][1])
                find("home", sh, ref, f"{ref}'s section stands apart from {r}'s ({mine_anchors[r]}): {d:.0f} mm to the cluster")

        # fields far from their body (a multi-unit symbol's shared field position)
        for s in sh.parts:
            b = s.body()
            if not b:
                continue
            for fname in ("Reference", "Value"):
                f = s.props.get(fname)
                if not f or f["hide"] or not f["value"]:
                    continue
                dx = max(b[0] - f["x"], 0, f["x"] - b[2])
                dy = max(b[1] - f["y"], 0, f["y"] - b[3])
                d = math.hypot(dx, dy)
                if d > FIELD_NEAR:
                    find("field-far", sh, f"{s.ref}:{s.unit}:{fname}",
                         f"{s.ref} unit {s.unit}: its {fname} sits {d:.0f} mm from its body")

        # sections: titled frames that hold everything, never overlap, and
        # are crossed by no wire
        if sh.hpath != "/" or not design.hierarchical:
            secs = sh.sections()
            frames = [b for _, b, _, _ in secs]
            for t, b, _, kind in secs:
                if not t.strip():
                    find("section-title", sh, f"{b[0]:.1f},{b[1]:.1f}",
                         f"the {kind} at ({b[0]:.1f}, {b[1]:.1f}) has no title")
            for t, n in Counter(t.strip() for t, _, _, _ in secs if t.strip()).items():
                if n > 1:
                    find("section-title", sh, t, f"{n} sections are titled '{t}': a title names one block")
            for i, j in pairs_overlapping(frames):
                find("section", sh, f"{secs[i][0]}|{secs[j][0]}",
                     f"sections '{secs[i][0]}' and '{secs[j][0]}' overlap")

            def inside(b):
                return any(b[0] >= f[0] - 1e-6 and b[1] >= f[1] - 1e-6 and b[2] <= f[2] + 1e-6 and b[3] <= f[3] + 1e-6
                           for f in frames)
            out = []
            for s in sh.symbols:
                pts = [e for _, e, _ in s.pins()]
                b = s.body() if not s.power else None
                b = b or (s.x, s.y, s.x, s.y)
                b = (min([b[0]] + [e[0] for e in pts]), min([b[1]] + [e[1] for e in pts]),
                     max([b[2]] + [e[0] for e in pts]), max([b[3]] + [e[1] for e in pts]))
                if not inside(b):
                    out.append(s.ref if not s.ref.startswith("#") else s.value)
            for l in sh.labels:
                if not inside((l.x, l.y, l.x, l.y)):
                    out.append(l.text)
            if out:
                find("section", sh, "outside", f"outside every section: {', '.join(out[:8])}"
                     + (f" and {len(out) - 8} more" if len(out) > 8 else ""))
            if not secs and (sh.symbols or sh.wires):
                find("section", sh, "none", "no section frames on this sheet")
            for a, b, _ in sh.wires:
                wb = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
                if not inside(wb) and any(seg_hits_box(a, b, f) for f in frames):
                    find("section-wire", sh, f"{a[0]:.1f},{a[1]:.1f}",
                         f"a wire from ({a[0]:.1f}, {a[1]:.1f}) crosses a section frame")
            used = defaultdict(int)
            for s in sh.symbols:
                for t, f, _, _ in secs:
                    if f[0] <= s.x <= f[2] and f[1] <= s.y <= f[3]:
                        used[t] += 1
            for t, f, _, _ in secs:
                if t.strip() and not used.get(t):
                    find("section", sh, f"empty:{t}", f"section '{t}' holds no symbol")

        # pins per side, per placed unit
        seen = set()
        for s in sh.parts:
            if (s.lib_id, s.unit) in seen or not s.lib:
                continue
            seen.add((s.lib_id, s.unit))
            sides = defaultdict(set)
            for p, e, d in s.pins():
                side = {(1, 0): "left", (-1, 0): "right", (0, 1): "top", (0, -1): "bottom"}.get(
                    (round(d[0]), round(d[1])), "?")
                sides[side].add(key(*e))
            worst = max(sides.items(), key=lambda kv: len(kv[1]), default=None)
            if worst and len(worst[1]) > PINS_PER_SIDE:
                find("pins-side", sh, f"{s.lib_id}:{s.unit}",
                     f"{s.ref} unit {s.unit} ({s.lib_id}): {len(worst[1])} pins on its {worst[0]} side")

    # one finding per (rule, sheet, key); repeats are counted, not listed
    merged, order = {}, []
    for f in findings:
        k = f[:3]
        if k in merged:
            merged[k][1] += 1
        else:
            merged[k] = [f, 1]
            order.append(k)
    findings = [(merged[k][0][0], merged[k][0][1], merged[k][0][2],
                 merged[k][0][3] + (f" (x{merged[k][1]})" if merged[k][1] > 1 else "")) for k in order]
    waived = []
    if waivers:
        keep = []
        for f in findings:
            w = next((w for w in waivers if w.get("rule") == f[0] and w.get("key") == f[2]
                      and w.get("sheet", f[1]) == f[1]), None)
            (waived if w else keep).append((f, w))
        findings = [f for f, _ in keep]
    return findings, waived
