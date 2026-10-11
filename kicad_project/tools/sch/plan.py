"""
The layout: a netlist -> every sheet drawn to the standard.

Per sheet, the big units (more than four pins: ICs, connectors) are anchors.
Each anchor pin gets a stub and its terminator -- a power symbol for a rail,
a hierarchical label for a net that leaves the sheet, a net label otherwise,
a no-connect flag -- and adjacent pins of one net share one spine wire and
one terminator. The small parts (at most four pins) are grouped by function
into blocks drawn with wires inside: a decoupling bank per rail (caps between
a rail wire and a ground wire), a pull bank per rail (resistors hanging from
one rail wire, each ending in its signal's label), a series stack, and a
ladder for anything else (a crystal with its load caps, a filter). A block
sits beside the anchor pins it serves, ordered as they are; banks go below.
Groups are packed onto the smallest ISO paper that holds them.

Output, under <out>/:
  plan.json             the layout, every sheet
  <file>.ops.json       the Konnect calls for that sheet, in order
  netlist.json          the source netlist, for `sch.py parity` after drawing
  preview/<board>/      a scratch KiCad project of the plan, which kicad-cli
                        nets up (parity with the source), runs ERC on and
                        renders for `check` -- the plan is proven before any
                        Konnect call is made
"""
import datetime
import itertools
import json
import math
import os
import re
import shutil
import uuid as uuidlib
from collections import defaultdict, Counter
from pathlib import Path

import check
import model
import sexpr
from model import GROUND_RE, text_w, FRAME, TITLE_BLOCK, ISO

G = 2.54
STUB = 2.54
GAP = 2.54             # between two elements of a block
BLOCK_GAP = 7.62       # between two blocks
SIDE_GAP = 10.16       # between an anchor's label lane and its blocks
GROUP_GAP = 10.16      # between two sections
ROW_MIN = 10.16        # between two rows of a ladder
BANK_MAX = 8           # elements per bank row before it wraps
MARGIN = check.MARGIN + 2.54
SMALL = check.SMALL_PINS
TITLE_SIZE = check.TITLE_SIZE   # a section's title
PAD = 2.54             # a section's frame clears its content by this
TITLE_H = 5.08         # the title band inside the top of a frame


def nat(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


def snap(v, g=G):
    return round(round(v / g) * g, 4)


def rs(x, y, rot):
    """Rotate a sheet-space offset (y down) counter-clockwise on screen."""
    t = math.radians(rot)
    c, s = round(math.cos(t)), round(math.sin(t))
    return x * c + y * s, -x * s + y * c


def ubox(*bs):
    bs = [b for b in bs if b]
    if not bs:
        return None
    return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))


def mbox(b, dx, dy):
    return (b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy) if b else None


def uid():
    return str(uuidlib.uuid4())


# == the source ==============================================================

class Part:
    def __init__(self, ref, lib_id, lib, value, footprint, fields, units, dnp=False):
        self.ref, self.lib_id, self.lib, self.value = ref, lib_id, lib, value
        self.footprint, self.fields, self.units, self.dnp = footprint, fields, units, dnp

    def pin_count(self, unit):
        return len({p.number for p in self.lib.unit_pins(unit)})

    @property
    def small(self):
        return len(self.units) == 1 and self.pin_count(next(iter(self.units))) <= SMALL


class Source:
    """Everything the layout needs, whatever it was read from."""

    def __init__(self, board):
        self.board = Path(board)
        self.sheets = []           # [(name, file)], root first
        self.parts = {}
        self.libs = {}             # lib_id -> LibSym
        self.lib_nodes = {}        # lib_id -> parsed node, for the preview
        self.nets = {}             # name -> [(ref, pin)]
        self.rails = set()
        self.rail_lib = {}
        self.nc = set()
        self.flags = set()         # nets that carry a PWR_FLAG
        self.title, self.rev, self.version = self.board.name, "", "20250610"
        self.date = datetime.date.today().isoformat()
        ho = self.board / "handoff"
        try:
            self.rev = f"handoff rev {json.loads((ho / 'MANIFEST.json').read_text())['rev']}"
            first = (ho / "00_READ_FIRST.md").read_text().splitlines()[0]
            m = re.search(r"—\s*(.+?)\s+handoff", first)
            if m:
                self.title = m.group(1)
        except (OSError, KeyError, ValueError, IndexError):
            pass

    # -- read the drawn schematic (a redraw) ---------------------------------
    @classmethod
    def from_sch(cls, board):
        src = cls(board)
        d = model.Design(board)
        src.root_name = d.root.stem
        src.version = d.sheets[0].version or src.version
        src.sheets = [(sh.name, sh.path.name) for sh in d.sheets]
        for sh in d.sheets:
            ls = sexpr.first(sh.tree, "lib_symbols")
            for n in sexpr.every(ls, "symbol") if ls is not None else []:
                src.lib_nodes.setdefault(str(n[1]), n)
            src.libs.update({k: v for k, v in sh.libs.items() if k not in src.libs})
        raw_nets, comps = model.netlist(d.root)
        names = src._names(raw_nets)
        for raw, nodes in raw_nets.items():
            if raw.startswith("unconnected-"):
                continue
            src.nets[names[raw]] = sorted(set(nodes), key=lambda t: (nat(t[0]), nat(t[1])))
        src.rails = d.rails() - {"PWR_FLAG"}
        for sh in d.sheets:
            for s in sh.power_syms:
                if s.value in src.rails:
                    src.rail_lib.setdefault(s.value, s.lib_id)
        net_of = {n: k for k, ns in src.nets.items() for n in ns}
        for sh in d.sheets:
            uf = check.pieces(sh)
            piece_net = {}
            for s in sh.parts:
                for p, e, _ in s.pins():
                    n = net_of.get((s.ref, p.number))
                    if n:
                        piece_net[uf.find(check.key(*e))] = n
            for l in sh.labels:
                piece_net.setdefault(uf.find(check.key(l.x, l.y)), l.text)
            for s in sh.power_syms:
                if s.value in src.rails:
                    for _, e, _ in s.pins():
                        piece_net.setdefault(uf.find(check.key(*e)), s.value)
            ncs = {check.key(x, y) for x, y, _ in sh.no_connects}
            for s in sh.parts:
                for p, e, _ in s.pins():
                    if check.key(*e) in ncs:
                        src.nc.add((s.ref, p.number))
            for s in sh.power_syms:
                if s.lib_id.endswith("PWR_FLAG"):
                    for _, e, _ in s.pins():
                        n = piece_net.get(uf.find(check.key(*e)))
                        if n:
                            src.flags.add(n)
            for s in sh.parts:
                part = src.parts.get(s.ref)
                if part is None:
                    keep = {k: v["value"] for k, v in s.props.items()
                            if k not in ("Reference", "Value", "Footprint") and v["value"]}
                    part = Part(s.ref, s.lib_id, s.lib, s.value, s.props.get("Footprint", {}).get("value", ""),
                                keep, {}, s.dnp)
                    src.parts[s.ref] = part
                part.units[s.unit] = sh.name
            tb = sh.title_block
            if sh.hpath == "/" and tb.get("title"):
                src.title = tb["title"]
        return src

    # -- read a netlist JSON (a new board) -------------------------------------
    @classmethod
    def from_json(cls, board, path):
        src = cls(board)
        j = json.loads(Path(path).read_text())
        src.root_name = Path(board).name
        src.title = j.get("title", src.title)
        src.rev = j.get("rev", src.rev)
        src.sheets = [("Root", f"{src.root_name}.kicad_sch")] + [(s["name"], s["file"]) for s in j["sheets"]]
        libs = Libraries(board)
        for ref, p in j["parts"].items():
            node, lib = libs.get(p["lib_id"])
            src.lib_nodes[p["lib_id"]], src.libs[p["lib_id"]] = node, lib
            src.parts[ref] = Part(ref, p["lib_id"], lib, p.get("value", ""), p.get("footprint", ""),
                                  p.get("fields", {}), {int(u): s for u, s in p["units"].items()},
                                  p.get("dnp", False))
        src.nets = {n: [tuple(x) for x in nodes] for n, nodes in j["nets"].items()}
        src.rails = set(j.get("rails", []))
        src.nc = {tuple(x) for x in j.get("nc", [])}
        src.flags = set(j.get("pwr_flags", []))
        return src

    # -- the handoff's own netlist (a new board, when it is complete) ---------
    @classmethod
    def from_handoff(cls, board):
        """handoff/netlist.json for the connections, <board>/schematic.json
        for what is the board's: the sheets and, per refdes (a glob: "C*"),
        the symbol, footprint, unit -> sheet and, optionally, the Value and
        DNP flag:

            {"sheets": [{"name": "Power", "file": "power.kicad_sch"}, ...],
             "symbols": {"U1": {"lib_id": "board1:STM32", "footprint": "...",
                                "units": {"1": "MCU"}},
                         "C*": {"lib_id": "Device:C", "footprint": "...", "sheet": "Power"}}}
        """
        board = Path(board)
        nl = json.loads((board / "handoff" / "netlist.json").read_text())
        if not nl.get("complete"):
            raise SystemExit(f"sch: handoff/netlist.json is not complete ({len(nl.get('prose_only', []))} parts "
                             f"only in prose, {len(nl.get('open', []))} OPEN pins) -- plan from it when it is, or "
                             "ask concept for the missing PINS")
        sj = board / "schematic.json"
        cfg = json.loads(sj.read_text()) if sj.exists() else {}
        if not cfg.get("sheets") or not cfg.get("symbols"):
            raise SystemExit("sch: <board>/schematic.json needs 'sheets' and 'symbols' to plan from the handoff "
                             "(tools/sch/README.md)")
        src = cls(board)
        src.root_name = board.name
        src.title = nl.get("title", src.title)
        src.sheets = [("Root", f"{src.root_name}.kicad_sch")] + [(s["name"], s["file"]) for s in cfg["sheets"]]
        libs = Libraries(board)
        import fnmatch
        for ref, hp in nl["parts"].items():
            if hp["placeholder"]:
                continue
            sym = next((v for k, v in cfg["symbols"].items() if fnmatch.fnmatchcase(ref, k)), None)
            if sym is None:
                raise SystemExit(f"sch: schematic.json has no symbol for {ref} ({hp['function']})")
            node, lib = libs.get(sym["lib_id"])
            src.lib_nodes[sym["lib_id"]], src.libs[sym["lib_id"]] = node, lib
            units = {int(u): s for u, s in sym.get("units", {}).items()} or {1: sym["sheet"]}
            value = sym.get("value") or re.split(r",| -- |;", hp["value"])[0].strip()
            src.parts[ref] = Part(ref, sym["lib_id"], lib, value, sym.get("footprint", ""),
                                  dict(sym.get("fields", {})), units, sym.get("dnp", False))
        src.nets = {n: [tuple(x) for x in nodes if x[0] in src.parts] for n, nodes in nl["nets"].items()}
        src.rails = set(nl.get("rails", []))
        src.nc = {tuple(x) for x in nl.get("nc", []) if x[0] in src.parts}
        src.flags = set(cfg.get("pwr_flags", []))
        return src

    @staticmethod
    def _names(raw_nets):
        """KiCad's net names -> the names labels will carry: the last path
        element, made unique; an unnamed net becomes N_<ref>_<pin>."""
        out, used = {}, Counter()
        for raw in raw_nets:
            if raw.startswith("Net-("):
                m = re.match(r"Net-\((.+?)-Pad(.+)\)", raw)
                b = f"N_{m.group(1)}_{m.group(2)}" if m else re.sub(r"\W", "_", raw)
            else:
                b = raw.rsplit("/", 1)[-1] or raw
            out[raw] = b
            used[b] += 1
        for raw, b in list(out.items()):
            if used[b] > 1:
                path = [x for x in raw.split("/") if x][:-1]
                out[raw] = re.sub(r"\W+", "_", "_".join(path[-1:] + [b])).strip("_")
        return out


class Libraries:
    """Symbols from the board's sym-lib-table and KiCad's own libraries."""

    def __init__(self, board):
        self.tables = {}
        stock = next((Path(os.environ[v]) for v in ("KICAD10_SYMBOL_DIR", "KICAD9_SYMBOL_DIR", "KICAD8_SYMBOL_DIR")
                      if os.environ.get(v)), None) or next(
            (p for p in (Path("/usr/share/kicad/symbols"),
                         Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols")) if p.exists()), None)
        self.stock = stock
        t = Path(board) / "sym-lib-table"
        if t.exists():
            tree = sexpr.load(t)
            for lib in sexpr.every(tree, "lib"):
                uri = str(sexpr.atom(lib, "uri")).replace("${KIPRJMOD}", str(board))
                self.tables[str(sexpr.atom(lib, "name"))] = Path(uri)
        self.cache = {}

    def file(self, libname):
        if libname in self.tables:
            return self.tables[libname]
        if self.stock:
            return self.stock / f"{libname}.kicad_sym"
        raise SystemExit(f"sch: no library {libname} (sym-lib-table or KiCad's symbol directory)")

    def get(self, lib_id):
        """(embedded-form node, LibSym) for 'Lib:Name', `extends` flattened."""
        libname, name = lib_id.split(":", 1)
        f = self.file(libname)
        if f not in self.cache:
            self.cache[f] = {str(n[1]): n for n in sexpr.every(sexpr.load(f), "symbol")}
        syms = self.cache[f]
        if name not in syms:
            raise SystemExit(f"sch: {lib_id} not found in {f}")
        n = syms[name]
        parent = sexpr.atom(n, "extends")
        if parent:
            base = syms[str(parent)]
            props = {str(p[1]): p for p in sexpr.every(n, "property")}
            node = [base[0], sexpr.Q(lib_id)]
            for c in base[2:]:
                if sexpr.is_node(c, "property") and str(c[1]) in props:
                    node.append(props.pop(str(c[1])))
                elif sexpr.is_node(c, "symbol"):
                    node.append([c[0], sexpr.Q(str(c[1]).replace(str(parent), name, 1))] + c[2:])
                else:
                    node.append(c)
            node += list(props.values())
        else:
            node = [n[0], sexpr.Q(lib_id)] + n[2:]
        return node, model.LibSym(node)


# == placed things ===========================================================

class El:
    """One symbol unit at (x, y, rot) relative to its block."""

    def __init__(self, part, unit=1, rot=0, x=0.0, y=0.0, value=None, lib_id=None, lib=None):
        self.part, self.unit, self.rot, self.x, self.y = part, unit, rot, x, y
        self.lib = lib or part.lib
        self.lib_id = lib_id or part.lib_id
        self.value = value if value is not None else part.value
        self.ref = part.ref if part else None
        self.pins = {}
        for p in self.lib.unit_pins(unit):
            ox, oy = model.xform(p.x, p.y, rot)
            d = model.xdir(p.angle, rot)
            self.pins.setdefault(p.number, (round(ox, 4), round(oy, 4), round(d[0]), round(d[1])))

    def end(self, num):
        ox, oy, _, _ = self.pins[num]
        return (round(self.x + ox, 4), round(self.y + oy, 4))

    def out(self, num):
        _, _, dx, dy = self.pins[num]
        return (-dx, -dy)

    def body(self):
        b = self.lib.unit_box(self.unit)
        if not b:
            return None
        a, c = model.xform(b[0], b[1], self.rot), model.xform(b[2], b[3], self.rot)
        return (self.x + min(a[0], c[0]), self.y + min(a[1], c[1]), self.x + max(a[0], c[0]), self.y + max(a[1], c[1]))

    def field_box(self, name, text):
        f = self.lib.fields.get(name)
        if not f or f["hide"] or not text:
            return None
        return mbox(field_box(f, text, self.rot), self.x, self.y)

    def box(self):
        bs = [self.body()]
        for num, (ox, oy, dx, dy) in self.pins.items():
            bs.append((self.x + min(ox, ox + dx * 2.54), self.y + min(oy, oy + dy * 2.54),
                       self.x + max(ox, ox + dx * 2.54), self.y + max(oy, oy + dy * 2.54)))
        bs.append(self.field_box("Reference", self.ref if self.ref and not self.ref.startswith("#") else ""))
        bs.append(self.field_box("Value", self.value))
        return ubox(*bs)

    def moved(self, dx, dy):
        self.x, self.y = round(self.x + dx, 4), round(self.y + dy, 4)


def field_box(f, text, rot, size=1.27):
    """A field's text box, offset from the symbol origin, as KiCad lays it:
    in the symbol's own frame with the library angle and justification, then
    turned with the symbol."""
    fx, fy = f["x"], -f["y"]
    w, h = text_w(text, size), size * 1.2
    j = f["justify"]
    hj = "left" if "left" in j else "right" if "right" in j else "center"
    vj = "top" if "top" in j else "bottom" if "bottom" in j else "center"
    if round(f["angle"]) % 180 == 0:
        x0 = fx if hj == "left" else fx - w if hj == "right" else fx - w / 2
        y0 = fy if vj == "top" else fy - h if vj == "bottom" else fy - h / 2
        b = (x0, y0, x0 + w, y0 + h)
    else:
        y1 = fy if hj == "left" else fy + w if hj == "right" else fy + w / 2
        x0 = fx if vj == "top" else fx - h if vj == "bottom" else fx - h / 2
        b = (x0, y1 - w, x0 + h, y1)
    pts = [rs(b[0], b[1], rot), rs(b[2], b[3], rot)]
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


def label_rot(o):
    return {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}[(round(o[0]), round(o[1]))]


def label_box(kind, text, x, y, rot):
    L = text_w(text) + (2.6 if kind != "label" else 0.8)
    if kind == "label":
        return {0: (x, y - 1.8, x + L, y + 0.2), 180: (x - L, y - 1.8, x, y + 0.2),
                90: (x - 1.8, y - L, x + 0.2, y), 270: (x - 1.8, y, x + 0.2, y + L)}[rot]
    return {0: (x, y - 1.2, x + L, y + 1.2), 180: (x - L, y - 1.2, x, y + 1.2),
            90: (x - 1.2, y - L, x + 1.2, y), 270: (x - 1.2, y, x + 1.2, y + L)}[rot]


class Block:
    """Things drawn together, coordinates relative to the block."""

    def __init__(self, kind="", hint=0.0):
        self.kind, self.hint = kind, hint
        self.els, self.wires, self.junctions, self.ncs = [], [], [], []
        self.labels = []           # (kind, text, x, y, rot)
        self.power = []            # El of a power symbol
        self.extra = []            # boxes with no item (reserved room)
        self.title, self.frame = "", None     # a section: its title and frame box
        self.sections = []         # [[title, frame]] of the sections absorbed

    def box(self):
        bs = [e.box() for e in self.els + self.power]
        bs += [(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])) for a, b in self.wires]
        bs += [label_box(*l) for l in self.labels]
        bs += [(x - 0.7, y - 0.7, x + 0.7, y + 0.7) for x, y in self.ncs]
        bs += self.extra
        bs += [self.frame] + [f for _, f in self.sections]
        return ubox(*bs) or (0, 0, 0, 0)

    def move(self, dx, dy):
        for e in self.els + self.power:
            e.moved(dx, dy)
        self.wires = [((round(a[0] + dx, 4), round(a[1] + dy, 4)), (round(b[0] + dx, 4), round(b[1] + dy, 4)))
                      for a, b in self.wires]
        self.junctions = [(round(x + dx, 4), round(y + dy, 4)) for x, y in self.junctions]
        self.ncs = [(round(x + dx, 4), round(y + dy, 4)) for x, y in self.ncs]
        self.labels = [(k, t, round(x + dx, 4), round(y + dy, 4), r) for k, t, x, y, r in self.labels]
        self.extra = [mbox(b, dx, dy) for b in self.extra]
        self.frame = mbox(self.frame, dx, dy)
        self.sections = [[t, mbox(f, dx, dy)] for t, f in self.sections]

    def absorb(self, other):
        self.els += other.els
        self.wires += other.wires
        self.junctions += other.junctions
        self.ncs += other.ncs
        self.labels += other.labels
        self.power += other.power
        self.extra += other.extra
        self.sections += ([[other.title, other.frame]] if other.frame else []) + other.sections

    def wire(self, a, b):
        a, b = (round(a[0], 4), round(a[1], 4)), (round(b[0], 4), round(b[1], 4))
        if a != b:
            self.wires.append((a, b))


# == the sheet context =======================================================

class Ctx:
    """What one sheet's layout asks of the netlist."""

    def __init__(self, src, sheet):
        self.src, self.sheet = src, sheet
        self.net_of, self.unit_of = {}, {}
        for p in src.parts.values():
            for u in p.units:
                for pin in p.lib.unit_pins(u):
                    self.unit_of.setdefault((p.ref, pin.number), u)
        self.sheets_of = defaultdict(set)
        for n, nodes in src.nets.items():
            for r, pin in nodes:
                self.net_of[(r, pin)] = n
                part = src.parts.get(r)
                u = self.unit_of.get((r, pin))
                if part and u in part.units:
                    self.sheets_of[n].add(part.units[u])
        self.power_lib = PowerLibs(src)

    def net(self, ref, pin):
        if (ref, pin) in self.src.nc:
            return None
        n = self.net_of.get((ref, pin))
        if n and len(self.src.nets[n]) == 1 and n.startswith("N_") and n not in self.src.rails:
            return None                                  # a pin alone on an unnamed net
        return n

    def is_rail(self, n):
        return n in self.src.rails

    def ground(self, n):
        return bool(GROUND_RE.match(n or ""))

    def kind(self, n):
        return "hier" if len(self.sheets_of.get(n, ())) > 1 else "label"

    def power_el(self, rail, at, outward):
        """A power glyph for `rail` with its pin at `at`, pointing `outward`;
        its name reads horizontally whichever way it points."""
        d = {(-1, 0): "L", (1, 0): "R", (0, -1): "U", (0, 1): "D"}[(round(outward[0]), round(outward[1]))]
        lib_id, lib = self.power_lib.get(("GND_" if self.ground(rail) else "PWR_") + d)
        e = El(None, 1, 0, at[0], at[1], value=rail, lib_id=lib_id, lib=lib)
        e.rail = rail
        return e

    def terminate(self, blk, net, at, outward, clear=None):
        """Stub from `at` outward, then a power symbol or a label; with
        `clear` (a box: the part and its own text), the stub first leaves it."""
        n = STUB
        if clear:
            past = {(1, 0): clear[2] - at[0], (-1, 0): at[0] - clear[0],
                    (0, 1): clear[3] - at[1], (0, -1): at[1] - clear[1]}[(round(outward[0]), round(outward[1]))]
            n = max(STUB, snap(past + 1.27 + 1.26, 1.27))
        end = (round(at[0] + outward[0] * n, 4), round(at[1] + outward[1] * n, 4))
        blk.wire(at, end)
        if self.is_rail(net):
            blk.power.append(self.power_el(net, end, outward))
        else:
            blk.labels.append(("hier" if self.kind(net) == "hier" else "label", net, end[0], end[1],
                               label_rot(outward)))


HW_SCH = Path(__file__).resolve().parent / "hw_sch.kicad_sym"


class PowerLibs:
    """The glyphs that draw a rail (hw_sch, shipped with this tool) and KiCad's
    PWR_FLAG, loaded into the source's libraries on first use."""

    def __init__(self, src):
        self.src = src
        self.nodes = None

    def get(self, name):
        if name == "PWR_FLAG":
            lib_id = "power:PWR_FLAG"
            if lib_id not in self.src.libs:
                node, lib = Libraries(self.src.board).get(lib_id)
                self.src.lib_nodes[lib_id], self.src.libs[lib_id] = node, lib
            return lib_id, self.src.libs[lib_id]
        lib_id = f"hw_sch:{name}"
        if lib_id not in self.src.libs:
            if self.nodes is None:
                self.nodes = {str(n[1]): n for n in sexpr.every(sexpr.load(HW_SCH), "symbol")}
            n = self.nodes[name]
            node = [n[0], sexpr.Q(lib_id)] + n[2:]
            self.src.lib_nodes[lib_id], self.src.libs[lib_id] = node, model.LibSym(node)
        return lib_id, self.src.libs[lib_id]


# == an anchor and its pins ==================================================

def side_of(outward):
    return {(-1, 0): "left", (1, 0): "right", (0, -1): "top", (0, 1): "bottom"}[(round(outward[0]), round(outward[1]))]


def anchor_block(ctx, part, unit):
    """The unit at the origin, every pin terminated. Returns (block, pin_sides)
    where pin_sides maps net -> [(side, along)] for placing its satellites."""
    blk = Block("anchor")
    el = El(part, unit, 0, 0.0, 0.0)
    blk.els.append(el)
    by_side = defaultdict(list)
    for num in el.pins:
        o = el.out(num)
        e = el.end(num)
        along = e[1] if o[0] else e[0]
        by_side[side_of(o)].append((along, num, e, o))
    where = defaultdict(list)
    for side, pins in by_side.items():
        pins.sort()
        o = pins[0][3]
        h = bool(o[0])                                 # a left / right side: lanes run horizontally
        runs = []
        for along, num, e, _ in pins:
            n = ctx.net(part.ref, num)
            if (part.ref, num) in ctx.src.nc:
                blk.ncs.append(e)
                runs.append(None)
                continue
            if n is None:
                runs.append(None)
                continue
            where[n].append((side, along))
            last = runs[-1] if runs else None
            colinear = last and (last["pins"][-1][1][0] == e[0] if h else last["pins"][-1][1][1] == e[1])
            if last and last["net"] == n and colinear and abs(along - last["pins"][-1][0]) <= 7.62 + 1e-6:
                last["pins"].append((along, e))
            else:
                runs.append(dict(net=n, pins=[(along, e)]))
        runs = [r for r in runs if r]
        for r in runs:
            ps = r["pins"]
            if len(ps) == 1:
                r["at"] = ps[0][1]
                continue
            # a stub per pin first: a spine along the pin ends would cut through
            # the pin numbers KiCad draws there
            ss = [(round(e[0] + o[0] * G, 4), round(e[1] + o[1] * G, 4)) for _, e in ps]
            for (_, e), q in zip(ps, ss):
                blk.wire(e, q)
            for a, b in zip(ss, ss[1:]):
                blk.wire(a, b)
            r["at"] = ss[(len(ss) - 1) // 2]
        # every run: a stub and its terminator in the pin's own lane -- a label,
        # or a power glyph whose name reads horizontally like one
        for r in runs:
            ctx.terminate(blk, r["net"], r["at"], o)
    return blk, where


# == small parts: blocks =====================================================

def orient(part, want):
    """A rotation for a small part: `want` maps pin -> 'top' / 'bottom' /
    'left' / 'right'; among the rotations that satisfy it, the one whose
    Reference reads horizontally."""
    best = None
    for rot in (0, 90, 180, 270):
        el = El(part, next(iter(part.units)), rot)
        ok = True
        for num, side in want.items():
            if num not in el.pins:
                ok = False
                break
            ox, oy, _, _ = el.pins[num]
            others = [v for k, v in el.pins.items() if k != num]
            if side == "top" and any(oy >= v[1] for v in others):
                ok = False
            if side == "bottom" and any(oy <= v[1] for v in others):
                ok = False
            if side == "left" and any(ox >= v[0] for v in others):
                ok = False
            if side == "right" and any(ox <= v[0] for v in others):
                ok = False
        if not ok:
            continue
        f = part.lib.fields.get("Reference", {"angle": 0})
        readable = (round(f.get("angle", 0)) % 180 == 0) == (rot % 180 == 0)
        score = (0 if readable else 1, rot)
        if best is None or score < best[0]:
            best = (score, rot)
    return best[1] if best else 0


def ladder(ctx, parts, hint=0.0, kind="ladder"):
    """Small parts with wires between them: a row per net that two or more of
    their pins share, each part a column between its rows; a pin on a net no
    other part here shares ends in its terminator."""
    blk = Block(kind, hint)
    pins = {}
    for p in parts:
        nums = dict.fromkeys(pin.number for pin in p.lib.unit_pins(next(iter(p.units))))
        pins[p.ref] = [(n, ctx.net(p.ref, n)) for n in nums]
    here = {(p.ref, n) for p in parts for n, _ in pins[p.ref]}
    cnt = Counter(net for ps in pins.values() for _, net in ps if net)
    rows = [n for n, c in cnt.items() if c >= 2]
    top = sorted([n for n in rows if ctx.is_rail(n) and not ctx.ground(n)], key=nat)
    bot = sorted([n for n in rows if ctx.ground(n)], key=nat)
    mid = [n for n in rows if n not in top and n not in bot]
    inrow = [p for p in parts if any(net in rows for _, net in pins[p.ref])]
    free = [p for p in parts if p not in inrow]

    def crossings(order, cols):
        ri = {n: i for i, n in enumerate(order)}
        att = [{ri[net] for _, net in pins[p.ref] if net in ri} for p in cols]
        ext = {}
        for i, a in enumerate(att):
            for r in a:
                lo, hi = ext.get(r, (i, i))
                ext[r] = (min(lo, i), max(hi, i))
        n = 0
        for i, a in enumerate(att):
            lo, hi = min(a), max(a)
            for r, (x0, x1) in ext.items():
                if x0 < i < x1 and r not in a and lo <= r <= hi:
                    n += 1
        return n

    def sorted_cols(order):
        ri = {n: i for i, n in enumerate(order)}
        return sorted(inrow, key=lambda p: (min(ri[net] for _, net in pins[p.ref] if net in ri),
                                            max(ri[net] for _, net in pins[p.ref] if net in ri)))

    best = None
    for m in itertools.permutations(mid) if len(mid) <= 6 else [tuple(mid)]:
        order = top + list(m) + bot
        base_cols = sorted_cols(order)
        cands = itertools.permutations(base_cols) if len(inrow) <= 7 and len(mid) <= 3 else [base_cols]
        for cols in cands:
            score = (crossings(order, cols), sum(i != base_cols.index(p) for i, p in enumerate(cols)))
            if best is None or score < best[0]:
                best = (score, order, list(cols))
    if best:
        _, order, cols = best
    else:
        order, cols = [], []
    ri = {n: i for i, n in enumerate(order)}

    # rows' y: room for every part that sits between two rows
    spans = [ROW_MIN]
    for p in cols:
        r = sorted({ri[net] for _, net in pins[p.ref] if net in ri})
        # sized as the ladder will place it: a two-pin part upright
        el = El(p, next(iter(p.units)), orient(p, {pins[p.ref][0][0]: "top"}) if len(pins[p.ref]) == 2 else 0)
        ys = [v[1] for v in el.pins.values()]
        b = el.box()
        tall = max(max(ys) - min(ys), b[3] - b[1])         # pins, body and its own text
        if len(pins[p.ref]) == 2:
            tall = (max(ys) - min(ys)) + 2 * max(0.0, (min(ys) - b[1]), (b[3] - max(ys))) + 2.54
        if len(r) > 1 and len(pins[p.ref]) == 2:
            spans.append((tall + G) / (r[-1] - r[0]))
        elif len(pins[p.ref]) > 2:
            spans.append(tall + 2 * G + 2.54)
    pitch = snap(max(spans) + 1.26)
    ry = {n: i * pitch for i, n in enumerate(order)}

    attach = defaultdict(list)                    # row net -> [x on the row]
    x = 0.0
    for p in cols:
        u = next(iter(p.units))
        mine = pins[p.ref]
        att = sorted({ri[net] for _, net in mine if net in ri})
        def past(el, num):
            """How far the part's own text runs past pin `num`'s end, along
            its axis: the part then sits that far off the row, on a wire."""
            b, (ox, oy, _, _) = el.box(), el.pins[num]
            ys = [v[1] for v in el.pins.values()]
            over = (oy - b[1]) if oy == min(ys) else (b[3] - oy)
            return snap(over + 1.26, 1.27) if over > 0.05 else 0.0

        if len(mine) == 2:
            (n1, a), (n2, b) = mine
            if a in ri and b in ri:
                up, dn = (n1, n2) if ri[a] <= ri[b] else (n2, n1)
                rot = orient(p, {up: "top", dn: "bottom"})
                el = El(p, u, rot)
                el.x, el.y = 0, ry[ctx.net(p.ref, up)] - el.pins[up][1] + past(el, up)
            else:
                rn = n1 if a in ri else n2
                net = ctx.net(p.ref, rn)
                hang_up = ctx.ground(net) or (ri[net] == len(order) - 1 and len(order) > 1)
                rot = orient(p, {rn: "bottom" if hang_up else "top"})
                el = El(p, u, rot)
                el.x, el.y = 0, ry[net] - el.pins[rn][1] + (-past(el, rn) if hang_up else past(el, rn))
        else:
            el = El(p, u, 0)
            ys = [v[1] for v in el.pins.values()]
            if len(att) > 1:
                y0, y1 = ry[order[att[0]]], ry[order[att[1]]]
                el.x, el.y = 0, snap((y0 + y1) / 2 - (max(ys) + min(ys)) / 2, 1.27)
            else:
                net = order[att[0]]
                lift = (max(ys) - min(ys)) / 2 + G * 2
                c = ry[net] + (-lift if ctx.ground(net) else lift)
                el.x, el.y = 0, snap(c - (max(ys) + min(ys)) / 2, 1.27)
        sub = Block()
        sub.els.append(el)
        mine_attach = []
        body = el.body() or (el.x - 1.27, el.y - 1.27, el.x + 1.27, el.y + 1.27)
        for num, net in mine:
            e, o = el.end(num), el.out(num)
            if net is None:
                if (p.ref, num) in ctx.src.nc:
                    sub.ncs.append(e)
                continue
            if net not in ri:
                ctx.terminate(sub, net, e, o, el.box())
                continue
            y = ry[net]
            if abs(e[1] - y) < 1e-6:
                mine_attach.append((net, e[0]))
            elif o[0]:
                s1 = (round(e[0] + o[0] * STUB, 4), e[1])
                sub.wire(e, s1)
                sub.wire(s1, (s1[0], y))
                mine_attach.append((net, s1[0]))
            elif (o[1] < 0 and y < e[1]) or (o[1] > 0 and y > e[1]):
                sub.wire(e, (e[0], y))
                mine_attach.append((net, e[0]))
            else:
                # the row is behind the part: out, around its right side, then to the row
                s1 = (e[0], round(e[1] + o[1] * STUB, 4))
                xd = snap(max(body[2], max(v[0] for v in [el.end(k) for k in el.pins])) + 2 * G, 1.27)
                s2 = (xd, s1[1])
                sub.wire(e, s1)
                sub.wire(s1, s2)
                sub.wire(s2, (xd, y))
                mine_attach.append((net, xd))
        b = sub.box()
        dx = snap(x - b[0], 1.27)
        if b[0] + dx < x - 1e-6:
            dx = round(dx + 1.27, 4)
        sub.move(dx, 0)
        for net, ax in mine_attach:
            attach[net].append(round(ax + dx, 4))
        blk.absorb(sub)
        x = sub.box()[2] + GAP

    for net in order:
        xs = sorted(set(attach[net]))
        if not xs:
            continue
        y = ry[net]
        outside = ctx.is_rail(net) or any((r, pn) not in here for r, pn in ctx.src.nets.get(net, []))
        if outside:
            # the terminator at whichever end of the row has room for it
            kind_ = "hier" if ctx.kind(net) == "hier" else "label"
            boxes = [e.box() for e in blk.els] + [e.box() for e in blk.power]
            pick = None
            for t, rot in ((round(xs[0] - STUB * 2, 4), 180), (round(xs[-1] + STUB * 2, 4), 0)):
                if ctx.is_rail(net):
                    term = ctx.power_el(net, (t, y), (0, 1) if ctx.ground(net) else (0, -1))
                    tb = term.box()
                else:
                    term, tb = None, label_box(kind_, net, t, y, rot)
                tb = check.shrink(tb, 0.3)
                hit = any(check.overlap(tb, bx) for bx in boxes if bx) or \
                    any(check.seg_hits_box(a, c, tb) for a, c in blk.wires)
                if not hit:
                    pick = (t, rot, term)
                    break
            t, rot, term = pick or (round(xs[0] - STUB * 2, 4), 180, None)
            xs = sorted(xs + [t])
            if ctx.is_rail(net):
                blk.power.append(term or ctx.power_el(net, (t, y), (0, 1) if ctx.ground(net) else (0, -1)))
            else:
                blk.labels.append((kind_, net, t, y, rot))
        for a, b in zip(xs, xs[1:]):
            blk.wire((a, y), (b, y))
    if free:
        fb = stack(ctx, free)
        b0, fb0 = blk.box(), fb.box()
        fb.move(snap((b0[0] if blk.els else 0) - fb0[0], 1.27), snap(b0[3] + BLOCK_GAP - fb0[1] if blk.els else -fb0[1]))
        blk.absorb(fb)
    return blk


def stack(ctx, parts, hint=0.0):
    """Parts with no shared net: one per line, each pin to its terminator,
    horizontal when the part reads horizontally."""
    blk = Block("stack", hint)
    y = 0.0
    for p in parts:
        u = next(iter(p.units))
        nums = list(dict.fromkeys(pin.number for pin in p.lib.unit_pins(u)))
        want = {nums[0]: "left", nums[1]: "right"} if len(nums) == 2 else {}
        el = El(p, u, orient(p, want))
        sub = Block()
        sub.els.append(el)
        for num in nums:
            net = ctx.net(p.ref, num)
            e, o = el.end(num), el.out(num)
            if net is None:
                if (p.ref, num) in ctx.src.nc:
                    sub.ncs.append(e)
                continue
            ctx.terminate(sub, net, e, o, el.box())
        b = sub.box()
        sub.move(snap(-b[0], 1.27), snap(y - b[1] + 1.27, 1.27))
        blk.absorb(sub)
        y = sub.box()[3] + GAP
    return blk


def clean(blk):
    """True if no wire of the block crosses a body; a wire leaving one of the
    part's own pins is the part's (its box can reach past a pin end)."""
    for e in blk.els:
        b = e.body()
        if not b:
            continue
        mine = {e.end(k) for k in e.pins}
        bx = check.shrink(b, 0.1)
        for a, c in blk.wires:
            if a in mine or c in mine:
                continue
            if check.seg_hits_box(a, c, bx):
                return False
    return True


# == a sheet =================================================================

def plan_sheet(ctx, name):
    src = ctx.src
    units = [(p, u) for p in src.parts.values() for u, s in p.units.items() if s == name]
    anchors = sorted([(p, u) for p, u in units if not p.small], key=lambda t: (nat(t[0].ref), t[1]))
    smalls = sorted([p for p, u in units if p.small], key=lambda p: nat(p.ref))
    groups = []
    homes = {}
    where_all = {}
    for p, u in anchors:
        blk, where = anchor_block(ctx, p, u)
        k = (p.ref, u)
        homes[k] = dict(block=blk, sats=[])
        where_all[k] = where

    def home_of(nets, also=()):
        """The anchor a block serves: most pins on its signal nets, then on
        its rails (`also`)."""
        score = Counter()
        side = defaultdict(list)
        for k, w in where_all.items():
            for n in nets:
                for s, along in w.get(n, []):
                    score[k] += 10
                    side[k].append((s, along))
            if score.get(k):
                score[k] += sum(len(w.get(n, [])) for n in also)
        score = Counter({k: v for k, v in score.items() if v > 0})
        if not score:
            return None, None, 0.0
        k = max(score, key=lambda k: (score[k], -anchors.index(next(a for a in anchors if (a[0].ref, a[1]) == k))))
        sides = Counter(s for s, _ in side[k])
        s = sides.most_common(1)[0][0]
        s = {"top": "right", "bottom": "right"}.get(s, s)
        hint = sum(a for _, a in side[k]) / len(side[k])
        return k, s, hint

    # clusters of small parts over shared signal nets
    uf = check.UF()
    sig = {}
    for p in smalls:
        u = next(iter(p.units))
        nets = {ctx.net(p.ref, pin.number) for pin in p.lib.unit_pins(u)} - {None}
        sig[p.ref] = {n for n in nets if not ctx.is_rail(n)}
        uf.find(("p", p.ref))
        for n in sig[p.ref]:
            uf.union(("p", p.ref), ("n", n))
    clusters = defaultdict(list)
    for p in smalls:
        clusters[uf.find(("p", p.ref))].append(p)

    merged = defaultdict(list)
    bank_hint = {}
    loose = []
    for cl in clusters.values():
        nets = set().union(*(sig[p.ref] for p in cl))
        rails_of = {ctx.net(p.ref, pin.number) for p in cl for pin in p.lib.unit_pins(next(iter(p.units)))}
        rails_of = {n for n in rails_of if n and ctx.is_rail(n)}
        if len(cl) == 1:
            p = cl[0]
            u = next(iter(p.units))
            ns = [ctx.net(p.ref, pin.number) for pin in p.lib.unit_pins(u)]
            ns = list(dict.fromkeys(ns))
            rails = sorted({n for n in ns if n and ctx.is_rail(n)}, key=lambda n: (ctx.ground(n), nat(n)))
            if len(ns) == 2 and len(rails) == 2:
                k, s, hint = home_of([rails[0]])
                merged[("bank", k, s, rails[0])].append((nat(p.ref), p))
                bank_hint[("bank", k, s, rails[0])] = hint
                continue
            if len(ns) == 2 and len(rails) == 1 and len(nets) == 1 and re.match(r"(L|FB)\d", p.ref):
                k, s, hint = home_of([rails[0]])
                merged[("bank", k, s, rails[0])].append(([""], p))
                bank_hint[("bank", k, s, rails[0])] = hint
                continue
            if len(ns) == 2 and len(rails) == 1 and len(nets) == 1:
                k, s, hint = home_of(nets, rails)
                merged[("pull", k, s, rails[0])].append((hint, p))
                continue
            if len(ns) == 2 and not rails and len(nets) == 2:
                k, s, hint = home_of(nets)
                merged[("series", k, s)].append((hint, p))
                continue
            if len(ns) <= 2:
                k, s, hint = home_of(nets, rails_of)       # a test point, a single pin to a rail
                merged[("misc", k, s)].append((hint, p))
                continue
        k, s, hint = home_of(nets, rails_of)
        b = ladder(ctx, cl, hint)
        if not clean(b):
            b = stack(ctx, cl, hint)
        merged_key = ("cluster", k, s, cl[0].ref)
        merged[merged_key].append((hint, b))

    blocks = []
    for key_, items in merged.items():
        kind, k, s = key_[0], key_[1], key_[2]
        items.sort(key=lambda t: (t[0], nat(getattr(t[1], "ref", ""))))
        if kind == "cluster":
            for hint, b in items:
                blocks.append((k, hint, b, kind))
            continue
        ps = [p for _, p in items]
        hint = sum(h for h, _ in items) / len(items) if kind != "bank" else bank_hint[key_]
        if kind == "series":
            blocks.append((k, hint, stack(ctx, ps, hint), kind))
            continue
        if kind == "misc":
            for h_, p_ in items:
                blocks.append((k, h_, stack(ctx, [p_], h_), kind))
            continue
        for i in range(0, len(ps), BANK_MAX):
            chunk = ps[i:i + BANK_MAX]
            b = ladder(ctx, chunk, hint, kind)
            if not clean(b):
                b = stack(ctx, chunk, hint)
            blocks.append((k, hint, b, kind))

    for k, hint, b, kind in blocks:
        if k is None:
            loose.append(b)
        else:
            homes[k]["sats"].append((kind, hint, b))

    # every anchor is a section of its own; its satellites are one section
    # per kind beside it, sized to its height, so the sheet reads as titled
    # blocks joined by labels and rails
    order = sorted(homes, key=lambda k: (-src.parts[k[0]].pin_count(k[1]), nat(k[0]), k[1]))
    for (ref, u) in order:
        h = homes[(ref, u)]
        part = src.parts[ref]
        unit = f" unit {u}" if len(part.lib.units()) > 1 else ""
        a = section(f"{ref}{unit} — {part.value}", [h["block"]])
        ab = a.box()
        by_kind = defaultdict(list)
        for kind, hint, b in h["sats"]:
            by_kind[kind].append((hint, b))
        home = [a]
        for kind in ("bank", "pull", "series", "cluster", "misc"):
            items = sorted(by_kind.get(kind, []), key=lambda t: t[0])
            if not items:
                continue
            if kind == "cluster":
                small_, big_ = [b for _, b in items if len(b.els) <= 2], [b for _, b in items if len(b.els) > 2]
                for b in big_:
                    home.append(section(cluster_title(ref, b), [b]))
                if len(small_) == 1:
                    home.append(section(cluster_title(ref, small_[0]), small_))
                elif small_:
                    refs = sorted({e.ref for b in small_ for e in b.els if e.ref}, key=nat)
                    t = ", ".join(refs) if len(refs) <= 5 else f"{refs[0]} … {refs[-1]} ({len(refs)} parts)"
                    home.append(balanced(f"{ref}: {t}", small_, ab[3] - ab[1]))
                continue
            bs = [b for _, b in items]
            if kind == "bank":
                what = "output filters and decoupling" if any(re.match(r"(L|FB)\d", e.ref or "")
                                                               for b in bs for e in b.els) else "decoupling"
            elif kind == "pull":
                what = "pull-ups and pull-downs"
            elif kind == "series":
                what = "series parts"
            else:
                refs = [e.ref for b in bs for e in b.els]
                what = "test points" if all(r.startswith("TP") for r in refs) else "other parts"
            home.append(balanced(f"{ref} {what}", bs, ab[3] - ab[1]))
        home[1:] = sorted(home[1:], key=lambda g: (not g.title.endswith("decoupling"), -(g.box()[3] - g.box()[1])))
        groups.append(home)
    if loose:
        # a homeless cluster led by an IC-like part (a small regulator, a
        # transistor) is its own section; the rest share one
        rest = []
        for b in loose:
            lead = {e.ref for e in b.els if e.ref and not re.match(r"(R|C|L|FB|TP|H|FID|J)\d", e.ref)}
            if len(lead) == 1 and len(b.els) > 1:
                e = next(e for e in b.els if e.ref in lead)
                groups.append([section(f"{e.ref} {(e.value or '').split()[0] if e.value else ''}".rstrip(), [b])])
            else:
                rest.append(b)
        if rest:
            groups.append([balanced("Other parts", rest, 120.0)])
    flags = flag_block(ctx, name)
    if flags:
        groups.append([section("Power sources", [flags])])
    return pack(groups)


def cluster_title(home, blk):
    """A wired cluster is named by the part that is not an R, C or L (a
    crystal, a diode, a transistor) with its value's first word, else by its
    parts."""
    refs = sorted({e.ref for e in blk.els if e.ref}, key=nat)
    lead = [e for e in blk.els if e.ref and not re.match(r"(R|C|L|FB)\d", e.ref)]
    if len({e.ref for e in lead}) == 1:
        e = lead[0]
        return f"{home}: {e.ref} {(e.value or '').split()[0] if e.value else ''}".rstrip()
    return f"{home}: {', '.join(refs[:5])}{', …' if len(refs) > 5 else ''}"


def section(title, blocks, width=None):
    """Blocks packed bottom-left, tallest first, into `width` (unbounded by
    default), inside a titled frame: the frame clears the content by PAD,
    the title band sits in its top-left corner, and the frame is wide
    enough for the title."""
    blocks = sorted(blocks, key=lambda b: -(b.box()[3] - b.box()[1]))
    boxes = [b.box() for b in blocks]
    pos, _ = skyline([(b[2] - b[0], b[3] - b[1]) for b in boxes], width or 1e9, BLOCK_GAP)
    g = Block("section")
    for b, bb, (x, y) in zip(blocks, boxes, pos):
        b.move(snap(x - bb[0], G), snap(y - bb[1], G))
        g.absorb(b)
    b = g.box()
    tw = text_w(title, TITLE_SIZE) * 1.1 + 2 * PAD + 2.54
    x0, y0 = b[0] - PAD, b[1] - PAD - TITLE_H
    x1, y1 = max(b[2] + PAD, x0 + tw), b[3] + PAD
    g.title = title
    g.frame = (math.floor(x0 / 1.27) * 1.27, math.floor(y0 / 1.27) * 1.27,
               math.ceil(x1 / 1.27) * 1.27, math.ceil(y1 / 1.27) * 1.27)
    g.frame = tuple(round(v, 4) for v in g.frame)
    return g


def balanced(title, blocks, target_h):
    """A section packed to a width that keeps it about `target_h` tall (or
    about square when there is more than that), so a satellite section
    stands beside its anchor instead of under it."""
    boxes = sorted([b.box() for b in blocks], key=lambda b: -(b[3] - b[1]))
    items = [(b[2] - b[0], b[3] - b[1]) for b in boxes]
    area = sum((w + BLOCK_GAP) * (h + BLOCK_GAP) for w, h in items)
    target_h = max(target_h, math.sqrt(area))
    w = max(w for w, _ in items)
    while w < 254.0:
        _, used = skyline(items, w, BLOCK_GAP)
        if used + 2 * PAD + TITLE_H <= target_h * 1.15:
            break
        w += 12.7
    return section(title, blocks, w)


def flag_block(ctx, name):
    """PWR_FLAGs for the rails this sheet draws that carry one."""
    want = []
    for n in sorted(ctx.src.flags, key=nat):
        sheets = ctx.sheets_of.get(n, set())
        home = min(sheets, key=lambda s: [x for x, _ in ctx.src.sheets].index(s)) if sheets else None
        if home == name:
            want.append(n)
    if not want:
        return None
    b = Block("flags")
    x, prev = 0.0, None
    for n in want:
        if prev is not None:
            x += snap(max(text_w(prev), text_w("PWR_FLAG")) / 2 + max(text_w(n), text_w("PWR_FLAG")) / 2 + 3.81)
        prev = n
        top, bot = (x, 0.0), (x, 5.08)
        # the rail's glyph and the flag at the two ends of one short wire, each
        # pointing away from it: a ground's glyph below, any other's above
        g_at, f_at = (bot, top) if ctx.ground(n) else (top, bot)
        if ctx.is_rail(n):
            b.power.append(ctx.power_el(n, g_at, (0, 1) if ctx.ground(n) else (0, -1)))
        else:
            b.labels.append(("hier" if ctx.kind(n) == "hier" else "label", n, g_at[0], g_at[1], 90))
        b.wire(top, bot)
        lib_id, lib = ctx.power_lib.get("PWR_FLAG")
        e = El(None, 1, 0 if ctx.ground(n) else 180, f_at[0], f_at[1], value="PWR_FLAG", lib_id="power:PWR_FLAG",
               lib=ctx.src.libs.get("power:PWR_FLAG") or lib)
        e.rail = None
        b.power.append(e)
    return b


class Skyline:
    """Bottom-left packing into a strip `aw` wide with `gap` between boxes:
    a box goes where the skyline under it is lowest (leftmost on a tie) --
    or, given `near` boxes, next to one of them if any spot touches one."""

    def __init__(self, aw, gap):
        self.aw, self.gap = aw + gap, gap
        self.sky = [(0.0, self.aw, 0.0)]            # (x, width, top)

    def place(self, w, h, near=()):
        """(x, y) for a w x h box, or None if it is wider than the strip."""
        gap = self.gap
        w, h = w + gap, h + gap
        best = None
        for i, (sx, sw, st) in enumerate(self.sky):
            if sx + w > self.aw + 1e-6:
                continue
            top, x1, j = st, sx + w, i
            while x1 > self.sky[j][0] + self.sky[j][1] + 1e-6:
                j += 1
                top = max(top, self.sky[j][2])
            box = (sx, top, sx + w - gap, top + h - gap)
            far = 0 if any(_gap(box, nb) <= gap + 0.01 for nb in near) else 1
            if best is None or (far, top, sx) < best[0]:
                best = ((far, top, sx), (sx, top))
        if best is None:
            return None
        x, top = best[1]
        new = []
        for sx, sw, st in self.sky:
            if sx + sw <= x + 1e-6 or sx >= x + w - 1e-6:
                new.append((sx, sw, st))
                continue
            if sx < x:
                new.append((sx, x - sx, st))
            if sx + sw > x + w:
                new.append((x + w, sx + sw - x - w, st))
        new.append((x, w, top + h))
        self.sky = sorted(new)
        return x, top

    def used(self):
        return max(st for _, _, st in self.sky) - self.gap


def _gap(a, b):
    return math.hypot(max(b[0] - a[2], 0, a[0] - b[2]), max(b[1] - a[3], 0, a[1] - b[3]))


def skyline(items, aw, gap):
    """[(x, y)] for (w, h) boxes packed in order, and the height used; (None,
    None) if one is wider than `aw`."""
    s = Skyline(aw, gap)
    pos = []
    for w, h in items:
        at = s.place(w, h)
        if at is None:
            return None, None
        pos.append(at)
    return pos, s.used()


def pack(groups):
    """`groups` is a list of homes, each a list of sections: the anchor first,
    its satellites after it. The sections are packed bottom-left in that
    order, a satellite next to a section of its own home whenever a spot
    touches one, on the smallest ISO paper that holds them, the title
    block's corner kept clear."""
    for paper in ISO:
        pw, ph = model.paper_size(paper)
        aw, ah = pw - 2 * FRAME - 2 * MARGIN, ph - 2 * FRAME - 2 * MARGIN - TITLE_BLOCK[1]
        sky = Skyline(aw, GROUP_GAP)
        pos, ok = [], True
        for gs in groups:
            placed = []
            for g in gs:
                b = g.box()
                w, h = b[2] - b[0], b[3] - b[1]
                at = sky.place(w, h, placed)
                if at is None:
                    ok = False
                    break
                placed.append((at[0], at[1], at[0] + w, at[1] + h))
                pos.append((g, b, at))
            if not ok:
                break
        if not ok:
            continue
        if sky.used() <= ah or paper == ISO[-1]:
            ox, oy = FRAME + MARGIN, FRAME + MARGIN
            sheet = Block("sheet")
            for g, b, (x, y) in pos:
                g.move(snap(ox + x - b[0] + 1.27, G), snap(oy + y - b[1] + 1.27, G))
                sheet.absorb(g)
            return paper, sheet
    raise AssertionError


# == the root ================================================================

def plan_root(ctx, sub):
    """Sheet symbols, each with a pin per hierarchical label of its sheet and
    a label on the root at each pin."""
    src = ctx.src
    syms = []
    for name, file in src.sheets[1:]:
        labels = sorted({l[1] for l in sub[name][1].labels if l[0] == "hier"}, key=nat)
        if not labels:
            labels = []
        peers = {n: sorted(ctx.sheets_of[n] - {name}) for n in labels}
        labels.sort(key=lambda n: (peers[n], nat(n)))
        half = (len(labels) + 1) // 2
        left, right = labels[:half], labels[half:]
        longest = max([text_w(n) for n in labels] + [0])
        w = snap(max(text_w(name) + 5.08, 2 * longest + 10.16, 38.1))
        h = snap(max(len(left), len(right), 2) * G + 5.08)
        syms.append(dict(name=name, file=file, w=w, h=h, left=left, right=right))
    groups = []
    for s in syms:
        b = Block("sheet")
        b.sheet = s
        lw = max([text_w(n) + 4 for n in s["left"]] + [0])
        rw = max([text_w(n) + 4 for n in s["right"]] + [0])
        b.extra.append((-lw - STUB, -5.0, s["w"] + rw + STUB, s["h"] + 4.0))
        s["pins"] = []
        for side, names in (("left", s["left"]), ("right", s["right"])):
            for i, n in enumerate(names):
                y = G * (i + 1) + 1.27
                x = 0.0 if side == "left" else s["w"]
                o = (-1, 0) if side == "left" else (1, 0)
                s["pins"].append(dict(name=n, x=x, y=y, side=side))
                end = (round(x + o[0] * STUB, 4), y)
                b.wire((x, y), end)
                b.labels.append(("label", n, end[0], end[1], label_rot(o)))
        groups.append([b])
    paper, sheet = pack(groups)
    # recover each sheet's offset from its reserved box
    for (g,) in groups:
        s = g.sheet
        bx = g.extra[0]
        lw = max([text_w(n) + 4 for n in s["left"]] + [0])
        s["x"], s["y"] = round(bx[0] + lw + STUB, 4), round(bx[1] + 5.0, 4)
        for p in s["pins"]:
            p["x"], p["y"] = round(p["x"] + s["x"], 4), round(p["y"] + s["y"], 4)
    return paper, sheet, syms


# == output ==================================================================

def run(src, out, only=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    plans = {}
    ctx0 = Ctx(src, None)
    for name, file in src.sheets[1:] if len(src.sheets) > 1 else src.sheets:
        ctx = Ctx(src, name)
        ctx.power_lib = ctx0.power_lib
        plans[name] = plan_sheet(ctx, name)
    root = None
    if len(src.sheets) > 1:
        root = plan_root(ctx0, plans)
    import emit
    emit.write_all(src, plans, root, out, only)
    return emit.prove(src, out)
