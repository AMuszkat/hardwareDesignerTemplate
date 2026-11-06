"""KiCad's own Do-not-populate and Exclude-from-BOM flags on a drawn
schematic -- the two booleans no Konnect call sets (tools/sch/README.md,
"What Konnect cannot do").

    tools/.venv/bin/python tools/sch/sch.py flags --board board1 [--dry-run]

DNP: exactly the parts the plan marks DNP (sch_plan/netlist.json "dnp", from
the board's source: "dnp": true, or the handoff's line). Exclude from BOM:
the parts named in <board>/schematic.json "exclude_from_bom" (refdes globs:
pad-only parts, fiducials, plated holes -- copper with no part). Every other
part gets both flags cleared, so the drawing says exactly what the plan says.

How the files are written (the parsed tree, as `frame` and symfields.py):
only the `(dnp ...)` and `(in_bom ...)` atoms of placed symbols change -- each
sheet's tree is compared with those atoms removed before and after -- the
netlist is exported by kicad-cli before and after and must be identical, and
the flags are read back from the written files. A backup of every sheet goes
to <board>/.sch_backups/<timestamp>/ first. Run it with KiCad's schematic
editor closed (or revert it afterwards).
"""
import copy
import fnmatch
import json
import shutil
import time
from pathlib import Path

import model
import sexpr


def _ref(s):
    return next((str(p[2]) for p in sexpr.every(s, "property") if str(p[1]) == "Reference"), None)


def _set(s, key, yes):
    node = sexpr.first(s, key)
    v = "yes" if yes else "no"
    if node is None:
        i = next((i for i, c in enumerate(s) if sexpr.is_node(c, "uuid")), len(s))
        s.insert(i, [key, v])
        return True
    if str(node[1]) != v:
        node[1:] = [v]
        return True
    return False


def _strip(tree):
    t = copy.deepcopy(tree)
    for s in sexpr.every(t, "symbol"):
        s[:] = [c for c in s if not (sexpr.is_node(c, "dnp") or sexpr.is_node(c, "in_bom"))]
    return t


def run(board, plan_dir, dry_run=False):
    board, plan_dir = Path(board), Path(plan_dir)
    nl = plan_dir / "netlist.json"
    if not nl.exists():
        raise SystemExit(f"sch: {nl} not found -- run sch.py plan first")
    dnp = set(json.loads(nl.read_text()).get("dnp", []))
    cfg = board / "schematic.json"
    globs = json.loads(cfg.read_text()).get("exclude_from_bom", []) if cfg.exists() else []
    root = board / f"{board.name}.kicad_sch"
    design = model.Design(board)
    before, _ = model.netlist(root)
    edits, seen, changed = {}, set(), []
    for sh in design.sheets:
        tree = sexpr.load(sh.path)
        new = copy.deepcopy(tree)
        n = 0
        for s in sexpr.every(new, "symbol"):
            ref = _ref(s)
            if not ref or ref.startswith("#") or not sexpr.atom(s, "lib_id"):
                continue
            seen.add(ref)
            no_bom = any(fnmatch.fnmatchcase(ref, g) for g in globs)
            a = _set(s, "dnp", ref in dnp)
            b = _set(s, "in_bom", not no_bom)
            if a or b:
                n += 1
                changed.append(ref)
        if _strip(tree) != _strip(new):
            raise SystemExit(f"sch: {sh.path.name}: the edit touched more than the two flags -- nothing written")
        if n:
            edits[sh.path] = new
    missing = sorted(dnp - seen)
    if missing:
        raise SystemExit(f"sch: the plan marks {', '.join(missing)} DNP, and the drawing has no such part")
    no_bom = sorted(r for r in seen if any(fnmatch.fnmatchcase(r, g) for g in globs))
    print(f"DNP on {len(dnp)} part(s); exclude from BOM on {len(no_bom)}; "
          f"{len(set(changed))} part instance(s) to change in {len(edits)} sheet(s)")
    if dry_run or not edits:
        print("dry run: nothing written" if dry_run else "nothing to change")
        return 0
    bak = board / ".sch_backups" / time.strftime("%Y%m%d-%H%M%S")
    bak.mkdir(parents=True, exist_ok=True)
    for f in edits:
        shutil.copy2(f, bak / f.name)
    for f, t in edits.items():
        f.write_text(sexpr.dump(t) + "\n", encoding="utf-8")
    after, _ = model.netlist(root)
    if before != after:
        for f in edits:
            shutil.copy2(bak / f.name, f)
        raise SystemExit("sch: the netlist changed -- every sheet restored from the backup")
    got = {}
    for sh in model.Design(board).sheets:
        for s in sh.parts:
            got[s.ref] = got.get(s.ref, False) or s.dnp
    bad = sorted(r for r in seen if got.get(r, False) != (r in dnp))
    if bad:
        raise SystemExit(f"sch: read back: DNP not as planned on {', '.join(bad)} (backup {bak})")
    print(f"written (backup {bak}); netlist unchanged, DNP read back on exactly {len(dnp)} part(s)")
    return 0
