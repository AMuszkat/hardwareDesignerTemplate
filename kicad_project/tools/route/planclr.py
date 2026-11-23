#!/usr/bin/env python3
"""planclr.py -- a route.py plan's clearances, judged per item against the board's OWN rules, in
seconds and before the round: the PCIE_85 items route.py check finds only after a two-minute DRC.

    tools/kpy tools/route/planclr.py --board B PLAN.json [--pcb SCRATCH.kicad_pcb] [--all] [--no-lanes]
    tools/kpy tools/route/planclr.py --selftest

A DESIGN AID.  It never replaces route.py check, which still runs every round and is still counted.

The plan goes onto the board IN MEMORY, the way route.py writes it: its "rips", "ripvias" and
"polyrips" removed FIRST, by route.py apply's own matching (RBoard.rip_plan: a rip not on the board
stops the run, exit 3, as it stops apply), "nfpvias" retyped, then poly.add, add_track, add_via -- no
zone refill, nothing saved.  Copper the plan removes is no obstacle.  Then, for every plan track, via
and polygon, on every copper layer it is on:

  clearance  the least gap to another net's tracks, vias, pads and copper graphics (the board's
             and the plan's own), against the clearance KiCad would require for that pair on that
             layer: the (constraint clearance ...) rules of <board>.kicad_dru, LAST matching rule
             wins, the condition tried as (A, B) and as (B, A), (layer outer|inner|<name>) honoured,
             (severity ignore) needs nothing; no rule matches -> the stricter of the two nets' class
             clearances (.kicad_pro; a net in several classes takes the clearance of its
             highest-priority class that sets one, Default filling in).  A pad's local clearance
             override takes precedence, floored at Board Setup's minimum clearance.
  hole       copper to another net's drilled hole (a via, a plated or unplated pad), and another
             net's copper to a plan via's hole: the (constraint hole_clearance ...) rules the same
             way, else Board Setup's hole clearance.
  exitlane   when routing/prelayout.json has "exit_lanes": exitlane.py's judge on the board without
             and with the plan, for each entry the plan touches, as route.py check runs it; a ball the
             plan leaves without a lane is an item.

The condition language is read, not guessed: A/B .NetName .NetClass .Type .Layer, == != (strings
case-insensitive, '*' and '?' wildcards as KiCad's), && || ! ( ), intersectsCourtyard (Front/Back,
a reference wildcard or 'A'/'B'), intersectsArea (a rule area on the context layer), isCoupledDiffPair,
inDiffPair, hasNetclass, existsOnLayer.  ANYTHING ELSE -- another property, function or a syntax this
reader does not know -- is not evaluated: a pair whose outcome depends on it is printed as UNJUDGED,
with the values it would need either way, and counts as an item.  A gap that passes every value it
could need, or fails every one, is judged.

Not modelled (route.py check judges them): zone fills (a refill yields to a track), board-edge and
hole-to-hole clearance, copper text, disallow / keep-out areas, net-tie exemptions, track widths.

--pcb: judge that file with the board's rules, project and routing/ folder.  pcbnew finds a board's
project by the board file's own name (<stem>.kicad_pro), so `r_final.kicad_pcb.scratch` loads with no
net classes and every net reads Default -- silently.  The file is staged as <board stem>.kicad_pcb in
a temporary folder OUTSIDE kicad_project/ with links to the board's other files, and read from there.

Output, per (plan item, layer, kind), the worst pair:
    NET LAYER at (x, y) gap G < need N (RULE) vs OTHER            and its other breaches as "also:"
    NET LAYER at (x, y) gap G UNJUDGED: need N1 (RULE1) or N2 (RULE2: condition not evaluated)
--all adds the least margin of every plan item and layer.

Exit 0: no item.  1: items (clearance, hole, UNJUDGED or exitlane).  2: --selftest failed.
3: the board, its rules or the plan could not be read.

--selftest builds a tiny two-layer board in a temporary folder (outline, nets, tracks, a via, a
.kicad_pro with a class "Wide" by pattern, a .kicad_dru with five rules) and runs a hand-counted plan
that must FAIL with the hand's numbers -- 0.275 < 0.3 (netclass Wide), 0.1 < 0.12 (the last matching
rule, not an earlier looser one, not an inner-layer one), a hole at 0.36 < 0.4 (a hole_clearance rule),
a plan via at 0.15 < 0.2 (netclass Default) and one UNJUDGED pair -- and the same plan moved clear,
which must PASS with no item.  Then the evaluator's own cases (diff-pair suffixes, wildcards).
"""
import argparse
import json
import math
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "copper"))
import route as R          # noqa: E402  (RBoard: the plan written in memory as route.py writes it)
import exitlane as EL      # noqa: E402
import poly as POLY        # noqa: E402

boardsel = R.P.boardsel


class _Unknown:
    def __repr__(self):
        return "UNKNOWN"


U = _Unknown()                         # a condition this reader cannot evaluate


class Bad(Exception):
    """The board, its rules or the plan cannot be read (exit 3)."""


# ---------------------------------------------------------------------------------------------
# the .kicad_dru
# ---------------------------------------------------------------------------------------------

UNIT = {"mm": 1.0, "mil": 0.0254, "in": 25.4, "um": 0.001}


def length(s):
    """'0.1016mm', '4mil', '0.1in', '100um', '0.2' (mm) -> mm; anything else (an expression) -> None."""
    m = re.fullmatch(r"\s*([-+]?(?:\d+\.?\d*|\.\d+))\s*(mm|mil|in|um)?\s*", str(s))
    return float(m.group(1)) * UNIT[m.group(2) or "mm"] if m else None


def sexpr(text):
    """The top-level forms of an s-expression text, '#' comment lines dropped; strings unquoted."""
    text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    stack = [[]]
    for t in re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+', text):
        if t == "(":
            stack.append([])
        elif t == ")":
            if len(stack) < 2:
                raise Bad("unbalanced ')' in the .kicad_dru")
            x = stack.pop()
            stack[-1].append(x)
        elif t.startswith('"'):
            stack[-1].append(t[1:-1].replace('\\"', '"').replace("\\\\", "\\"))
        else:
            stack[-1].append(t)
    if len(stack) != 1:
        raise Bad("unbalanced '(' in the .kicad_dru")
    return stack[0]


def load_rules(text):
    """[{name, layer, ctext, cond, cons: {clearance|hole_clearance: mm or None}, ignore}] in file order.
    cond: the parsed condition, None (no condition: always), or a str (why it cannot be read)."""
    rules = []
    for f in sexpr(text):
        if not isinstance(f, list) or not f or f[0] != "rule":
            continue
        r = {"name": f[1] if len(f) > 1 and isinstance(f[1], str) else "?", "layer": None, "ctext": "",
             "cond": None, "cons": {}, "ignore": False}
        for x in f[2:]:
            if not isinstance(x, list) or not x:
                continue
            if x[0] == "layer":
                r["layer"] = x[1] if len(x) == 2 and isinstance(x[1], str) else ("?", x[1:])
            elif x[0] == "condition" and len(x) > 1:
                r["ctext"] = x[1]
                try:
                    r["cond"] = parse(x[1])
                except ValueError as ex:
                    r["cond"] = str(ex)
            elif x[0] == "constraint" and len(x) > 1 and x[1] in ("clearance", "hole_clearance"):
                v = None
                for y in x[2:]:
                    if isinstance(y, list) and len(y) == 2 and y[0] == "min":
                        v = length(y[1])
                r["cons"][x[1]] = v
            elif x[0] == "severity":
                r["ignore"] = len(x) > 1 and x[1] == "ignore"
        if r["cons"]:
            rules.append(r)
    return rules


# ---------------------------------------------------------------------------------------------
# the condition language: lexer, parser, three-valued evaluator
# ---------------------------------------------------------------------------------------------

_TOK = re.compile(r"\s*(?:((?:\d+\.?\d*|\.\d+)(?:mm|mil|in|um|deg)?)|('(?:[^'\\]|\\.)*')|([A-Za-z_]\w*)"
                  r"|(==|!=|<=|>=|&&|\|\||[<>!().,+\-*/]))")


def lex(s):
    out, i, s = [], 0, s.strip()
    while i < len(s):
        m = _TOK.match(s, i)
        if not m or m.end() == i:
            raise ValueError(f"cannot read {s[i:i + 20]!r}")
        i = m.end()
        num, st, ident, op = m.groups()
        out.append(("num", num) if num is not None else ("str", st[1:-1]) if st is not None
                   else ("id", ident) if ident is not None else ("op", op))
    return out


class _Parser:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, kind, val=None):
        k, v = self.peek()
        if k != kind or val is not None and v != val:
            raise ValueError(f"expected {val or kind}, read {v!r}")
        self.i += 1
        return v

    def expr(self):
        xs = [self.conj()]
        while self.peek() == ("op", "||"):
            self.i += 1
            xs.append(self.conj())
        return xs[0] if len(xs) == 1 else ("or", xs)

    def conj(self):
        xs = [self.neg()]
        while self.peek() == ("op", "&&"):
            self.i += 1
            xs.append(self.neg())
        return xs[0] if len(xs) == 1 else ("and", xs)

    def neg(self):
        if self.peek() == ("op", "!"):
            self.i += 1
            return ("not", self.neg())
        a = self.atom()
        k, v = self.peek()
        if k == "op" and v in ("==", "!=", "<", "<=", ">", ">="):
            self.i += 1
            return ("cmp", v, a, self.atom())
        if k == "op" and v in "+-*/":
            raise ValueError(f"arithmetic ({v}) is not read")
        return a

    def atom(self):
        k, v = self.peek()
        if (k, v) == ("op", "("):
            self.i += 1
            x = self.expr()
            self.take("op", ")")
            return x
        if k in ("str", "num"):
            self.i += 1
            return (k, v)
        if k == "id":
            self.i += 1
            if self.peek() != ("op", "."):
                return ("id", v)
            self.i += 1
            name = self.take("id")
            if self.peek() != ("op", "("):
                return ("prop", v, name)
            self.i += 1
            args = []
            while self.peek() != ("op", ")"):
                args.append(self.expr())
                if self.peek() == ("op", ","):
                    self.i += 1
                elif self.peek() != ("op", ")"):
                    raise ValueError(f"expected , or ) in {name}(...)")
            self.take("op", ")")
            return ("call", v, name, args)
        raise ValueError(f"unexpected {v!r}")


def parse(text):
    p = _Parser(lex(text))
    x = p.expr()
    if p.i != len(p.t):
        raise ValueError(f"unexpected {p.t[p.i][1]!r}")
    return x


_WILD = {}


def wild(pat, s, case=False):
    """KiCad's WildCompareString / wxString::Matches: '*' any run, '?' one character."""
    k = (pat, case)
    if k not in _WILD:
        _WILD[k] = re.compile("".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pat),
                              re.S | (0 if case else re.I))
    return _WILD[k].fullmatch(s) is not None


def dp_suffix(name):
    """KiCad's DRC_ENGINE::MatchDpSuffix: (polarity, complement net, base name) or (0, None, None).
    Trailing digits and '_' are skipped; then P/+ (1) or N/- (-1)."""
    count = 0
    for ch in reversed(name):
        count += 1
        if ch.isdigit() and ch.isascii() or ch == "_":
            continue
        comp = {"+": ("-", 1), "-": ("+", -1), "P": ("N", 1), "N": ("P", -1)}.get(ch)
        if comp is None:
            return 0, None, None
        base = name[:len(name) - count]
        return comp[1], base + comp[0] + name[len(name) - count + 1:], base
    return 0, None, None


class Lit(str):
    """A string literal of the condition (KiCad tests a wildcard only on the right-hand literal)."""


class NC:
    """A NetClass value: equal to a literal naming one of its classes, or matching its full name."""

    def __init__(self, it):
        self.names, self.full = it.classes, it.classname

    def eq(self, other):
        if isinstance(other, NC):
            return self.full == other.full
        if isinstance(other, str):
            if other in self.names:
                return True
            return wild(other, self.full) if isinstance(other, Lit) and ("*" in other or "?" in other) \
                else self.full.lower() == other.lower()
        return False


def _truth(v):
    if v is U or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return U


class Ctx:
    """The board's rules, classes and geometry the evaluator needs."""

    def __init__(self, B, rules_text):
        pn, b = B.pn, B.b
        self.pn, self.b, self.mm = pn, b, pn.ToMM
        ds = b.GetDesignSettings()
        self.min_clr = pn.ToMM(ds.m_MinClearance)
        self.hole_clr = pn.ToMM(ds.m_HoleClearance)
        self.eps_nm = ds.GetDRCEpsilon()
        self.eps = pn.ToMM(self.eps_nm)
        self.rules = load_rules(rules_text) if rules_text else []
        pro = next(Path(B.dir).glob("*.kicad_pro"), None)
        j = json.loads(pro.read_text()) if pro else {}
        self.cls = {c.get("name"): c for c in j.get("net_settings", {}).get("classes", [])}
        self.fps = {f.GetReference(): f for f in b.GetFootprints()}
        for f in b.GetFootprints():
            f.BuildCourtyardCaches()
        self.zones = list(b.Zones()) + [z for f in b.GetFootprints() for z in f.Zones()]
        self.outer = {pn.F_Cu, pn.B_Cu}
        self._cache = {}

    # -- net classes --
    def class_clr(self, it):
        cs = sorted((self.cls[n] for n in it.classes if n in self.cls), key=lambda c: c.get("priority", 1 << 31))
        for c in cs:
            if c.get("clearance") is not None:
                return float(c["clearance"]), c.get("name")
        d = self.cls.get("Default", {})
        return float(d.get("clearance", 0.0)), "Default"

    # -- the context functions --
    def courtyard(self, it, L, pat, sides, A, B):
        if pat in ("A", "B"):
            ref = (A if pat == "A" else B).fp
            fps = [self.fps[ref]] if ref in self.fps else []
        elif pat.startswith("${"):
            return U
        else:
            fps = [f for r, f in self.fps.items() if wild(pat, r, case=True)]
        k = ("cy", it.uid, L, pat, sides)
        if k not in self._cache:
            sh, hit = it.shape(L), False
            for f in fps:
                for side in sides:
                    poly = f.GetCourtyard(side)
                    if poly.OutlineCount() and poly.Collide(sh, 0):
                        hit = True
                        break
                if hit:
                    break
            self._cache[k] = hit
        return self._cache[k]

    def area(self, it, L, pat):
        k = ("ar", it.uid, L, pat)
        if k not in self._cache:
            hit = False
            for z in self.zones:
                if not (wild(pat, z.GetZoneName(), case=True) or z.m_Uuid.AsString() == pat):
                    continue
                if not z.IsOnLayer(L):
                    continue
                ol = self._cache.get(("ol", z.m_Uuid.AsString()))
                if ol is None:
                    ol = self.pn.SHAPE_POLY_SET(z.Outline())
                    try:                              # KiCad deflates the area by the DRC epsilon:
                        ol.Deflate(self.eps_nm, self.pn.CORNER_STRATEGY_ALLOW_ACUTE_CORNERS, self.pn.ARC_LOW_DEF)
                    except (AttributeError, TypeError):   # touching is not intersecting
                        pass
                    self._cache[("ol", z.m_Uuid.AsString())] = ol
                if ol.Collide(it.shape(L), 0):
                    hit = True
                    break
            self._cache[k] = hit
        return self._cache[k]

    # -- evaluation --
    def ev(self, n, A, B, L):
        tag = n[0]
        if tag in ("or", "and"):
            unk = False
            for x in n[1]:
                v = _truth(self.ev(x, A, B, L))
                if v is U:
                    unk = True
                elif v is (tag == "or"):
                    return v
            return U if unk else tag == "and"
        if tag == "not":
            v = _truth(self.ev(n[1], A, B, L))
            return U if v is U else not v
        if tag == "str":
            return Lit(n[1])
        if tag == "num":
            v = length(n[1])
            return U if v is None else v
        if tag == "cmp":
            a, c = self.ev(n[2], A, B, L), self.ev(n[3], A, B, L)
            if a is U or c is U:
                return U
            if n[1] in ("==", "!="):
                if isinstance(a, NC):
                    e = a.eq(c)
                elif isinstance(c, NC):
                    e = c.eq(a)
                elif isinstance(a, str) and isinstance(c, str):
                    e = wild(c, a) if isinstance(c, Lit) and ("*" in c or "?" in c) else a.lower() == c.lower()
                elif isinstance(a, (int, float)) and isinstance(c, (int, float)):
                    e = a == c
                else:
                    return U
                return e if n[1] == "==" else not e
            if isinstance(a, (int, float)) and isinstance(c, (int, float)) and not isinstance(a, bool):
                return {"<": a < c, "<=": a <= c, ">": a > c, ">=": a >= c}[n[1]]
            return U
        if tag == "prop":
            it = {"A": A, "B": B}.get(n[1])
            if it is None:
                return U
            p = n[2].lower().replace("_", "")
            if p == "netname":
                return it.net
            if p == "netclass":
                return NC(it)
            if p == "type":
                return it.kind
            if p == "layer" and it.kind in ("Track", "Graphic"):
                return self.b.GetLayerName(it.o.GetLayer())
            return U
        if tag == "call":
            it = {"A": A, "B": B}.get(n[1])
            if it is None:
                return U
            args = [self.ev(x, A, B, L) for x in n[3]]
            if any(a is U for a in args):
                return U
            f, pn = n[2].lower(), self.pn
            sides = {"intersectscourtyard": (pn.F_CrtYd, pn.B_CrtYd), "insidecourtyard": (pn.F_CrtYd, pn.B_CrtYd),
                     "intersectsfrontcourtyard": (pn.F_CrtYd,), "insidefrontcourtyard": (pn.F_CrtYd,),
                     "intersectsbackcourtyard": (pn.B_CrtYd,), "insidebackcourtyard": (pn.B_CrtYd,)}.get(f)
            if sides and len(args) == 1 and isinstance(args[0], str):
                return self.courtyard(it, L, str(args[0]), sides, A, B)
            if f in ("intersectsarea", "insidearea") and len(args) == 1 and isinstance(args[0], str):
                return self.area(it, L, str(args[0]))
            if f == "iscoupleddiffpair" and not args:
                pol, comp, _ = dp_suffix(A.net) if A.code > 0 else (0, None, None)
                return bool(pol) and B.code > 0 and B.net == comp
            if f == "indiffpair" and len(args) == 1 and isinstance(args[0], str):
                pol, comp, base = dp_suffix(it.net) if it.code > 0 else (0, None, None)
                if not pol or self.b.FindNet(comp) is None:
                    return False
                return wild(args[0], base, case=True) or base.endswith("_") and wild(args[0], base[:-1], case=True)
            if f == "hasnetclass" and len(args) == 1 and isinstance(args[0], str):
                return U if "*" in args[0] or "?" in args[0] else args[0] in it.classes
            if f == "existsonlayer" and len(args) == 1 and isinstance(args[0], str):
                lid = self.b.GetLayerID(str(args[0]))
                return U if lid < 0 else bool(it.o.IsOnLayer(lid))
            return U
        return U                               # a bare identifier

    def layer_ok(self, r, L):
        lay = r["layer"]
        if lay is None:
            return True
        if not isinstance(lay, str):
            return U
        if lay == "outer":
            return L in self.outer
        if lay == "inner":
            return L not in self.outer
        lid = self.b.GetLayerID(lay)
        return U if lid < 0 else lid == L

    def match(self, r, a, c, L):
        lo = self.layer_ok(r, L)
        if lo is False:
            return False
        if r["cond"] is None:
            m = True
        elif isinstance(r["cond"], str):
            m = U
        else:
            m = False
            for A, B in ((a, c), (c, a)):
                v = _truth(self.ev(r["cond"], A, B, L))
                if v is True:
                    m = True
                    break
                if v is U:
                    m = U
        return U if lo is U and m is not False else m

    def required(self, a, c, L, kind):
        """(definite, maybes): definite = (mm or None, name); maybes = [(mm or None, name, rule)], the rules
        hit before it whose condition could not be evaluated."""
        if kind == "clearance":
            ov = [x for x in (a.override, c.override) if x is not None]
            if ov:
                return (max(max(ov), self.min_clr), "local clearance override"), []
        maybes = []
        for r in reversed(self.rules):
            if kind not in r["cons"]:
                continue
            m = self.match(r, a, c, L)
            if m is False:
                continue
            v = 0.0 if r["ignore"] else r["cons"][kind]
            if m is True:
                return (v, r["name"]), maybes
            maybes.append((v, r["name"], r))
        if kind == "hole_clearance":
            return (self.hole_clr, "board hole clearance"), maybes
        (va, na), (vc, nc) = self.class_clr(a), self.class_clr(c)
        return ((va, "netclass " + na) if va >= vc else (vc, "netclass " + nc)), maybes


# ---------------------------------------------------------------------------------------------
# the items
# ---------------------------------------------------------------------------------------------

class It:
    """One copper item, with what the judge asks of it, cached."""

    def __init__(self, ctx, o, fp=None, plan=False):
        pn = ctx.pn
        self.o, self.fp, self.plan = o, fp, plan
        self.uid = o.m_Uuid.AsString()
        t = o.Type()
        self.kind = {pn.PCB_TRACE_T: "Track", pn.PCB_ARC_T: "Track", pn.PCB_VIA_T: "Via",
                     pn.PCB_PAD_T: "Pad", pn.PCB_SHAPE_T: "Graphic"}.get(t, "?")
        self.code = o.GetNetCode()
        self.net = o.GetNetname()
        n = o.GetNet()
        self.classname = ((n.GetNetClassName() if n is not None else "") or "Default")
        self.classes = [c.strip() for c in self.classname.split(",") if c.strip()] or ["Default"]
        try:
            ov = o.GetClearanceOverrides(None) if self.kind == "Pad" else None
        except (AttributeError, TypeError):
            ov = None
        self.override = pn.ToMM(ov) if ov is not None else None
        self.bbox = o.GetBoundingBox()
        self._sh = {}
        self._hole = False
        self.flashing = self.kind in ("Via", "Pad")

    def on(self, L):
        """copper on L"""
        return bool(self.o.IsOnLayer(L) and (not self.flashing or self.o.FlashLayer(L)))

    def drilled_through(self, L):
        if self.hole is None:
            return False
        return bool(self.o.IsOnLayer(L)) if self.kind == "Via" else True

    def shape(self, L):
        if L not in self._sh:
            self._sh[L] = self.o.GetEffectiveShape(L)
        return self._sh[L]

    @property
    def hole(self):
        if self._hole is False:
            h = None
            if self.kind == "Via" or self.kind == "Pad" and self.o.HasHole():
                h = self.o.GetEffectiveHoleShape()
            self._hole = h
        return self._hole

    def label(self, mm):
        n = R.short(self.net) or "<no net>"
        if self.kind == "Via":
            p = self.o.GetPosition()
            return f"via {n} ({mm(p.x):.3f}, {mm(p.y):.3f})"
        if self.kind == "Pad":
            return f"pad {self.fp}.{self.o.GetNumber()} {n}"
        if self.kind == "Track":
            a, c = self.o.GetStart(), self.o.GetEnd()
            return (f"track {n} ({mm(a.x):.4f}, {mm(a.y):.4f})-({mm(c.x):.4f}, {mm(c.y):.4f}) "
                    f"len {math.dist((mm(a.x), mm(a.y)), (mm(c.x), mm(c.y))):.4f}")
        return f"{'polygon' if self.code else 'copper graphic'} {n}" + (f" of {self.fp}" if self.fp else "")


# ---------------------------------------------------------------------------------------------
# the judge
# ---------------------------------------------------------------------------------------------

def plan_in_memory(B, plan):
    """The plan onto B's board as route.py writes it, without the refill: rips, ripvias and polyrips
    removed first by route.py's own matching (a rip that is not there dies, as apply does).
    Returns the new items; B.planclr_removed holds the counts removed."""
    B.planclr_removed = B.rip_plan(plan)
    B.retype_plan(plan)
    made = []
    for q in plan.get("polys", []):
        made.append(POLY.add(B.b, B.pn, q))
    for t in plan.get("tracks", []):
        for a, c in zip(t["pts"], t["pts"][1:]):
            made.append(B.add_track(t["net"], t.get("layer", "F.Cu"), t["width"], a, c))
    for v in plan.get("vias", []):
        made.append(B.add_via(v["net"], v["at"], v.get("pad", 0.6), v.get("drill", 0.3),
                              v.get("top", "F.Cu"), v.get("bottom", "B.Cu"), nfp=bool(v.get("nfp"))))
    return made


def judge_board(B, plan, made, rules_text, out=print, show_all=False):
    """Every plan item against every other-net item near it.  Returns {"items": [...], "least": {...}}."""
    pn, b = B.pn, B.b
    mm = pn.ToMM
    ctx = Ctx(B, rules_text)
    mine = {x.m_Uuid.AsString() for x in made}
    its = []
    for t in b.GetTracks():
        its.append(It(ctx, t, plan=t.m_Uuid.AsString() in mine))
    for f in b.GetFootprints():
        ref = f.GetReference()
        for p in f.Pads():
            its.append(It(ctx, p, fp=ref))
        for g in f.GraphicalItems():
            if g.Type() == pn.PCB_SHAPE_T and g.IsOnCopperLayer():
                its.append(It(ctx, g, fp=ref))
    for d in b.GetDrawings():
        if d.Type() == pn.PCB_SHAPE_T and d.IsOnCopperLayer():
            its.append(It(ctx, d, plan=d.m_Uuid.AsString() in mine))
    plan_its = [x for x in its if x.plan]
    copper = list(b.GetEnabledLayers().CuStack())

    # the widest value any rule or class can ask: no pair farther apart can breach a known value
    vals = [v for r in ctx.rules for v in r["cons"].values() if v is not None]
    vals += [float(c["clearance"]) for c in ctx.cls.values() if c.get("clearance") is not None]
    vals += [ctx.hole_clr, ctx.min_clr]
    W = max(vals) + 0.05
    lows = sorted({(r["name"], v) for r in ctx.rules for k, v in r["cons"].items()
                   if k == "clearance" and v is not None and v < ctx.min_clr - 1e-9})
    for name, v in lows:
        out(f"NOTE rule '{name}' asks {v:.4f} mm, below Board Setup's minimum clearance {ctx.min_clr:.4f}: "
            "judged at the rule's value")
    for r in ctx.rules:
        if isinstance(r["cond"], str):
            out(f"NOTE rule '{r['name']}': condition not read ({r['cond']}) -- a pair it may match is UNJUDGED")

    def gap(s1, s2):
        if s1.Collide(s2, 0):
            return 0.0
        return mm(s1.GetClearance(s2))

    rec = {}             # (plan uid, L, kind) -> [records]
    least = {}           # (plan uid, L) -> (margin, gap, need, rule, other)
    done = {}            # pair key -> evaluated records (a plan-plan pair once)
    pairs = set()        # the breaching (or unjudged) pairs, each once: what KiCad's DRC would list

    def judge_pair(cu, ho, L, kind, g):
        """cu's copper against ho's copper (kind clearance) or ho's hole (kind hole_clearance)."""
        (dv, dname), maybes = ctx.required(cu, ho, L, kind)
        need_known = [v for v, *_ in maybes if v is not None] + ([dv] if dv is not None else [])
        unknown_value = dv is None or any(v is None for v, *_ in maybes)
        if not unknown_value and g >= max(need_known) - ctx.eps:
            return "ok", dv, dname, maybes
        if not unknown_value and g < min(need_known) - ctx.eps:
            lo = min([(dv, dname)] + [(v, nm) for v, nm, _ in maybes])
            return "fail", lo[0], lo[1], maybes
        return "unjudged", dv, dname, maybes

    def record(p, o, L, kind, g, status, need, name, maybes, s1, s2, p_is_first):
        k = (p.uid, L, kind)
        if status == "ok":
            pass
        else:
            a, c = pn.VECTOR2I(0, 0), pn.VECTOR2I(0, 0)
            try:
                s1.NearestPoints(s2, a, c)
                at = (mm(a.x), mm(a.y)) if p_is_first else (mm(c.x), mm(c.y))
            except Exception:
                bb = p.o.GetBoundingBox()
                at = (mm(bb.Centre().x), mm(bb.Centre().y))
            rec.setdefault(k, []).append({"net": R.short(p.net), "layer": b.GetLayerName(L), "kind": kind,
                                          "gap": g, "need": need, "rule": name, "status": status, "at": at,
                                          "maybes": [(v, nm, r["ctext"]) for v, nm, r in maybes],
                                          "other": o.label(mm), "item": p.label(mm),
                                          "hole_of": "other" if kind == "hole_clearance" and p_is_first else
                                          ("item" if kind == "hole_clearance" else None)})
        if need is not None:
            m = g - need
            k2 = (p.uid, L)
            if k2 not in least or m < least[k2][0]:
                least[k2] = (m, g, need, name, o.label(mm) + (" (hole)" if kind == "hole_clearance" else ""),
                             f"{p.label(mm)} {b.GetLayerName(L)}")

    for p in plan_its:
        bb = pn.BOX2I(p.bbox.GetPosition(), p.bbox.GetSize())
        bb.Inflate(pn.FromMM(W))
        near = [o for o in its if o is not p and not (o.code > 0 and o.code == p.code)
                and o.bbox.Intersects(bb)]
        for L in copper:
            p_cu, p_drill = p.on(L), p.drilled_through(L)
            if not (p_cu or p_drill):
                continue
            for o in near:
                o_cu = o.on(L)
                checks = []
                if p_cu and o_cu:
                    checks.append(("clearance", p, o, True))
                if p_cu and o.drilled_through(L):
                    checks.append(("hole_clearance", p, o, True))       # p's copper, o's hole
                if p_drill and o_cu:
                    checks.append(("hole_clearance", o, p, False))      # o's copper, p's hole
                for kind, cu, ho, p_first in checks:
                    key = (kind, cu.uid, ho.uid, L)
                    if key not in done:
                        s1 = cu.shape(L)
                        s2 = ho.shape(L) if kind == "clearance" else ho.hole
                        if s2 is None:
                            done[key] = None
                            continue
                        g = gap(s1, s2)
                        done[key] = None if g > W else (g, judge_pair(cu, ho, L, kind, g), s1, s2)
                    r = done[key]
                    if r is None:
                        continue
                    g, (status, need, name, maybes), s1, s2 = r
                    # a pair of two plan items is recorded on each, in its own pass (the evaluation is shared)
                    record(p, o, L, kind, g, status, need, name, maybes, s1, s2, p_first)
                    if status != "ok":
                        pairs.add((kind, L) + tuple(sorted((cu.uid, ho.uid))) if kind == "clearance"
                                  else (kind, L, cu.uid, ho.uid))
    return {"items": rec, "least": least, "n": len(plan_its), "ctx": ctx, "pairs": pairs}


def fmt(r):
    where = f"{r['net']} {r['layer']} at ({r['at'][0]:.4f}, {r['at'][1]:.4f}) gap {r['gap']:.4f}"
    vs = (f"vs the hole of {r['other']}" if r["hole_of"] == "other" else
          f"vs {r['other']} (to this item's hole)" if r["hole_of"] == "item" else f"vs {r['other']}")
    if r["status"] == "fail":
        tail = "".join(f"; also needs {v:.4f} if UNJUDGED rule '{nm}' matches" for v, nm, _ in r["maybes"]
                       if v is not None)
        return f"{where} < need {r['need']:.4f} ({r['rule']}) {vs}{tail}"
    alts = " or ".join(f"{'?' if v is None else f'{v:.4f}'} ('{nm}': condition not evaluated: {ct})"
                       for v, nm, ct in r["maybes"])
    need = "?" if r["need"] is None else f"{r['need']:.4f}"
    return f"{where} UNJUDGED: need {need} ({r['rule']}) or {alts} {vs}"


def report(res, out=print, show_all=False):
    """Print the items; returns (clearance fails, hole fails, unjudged) counted per item, layer and kind."""
    n = {"clearance": 0, "hole_clearance": 0, "unjudged": 0}
    lines = []
    for k, rs in res["items"].items():
        fails = sorted((r for r in rs if r["status"] == "fail"), key=lambda r: r["gap"] - r["need"])
        unj = [r for r in rs if r["status"] == "unjudged"]
        if fails:
            n[fails[0]["kind"]] += 1
            lines.append((fails[0]["gap"] - fails[0]["need"], fails[0], fails[1:] + unj))
        elif unj:
            n["unjudged"] += 1
            lines.append((0.0, unj[0], unj[1:]))
    for _, r, more in sorted(lines, key=lambda x: (x[1]["status"] != "fail", x[0])):
        out(("  " if r["status"] == "fail" else "  ") + fmt(r))
        for m in more:
            out("      also: " + fmt(m))
    if show_all:
        rows = sorted(res["least"].values(), key=lambda v: v[0])
        out(f"least margin per plan item and layer (gap - need), worst first ({min(len(rows), 40)} of {len(rows)}):")
        for m, g, need, name, other, item in rows[:40]:
            out(f"  {m:+.4f}  {item}: gap {g:.4f} need {need:.4f} ({name}) vs {other}")
    return n


def lanes(B1, plan, out=print):
    """exitlane.py's judge without and with the plan (in memory), as route.py check runs it."""
    try:
        spec = EL.load_spec(B1.dir)
    except EL.Usage as ex:
        raise Bad(f"{EL.SPEC}: {ex}")
    if not spec:
        out("EXITLANE not run: the board's routing/prelayout.json has no \"exit_lanes\"")
        return []
    B0 = R.RBoard(B1.dir)
    items = []
    for e in spec:
        if e["ref"] not in B0.fps:
            raise Bad(f"{EL.SPEC}: \"exit_lanes\" names {e['ref']}, which is not on the board")
        if not EL.plan_touches(B0, e["ref"], plan):
            out(f"EXITLANE {e['ref']}: the plan's copper does not reach its courtyard (+1 mm): not judged")
            continue
        try:
            r0, r1 = EL.judge_entry(B0, e), EL.judge_entry(B1, e)
        except EL.Usage as ex:
            raise Bad(f"{EL.SPEC}: exit_lanes {e['ref']}: {ex}")
        g = r1["gate"]
        new = sorted(set(r1["gate_balls"][g]) - set(r0["gate_balls"][g]))
        out(f"EXITLANE {e['ref']}: {len(r0['gate_balls'][g])} ball(s) without a lane before, "
            f"{len(r1['gate_balls'][g])} with the plan ({len(new)} new)")
        for it in r1["gate_items"][g]:
            ls = [h for h in it["lanes"] if h["lane"]["ball"] in new]
            if ls:
                items.append(f"{EL.item_text(it['item'])} on {r1['layer']} blocks {e['ref']} "
                             + "; ".join(EL.lane_text(h) for h in ls))
                out("  " + items[-1])
    return items


def run(board_dir, plan, plan_name="plan", show_all=False, with_lanes=True, out=print):
    """Judge `plan` on the board in board_dir.  Returns (exit code, result)."""
    B = R.RBoard(Path(board_dir))
    dru = B.pcb.with_suffix(".kicad_dru")
    rules_text = dru.read_text() if dru.exists() else ""
    made = plan_in_memory(B, plan)
    nt = sum(1 for x in made if x.Type() in (B.pn.PCB_TRACE_T, B.pn.PCB_ARC_T))
    nv = sum(1 for x in made if x.Type() == B.pn.PCB_VIA_T)
    res = judge_board(B, plan, made, rules_text, out=out)
    ctx = res["ctx"]
    rm = B.planclr_removed
    out(f"planclr: {plan_name} on {B.pcb}: {len(made)} plan item(s) ({nt} segment(s), {nv} via(s), "
        f"{len(made) - nt - nv} polygon(s)), after removing {rm['rips']} ripped segment(s), {rm['ripvias']} "
        f"via(s), {rm['polyrips']} polygon(s); {sum(1 for r in ctx.rules if 'clearance' in r['cons'])} clearance and "
        f"{sum(1 for r in ctx.rules if 'hole_clearance' in r['cons'])} hole_clearance rule(s) from "
        f"{dru.name if dru.exists() else 'no .kicad_dru'}; board minimum {ctx.min_clr:.4f}, hole {ctx.hole_clr:.4f}")
    if plan.get("zones"):
        out(f"NOTE {len(plan['zones'])} zone(s) in the plan: not judged (route.py check fills and judges them)")
    n = report(res, out=out, show_all=show_all)
    lane_items = lanes(B, plan, out=out) if with_lanes else []
    total = n["clearance"] + n["hole_clearance"] + n["unjudged"] + len(lane_items)
    out(f"PLANCLR {'PASS' if total == 0 else 'FAIL'}: {n['clearance']} clearance + {n['hole_clearance']} hole + "
        f"{n['unjudged']} UNJUDGED (per plan item and layer; {len(res['pairs'])} distinct pair(s)) + "
        f"{len(lane_items)} exitlane item(s) (a design aid: route.py check still runs)")
    return (0 if total == 0 else 1), {"res": res, "n": n, "lanes": lane_items}


def stage(board_dir, pcb):
    """A temporary board folder OUTSIDE kicad_project/: `pcb` as <board stem>.kicad_pcb, links to the
    board's other files (project, rules, routing/), so pcbnew loads its net classes with it."""
    own = boardsel.one_pcb(board_dir)
    pcb = Path(pcb).resolve()
    if not pcb.is_file():
        raise Bad(f"--pcb {pcb}: no such file")
    if pcb == own.resolve():
        return None
    tmp = Path(tempfile.mkdtemp(prefix="planclr_"))
    for p in Path(board_dir).iterdir():      # files COPIED (nothing written here reaches the board),
        if p.name.endswith(".kicad_pcb") or p.name.startswith("~") or p.name.endswith(".lck"):
            continue                          # folders (routing/, lib/) linked: read only
        if p.is_dir():
            (tmp / p.name).symlink_to(p.resolve())
        elif p.is_file() and p.stat().st_size < 8 << 20:
            shutil.copyfile(p, tmp / p.name)
    shutil.copyfile(pcb, tmp / own.name)
    return tmp


# ---------------------------------------------------------------------------------------------
# --selftest
# ---------------------------------------------------------------------------------------------

ST_DRU = """(version 1)
# planclr --selftest: the last matching rule wins; a looser earlier rule and an inner-layer rule must lose
(rule "early loose"
  (constraint clearance (min 0.05mm))
  (condition "A.NetName == 'NA'"))
(rule "A to C"
  (layer outer)
  (constraint clearance (min 0.12mm))
  (condition "A.NetName == '*NA' && B.NetName == 'nc'"))
(rule "inner trap"
  (layer inner)
  (constraint clearance (min 0.5mm))
  (condition "A.NetName == 'NA'"))
(rule "NH hole"
  (constraint hole_clearance (min 0.4mm))
  (condition "B.NetName == 'NH'"))
(rule "odd"
  (constraint clearance (min 0.05mm))
  (condition "A.NetName == 'NU' && A.fromTo('x', 'y')"))
"""


def _fixture(d):
    """A two-layer 10 x 24 mm board: existing ND, NC, NH (a via), ND2 and NE copper; nets W1 (class Wide,
    0.3 by pattern), NA, NX, NU, NV for the plan.  Default 0.2, board hole clearance 0.25."""
    pn = R.P.pcbnew_module()
    FM = pn.FromMM
    path = d / "st.kicad_pcb"
    b = pn.NewBoard(str(path))
    b.SetCopperLayerCount(2)
    for (x0, y0), (x1, y1) in (((-2, -2), (8, -2)), ((8, -2), (8, 22)), ((8, 22), (-2, 22)), ((-2, 22), (-2, -2))):
        s = pn.PCB_SHAPE(b)
        s.SetShape(pn.SHAPE_T_SEGMENT)
        s.SetStart(pn.VECTOR2I(FM(x0), FM(y0)))
        s.SetEnd(pn.VECTOR2I(FM(x1), FM(y1)))
        s.SetLayer(pn.Edge_Cuts)
        s.SetWidth(FM(0.05))
        b.Add(s)
    for n in ("ND", "NC", "NH", "ND2", "NE", "NR", "W1", "NA", "NX", "NU", "NV", "NQ"):
        b.Add(pn.NETINFO_ITEM(b, n))

    def track(net, y, x0=0.0, x1=4.0, w=0.2):
        t = pn.PCB_TRACK(b)
        t.SetStart(pn.VECTOR2I(FM(x0), FM(y)))
        t.SetEnd(pn.VECTOR2I(FM(x1), FM(y)))
        t.SetWidth(FM(w))
        t.SetLayer(pn.F_Cu)
        t.SetNet(b.FindNet(net))
        b.Add(t)
    track("ND", 0.0)
    track("NC", 5.0)
    track("ND2", 15.0)
    track("NE", 20.55)
    track("NR", 3.0)                     # NR's track and via: copper the plan rips
    for net, y in (("W1", 0.0), ("NQ", 3.0), ("NA", 5.0), ("NX", 10.0), ("NU", 15.0), ("NV", 20.0)):
        track(net, y, -1.5, -1.3)        # a stub far off: a net with no copper is not saved

    def via(net, x, y):
        v = pn.PCB_VIA(b)
        v.SetPosition(pn.VECTOR2I(FM(x), FM(y)))
        v.SetWidth(FM(0.6))
        v.SetDrill(FM(0.3))
        v.SetLayerPair(pn.F_Cu, pn.B_Cu)
        v.SetNet(b.FindNet(net))
        b.Add(v)
    via("NH", 2.0, 10.0)
    via("NR", 6.0, 3.0)
    ds = b.GetDesignSettings()
    ds.m_HoleClearance = FM(0.25)
    ds.m_MinClearance = FM(0.05)
    ns = ds.m_NetSettings                # classes through the API, as board_setup.py types them:
    ns.GetDefaultNetclass().SetClearance(FM(0.2))   # a .kicad_pro written beside a NewBoard is not read
    wide = pn.NETCLASS("Wide")
    wide.SetClearance(FM(0.3))
    wide.SetTrackWidth(FM(0.2))
    wide.SetPriority(0)
    ns.SetNetclass("Wide", wide)
    ns.SetNetclassPatternAssignment("W1", "Wide")
    b.SynchronizeNetsAndNetClasses(True)
    pn.SaveBoard(str(path), b)
    (d / "st.kicad_dru").write_text(ST_DRU)


def _st_plan(clear, rips=True):
    """The hand's plan: every case 5 mm from the next.  `clear`: the same plan moved clear.
    NQ runs over NR's track and through NR's via (y 3, x 0..6), which the plan rips: no item, ever;
    rips=False keeps them and must report them (the case is live)."""
    def tr(net, y, x1=4.0):
        return {"net": net, "layer": "F.Cu", "width": 0.2, "pts": [[0.0, y], [x1, y]]}
    rip = {"rips": [{"net": "NR", "layer": "F.Cu", "pts": [[0.0, 3.0], [4.0, 3.0]], "width": 0.2}],
           "ripvias": [{"net": "NR", "at": [6.0, 3.0]}]} if rips else {}
    return {"group": "selftest", **rip, "tracks": [tr("NQ", 3.1, 7.0),
        tr("W1", 0.52 if clear else 0.475),    # vs ND at y 0:  gap 0.32 / 0.275, netclass Wide 0.3
        tr("NA", 5.35 if clear else 5.30),     # vs NC at y 5:  gap 0.15 / 0.10, rule "A to C" 0.12
        tr("NX", 10.70 if clear else 10.61),   # NH via at y 10: pad gap 0.30 / 0.21 (0.2), hole 0.45 / 0.36 (0.4)
        tr("NU", 15.45 if clear else 15.30)],  # vs ND2 at y 15: gap 0.25 / 0.10: 0.2, or 0.05 ("odd")
        "vias": [{"net": "NV", "at": [2.0, 19.9 if clear else 20.0], "pad": 0.6, "drill": 0.3}]}
    #                                          vs NE at y 20.55: gap 0.25 / 0.15, Default 0.2; hole 0.40 / 0.30 (0.25)


def selftest():
    errs = []
    # the evaluator's own cases
    for name, want in (("PCIE_TX_P", "PCIE_TX_N"), ("/a/USB_D+", "/a/USB_D-"), ("X_P2", "X_N2"),
                       ("CLK_N_1", "CLK_P_1"), ("DRAM_CK_T_A", None), ("GND", None)):
        got = dp_suffix(name)[1]
        if got != want:
            errs.append(f"dp_suffix({name!r}) = {got!r}, want {want!r}")
    for pat, s, want in (("*EMMC_*", "/emmc/eMMC_D0", True), ("*DRAM_CK_T_A", "/LPDDR4/DRAM_CK_T_A", True),
                         ("PCIE_?X_P", "PCIE_TX_P", True), ("*EMMC_*", "/SD/SD_D0", False)):
        if wild(pat, s) != want:
            errs.append(f"wild({pat!r}, {s!r}) != {want}")
    for text, ok in (("A.NetClass == 'SE_50' && !A.intersectsCourtyard('U1')", True),
                     ("(A.NetName == 'a' || B.NetName == 'b') && A.Type == 'Via'", True),
                     ("A.Width + 0.1mm > 0.2mm", False), ("A.NetName == ", False)):
        try:
            parse(text)
            got = True
        except ValueError:
            got = False
        if got != ok:
            errs.append(f"parse({text!r}) {'read' if got else 'refused'}")

    want_fail = sorted([("W1", "F.Cu", "clearance", 0.275, 0.3, "netclass Wide", "fail"),
                        ("NA", "F.Cu", "clearance", 0.1, 0.12, "A to C", "fail"),
                        ("NX", "F.Cu", "hole_clearance", 0.36, 0.4, "NH hole", "fail"),
                        ("NV", "F.Cu", "clearance", 0.15, 0.2, "netclass Default", "fail"),
                        ("NU", "F.Cu", "clearance", 0.1, 0.2, "netclass Default", "unjudged")])
    td = Path(tempfile.mkdtemp(prefix="planclr_st_"))
    try:
        _fixture(td)
        for clear in (False, True):
            print(f"-- selftest plan {'moved clear (must PASS)' if clear else 'hand-counted (must FAIL)'}")
            try:
                code, r = run(td, _st_plan(clear), plan_name="selftest", with_lanes=True)
            except SystemExit as ex:
                errs.append(f"plan {'clear' if clear else 'hand'}: died ({ex.code})")
                continue
            got = []
            for rs in r["res"]["items"].values():
                for x in rs:
                    got.append((x["net"], x["layer"], x["kind"], round(x["gap"], 4),
                                None if x["need"] is None else round(x["need"], 4), x["rule"], x["status"]))
            got.sort()
            want = [] if clear else want_fail
            if got != want:
                errs.append(f"plan {'clear' if clear else 'hand'}: got {got}, want {want}")
            if code != (0 if clear else 1):
                errs.append(f"plan {'clear' if clear else 'hand'}: exit {code}")
        print("-- selftest plan moved clear, its rips dropped (must report NQ against NR's track, via and hole)")
        try:
            code, r = run(td, _st_plan(True, rips=False), plan_name="selftest", with_lanes=False)
        except SystemExit as ex:
            code, r = ex.code, {"res": {"items": {}}}
        got = sorted({(x["net"], x["kind"], x["other"].split(" (")[0]) for rs in r["res"]["items"].values() for x in rs})
        want = [("NQ", "clearance", "track NR"), ("NQ", "clearance", "via NR"), ("NQ", "hole_clearance", "via NR")]
        if got != want or code != 1:
            errs.append(f"rips dropped: got {got} exit {code}, want {want} exit 1")
    finally:
        shutil.rmtree(td, ignore_errors=True)
    for e in errs:
        print("  SELFTEST: " + e)
    print(f"SELFTEST {'PASS' if not errs else 'FAIL'}: {len(errs)} error(s)")
    return 0 if not errs else 2


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan", nargs="?", help="the route.py plan (json)")
    ap.add_argument("--board", help="boardN, the folder name or a path (a scratch copy OUTSIDE kicad_project/)")
    ap.add_argument("--pcb", help="judge this board file with the board's rules and project (staged in a temp folder)")
    ap.add_argument("--all", action="store_true", help="also the least margin of every plan item and layer")
    ap.add_argument("--no-lanes", action="store_true", help="skip the exitlane judge")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.plan:
        ap.error("a plan is needed (or --selftest)")
    tmp = None
    try:
        plan = json.loads(Path(a.plan).read_text())
        board_dir = R.P.board_dir_arg(a.board)
        tmp = stage(board_dir, a.pcb) if a.pcb else None
        code, _ = run(tmp or board_dir, plan, plan_name=a.plan, show_all=a.all, with_lanes=not a.no_lanes)
        return code
    except (Bad, OSError, ValueError) as ex:
        print(f"planclr: {ex}", file=sys.stderr)
        return 3
    except SystemExit as ex:                     # route.py's own die (a rip not on the board, an unknown net)
        return 3 if ex.code not in (0, None) else 0
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
