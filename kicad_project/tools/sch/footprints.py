"""Each placed symbol's Footprint field, as <board>/schematic.json says, on a
drawn schematic -- footprint work finishes after the sheet is drawn, and a
redraw only to fill one field per part costs every UUID on the sheet.

    tools/.venv/bin/python tools/sch/sch.py footprints --board board2 [--dry-run]

The footprint comes from schematic.json "symbols", matched per refdes the way
`plan` matches it (the first key that fnmatches, in file order). A part whose
entry names no footprint is left as it is, and listed; a part with no entry
at all is an error. A library id whose library the board's fp-lib-table does
not name (or KiCad's global one: a stock "Capacitor_SMD:...") is refused, and
so is a name with no .kicad_mod in a board library.

How the files are written (the parsed tree, as `flags` and `frame`): only the
value of the `(property "Footprint" ...)` of placed symbols changes -- each
sheet's tree is compared with those values blanked before and after -- the
netlist's nets are exported by kicad-cli before and after and must be
identical, and the footprints are read back from kicad-cli's netlist. A backup
of every sheet goes to <board>/.sch_backups/<timestamp>/ first. Run it with
KiCad's schematic editor closed (or revert it afterwards); then plan again so
the next `drawn` checks the field too.
"""
import copy
import fnmatch
import json
import shutil
import time
from pathlib import Path

import model
import sexpr


def _props(s):
    return [p for p in sexpr.every(s, "property") if len(p) > 2]


def _ref(s):
    return next((str(p[2]) for p in _props(s) if str(p[1]) == "Reference"), None)


def _blank(tree):
    t = copy.deepcopy(tree)
    for s in sexpr.every(t, "symbol"):
        for p in _props(s):
            if str(p[1]) == "Footprint":
                p[2] = sexpr.Q("")
    return t


def _board_libs(board):
    """Nickname -> .pretty dir for the board's own fp-lib-table."""
    t = board / "fp-lib-table"
    if not t.exists():
        return {}
    out = {}
    for lib in sexpr.every(sexpr.load(t), "lib"):
        name, uri = sexpr.atom(lib, "name"), sexpr.atom(lib, "uri")
        if name and uri:
            out[str(name)] = Path(str(uri).replace("${KIPRJMOD}", str(board)))
    return out


def _check_id(fid, libs):
    lib, _, name = fid.partition(":")
    if not lib or not name:
        return f"'{fid}' is not lib:footprint"
    if lib in libs:
        f = libs[lib] / f"{name}.kicad_mod"
        return None if f.exists() else f"{f} does not exist"
    stock = Path(model.kicad_cli()).resolve().parent.parent / "share" / "kicad" / "footprints"
    for d in (stock, Path("/usr/share/kicad/footprints")):
        if (d / f"{lib}.pretty" / f"{name}.kicad_mod").exists():
            return None
    return f"'{fid}': no library '{lib}' in the board's fp-lib-table, and no such stock footprint"


def run(board, dry_run=False):
    board = Path(board)
    cfg = json.loads((board / "schematic.json").read_text())["symbols"]
    libs = _board_libs(board)
    root = board / f"{board.name}.kicad_sch"
    before, _ = model.netlist(root)
    edits, want, unset, problems = {}, {}, set(), []
    for sh in model.Design(board).sheets:
        tree = sexpr.load(sh.path)
        new = copy.deepcopy(tree)
        n = 0
        for s in sexpr.every(new, "symbol"):
            ref = _ref(s)
            if not ref or ref.startswith("#") or not sexpr.atom(s, "lib_id"):
                continue
            ent = next((v for k, v in cfg.items() if fnmatch.fnmatchcase(ref, k)), None)
            if ent is None:
                problems.append(f"{ref}: no entry in schematic.json symbols")
                continue
            fid = ent.get("footprint", "")
            if not fid:
                unset.add(ref)
                continue
            why = _check_id(fid, libs)
            if why:
                problems.append(f"{ref}: {why}")
                continue
            want[ref] = fid
            p = next((p for p in _props(s) if str(p[1]) == "Footprint"), None)
            if p is None:
                problems.append(f"{ref}: the placed symbol has no Footprint property")
            elif str(p[2]) != fid:
                p[2] = sexpr.Q(fid)
                n += 1
        if _blank(tree) != _blank(new):
            raise SystemExit(f"sch: {sh.path.name}: the edit touched more than Footprint values -- nothing written")
        if n:
            edits[sh.path] = new
    if problems:
        raise SystemExit("sch: nothing written:\n  " + "\n  ".join(problems))
    print(f"{len(want)} part(s) with a footprint in schematic.json; "
          f"{sum(1 for _ in edits)} sheet(s) to change"
          + (f"; no footprint named for {', '.join(sorted(unset))}" if unset else ""))
    if dry_run or not edits:
        print("dry run: nothing written" if dry_run else "nothing to change")
        return 0
    bak = board / ".sch_backups" / time.strftime("%Y%m%d-%H%M%S")
    bak.mkdir(parents=True, exist_ok=True)
    for f in edits:
        shutil.copy2(f, bak / f.name)
    for f, t in edits.items():
        f.write_text(sexpr.dump(t) + "\n", encoding="utf-8")
    after, comps = model.netlist(root)
    if before != after:
        for f in edits:
            shutil.copy2(bak / f.name, f)
        raise SystemExit("sch: the netlist changed -- every sheet restored from the backup")
    bad = sorted(r for r, fid in want.items() if comps.get(r, {}).get("footprint") != fid)
    if bad:
        raise SystemExit(f"sch: read back: Footprint not as schematic.json says on {', '.join(bad)} (backup {bak})")
    print(f"written (backup {bak}); nets unchanged, Footprint read back on all {len(want)}")
    return 0
