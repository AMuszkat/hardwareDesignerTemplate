#!/usr/bin/env python3
"""Update PCB from Schematic for the parts already on the board, on the CLOSED board.

    tools/kpy tools/place/schupdate.py --board board1 [--dry-run]

What KiCad's Tools > Update PCB from Schematic does to footprints that exist,
with "Re-link footprints to schematic symbols based on their reference
designators" ticked -- from the schematic's own netlist
(`kicad-cli sch export netlist`), nothing guessed:

  * the footprint's symbol path (sheet UUIDs + the symbol's first unit UUID),
    Sheetname and Sheetfile: a redraw gives every symbol a new UUID, and a
    footprint placed by script has no path at all;
  * every pad's net, by the netlist's own name (`/POR_B`, `unconnected-(...)`);
    a pad the netlist does not name gets no net;
  * Value, every symbol field (MPN, Description, ...), and the fields the
    symbol no longer has removed (an old DNP text field);
  * the DNP and Exclude-from-BOM attributes;
  * then the nets no pad uses any more are removed.

Position, side, rotation, footprint and tracks are never touched; a reference
on one side only is refused (add a part with addfp.py; remove one by hand).
Backup `<board>.schupdate-bak`, saved with pcbnew, read back; `place.py drc`'s
schematic parity is the proof (0).

Why not Konnect's `update_pcb_from_schematic` (KiCad open, IPC): it matches by
symbol path, so it refuses a board without paths, and once re-linked it moved
only the pads whose connection changed -- renamed nets, `unconnected-(...)`
names and new fields stayed behind (board1, 2026-11-07: parity 325 after it).
"""

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import place as P  # noqa: E402

KEEP = {"Reference", "Value", "Footprint"}          # the footprint's own, never removed


def schematic(pcb):
    sch = Path(pcb).with_suffix(".kicad_sch")
    if not sch.exists():
        sys.exit(f"{sch.name} is not beside the board")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "n.net"
        r = subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadsexpr",
                            "-o", str(out), str(sch)], capture_output=True, text=True)
        if not out.exists():
            sys.exit(f"netlist export failed: {r.stderr.strip()[-300:]}")
        s = out.read_text()
    end = min([i for i in (s.find("(libparts"), s.find("(nets")) if i > 0] or [len(s)])
    comps = {}
    for c in re.split(r"\n\t*\(comp\b", s[:end])[1:]:
        ref = re.search(r'\(ref "([^"]+)"\)', c)
        val = re.search(r'\(value "([^"]*)"\)', c)
        sp = re.search(r'\(sheetpath\s*\(names "([^"]*)"\)\s*\(tstamps "([^"]*)"\)\s*\)', c)
        # a multi-unit symbol lists one UUID per unit; KiCad keeps the first on the footprint
        ts = re.search(r'\)\s*\(tstamps "([^"]+)"', c[sp.end() - 1:]) if sp else None
        if not (ref and sp and ts):
            sys.exit(f"netlist component without a sheet path or a UUID: {c[:120]!r}")
        fields = dict(re.findall(r'\(field\s*\(name "([^"]+)"\)\s*"([^"]*)"\)', c))
        fields.update({k: "" for k in re.findall(r'\(field\s*\(name "([^"]+)"\)\s*\)', c)})
        props = dict(re.findall(r'\(property\s*\(name "([^"]+)"\)\s*\(value "([^"]*)"\)\s*\)', c))
        flags = set(re.findall(r'\(property\s*\(name "([^"]+)"\)\s*\)', c))
        comps[ref.group(1)] = dict(
            path=sp.group(2) + ts.group(1), sheetname=props.get("Sheetname", ""),
            sheetfile=props.get("Sheetfile", ""), value=val.group(1) if val else "",
            fields={k: v for k, v in fields.items() if k not in KEEP},
            dnp="dnp" in flags, no_bom="exclude_from_bom" in flags, pads={})
    for b in re.split(r"\n\t*\(net\b", s[s.find("(nets"):])[1:]:
        name = re.search(r'\(name "([^"]*)"\)', b)
        if name:
            for ref, pin in re.findall(r'\(ref "([^"]+)"\)\s*\(pin "([^"]+)"', b):
                if ref in comps:
                    # kicad-cli names a multi-unit part's open pin with its unit letter,
                    # "unconnected-(U1C-USB2_DN-PadA23)"; KiCad's update and DRC parity name it
                    # without, "unconnected-(U1-USB2_DN-PadA23)" -- the board takes theirs
                    n = re.sub(rf"^unconnected-\({re.escape(ref)}[A-Z]{{1,2}}-", f"unconnected-({ref}-", name.group(1))
                    comps[ref]["pads"][pin] = n
    return comps


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    a.board = P.board_dir_arg(a.board)
    B = P.Board(a.board)
    pn, b = B.pn, B.b
    if not a.dry_run and P.locks(B.pcb):
        P.die("KiCad has the board open; close it first (this writes with pcbnew)", 3)
    comps = schematic(B.pcb)
    # A board-only footprint (a plain hole, a fiducial) has no symbol by design: KiCad's own
    # update and DRC parity skip it, and so does this.
    fps = {f.GetReference(): f for f in b.GetFootprints() if not f.GetAttributes() & pn.FP_BOARD_ONLY}
    only_sch, only_pcb = sorted(set(comps) - set(fps)), sorted(set(fps) - set(comps))
    if only_sch or only_pcb:
        P.die(f"references on one side only -- schematic: {only_sch[:8]}, board: {only_pcb[:8]}; "
              "add with addfp.py, remove by hand, then run this again", 2)

    nets = {}

    def net(name):
        if name not in nets:
            n = b.FindNet(name)
            if n is None:
                n = pn.NETINFO_ITEM(b, name)
                b.Add(n)
            nets[name] = n
        return nets[name]

    count = dict(path=0, pads=0, value=0, fields=0, removed=0, attrs=0)
    for r, c in sorted(comps.items()):
        f = fps[r]
        if (f.GetPath().AsString(), f.GetSheetname(), f.GetSheetfile()) != (c["path"], c["sheetname"], c["sheetfile"]):
            count["path"] += 1
            if not a.dry_run:
                f.SetPath(pn.KIID_PATH(c["path"]))
                f.SetSheetname(c["sheetname"])
                f.SetSheetfile(c["sheetfile"])
        for p in f.Pads():
            want = c["pads"].get(p.GetNumber(), "")
            if p.GetNetname() != want:
                count["pads"] += 1
                if not a.dry_run:
                    p.SetNet(net(want) if want else b.FindNet(0))
        if f.GetValue() != c["value"]:
            count["value"] += 1
            if not a.dry_run:
                f.SetValue(c["value"])
        for k, v in c["fields"].items():
            if not f.HasField(k) or f.GetFieldText(k) != v:
                count["fields"] += 1
                if not a.dry_run:
                    f.SetField(k, v)
                    if not f.HasField(k):
                        P.die(f"{r}: field {k} did not take", 4)
        # every symbol field but Reference / Value stays off the silk: a new one is
        # created visible at the origin, across the part's own pads (board2 RV4.4)
        for k in c["fields"]:
            if f.HasField(k) and f.GetField(k).IsVisible():
                count["fields"] += 1
                if not a.dry_run:
                    f.GetField(k).SetVisible(False)
        for fld in list(f.GetFields()):
            k = fld.GetName()
            if k not in KEEP and k not in c["fields"] and k not in ("Datasheet", "Description"):
                count["removed"] += 1
                if not a.dry_run:
                    # RemoveNative, never Remove: the Python wrapper's Remove corrupts the footprints
                    # that follow (seen as GetPath() -> a bare SwigPyObject, then a segfault in GC)
                    f.RemoveNative(fld)
        if (f.IsDNP(), f.IsExcludedFromBOM()) != (c["dnp"], c["no_bom"]):
            count["attrs"] += 1
            if not a.dry_run:
                f.SetDNP(c["dnp"])
                f.SetExcludedFromBOM(c["no_bom"])
    print(f"{len(comps)} footprints: {count['path']} re-linked, {count['pads']} pad nets, {count['value']} values, "
          f"{count['fields']} fields set, {count['removed']} fields removed, {count['attrs']} DNP / BOM attributes")
    if a.dry_run:
        print("(dry run, nothing written)")
        return 0
    with P.quiet_stderr():
        try:
            b.RemoveUnusedNets(None)
        except TypeError:
            b.RemoveUnusedNets()
    bak = Path(str(B.pcb) + ".schupdate-bak")
    bak.write_bytes(B.pcb.read_bytes())
    with P.quiet_stderr():
        if not pn.SaveBoard(str(B.pcb), b):
            P.die("SaveBoard failed", 4)
        back = pn.LoadBoard(str(B.pcb))
    bad = []
    for f in back.GetFootprints():
        if f.GetAttributes() & pn.FP_BOARD_ONLY:
            continue
        c = comps[f.GetReference()]
        if f.GetPath().AsString() != c["path"] or any(p.GetNetname() != c["pads"].get(p.GetNumber(), "")
                                                       for p in f.Pads()):
            bad.append(f.GetReference())
    if bad:
        P.die(f"read back: path or pad nets not as written on {bad[:8]} -- restore {bak}", 4)
    print(f"saved; read back OK (backup {bak.name}). Now: tools/kpy tools/place/place.py drc --board <board>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
