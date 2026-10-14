"""The board's general_routing_guide.md, read for the tools.

    import guide
    G = guide.load(board_dir)        # {rule: settings}, rules not in the file absent
    guide.describe(G)                # one line per active rule, for `route.py audit`

The guide is prose for the reader, plus ONE table the tools read: rows
`| rule | setting | source |` under "## The numbers the tools read".  Row names
are fixed (RULES below) and a name the file does not know stops with the list
of known ones, so a typo cannot silently switch a rule off.  Rows inside a
fenced code block are examples and are not read.

The dict's keys are the ones route.py's gates always had, so a guide row is a
gate: plane_layers, class_vias, class_layers, field_vias, keepouts,
class_width, edge, angles -- and the copper rules: shape, via_link,
ground_beside, frozen, thermals, thermal_pads.
"""
import re
from pathlib import Path

FILE = "general_routing_guide.md"
RULES = ("joints", "copper to edge", "via to pad gap", "45-degree via link", "copper shape",
         "class width", "thermals", "plane layer", "no vias", "class layers", "keep-out",
         "via field", "ground beside", "frozen", "thermal pad")


class GuideError(SystemExit):
    pass


def _mm(s, row):
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(mm)?\s*", s)
    if not m:
        raise GuideError(f"{FILE}: row '{row}': expected a number in mm, got '{s}'")
    return float(m.group(1))


def _rows(text):
    """(rule, setting, source) for every table row outside a code fence."""
    out, fence, intable = [], False, False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if line.startswith("## "):
            intable = line.lower().startswith("## the numbers")
            continue
        if not intable or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0].lower() == "rule" or set(cells[0]) <= set("-: "):
            continue
        out.append((cells[0].strip().lower(), cells[1].strip(), cells[2].strip() if len(cells) > 2 else ""))
    return out


def _parse(rule, val, src, G):
    """One row into G."""
    s = {"source": src or f"{FILE} (no source given)"}
    if rule == "joints":
        if val.replace(" ", "") not in ("0/45", "0/45degrees"):
            raise GuideError(f"{FILE}: 'joints' takes '0/45' (the only joint rule the tools know), got '{val}'")
        G["angles"] = s
    elif rule == "copper to edge":
        G["edge"] = dict(s, min=_mm(val, rule))
    elif rule == "via to pad gap":
        G.setdefault("via_link", dict(s))["gap"] = _mm(val, rule)
        G["via_link"]["source"] = s["source"]
    elif rule == "45-degree via link":
        v = val.lower()
        if v not in ("last resort", "allowed", "never"):
            raise GuideError(f"{FILE}: '45-degree via link' takes 'last resort', 'allowed' or 'never', got '{val}'")
        G.setdefault("via_link", dict(s))["diag"] = v
    elif rule == "copper shape":
        if val.lower() not in ("on", "off"):
            raise GuideError(f"{FILE}: 'copper shape' takes 'on' or 'off'")
        if val.lower() == "on":
            G["shape"] = s
    elif rule == "class width":
        if val.lower() == "on":
            G["class_width"] = s
    elif rule == "thermals":
        v = val.lower().replace(" ", "")
        m = re.fullmatch(r"outer:(allpads|thtonly|none);inner:(allpads|thtonly|none)", v)
        if not m:
            raise GuideError(f"{FILE}: 'thermals' takes 'outer: all pads; inner: THT only' (each: all pads / THT only / none)")
        G["thermals"] = dict(s, outer=m.group(1), inner=m.group(2))
    elif rule == "plane layer":
        m = re.fullmatch(r"\s*([A-Za-z0-9_.]+)\s*:\s*(\S+)\s*", val)
        if not m:
            raise GuideError(f"{FILE}: 'plane layer' takes 'In1.Cu: GND'")
        G.setdefault("plane_layers", []).append(dict(s, layer=m.group(1), net=m.group(2)))
    elif rule == "no vias":
        m = re.fullmatch(r"\s*([A-Za-z0-9_]+)\s*(?:\((.*)\))?\s*", val)
        if not m:
            raise GuideError(f"{FILE}: 'no vias' takes 'Class' or 'Class (NET: 1, OTHER: 2)'")
        exc = {}
        for part in (m.group(2) or "").split(","):
            if part.strip():
                n, _, k = part.partition(":")
                exc[n.strip()] = int(k.strip() or 0)
        G.setdefault("class_vias", []).append(dict(s, **{"class": m.group(1)}, max_vias=0, **{"except": exc}))
    elif rule == "class layers":
        m = re.fullmatch(r"\s*([A-Za-z0-9_]+)\s*:\s*([A-Za-z0-9_.,\s]+?)\s*(?:\(except\s+(.*)\))?\s*", val)
        if not m:
            raise GuideError(f"{FILE}: 'class layers' takes 'Class: F.Cu, B.Cu (except NET_A)'")
        G.setdefault("class_layers", []).append(dict(
            s, **{"class": m.group(1)}, layers=[x.strip() for x in m.group(2).split(",") if x.strip()],
            **{"except": [x.strip() for x in (m.group(3) or "").split(",") if x.strip()]}))
    elif rule == "keep-out":
        m = re.fullmatch(r"\s*(.+?)\s*:\s*([A-Za-z0-9_,\s]+?)\s*(?:on\s+([A-Za-z0-9_.,\s]+))?\s*", val)
        if not m:
            raise GuideError(f"{FILE}: 'keep-out' takes '<placement.json keep-out name>: Class, Class [on F.Cu, B.Cu]'")
        k = dict(s, name=m.group(1), from_placement=m.group(1),
                 forbid_classes=[x.strip() for x in m.group(2).split(",") if x.strip()])
        if m.group(3):
            k["layers"] = [x.strip() for x in m.group(3).split(",") if x.strip()]
        G.setdefault("keepouts", []).append(k)
    elif rule == "via field":
        m = re.fullmatch(r"\s*([A-Za-z]+\d+)\s*:\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*(lattice)?\s*"
                         r"(?:,\s*widths\s+(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?))?\s*(?:,\s*margin\s+(\d+(?:\.\d+)?))?\s*", val)
        if not m:
            raise GuideError(f"{FILE}: 'via field' takes 'U1: 0.45/0.15 lattice, widths 0.10-0.13, margin 0.4'")
        f = dict(s, ref=m.group(1), pad=float(m.group(2)), drill=float(m.group(3)), on_lattice=bool(m.group(4)),
                 margin=float(m.group(7) or 0.4), outside_refused=True)
        if m.group(5):
            f["widths"] = [float(m.group(5)), float(m.group(6))]
        G.setdefault("field_vias", []).append(f)
    elif rule == "ground beside":
        m = re.fullmatch(r"\s*([A-Za-z0-9_]+)\s*:\s*(\S+)\s+within\s+(\d+(?:\.\d+)?)\s*mm\s*", val)
        if not m:
            raise GuideError(f"{FILE}: 'ground beside' takes 'Fast: GND within 0.6 mm'")
        G["ground_beside"] = dict(s, **{"class": m.group(1)}, net=m.group(2), within=float(m.group(3)))
    elif rule == "frozen":
        fr = G.setdefault("frozen", dict(s, classes=[], nets=[]))
        for part in val.split(","):
            kind, _, name = part.strip().partition(" ")
            if kind == "class" and name.strip():
                fr["classes"].append(name.strip())
            elif kind == "net" and name.strip():
                fr["nets"].append(name.strip())
            else:
                raise GuideError(f"{FILE}: 'frozen' takes 'class Reference, net ref_taps'")
    elif rule == "thermal pad":
        m = re.fullmatch(r"\s*([A-Za-z]+\d+)\.(\S+)\s*:\s*(solid|none)\s*", val, re.I)
        if not m:
            raise GuideError(f"{FILE}: 'thermal pad' takes 'U5.5: solid' or 'C12.2: none'")
        G.setdefault("thermal_pads", []).append(dict(s, ref=m.group(1), pad=m.group(2), how=m.group(3).lower()))


def load(board_dir):
    p = Path(board_dir) / FILE
    if not p.exists():
        return {}
    G = {}
    for rule, val, src in _rows(p.read_text()):
        if rule not in RULES:
            raise GuideError(f"{FILE}: unknown rule '{rule}' -- the tools know: {', '.join(RULES)}")
        _parse(rule, val, src, G)
    v = G.get("via_link")
    if v and "gap" not in v:
        raise GuideError(f"{FILE}: '45-degree via link' needs a 'via to pad gap' row too")
    if v:
        v.setdefault("diag", "last resort")
    return G


def describe(G):
    """One line per active rule."""
    L = []
    for k in ("angles", "edge", "via_link", "shape", "class_width", "thermals", "ground_beside", "frozen"):
        if k in G:
            e = {q: v for q, v in G[k].items() if q != "source"}
            L.append(f"  {k:13s} {e or 'on'}   source: {G[k]['source']}")
    for k in ("plane_layers", "class_vias", "class_layers", "keepouts", "field_vias", "thermal_pads"):
        for e in G.get(k, []):
            body = {q: v for q, v in e.items() if q != "source"}
            L.append(f"  {k:13s} {body}   source: {e['source']}")
    return L or ["  (no rules: the guide's table is empty)"]


def field_boxes(b, G, pcbnew):
    """The courtyard boxes (nm) of the via-field parts, which the copper rules do
    not judge inside (a BGA's dog-bones are the fab rules' business)."""
    out = []
    for f in G.get("field_vias", []):
        fp = b.FindFootprintByReference(f["ref"])
        if fp is None:
            continue
        cy = fp.GetCourtyard(pcbnew.F_CrtYd).BBox()
        out.append((cy.GetX(), cy.GetY(), cy.GetRight(), cy.GetBottom()))
    return out
