"""
Publish each board's handoff into `kicad_project/<BOARD>/handoff/`.

The handoff is the COMPLETE design-data package a board session builds from:
components with status, nets with constraints, connectors, layout and
fabrication rules, the datasheets it names, the answers to its questions, and
a changelog that is the only push channel.

WHY GENERATED AND NOT WRITTEN BY HAND: a hand copy diverges silently -- a
value moves in the design, and any copy made before it still reads as
authoritative. So the handoff is regenerated from its sources and carries a
MANIFEST of their hashes.

    python tools/handoff/publish.py --all              # every board
    python tools/handoff/publish.py --board board1     # one board
    python tools/handoff/publish.py --check --all      # stale? exit 1 if so
    python tools/handoff/publish.py --check --board board1

`--check` is for the CONCEPT session (the only side that can see the
sources); run it before answering any question. It RENDERS the handoff again
and compares every page with what is on disk, so "fresh" means the folder is
byte-for-byte what the sources produce today: a source that moved, a page
somebody edited or deleted, a datasheet that was revised all read STALE. It
prints one line per board first -- `boardN: fresh (rev R)` or
`boardN: STALE — <pages>` -- and exits 1 if any board is stale or broken.

A publish is atomic: the whole folder is built beside the old one and swapped
in, so a crash never leaves half a handoff.

The engine is board-blind. Everything board-specific lives in
`boards/boardN.py` (data), `studies/components_boardN.py` (values),
`answers/boardN.md` (answers) and the fab profile the board names in
`fabs/`. The registry of boards IS the `boards/` directory.
"""
import datetime
import hashlib
import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BOARDS = os.path.join(HERE, "boards")
FABS = os.path.join(HERE, "fabs")
sys.path.insert(0, os.path.join(ROOT, "studies"))     # board modules may import their bus
sys.dont_write_bytecode = True     # no __pycache__ inside boards/ or studies/ from a publish

DS_INDEX = "reference/vendor/datasheets.json"
AMEND_MIN = 20          # a republish this soon that moves no NEW source amends the last rev
UNSET = "UNSET"


class PublishError(Exception):
    """A board that cannot be published; main() reports it per board."""


def die(msg):
    raise PublishError(msg)


def warn(msg):
    print(f"WARNING: {msg}", file=sys.stderr)


def board_ids():
    """Every board in the registry, in board-number order."""
    if not os.path.isdir(BOARDS):
        return []
    ids = [f[:-3] for f in os.listdir(BOARDS) if re.fullmatch(r"board\d+\.py", f)]
    return sorted(ids, key=lambda b: int(b[5:]))


def _load(path, name):
    if not os.path.exists(path):
        die(f"{os.path.relpath(path, ROOT)} does not exist")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:                     # a broken module is ONE board's problem
        die(f"{os.path.relpath(path, ROOT)} does not load: {type(e).__name__}: {e}")
    return mod


def _hb(b):
    return hashlib.sha256(b).hexdigest()[:16]


def _h(rel):
    """Hash a source, normalising regenerated headers (a fresh '# Date:'
    line on every tool run would otherwise cry stale when nothing moved)."""
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        return None
    b = open(p, "rb").read()
    if rel.endswith(".csv"):
        b = b"\n".join(ln for ln in b.split(b"\n")
                       if not ln.startswith((b"# Date:", b"# Version:")))
    return hashlib.sha256(b).hexdigest()[:16]


def _v(x):
    """A value cell: None is a hole, and says so."""
    return UNSET if x is None or x == "" else str(x)


def _joined(x):
    return ", ".join(x) if isinstance(x, (list, tuple)) else str(x)


# Standard chip packages, and the three families the rule covers: RESISTORS,
# CAPACITORS and FERRITE BEADS (0-ohm links share their land). INDUCTORS are
# not in the set: a power inductor in a "1210" body has its own land. A part in one of these does NOT get its datasheet published and does
# NOT get its footprint verified against one: the land pattern is IPC's,
# KiCad's stock library already has it, and a 20 MB MLCC catalogue in the
# handoff is noise that hides the documents that matter.
# ⚠ DIODES AND EVERYTHING ELSE ARE NOT IN THIS SET, two pads or not -- a
# SOD-323 is within 0.1 mm of an 0805's span and has nothing to do with one.
PASSIVE_PKG = re.compile(r"\b(0201|0402|0603|0805|1206|1210|1812|2010|2512)\b")
PASSIVE_REF = re.compile(r"^(FB|LK|[RC])[_0-9]")

# Reference designators the lint looks for in net text.
REFDES = re.compile(r"\b(?:RN|FB|LK|SW|TP|[UJPRCLDQYXFKT])\d+\b")


class Board:
    def __init__(self, bid):
        self.id = bid
        self.B = _load(os.path.join(BOARDS, bid + ".py"), "handoff_" + bid)
        bus = getattr(self.B, "COMPONENTS_MODULE", "components_" + bid)
        self.bus_rel = f"studies/{bus}.py"
        self.C = _load(os.path.join(ROOT, self.bus_rel), "bus_" + bid)
        self.board = self.B.BOARD
        if not re.fullmatch(bid + r"_[a-z0-9_]+", self.board):
            die(f"{bid}.py: BOARD = '{self.board}' must be '{bid}_<slug>'")
        self.out = os.path.join(ROOT, "kicad_project", self.board)
        self.handoff = os.path.join(self.out, "handoff")
        kp = os.path.join(ROOT, "kicad_project")
        other = [d for d in (os.listdir(kp) if os.path.isdir(kp) else [])
                 if d.startswith(bid + "_") and d != self.board
                 and os.path.isdir(os.path.join(kp, d))]
        if other:
            die(f"{bid}.py: BOARD = '{self.board}' but kicad_project/{other[0]}/ exists -- "
                "a renamed slug would publish into a new folder while the board "
                "session keeps opening the old one; rename the folder too")
        if not getattr(self.B, "TITLE", None):
            die(f"{bid}.py: TITLE is required")
        self._validate()
        self.answers_rel = self.get("ANSWERS") or f"tools/handoff/answers/{bid}.md"
        self.pinout = self.get("PINOUT")
        F = self.get("FAB")
        self.fab = None
        if F:
            prof = F.get("profile")
            if not prof:
                die(f"{bid}.py: FAB names no 'profile' (see fabs/)")
            self.fab_rel = f"tools/handoff/fabs/{prof}.py"
            self.fab = _load(os.path.join(ROOT, self.fab_rel), "fab_" + prof)

    def get(self, name, default=None):
        return getattr(self.B, name, default)

    def _validate(self):
        """Shape errors as messages, not tracebacks from deep in a renderer."""
        def rows(name, seq, sizes):
            for i, r in enumerate(seq):
                if not isinstance(r, (tuple, list)) or len(r) not in sizes:
                    die(f"{self.id}: {name}[{i}] must have "
                        f"{' or '.join(map(str, sizes))} fields, got {r!r:.80}")
        if not hasattr(self.C, "COMPONENTS"):
            die(f"{self.bus_rel}: no COMPONENTS list")
        rows(f"{self.bus_rel} COMPONENTS", self.C.COMPONENTS, (5, 6))
        rows("NETS", self.get("NETS", []), (4,))
        rows("CONNECTORS", self.get("CONNECTORS", []), (4,))
        rows("LAYOUT", self.get("LAYOUT", []), (2,))
        F = self.get("FAB")
        if F:
            rows("FAB netclasses", F.get("netclasses", []), (6,))

    # -- sources ---------------------------------------------------------
    def sources(self):
        # Provenance only: freshness is judged on the rendered pages (check()).
        # A file the board module or the bus IMPORTS goes in the board's SOURCES.
        s = [self.bus_rel, "tools/handoff/publish.py",
             f"tools/handoff/boards/{self.id}.py", self.answers_rel]
        if self.fab:
            s.append(self.fab_rel)
        s += [self.get(a) for a in ("PINOUT", "DIAGRAM", "FLOORPLAN") if self.get(a)]
        s += list(self.get("SOURCES", []))
        return list(dict.fromkeys(s))

    def manifest(self):
        return {rel: _h(rel) for rel in self.sources()}

    def components(self):
        return list(self.C.COMPONENTS)

    def rev(self):
        p = os.path.join(self.handoff, "CHANGELOG.md")
        if not os.path.exists(p):
            return None
        m = re.search(r"^## rev (\d+) -- ", open(p).read(), re.M)
        return int(m.group(1)) if m else None

    # -- 00 ---------------------------------------------------------------
    def rules(self):
        """The rules every board session works under, then the board's own."""
        R = [
            "Do NOT place a part for any line marked PLACEHOLDER in "
            "`01_components.md`. Leave the position empty: `05_open.md` says "
            "what is owed and what it blocks. (It is a `../QUESTIONS.md` "
            "entry only if `05_open.md` does not mention it -- that is a "
            "handoff defect.) A plausible generic part in a placeholder "
            "position is worse than a hole, because it stops looking like "
            "an open question.",
            "Do NOT edit `handoff/` -- it is regenerated and your edits "
            "vanish. That includes `07_answers.md`: concept writes answers "
            "THERE, you read them and curate your own `QUESTIONS.md`.",
            "READ `CHANGELOG.md` FIRST, every session. It is the only way you "
            "are told the handoff moved under you; its *What actually "
            "changed* section says which of your drawn work is now wrong.",
            "Do NOT read anything outside `kicad_project/`, and write only "
            "in your own board folder and `kicad_project/tools/`. The ONE "
            "thing that reaches outside is the footprint fetcher "
            "(`tools/fp/fp.py`), which downloads vendor CAD data from the "
            "internet into your own `lib/`. The vendor documents it is "
            "verified against are in `handoff/datasheets/`.",
            "Your ONLY channel back to the concept session is "
            "`../QUESTIONS.md`. A missing fact, a contradiction or a rule "
            "that blocks the work is a question there -- quoting the "
            "revision and page hashes in `MANIFEST.json` you work from -- "
            "never a value you choose yourself.",
            "Do NOT 'fix' the deliberate items listed in `04_deliberate.md`.",
            "Connectors that leave this board belong on ONE dedicated "
            "connectors sheet, not scattered across functional sheets. "
            "Strictly local headers (test points, per-pin probes) stay with "
            "the circuit they serve.",
        ]
        if self.pinout:
            R += ["Do NOT edit the pinout. `03_pinout.csv` is generated on the "
                  "concept side; a pin move is a request in `QUESTIONS.md`.",
                  "Do NOT no-connect a pin that `08_pin_accounting.md` marks "
                  "OPEN or UNKNOWN, however unused it looks. A no-connect flag "
                  "asserts the pin is finished and hides the hole from the "
                  "next review. Leave it unconnected and let ERC complain."]
        return R + list(self.get("FORBIDDEN", []))

    def read_first(self):
        comps = self.components()
        n_ph = sum(1 for c in comps if c[3] != "set")
        second = ("**`07_answers.md`** — the concept session's answers to "
                  "your `QUESTIONS.md`. Read, act, then curate your file.")
        then = (["**`08_pin_accounting.md`** — every pin, classified and "
                 "reconciled to a total. Anything it marks OPEN or UNKNOWN "
                 "is a hole the concept session owes you: leave it "
                 "unconnected and ERC-erroring, and do NOT no-connect it."]
                if self.pinout else [])
        then += ["**`netlist.json`** — the same connectivity as data, for "
                 "`tools/sch` (`sch.py parity --handoff` checks your schematic "
                 "against it, pin for pin)."]
        then += ["**`05_open.md`** — what is not decided yet, what it "
                 "blocks, and what is safe to start; then `04_deliberate.md`.",
                "**`13_datasheets.md` before any footprint work** — the vendor "
                "documents themselves are in `datasheets/`, and they are the "
                "authority for every land pattern you draw or download."]
        if self.get("BOOT"):
            then.append("**`12_boot.md` before you draw the power-up chain** — "
                        "the ordered power-up table and what drives every enable.")
        if self.fab:
            then.append("**`14_fab.md` before Board Setup** — the fab's rule "
                        "set, the stackup and the net classes, each with its "
                        "source; `tools/board_setup/board_setup.py` types it "
                        "into KiCad.")
        L = [f"# READ FIRST — {self.B.TITLE} handoff", "",
             "This folder is the COMPLETE interface between the concept "
             "session and this one. If something you need is not here, that "
             "is a question for the concept session (write it in "
             "`../QUESTIONS.md`), not a reason to look elsewhere in the "
             "repository or to invent a value.",
             "", "## Ownership", "",
             "| path | who writes |", "|---|---|",
             "| `handoff/` | concept session — **read-only to you** |",
             "| `../QUESTIONS.md` | **you** — concept reads it |",
             f"| `../` (the {self.board} KiCad files, `lib/`, plans) | **you**, entirely |",
             "", "## The two files to open first, every session", "",
             "1. **`CHANGELOG.md`** — newest first. The handoff moves under "
             "you and this is the only channel that says so.",
             "2. " + second, "", "Then:", ""]
        L += [f"- {x}" for x in then]
        L += ["", "## Rules", ""]
        L += [f"{i + 1}. {r}" for i, r in enumerate(self.rules())]
        L += ["", "## Status of this handoff", "",
              f"- **{n_ph} of {len(comps)} component lines are NOT "
              f"committable** — see `01_components.md`.",
              "- Open design questions: see `05_open.md`. The board cannot be "
              "finished until they close; it can be started on the settled "
              "parts.",
              "", "## What this board is for", "",
              self.get("PURPOSE") or "*Not stated yet — that is a question.*", ""]
        A = getattr(self.C, "ACCEPTANCE", None)
        if A:
            L += [f"Acceptance, at **{A.get('node', 'the board output')}**:", ""]
            L += [f"- `{k}` = {v}" for k, v in A.items() if k not in ("node", "note")]
            if A.get("note"):
                L += ["", A["note"]]
            L.append("")
        L += ["## Revision", "",
              "`MANIFEST.json` carries this handoff's revision and the hash of "
              "every page in this folder. Quote the revision (and the hash of "
              "the page you are asking about) in every question. If the "
              "concept session says this handoff is stale, stop and wait for "
              "a republish rather than working from it."]
        return "\n".join(L) + "\n"

    # -- 01 02 09 10 -------------------------------------------------------
    def components_md(self):
        L = ["# Components", "",
             "`set` = derived or measured, commit it. "
             "`PROPOSED` = one analysis, unverified — confirm before "
             "committing. "
             "`PLACEHOLDER` = **no value exists; do not place a part**.", ""]
        label = {"set": "SET — commit these",
                 "proposed": "PROPOSED — confirm before committing",
                 "tbd": "PLACEHOLDER — DO NOT PLACE A PART"}
        comps = self.components()
        bad = sorted({c[3] for c in comps} - set(label))
        if bad:
            die(f"{self.bus_rel}: unknown status {bad} (set / proposed / tbd)")
        for st in ("set", "proposed", "tbd"):
            rows = [c for c in comps if c[3] == st]
            L += [f"## {label[st]}  ({len(rows)})", ""]
            for ref, fn, val, _, note in (c[:5] for c in rows):
                tag = "" if st == "set" else f"**{label[st].split(' —')[0]}.** "
                L += [f"### {ref} — {fn}", "", f"**{val}**", "", tag + note, ""]
            if not rows:
                L += ["None.", ""]
        return "\n".join(L)

    def nets_md(self):
        L = ["# Nets and their constraints", "",
             "A constraint here is a design requirement expressed as a wiring "
             "rule. Treat it as a rule, not commentary.", ""]
        on = self.netlist()["nets"]
        for name, src, dst, con in self.get("NETS", []):
            L += [f"### `{name}`", "", f"- **source:** {src}",
                  f"- **loads:** {dst}", f"- **constraint:** {con or '—'}"]
            if on.get(name):
                # the complete membership, from the data: the prose above says
                # why, and may name only the parts that matter to the reader
                L.append(f"- **every pin on it** (`netlist.json`): {_members(on[name])}")
            L.append("")
        if not self.get("NETS"):
            L += ["None published yet.", ""]
        return "\n".join(L)

    def connectors_md(self):
        L = ["# Connectors — everything that leaves this board", "",
             "One table per connector; these belong on the dedicated "
             "connectors sheet. A connector marked OPEN or NOT DEFINED is a "
             "hole the concept session owes you — draw nothing for it beyond "
             "a reservation. The board that OWNS a connector defines its pin "
             "order; every other board mirrors it.", ""]
        for name, status, intro, pins in self.get("CONNECTORS", []):
            L += [f"## {name} — {status}", "", intro, ""]
            off = (self.get("PIN_OFFSET") or {}).get(name)
            if off:
                L += [f"**Part pins:** this table's pin N is {name}'s own pin N {'+' if off > 0 else '−'} "
                      f"{abs(off)} (the symbol and footprint number the part; `netlist.json` carries the "
                      f"part's pins).", ""]
            if pins:
                L += ["| pin | net | dir | note |", "|---|---|---|---|"]
                L += [f"| {p} | `{n}` | {d} | {t} |" for p, n, d, t in pins]
            L.append("")
        if not self.get("CONNECTORS"):
            L += ["None published yet.", ""]
        return "\n".join(L)

    def layout_md(self):
        L = ["# Layout constraints", "",
             "These gate the PCB phase, not the schematic; at schematic time "
             "they are context for placement-relevant choices (sheet "
             "grouping, net classes). This list must be COMPLETE before "
             "layout starts — if routing raises a constraint not on it, that "
             "is a question.", ""]
        for i, (con, srcs) in enumerate(self.get("LAYOUT", []), 1):
            L += [f"{i}. {con}  *({_joined(srcs)})*", ""]
        if not self.get("LAYOUT"):
            L += ["None published yet — layout must not start until this "
                  "list exists.", ""]
        return "\n".join(L)

    def text_md(self, attr, title, empty):
        t = (self.get(attr) or "").strip()
        if not t:
            return f"# {title}\n\n{empty}\n"
        return (t if t.startswith("#") else f"# {title}\n\n{t}") + "\n"

    def answers_md(self):
        """Concept-owned answers. NEVER written into their QUESTIONS.md --
        that is the single-writer rule."""
        p = os.path.join(ROOT, self.answers_rel)
        if os.path.exists(p) and open(p).read().strip():
            return open(p).read()
        return ("# Answers\n\nNothing answered yet. Concept writes here; the "
                "board session reads and then curates its own "
                "`QUESTIONS.md`.\n")

    # -- 14 ----------------------------------------------------------------
    def fab_tables(self):
        """The rule set as data: profile defaults, then the board's choices."""
        F, P = self.B.FAB, self.fab
        layers = F.get("layers")
        W, H = (F.get("outline") or (None, None))
        qty = F.get("quantity")
        size = (f"single board, {W:g} x {H:g} mm, {qty} pcs"
                if W and H and qty else None)
        order = [("Board size / quantity", size, F.get("size_why", "")),
                 ("Layers", layers, F.get("layers_why", ""))]
        for opt, choice, why in P.ORDER:
            if opt.lower().startswith("inner") and (layers or 0) <= 2:
                continue
            order.append((opt, choice, why))
        order = _override(order, F.get("order", []), "order")
        cons = _override(list(P.CONSTRAINTS), F.get("constraints", []), "constraints")
        ol = P.OUTLINE
        cons.append(("Board outline",
                     f"{W:g} x {H:g} mm, {ol[0]}" if W and H and ol[0] else None, ol[1]))
        stack = [list(r) for r in P.STACKUPS.get(layers, [])] or \
            [[None, None, f"the profile has no {layers}-layer stackup"]]
        carries = F.get("carries", {})
        for r in stack:
            if r[0] in carries:
                r[2] = carries[r[0]]
        unknown = set(carries) - {r[0] for r in stack}
        if unknown:
            die(f"{self.id}.py FAB carries names layers not in the stackup: {sorted(unknown)}")
        dc = P.DEFAULT_CLASS
        classes = [("Default", "everything not listed", dc[0], dc[1], dc[2], dc[3])]
        for row in F.get("netclasses", []):
            row = (row[0], _joined(row[1]), *row[2:])
            if row[0] == "Default":
                # board_setup.py recognises the Default row by this nets cell.
                classes[0] = (row[0], "everything not listed", *row[2:])
            else:
                classes.append(row)
        rules = self.fab_rules(cons, classes)
        return order, cons, stack, classes, rules

    def fab_rules(self, cons, classes):
        """Derived rules first, then the profile's, then the board's own. A
        board rule with a derived rule's NAME replaces it -- the way to
        state an exception (one net exempted from a no-via class)."""
        derived = []
        for name, val, basis in cons:
            if name.lower() == "hole to board edge" and val is not None:
                derived.append(("hole to board edge",
                                f'(rule "fab: hole to board edge"\n'
                                f'  (constraint physical_hole_clearance (min {val}mm))\n'
                                f'  (condition "B.Layer == \'Edge.Cuts\'"))',
                                f"{basis}. Board Setup has no field for it."))
        for cls, nets, trk, clr, via, _note in classes:
            m = re.search(r"inside ([A-Za-z0-9_]+)'s", nets)
            if m and clr is not None:
                ref = m.group(1)
                derived.append((f"{cls} class inside {ref}",
                                f'(rule "fab: {cls} class inside {ref}"\n'
                                f'  (constraint clearance (min {clr}mm))\n'
                                f'  (condition "A.intersectsCourtyard(\'{ref}\') && '
                                f'B.intersectsCourtyard(\'{ref}\')"))',
                                f"A net class cannot say 'inside {ref}', so the "
                                f"{cls} class carries no nets and its clearance "
                                f"applies where both items touch {ref}'s "
                                "courtyard. Custom rules outrank net classes."))
            if via and "none" in str(via).lower():
                derived.append((f"{cls} class has no vias",
                                f'(rule "fab: {cls} class has no vias"\n'
                                f'  (constraint disallow via)\n'
                                f'  (condition "A.NetClass == \'{cls}\'"))',
                                f"The {cls} class takes no via (Net classes table)."))
        own = list(self.fab.RULES) + list(self.B.FAB.get("rules", []))
        names = set()
        for title, r, _ in own:
            m = re.search(r'\(rule "([^"]+)"', r or "")
            if not m:
                die(f"{self.id}: custom rule '{title}' has no (rule \"<name>\" ...) text")
            names.add(m.group(1))
        return [d for d in derived if f"fab: {d[0]}" not in names] + own

    def fab_md(self):
        if not self.fab:
            return None
        F, P = self.B.FAB, self.fab
        order, cons, stack, classes, rules = self.fab_tables()
        cells = ([c for _, c, _ in order] + [v for _, v, _ in cons] +
                 [x for r in stack for x in r[:2]] +
                 [x for r in classes for x in r[2:5]])
        n_unset = sum(1 for c in cells if c is None or c == "")
        self.fab_unset = n_unset
        # board_setup.py parses these tables cell by cell.
        for tbl, name in ((order, "order"), (cons, "constraints"), (stack, "stackup"),
                          (classes, "netclasses")):
            for r in tbl:
                for c in r:
                    if "|" in str(c) or "\n" in str(c):
                        die(f"{self.id} FAB {name} row '{r[0]}': a cell holds '|' or a "
                            f"line break, which breaks the table board_setup.py reads")
        L = ["# Fabrication rules — what to order, and what KiCad must enforce", "",
             f"**Fab house and service:** {_v(P.HOUSE)}. "
             f"**Profile:** `{P.NAME}` — {_v(P.READ_NOTE)}.", "",
             "Every value below is the fab's own, with the page it came "
             "from, or ours with the reason. Nothing here is typed by hand: "
             "`tools/board_setup/board_setup.py` puts the Constraints, the "
             "Physical Stackup, the Net classes and the Custom DRC rules into "
             "KiCad, and refuses a page with an UNSET value. A rule that "
             "blocks the layout is a question in `../QUESTIONS.md`, never a "
             "local edit — do not loosen a number to make routing fit, and do "
             "not tighten one on the assumption the fab can do better.", ""]
        if getattr(P, "EXAMPLE", False):
            L += ["⚠ **EXAMPLE profile** — the numbers below are illustrative, not "
                  "any fab's own. Practise with them; do not order against them. "
                  "The concept session owes a profile of the real fab, and "
                  "`board_setup.py check` reports this as a QUESTION until it "
                  "exists.", ""]
        if n_unset:
            L += [f"⚠ **{n_unset} value(s) are UNSET.** The concept session "
                  "owes them; Board Setup cannot be applied until they are "
                  "filled.", ""]
        L += ["## What to choose on the order form", "",
              "| option | choice | why |", "|---|---|---|"]
        L += [f"| {o} | **{_v(c)}** | {w} |" for o, c, w in order]
        notes = F.get("order_notes") or self._order_notes(order, cons)
        L += ["", "**Order notes** (paste into the form's special requirements):",
              "", f"> {notes}", "",
              "## KiCad Board Setup → Design Rules → Constraints (mm)", "",
              "| constraint | value | basis |", "|---|---|---|"]
        L += [f"| {n} | **{_v(v)}** | {b} |" for n, v, b in cons]
        L += ["", "## KiCad Board Setup → Physical Stackup", "",
              "| layer | material / thickness | carries |", "|---|---|---|"]
        L += [f"| {_v(a)} | {_v(m)} | {c} |" for a, m, c in stack]
        L += ["", "## Net classes (recommended; the constraints above are the floor)", "",
              "| class | nets | track | clearance | via drill / pad | note |",
              "|---|---|---|---|---|---|"]
        L += [f"| {c} | {n} | {_v(t)} | {_v(k)} | {_v(v)} | {o} |"
              for c, n, t, k, v, o in classes]
        if rules:
            L += ["", "## Custom DRC rules — generated into `.kicad_dru`", "",
                  "A Board Setup constraint is board-wide. These are the rules "
                  "scoped to one net class, one area or one footprint, so they "
                  "live here and nowhere else. A relaxation goes down to the "
                  "fab's own floor and never below it.", ""]
            for title, rule, why in rules:
                L += [f"**{title}** — {why}", "", "```", rule, "```", ""]
        if F.get("why"):
            L += ["", "## Why these numbers", ""]
            L += [f"{i + 1}. {t}" for i, t in enumerate(F["why"])]
        L += ["", "## Sources", "", "| source | where | what it gave |", "|---|---|---|"]
        L += [f"| {n} | {w} | {g} |" for n, w, g in list(P.SOURCES) + list(F.get("sources", []))]
        L.append("")
        return "\n".join(L)

    @staticmethod
    def _order_notes(order, cons):
        """The order rows, restated as one paste-able line. Fab-agnostic: it
        says only what the table says, and nothing while a choice is UNSET."""
        if any(c is None or c == "" for _, c, _ in order):
            return ("UNSET -- the order-form table above still has UNSET "
                    "choices; there is nothing to paste yet.")
        return "; ".join(f"{k}: {v}" for k, v, _ in order) + "."

    # -- 03 08: pin accounting (only for a board with a PINOUT) -------------
    def pin_rows(self):
        """Rows of the pinout CSV. Columns used: Pin Number, Pin Name,
        Signal Name, Function (others are carried along)."""
        if not self.pinout:
            return []
        p = os.path.join(ROOT, self.pinout)
        if not os.path.exists(p):
            return []
        # A real CSV reader, not str.split: a Function cell with a comma in it
        # is quoted by the writer, and a naive split tears it into pieces.
        import csv
        lines = [ln for ln in open(p) if ln.strip() and not ln.startswith("#")]
        rows = list(csv.DictReader(lines))
        need = {"Pin Number", "Pin Name", "Signal Name", "Function"}
        if rows and not need <= set(rows[0]):
            die(f"{self.pinout}: columns {sorted(need - set(rows[0]))} missing")
        return [{k: (v or "").strip() for k, v in r.items()} for r in rows]

    def _classify(self, rows):
        """Unassigned pins, classified by PIN NAME. The Function column is
        the vendor's description and is only consulted where the board says
        so (SPARE_FUNCTION, SHARED_PADS)."""
        T, O = self.get("PIN_TREATMENT", {}), self.get("PIN_OPEN", {})
        spare_n = self.get("SPARE_PINS")
        spare_f = self.get("SPARE_FUNCTION")
        out = {"decided": [], "open": [], "unknown": [], "spare": [], "reserved": []}
        for r in rows:
            if r["Signal Name"]:
                continue
            nm, fn, pin = r["Pin Name"], r["Function"], r["Pin Number"]
            # Exact name first; then the name without its trailing digits
            # (VCC1, VCC2 ... share an answer); then the group up to the
            # first underscore (a hard macro's pads all get one answer).
            key = next((k for k in (nm, re.sub(r"\d+$", "", nm), nm.split("_")[0])
                        if k in T or k in O), None)
            if key in O:
                out["open"].append((pin, nm, O[key]))
            elif key in T:
                out["decided"].append((pin, nm, T[key]))
            elif re.match(self.get("RESERVED_FUNCTION") or r"RESERVED EMPTY", fn, re.I):
                out["reserved"].append((pin, nm, fn))
            elif (spare_n and re.search(spare_n, nm)) or (spare_f and re.search(spare_f, fn)):
                out["spare"].append(pin)
            else:
                out["unknown"].append((pin, nm, fn))
        return out

    def pins_md(self):
        """Every pin accounted for, or explicitly flagged as not."""
        rows = self.pin_rows()
        if not rows:
            return "# Pin accounting\n\nPinout not available — that is a hole, not an empty package.\n"
        sig = [r for r in rows if r["Signal Name"]]
        k = self._classify(rows)
        n = {x: len(v) for x, v in k.items()}
        tot = len(sig) + sum(n.values())
        L = ["# Pin accounting — every pin, or an explicit hole", "",
             "GENERATED from `03_pinout.csv`, so it cannot drift from the "
             "build. Anything below marked OPEN or UNKNOWN is a hole the "
             "concept session owes you -- **leave it unconnected and "
             "ERC-erroring.**", "",
             f"- **{len(sig)}** assigned signals (in `03_pinout.csv`)",
             f"- **{n['decided']}** unassigned pins with a decided treatment",
             f"- **{n['open']}** pins with an OPEN decision",
             f"- **{n['unknown']}** UNKNOWN — not classified by the generator",
             f"- **{n['spare']}** spare (no connect)",
             *([f"- **{n['reserved']}** RESERVED EMPTY — no connect, and "
                "NEVER to be assigned"] if n["reserved"] else []),
             f"- total {len(rows)} pins ({len(sig)}+{n['decided']}+{n['open']}"
             f"+{n['unknown']}+{n['spare']}+{n['reserved']} = {tot})", ""]
        pk = self.get("PKG_TOTAL")
        if pk and pk != len(rows):
            L += [f"⚠ The package has **{pk}** pins and the pinout lists "
                  f"**{len(rows)}**. Treat the difference as UNKNOWN.", ""]
        L += [f"## The {len(sig)} assigned signals", "",
              "> " + ", ".join(f"`{s}`" for s in _buses(r["Signal Name"] for r in sig)), "",
              "Enumerated because a hand count of a list like this comes back "
              "one short; counting is the generator's job, not yours.", ""]
        sh = self.get("SHARED_PADS")
        if sh:
            hits = [(r["Pin Number"], r["Pin Name"], r["Signal Name"])
                    for r in sig if re.search(sh[0], r["Function"])]
            if hits:
                L += ["## Signals on shared-function pads", "", sh[1], "",
                      "| pin | pad | signal |", "|---|---|---|"]
                L += [f"| {b} | `{nm}` | `{g}` |" for b, nm, g in sorted(hits, key=lambda x: _pin(x[0]))]
                L.append("")
        if k["reserved"]:
            L += ["## RESERVED EMPTY", "",
                  "Left empty ON PURPOSE (an escape channel, a keep-clear). Not "
                  "spare: a signal placed on one strands what it protects. "
                  "Draw them as no-connect.", "",
                  "| pin | pad | why |", "|---|---|---|"]
            L += [f"| {b} | `{nm}` | {f} |" for b, nm, f in k["reserved"]]
            L.append("")
        if k["open"]:
            L += ["## OPEN — decision owed to you", "", "| pin | pad | status |", "|---|---|---|"]
            L += [f"| {b} | `{nm}` | {t} |" for b, nm, t in sorted(k["open"], key=lambda x: _pin(x[0]))]
            L.append("")
        if k["unknown"]:
            L += ["## ⚠ UNKNOWN — the generator could not classify these", "",
                  "Treat exactly like OPEN: the concept session has not "
                  "considered the pin at all.", "",
                  "| pin | pad | function |", "|---|---|---|"]
            L += [f"| {b} | `{nm}` | {f} |" for b, nm, f in sorted(k["unknown"], key=lambda x: _pin(x[0]))]
            L.append("")
        L += ["## Decided", "", "| pin | pad | connects to |", "|---|---|---|"]
        L += [f"| {b} | `{nm}` | {t} |" for b, nm, t in sorted(k["decided"], key=lambda x: _pin(x[0]))]
        L += ["", f"## Spare ({n['spare']})", "",
              "No connect. " + ", ".join(sorted(k["spare"], key=_pin)), ""]
        return "\n".join(L)

    # -- netlist.json: the connectivity as data -----------------------------
    NC_TEXT = re.compile(r"^(unused:\s*)?(no[ -]connect|leave (it )?(unconnected|open|floating)|unconnected|NC\b)", re.I)

    def netlist(self):
        """Every part and every pin this handoff fixes, as data for the board
        session's tools (tools/sch): parts from the value bus; pin -> net from
        the pinout (PINOUT_REF), the connector tables and PINS. `nc` is a pin
        decided unconnected, `open` one the board must leave unconnected
        WITHOUT a no-connect flag (OPEN / UNKNOWN), `decided` an unassigned
        pin whose treatment is text. `prose_only` names the parts whose
        connections are still only words in 02_nets -- `complete` is true
        when there are none."""
        parts = {}
        for c in self.components():
            for ref in sorted(_refset([c[0]])):
                parts[ref] = dict(line=c[0], function=c[1], value=c[2], status=c[3],
                                  placeholder=c[3] == "tbd")
        pin_net, nc, open_, decided = {}, set(), set(), {}

        def put(ref, pin, net, where):
            k = (ref, str(pin))
            net = (net or "").strip()
            if not net or net in ("—", "-"):
                return
            if net.upper() == "NC":
                if k in pin_net:
                    die(f"netlist: {ref}.{pin} is {pin_net[k]} in one table and NC in {where}")
                nc.add(k)
                return
            if k in nc or (k in pin_net and pin_net[k] != net):
                die(f"netlist: {ref}.{pin} is {pin_net.get(k, 'NC')} in one table and {net} in {where}")
            pin_net[k] = net
        ref = self.get("PINOUT_REF")
        rows = self.pin_rows()
        if rows and ref:
            k = self._classify(rows)
            for r in rows:
                if r["Signal Name"]:
                    put(ref, r["Pin Number"], r["Signal Name"], "the pinout")
            for pin, _, text in k["decided"]:
                # "no connect but for the DNP strap R61" is a connection
                if self.NC_TEXT.match(text) and not re.search(r"\b(but|except|strap)\b", text.split(";")[0], re.I):
                    nc.add((ref, pin))
                else:
                    decided[f"{ref}.{pin}"] = text
            for pin, _, _ in k["open"] + k["unknown"]:
                open_.add((ref, pin))
            for pin in k["spare"] + [x[0] for x in k["reserved"]]:
                nc.add((ref, pin))
        offs = self.get("PIN_OFFSET") or {}
        for cref, _, _, pins in self.get("CONNECTORS", []):
            off = offs.get(cref, 0)
            for cell, net, _, _ in pins:
                for pin in _pins(cell):
                    put(cref, str(int(pin) + off) if off and pin.isdigit() else pin, net, f"connector {cref}")
        swap = {}
        for pref, pins in (self.get("PINS") or {}).items():
            if isinstance(pins, (tuple, list)):
                if len(pins) != 2:
                    die(f"netlist: PINS {pref}: a tuple is a two-pin part either way round -- give two nets")
                pins = {"1": pins[0], "2": pins[1], "_swap": [["1", "2"]]}
            for pin, net in pins.items():
                if pin == "_swap":
                    swap[pref] = [list(map(str, g)) for g in net]
                else:
                    put(pref, pin, net, "PINS")
        nets = {}
        for (r, pin), n in pin_net.items():
            nets.setdefault(n, []).append([r, pin])
        for n in nets:
            nets[n].sort(key=lambda x: (x[0], _pin(x[1])))
        covered = {r for r, _ in pin_net} | {r for r, _ in nc} | {r for r, _ in open_} \
            | set(self.get("PINS") or {})                # a fiducial is given, with no pins
        prose = sorted((r for r, p in parts.items() if not p["placeholder"] and r not in covered), key=_natkey)
        unknown = sorted(covered - set(parts), key=_natkey)
        return dict(board=self.board, title=self.B.TITLE, parts=parts, nets=nets,
                    nc=sorted([list(x) for x in nc], key=lambda x: (x[0], _pin(x[1]))),
                    open=sorted([list(x) for x in open_], key=lambda x: (x[0], _pin(x[1]))),
                    decided=decided,
                    rails=sorted(set(self.get("GROUND_NETS") or ("GND",)) | set(self.get("RAILS") or ())),
                    swap=swap, prose_only=prose, not_in_components=unknown,
                    complete=not prose and not open_ and not unknown)

    # -- lint -------------------------------------------------------------
    def lint(self, files):
        """Cross-checks that catch real gaps."""
        prob = []
        refs = _refset(c[0] for c in self.components())
        rows = self.pin_rows()
        pins = {r["Pin Number"] for r in rows}
        flat = " ".join(f"{s} {d} {n}" for _, s, d, n in self.get("NETS", []))
        netnames = {n for n, _, _, _ in self.get("NETS", [])}
        signames = ({r["Signal Name"] for r in rows if r["Signal Name"]}
                    | {r["Pin Name"] for r in rows})
        grounds = set(self.get("GROUND_NETS") or ("GND",))
        for m in sorted(set(REFDES.findall(flat)) - pins):
            # D0, T1, ... are also ordinary signal names: a token that IS a
            # published net or signal is not a designator.
            if m not in refs and m not in netnames and m not in signames:
                prob.append(f"net text references {m}, which has no COMPONENTS line")
        for cname, _, _, pinlist in self.get("CONNECTORS", []):
            for _, net, _, _ in pinlist:
                if net not in netnames and net not in grounds and net not in signames:
                    prob.append(f"{cname} pin net `{net}` is neither a NETS entry, "
                                f"a ground net, nor a pinout signal")
            if cname not in refs:
                prob.append(f"connector {cname} has no COMPONENTS line")
        for c in self.components():
            # 'C1, C2, C3' with '100 nF; 1 uF; 22 uF': netlist.json gives every
            # part all three, and the board session cannot set one Value.
            if len(_refset([c[0]])) > 1 and ";" in c[2]:
                prob.append(f"{c[0]}: one line, several parts, several values ({c[2]}) -- "
                            f"which part takes which? Give each its own line")
        if rows:
            k = self._classify(rows)
            if k["unknown"]:
                prob.append(f"{len(k['unknown'])} pinout pins are UNKNOWN -- give each "
                            f"a PIN_TREATMENT or PIN_OPEN entry, or a spare pattern")
        for a in ("PINOUT", "DIAGRAM", "FLOORPLAN"):
            rel = self.get(a)
            if rel and not os.path.exists(os.path.join(ROOT, rel)):
                prob.append(f"{a} names {rel}, which does not exist -- run its generator")
        if self.fab and getattr(self.fab, "EXAMPLE", False):
            prob.append(f"14_fab.md is built from the EXAMPLE profile {self.fab.NAME}: no "
                        "number on it is a real fab's (copy fabs/_blank.py for yours)")
        if getattr(self, "fab_unset", 0):
            prob.append(f"14_fab.md carries {self.fab_unset} UNSET value(s); "
                        "board_setup.py will refuse to apply it")
        if self.pinout and not self.get("PINOUT_REF"):
            prob.append("PINOUT has no PINOUT_REF: say which refdes it belongs to, or netlist.json "
                        "carries none of its pins")
        nl = self.netlist()
        if nl["not_in_components"]:
            prob.append(f"netlist.json gives pins for {', '.join(nl['not_in_components'][:6])}, "
                        f"which have no COMPONENTS line")
        if nl["prose_only"]:
            prob.append(f"netlist.json: {len(nl['prose_only'])} of {len(nl['parts'])} parts have their "
                        f"connections only in prose (PINS gives them as data)")
        if not os.path.exists(os.path.join(self.out, "QUESTIONS.md")):
            prob.append(f"kicad_project/{self.board}/QUESTIONS.md is missing -- the board "
                        "session has no channel back (tools/handoff/new_board.py creates it)")
        return prob

    # -- datasheets -----------------------------------------------------------
    def is_passive(self, mpn):
        """True when every COMPONENTS line naming this MPN is a resistor,
        capacitor or ferrite bead in a standard chip package. Deliberately
        conservative: an MPN that also appears on any other line keeps its
        datasheet. A passive datasheet needed for something else (a DC-bias
        curve, an ESR limit) goes in the board's DATASHEETS_EXTRA."""
        rows = [c for c in self.components() if mpn in c[2] or mpn in c[4]]
        return bool(rows) and all(PASSIVE_REF.match(r[0]) and PASSIVE_PKG.search(r[2])
                                  for r in rows)

    def datasheet_set(self, text):
        """Every indexed datasheet this handoff NAMES, plus the board's extras.

        WHY THE PDFs CROSS THE WALL: footprint work belongs to the board
        session, and its verify gate is a comparison against the vendor's
        recommended land pattern -- which lives in the datasheet, under
        reference/, which that session is denied. Quoting pad dimensions into
        an answer instead hands over numbers nobody on that side can re-check.
        So the document itself ships, hashed like every other source."""
        p = os.path.join(ROOT, DS_INDEX)
        if not os.path.exists(p):
            return []
        idx = json.load(open(p))
        extra = set(self.get("DATASHEETS_EXTRA", ()))
        want = extra | {k for k in idx if k in text and not self.is_passive(k)}
        out = []
        for k in sorted(want):
            m = idx.get(k)
            if not m:
                warn(f"{self.id} DATASHEETS_EXTRA names {k}, which is not in "
                     f"{DS_INDEX} -- fetch it with tools/parts/datasheet.py")
                continue
            if os.path.exists(os.path.join(ROOT, m["path"])):
                out.append((k, m))
            else:
                warn(f"{self.id}: {k} is indexed but {m['path']} is missing -- its "
                     "datasheet is NOT in this handoff (tools/parts/datasheet.py --refresh)")
        return out

    @staticmethod
    def _ds_name(m):
        """The file name in handoff/datasheets/: the filed PDF's own name, which
        datasheet.py already made path-safe (an MPN may contain '/')."""
        return os.path.basename(m["path"])

    @staticmethod
    def datasheets_md(ds):
        L = ["# Datasheets", "",
             "Every vendor document this handoff names, filed by MPN in "
             "`datasheets/`. Read a page with the Read tool's `pages` argument "
             "— do not go looking for these on the web, and do not re-download "
             "one: the copy here is the revision the design was decided "
             "against.", "",
             "**This is the authority a footprint is verified against.** "
             "`tools/fp/fp.py sign` records the sha256 below next to the "
             "footprint it verified, so a revision that moves under drawn work "
             "is caught by `tools/fp/fp.py check` rather than at fab.", "",
             "⚠ **Resistors, capacitors and ferrite beads in standard chip "
             "packages are deliberately not here.** 0201 through 2512: use "
             "KiCad's own stock footprint — do not fetch one, do not verify "
             "one, do not ask for the datasheet. **Everything else with two "
             "pads is normal work**: a diode's land is its own. A passive "
             "datasheet needed for something other than its footprint (a "
             "DC-bias curve, an ESR limit) IS a question.", "",
             "| MPN | manufacturer | file | sha256 | fetched | fetched from |",
             "|---|---|---|---|---|---|"]
        for mpn, m in ds:
            L.append(f"| `{mpn}` | {m.get('mfr') or '—'} | `datasheets/{Board._ds_name(m)}` | "
                     f"`{m['sha256']}` | {m.get('fetched', '—')} | {m.get('url') or '—'} |")
        if not ds:
            L += ["", "Nothing indexed yet."]
        L.append("")
        return "\n".join(L)

    # -- the changelog ------------------------------------------------------
    def changelog(self, pages):
        """Append-only publish log -- THE HANDOFF'S ONLY PUSH CHANNEL. It names
        the PAGES that changed (the board session can open those; it cannot
        open our sources). State lives in a JSON comment, never in the prose:
        `pages` (name -> hash), `note` (hash of the last RELEASE_NOTE), `moved`
        and `gone` (what the head revision lists) and `first` (when the head
        revision was FIRST published -- the amend window runs from there)."""
        p = os.path.join(self.handoff, "CHANGELOG.md")
        prev, old = "", {}
        if os.path.exists(p):
            prev = open(p).read()
            m = re.search(r"<!--state (.*?)-->", prev)
            if m:
                old = json.loads(m.group(1))
        old_pages = old.get("pages", {})          # {} for a pre-"pages" state
        changed = sorted(k for k, v in pages.items() if old_pages.get(k) != v)
        # A page that DISAPPEARS has no key to differ -- and a handoff that
        # silently stops carrying a document is exactly the absence to name.
        gone = sorted(k for k in old_pages if k not in pages)
        if not changed and not gone and prev:
            return prev, False                    # nothing moved; log no noise
        amending = False
        now = datetime.datetime.now()
        stamp = now.strftime("%Y-%m-%d %H:%M")
        rev = prev.count("\n## rev ") + 1
        first = stamp
        m = re.search(r"## rev (\d+) -- ", prev)
        if m and old.get("first") and "moved" in old:
            age = (now - datetime.datetime.strptime(old["first"], "%Y-%m-%d %H:%M")).total_seconds()
            prior = set(old["moved"]) | set(old.get("gone", []))
            if age < AMEND_MIN * 60 and set(changed) | set(gone) <= prior:
                rev = int(m.group(1))
                rest = prev.split("-->\n", 1)[1].split("\n## rev ", 1)
                prev = (prev.split("-->\n", 1)[0] + "-->\n" +
                        ("## rev " + rest[1] if len(rest) > 1 else ""))
                changed = sorted(set(old["moved"]) - set(gone))
                gone = sorted(set(old.get("gone", [])) | set(gone))
                first = old["first"]              # the window does not slide
                amending = True
        entry = [f"## rev {rev} -- {stamp}" + (" (amended)" if amending else ""), ""]
        if not prev and not amending:
            entry += ["First publish."]
        else:
            entry += ["Pages that changed since the previous revision:", ""]
            entry += [f"- `{k}`" for k in changed]
            if gone:
                entry += ["", "No longer published here:", ""]
                entry += [f"- `{k}`" for k in gone]
            entry += ["", "**Re-read them.** If you are mid-work, check "
                      "whether anything you have already drawn depends on these."]
            if "03_pinout.csv" in changed:
                entry += ["The pinout moved: a PIN MOVED (its regeneration "
                          "timestamp is normalised out of the hash)."]
        note = self.get("RELEASE_NOTE")
        nh = hashlib.blake2b(note.encode(), digest_size=8).hexdigest() if note else ""
        same = nh == old.get("note", old.get("_note"))
        # When amending, the replaced revision carried the note, so it is
        # always re-emitted -- but an UNCHANGED note on an amend describes the
        # first publish only, and the pages have moved since.
        if note and (amending or not same):
            entry += ["", "### What actually changed", "", note.strip()]
            if amending and same:
                warn(f"{self.id} amends rev {rev} with an UNCHANGED RELEASE_NOTE -- "
                     "it must describe every change since the previous revision.")
        elif note:
            warn(f"{self.id} RELEASE_NOTE is unchanged since the previous "
                 "rev -- update or clear it; this rev lists pages only.")
        elif prev or amending:
            warn(f"{self.id} has no RELEASE_NOTE -- the board session gets "
                 "a bare page list and has to guess what moved.")
        state = {"pages": pages, "note": nh, "moved": changed, "gone": gone, "first": first}
        head = ("# Handoff changelog\n\nNewest first. Read this before "
                "resuming work; it is the only way you are told the handoff "
                "moved.\n\n")
        prev_body = prev.split("-->\n", 1)[1] if "-->" in prev else ""
        body = "\n".join(entry) + "\n\n"
        return head + f"<!--state {json.dumps(state)}-->\n" + body + prev_body, True

    # -- render, publish, check --------------------------------------------
    def render(self):
        """Everything the handoff folder holds except CHANGELOG and MANIFEST:
        (files: name -> text, copies: name -> repo-relative source,
        ds: [(mpn, index entry)]). publish() and check() both go through
        here, so they cannot disagree about what the handoff is."""
        files = {
            "01_components.md": self.components_md(),
            "02_nets.md": self.nets_md(),
            "04_deliberate.md": self.text_md("DELIBERATE", "Deliberate choices",
                                             "None recorded yet."),
            "05_open.md": self.text_md("OPEN", "Open questions",
                                       "Nothing open is recorded. If something "
                                       "you need is undecided, ask."),
            "07_answers.md": self.answers_md(),
            "09_connectors.md": self.connectors_md(),
            "10_layout.md": self.layout_md(),
        }
        if self.pinout:
            files["08_pin_accounting.md"] = self.pins_md()
        files["netlist.json"] = json.dumps(self.netlist(), indent=1, sort_keys=True) + "\n"
        if self.get("BOOT"):
            files["12_boot.md"] = self.text_md("BOOT", "Boot sequence", "")
        fab = self.fab_md()
        if fab:
            files["14_fab.md"] = fab
        ds = self.datasheet_set("\n".join(files.values()))
        # AFTER the scan: this file lists every MPN and would match all of them.
        files["13_datasheets.md"] = self.datasheets_md(ds)
        files["00_READ_FIRST.md"] = self.read_first()
        copies = {}
        for attr, dst in (("PINOUT", "03_pinout.csv"), ("DIAGRAM", "06_block_diagram.svg"),
                          ("FLOORPLAN", "11_floorplan.svg")):
            rel = self.get(attr)
            if rel and os.path.exists(os.path.join(ROOT, rel)):
                copies[dst] = rel
        return files, copies, ds

    def page_hashes(self, files, copies, ds):
        """name -> hash of every page as it will sit in the folder."""
        h = {n: _hb(t.encode()) for n, t in files.items()}
        h.update({n: _h(rel) for n, rel in copies.items()})
        h.update({"datasheets/" + self._ds_name(m): _hb(open(os.path.join(ROOT, m["path"]), "rb").read())
                  for _, m in ds})
        return dict(sorted(h.items()))

    def publish(self):
        files, copies, ds = self.render()
        pages = self.page_hashes(files, copies, ds)
        log, moved = self.changelog(pages)
        m = re.search(r"^## rev (\d+)", log, re.M)
        manifest = json.dumps({"board": self.board, "rev": int(m.group(1)) if m else None,
                               "pages": pages, "sources": self.manifest()}, indent=2) + "\n"
        # Build the whole folder beside the old one, then swap: a failure
        # anywhere above or below leaves the published handoff untouched.
        os.makedirs(self.out, exist_ok=True)
        new = tempfile.mkdtemp(prefix=".handoff-new-", dir=self.out)
        try:
            for n, t in {**files, "CHANGELOG.md": log, "MANIFEST.json": manifest}.items():
                with open(os.path.join(new, n), "w") as f:
                    f.write(t)
            for dst, rel in copies.items():
                shutil.copy2(os.path.join(ROOT, rel), os.path.join(new, dst))
            os.makedirs(os.path.join(new, "datasheets"))
            for _, mm in ds:
                shutil.copy2(os.path.join(ROOT, mm["path"]),
                             os.path.join(new, "datasheets", self._ds_name(mm)))
            os.chmod(new, 0o775)
            before = set(self._listing())
            old = None
            if os.path.isdir(self.handoff):
                old = self.handoff + ".old"
                shutil.rmtree(old, ignore_errors=True)
                os.rename(self.handoff, old)
            os.rename(new, self.handoff)
            if old:
                shutil.rmtree(old, ignore_errors=True)
        except BaseException:
            shutil.rmtree(new, ignore_errors=True)
            raise
        for f in sorted(before - set(self._listing())):
            print(f"   removed {f} (no longer published)")
        print(f"{self.id}: published rev {self.rev()} -> "
              f"{os.path.relpath(self.handoff, ROOT)}" + ("" if moved else " (nothing moved)"))
        for p in self.lint(files):
            print(f"  LINT: {p}")
        n_ph = sum(1 for c in self.components() if c[3] != "set")
        print(f"  {n_ph} placeholder/proposed component lines, {len(ds)} datasheets.")

    def _listing(self):
        """Relative paths of every file in the published folder."""
        out = []
        for d, _, fs in os.walk(self.handoff):
            out += [os.path.relpath(os.path.join(d, f), self.handoff) for f in fs]
        return out

    def check(self):
        """(fresh?, summary line, stale pages). Fresh = the folder on disk is
        exactly what the sources render to now."""
        p = os.path.join(self.handoff, "MANIFEST.json")
        if not os.path.exists(p):
            return False, f"{self.id}: STALE — never published", []
        files, copies, ds = self.render()
        now = self.page_hashes(files, copies, ds)
        disk = {}
        for rel in self._listing():
            if rel not in ("CHANGELOG.md", "MANIFEST.json"):
                disk[rel] = _hb(open(os.path.join(self.handoff, rel), "rb").read())
        bad = sorted(k for k in now if disk.get(k) != now[k])
        bad += sorted(f"{k} (on disk, not published)" for k in disk if k not in now)
        if bad:
            more = f" (+{len(bad) - 3} more)" if len(bad) > 3 else ""
            return False, f"{self.id}: STALE — {', '.join(bad[:3])}{more}", bad
        return True, f"{self.id}: fresh (rev {self.rev()})", []


def _override(rows, new, what):
    """Replace rows by their first cell (case-insensitive), append the rest."""
    rows = [tuple(r) for r in rows]
    idx = {r[0].lower(): i for i, r in enumerate(rows)}
    for r in new:
        i = idx.get(r[0].lower())
        if i is None:
            rows.append(tuple(r))
        else:
            rows[i] = tuple(r)
    return rows


def _refset(cells):
    """Every designator a COMPONENTS refdes cell names: 'R1..R24',
    'C3, C4', 'U5/U6' and plain 'J1' all expand."""
    out = set()
    for cell in cells:
        for tok in re.split(r"[,/\s]+", cell):
            m = re.fullmatch(r"([A-Z_]+)(\d+)\.\.(?:[A-Z_]+)?(\d+)", tok)
            if m:
                out |= {f"{m.group(1)}{i}" for i in range(int(m.group(2)), int(m.group(3)) + 1)}
            elif tok:
                out.add(tok)
    return out


def _members(pins):
    """[[ref, pin], ...] -> 'C18..C25, R40, U1 (13 pins)'."""
    n = {}
    for r, _ in pins:
        n[r] = n.get(r, 0) + 1
    runs, out = [], []
    for r in sorted(n, key=_natkey):
        m = re.fullmatch(r"([A-Z_]+)(\d+)", r)
        if m and runs and runs[-1][0] == m.group(1) and runs[-1][2] == int(m.group(2)) - 1 \
                and n[r] == 1 and runs[-1][3] == 1:
            runs[-1][2] = int(m.group(2))
        else:
            runs.append([m.group(1), int(m.group(2)), int(m.group(2)), n[r]] if m else [r, None, None, n[r]])
    for pre, a, b, k in runs:
        s = pre if a is None else f"{pre}{a}" if a == b else f"{pre}{a}..{pre}{b}" if b > a + 1 else f"{pre}{a}, {pre}{b}"
        out.append(s + (f" ({k} pins)" if k > 1 else ""))
    return ", ".join(out)


def _pins(cell):
    """The pins one table cell names: '7', 'A4, A9, B4, B9', 'S1..S4', '3..6'."""
    out = []
    for tok in re.split(r"[,/\s]+", str(cell).strip()):
        m = re.fullmatch(r"([A-Z_]*)(\d+)\.\.(?:[A-Z_]*)?(\d+)", tok)
        if m:
            out += [f"{m.group(1)}{i}" for i in range(int(m.group(2)), int(m.group(3)) + 1)]
        elif tok:
            out.append(tok)
    return out


def _natkey(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def _pin(p):
    """Sort key for a pin designator: BGA ball (row letters, then column
    number) or a plain pin number."""
    m = re.match(r"([A-Z]*)(\d+)$", p)
    return (m.group(1), int(m.group(2))) if m else (p, 0)


def _buses(names):
    """`d[0]`..`d[7]` -> `d[0..7]`; everything else sorted as is."""
    bus, out = {}, []
    for n in names:
        m = re.fullmatch(r"(.+)\[(\d+)\]", n)
        if m:
            bus.setdefault(m.group(1), []).append(int(m.group(2)))
        else:
            out.append(n)
    for b, ix in bus.items():
        ix.sort()
        if ix == list(range(ix[0], ix[-1] + 1)):
            out.append(f"{b}[{ix[0]}..{ix[-1]}]")
        else:
            out += [f"{b}[{i}]" for i in ix]
    return sorted(out)


def main(argv):
    ap = argparse.ArgumentParser(
        description="Publish, or check, each board's handoff (see the module docstring).")
    ap.add_argument("--board", metavar="boardN", help="one board (board1, or its folder name)")
    ap.add_argument("--all", action="store_true", help="every board in tools/handoff/boards/")
    ap.add_argument("--check", action="store_true",
                    help="publish nothing: say whether each handoff is fresh; exit 1 if not")
    a = ap.parse_args(argv)
    ids = board_ids()
    if a.board:
        m = re.match(r"board\d+", a.board)
        want = m.group(0) if m else a.board
        if want not in ids:
            print(f"publish: no board '{want}' in tools/handoff/boards/ "
                  f"(have: {', '.join(ids) or 'none'})", file=sys.stderr)
            return 2
        ids = [want]
    elif not a.all and not a.check:
        ap.error("say which: --board boardN, or --all")
    if not ids:
        print("no boards: tools/handoff/boards/ is empty "
              "(create one with ./setup.sh new-board)")
        return 0
    rc = 0
    if a.check:
        res = []
        for bid in ids:
            try:
                b = Board(bid)
                ok, line, bad = b.check()
                res.append((ok, line, bad, [] if not ok else b.lint({})))
            except PublishError as e:
                res.append((False, f"{bid}: BROKEN — {e}", [], []))
        for _, line, _, _ in res:
            print(line)
        for ok, line, bad, lint in res:
            if not ok and bad:
                print(f"\n{line.split(':')[0]} — republish before answering any question:")
                print("\n".join(f"   {k}" for k in bad))
            for p in lint:
                print(f"  LINT {line.split(':')[0]}: {p}", file=sys.stderr)
        return 0 if all(r[0] for r in res) else 1
    for bid in ids:
        try:
            Board(bid).publish()
        except PublishError as e:
            print(f"publish: {bid}: {e}", file=sys.stderr)
            rc = 2
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
