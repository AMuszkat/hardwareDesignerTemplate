#!/usr/bin/env python3
"""Add footprints the schematic has and the board has not, on the CLOSED board.

    tools/kpy tools/place/addfp.py --board board1_main C21 C22 C23
    tools/kpy tools/place/addfp.py --board board1_main --dry-run      # every missing part

Konnect's `update_pcb_from_schematic` is the documented way in, and it needs the
board open in live KiCad with its IPC API answering.  When that path is closed
-- KiCad shut for a scripted step, or an IPC that does not answer
(`GetOpenDocuments` returning AS_UNHANDLED has been seen) -- the `placement`
skill's other sanctioned route is "with KiCad closed, a targeted pcbnew edit
followed by sync", which is this.

It does exactly what the sync would for a NEW part and nothing else:

  * reads the schematic's own netlist (`kicad-cli sch export netlist`) for the
    reference's library footprint, value and the net on every pad,
  * loads that footprint from the library the board's fp-lib-table names,
  * adds it at `--at` (default: parked north-west of the outline, off the board,
    where `place.py check` will complain until it is placed on purpose),
  * sets the pad nets **after** `board.Add`: set before, and the nets are
    silently dropped,
  * copies Reference/Value text size and thickness from `place.py labels`' rule.

It never touches a footprint that is already on the board -- that is
`update_footprints_from_library`'s job, not this one.  Afterwards `place.py drc`
must show the parity entry gone, and `place.py sync` must still exit 0.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import place as P  # noqa: E402


def netlist(pcb):
    # the root sheet is the one named like the PROJECT (the board file's stem),
    # never "the first .kicad_sch": that can be a sub-sheet
    sch = Path(pcb).with_suffix(".kicad_sch")
    if not sch.exists():
        sys.exit(f"{sch.name} is not beside the board: draw the schematic first")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "n.net"
        r = subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadsexpr",
                            "-o", str(out), str(sch)], capture_output=True, text=True)
        if not out.exists():
            sys.exit(f"netlist export failed: {r.stderr.strip()[-300:]}")
        s = out.read_text()
    comps = {}
    for c in re.split(r"\n\t*\(comp\b", s)[1:]:
        ref = re.search(r'\(ref "([^"]+)"\)', c)
        val = re.search(r'\(value "([^"]*)"\)', c)
        fp = re.search(r'\(footprint "([^"]*)"\)', c)
        if ref:
            fields = {k: v for k, v in re.findall(
                r'\(field\s*\(name "([^"]+)"\)\s*"([^"]*)"\)', c)
                if k != "Footprint"}
            ds = re.search(r'\(datasheet "([^"]*)"\)', c)
            if ds and ds.group(1) not in ("", "~"):
                fields.setdefault("Datasheet", ds.group(1))
            props = set(re.findall(r'\(property\s*\(name "([^"]+)"\)\s*\)', c))
            comps[ref.group(1)] = dict(value=val.group(1) if val else "",
                                       fp=fp.group(1) if fp else "", pads={},
                                       fields=fields, props=props)
    for b in re.split(r"\n\t*\(net\b", s)[1:]:
        name = re.search(r'\(name "([^"]*)"\)', b)
        if not name:
            continue
        for ref, pin in re.findall(r'\(ref "([^"]+)"\)\s*\n?\s*\(pin "([^"]+)"', b):
            if ref in comps:
                comps[ref]["pads"][pin] = name.group(1)
    return comps


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("refs", nargs="*")
    ap.add_argument("--board", help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("--at", help="x,y of the first one; the rest step 3 mm east (default: parked)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    a.board = P.board_dir_arg(a.board)

    B = P.Board(a.board)
    pn, b = B.pn, B.b
    if not a.dry_run and P.locks(B.pcb):
        P.die("KiCad has the board open; close it first (this writes with pcbnew)", 3)

    def net(name):
        n = b.FindNet(str(name))
        if n is None:                    # a net this part introduces: a new board has none
            n = pn.NETINFO_ITEM(b, str(name))
            b.Add(n)
        return n

    def stock_dir():
        """KiCad's own footprint directory: the env var KiCad sets, else the usual places."""
        import os
        for k in sorted(os.environ, reverse=True):
            if re.fullmatch(r"KICAD\d*_FOOTPRINT_DIR", k) and Path(os.environ[k]).is_dir():
                return os.environ[k]
        for d in ("/usr/share/kicad/footprints", "/usr/local/share/kicad/footprints",
                  "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"):
            if Path(d).is_dir():
                return d
        return "/usr/share/kicad/footprints"

    def lib_path(nick):
        """The nickname's URI from the project's fp-lib-table, then the global one
        (newest KiCad config first; Linux and macOS locations)."""
        globals_ = sorted(list((Path.home() / ".config" / "kicad").glob("*/fp-lib-table"))
                          + list((Path.home() / "Library" / "Preferences" / "kicad").glob("*/fp-lib-table")),
                          key=lambda t: t.parent.name, reverse=True)
        def expand(uri):
            uri = uri.replace("${KIPRJMOD}", str(Path(a.board).resolve()))
            uri = re.sub(r"\$\{KICAD\d*_FOOTPRINT_DIR\}", lambda _m: stock_dir(), uri)
            return re.sub(r"\$\{KICAD\d*_TEMPLATE_DIR\}",
                          lambda _m: str(Path(stock_dir()).parent / "template"), uri)
        todo_t, seen = [Path(a.board) / "fp-lib-table"] + globals_, set()
        while todo_t:
            table = todo_t.pop(0)
            if table in seen or not table.exists():
                continue
            seen.add(table)
            for m in re.finditer(r'\(lib\s*\(name "([^"]+)"\)\s*\(type "([^"]+)"\)\s*\(uri "([^"]+)"\)',
                                 table.read_text(), re.S):
                if m.group(2) == "Table":            # KiCad 10: a table that names another table
                    todo_t.append(Path(expand(m.group(3))))
                elif m.group(1) == nick:
                    return expand(m.group(3))
        stock = Path(stock_dir()) / f"{nick}.pretty"     # KiCad's own, wherever the table hides it
        if stock.is_dir():
            return str(stock)
        sys.exit(f"footprint library {nick!r} is in no fp-lib-table and is not one of KiCad's own")
    have = set(B.fps)
    comps = netlist(B.pcb)
    todo = [r for r in (a.refs or sorted(comps)) if r not in have]
    if not todo:
        print("nothing to add")
        return 0
    o = B.outline
    x0, y0 = (float(v) for v in a.at.split(",")) if a.at else (o[0], None)
    x_next = x0

    for i, ref in enumerate(todo):
        c = comps.get(ref)
        if not c:
            sys.exit(f"{ref} is not in the schematic netlist")
        if not c["fp"]:
            sys.exit(f"{ref} has no footprint field in the schematic")
        nick, name = c["fp"].split(":", 1)
        print(f"{ref:6s} {c['fp']:44s} pads {c['pads']}")
        if a.dry_run:
            continue
        fp = pn.FootprintLoad(lib_path(nick), name)
        if fp is None:
            sys.exit(f"could not load {c['fp']} from {nick}")
        fp.SetFPID(pn.LIB_ID(nick, name))   # keep the library nickname: without it
        fp.SetReference(ref)                # parity reports footprint_symbol_mismatch
        fp.SetValue(c["value"])
        for k, v in c.get("fields", {}).items():
            fp.SetField(k, v)               # Description and the rest, or parity
                                            # reports footprint_symbol_field_mismatch
        for prop, setter in (("dnp", "SetDNP"), ("exclude_from_bom", "SetExcludedFromBOM"),
                             ("exclude_from_board", "SetExcludedFromPosFiles")):
            if prop in c.get("props", ()) and hasattr(fp, setter):
                getattr(fp, setter)(True)            # or parity reports the attribute differs
        # Parked north of the outline by the part's OWN extent, so a tall part
        # never reaches back onto the board; --at puts the first one where you say.
        bb = fp.GetBoundingBox(False)
        w, h = B.mm(bb.GetWidth()), B.mm(bb.GetHeight())
        cx, cy = B.mm(bb.GetCenter().x - fp.GetPosition().x), B.mm(bb.GetCenter().y - fp.GetPosition().y)
        x = x_next + w / 2 - cx
        y = y0 if y0 is not None else o[1] - 2.0 - h / 2 - cy
        x_next += w + 1.0
        fp.SetPosition(pn.VECTOR2I(pn.FromMM(x), pn.FromMM(y)))
        b.Add(fp)                       # add FIRST
        for p in fp.Pads():              # then the nets: set before Add, they are lost
            n = c["pads"].get(p.GetNumber())
            if n:
                p.SetNet(net(n))
        for t in (fp.Reference(), fp.Value()) if B.text_h > 0 and B.text_t > 0 else ():
            t.SetTextSize(pn.VECTOR2I(pn.FromMM(B.text_h), pn.FromMM(B.text_h)))
            t.SetTextThickness(pn.FromMM(B.text_t))
    if a.dry_run:
        print("(dry run, nothing written)")
        return 0
    bak = Path(str(B.pcb) + ".addfp-bak")
    bak.write_bytes(B.pcb.read_bytes())
    with P.quiet_stderr():
        if not pn.SaveBoard(str(B.pcb), b):
            sys.exit("SaveBoard failed")
    print(f"added {len(todo)}: {' '.join(todo)} (previous copy: {bak.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
