"""`sch.py draw`: install the plan's proven preview as the board's schematic.

`sch.py plan` already writes every sheet as a complete KiCad file under
<plan>/preview/<project>/ and proves it (netlist parity with the source, ERC,
the drawing standard): the preview IS the drawing. Replaying it through Konnect
call by call only reproduces that file. `draw` copies it instead:

1. UUIDs carried over from the board's current sheets, so the PCB's links
   survive a redraw: the root's, each sheet symbol's (by its file), each
   sheet file's own, and each symbol's (by reference and unit). A footprint
   links to "/<sheet uuid>/<symbol uuid>"; both are kept.
2. The board's sheets backed up under <board>/.sch_backups/draw-<time>/, the
   preview's files written in their place (the board's own library tables are
   kept; hw_sch is registered in its sym-lib-table if it is missing).
3. Each file resaved by kicad-cli (`sch upgrade --force`): the board holds
   KiCad's own text, and the resave must change neither the netlist nor a
   UUID.
4. The write proven: netlist parity with the plan (and DNP flags), parity
   with handoff/netlist.json when it is complete, the drawing standard
   (`check`, the board's waivers), ERC no worse than the preview's.
5. The PCB, when there is one: every footprint's link checked against the new
   sheets -- "linked" (nothing for schupdate.py to re-link) or listed.

Any proof failing restores the backup. Frames, title block, DNP flags and
Footprint fields are in the preview already, so `frame`, `flags` and
`footprints` have nothing left to do after a draw.
"""
import datetime
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import model  # noqa: E402
import sexpr  # noqa: E402

UUID = re.compile(r'"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"')


def _ref_unit(sym):
    ref = next((str(p[2]) for p in sexpr.every(sym, "property") if str(p[1]) == "Reference"), None)
    return ref, int(sexpr.atom(sym, "unit", "1"))


def _ids(tree):
    """(file uuid, {(ref, unit): symbol uuid}, {sheetfile: sheet uuid})."""
    syms = {}
    for s in sexpr.every(tree, "symbol"):
        ref, unit = _ref_unit(s)
        if ref and not ref.startswith("#"):
            syms[(ref, unit)] = str(sexpr.atom(s, "uuid", ""))
    sheets = {}
    for s in sexpr.every(tree, "sheet"):
        f = next((str(p[2]) for p in sexpr.every(s, "property") if str(p[1]) == "Sheetfile"), None)
        if f:
            sheets[f] = str(sexpr.atom(s, "uuid", ""))
    return str(sexpr.atom(tree, "uuid", "")), syms, sheets


def resave(path):
    r = subprocess.run([model.kicad_cli(), "sch", "upgrade", "--force", str(path)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"kicad-cli sch upgrade {Path(path).name}: {(r.stderr or r.stdout).strip()[-200:]}")


def uuid_set(files):
    out = set()
    for f in files:
        _fu, syms, sheets = _ids(sexpr.load(f))
        out |= {("sym",) + k + (v,) for k, v in syms.items()} | {("sheet", k, v) for k, v in sheets.items()}
    return out


def pcb_links(board, files):
    """(linked, [(ref, why)]) for every footprint of the board's PCB against the drawn sheets."""
    pcbs = sorted(Path(board).glob("*.kicad_pcb"))
    if not pcbs:
        return None, []
    root_tree = sexpr.load(files[0])
    _ru, _rs, sheet_of = _ids(root_tree)
    where = {}                                                 # ref -> "/sheet/symbol"
    for f in files[1:]:
        sid = sheet_of.get(Path(f).name)
        for (ref, _unit), u in _ids(sexpr.load(f))[1].items():
            where.setdefault(ref, set()).add(f"/{sid}/{u}")
    for (ref, _unit), u in _ids(root_tree)[1].items():
        where.setdefault(ref, set()).add(f"/{u}")
    text = pcbs[0].read_text()
    linked, bad = 0, []
    for blk in re.finditer(r'\(footprint\s+"[^"]*".*?\(property\s+"Reference"\s+"([^"]+)"', text, re.S):
        ref = blk.group(1)
        seg = text[blk.start():text.find("(pad", blk.start()) if text.find("(pad", blk.start()) > 0 else blk.end()]
        m = re.search(r'\(path\s+"([^"]+)"\)', seg)
        if re.search(r"\(attr[^)]*board_only", seg):
            continue
        if ref not in where:
            bad.append((ref, "on the PCB, not in the schematic"))
        elif not m:
            bad.append((ref, "no symbol path on the footprint"))
        elif m.group(1) not in where[ref]:
            bad.append((ref, f"links {m.group(1)}, the symbol is now {sorted(where[ref])[0]}"))
        else:
            linked += 1
    return linked, bad


def run(board, plan_dir, dry_run=False):
    import emit
    import check as CHECK
    board, plan_dir = Path(board), Path(plan_dir)
    pj = plan_dir / "plan.json"
    prevs = sorted((plan_dir / "preview").glob("*/*.kicad_pro"))
    if not pj.exists() or not prevs:
        raise SystemExit(f"sch: no plan under {plan_dir} -- run sch.py plan first (and draw only a plan that PASSES)")
    prev = prevs[0].parent
    proj = prev.name
    if proj != board.name:
        raise SystemExit(f"sch: the preview is project {proj!r}, the board folder {board.name!r}")
    plan = json.loads(pj.read_text())
    files = [f"{proj}.kicad_sch"] + [sh["file"] for n, sh in plan.items() if not n.startswith("_") and n != "Root"]
    missing = [f for f in files if not (prev / f).exists()]
    if missing:
        raise SystemExit(f"sch: the preview lacks {missing} -- plan again")

    # 1. the UUIDs to carry over
    root_old = board / files[0]
    old_root = _ids(sexpr.load(root_old)) if root_old.exists() else None
    new_root = _ids(sexpr.load(prev / files[0]))
    mapping = {}
    if old_root:
        mapping[new_root[0]] = old_root[0]
        for f, u in new_root[2].items():
            if f in old_root[2]:
                mapping[u] = old_root[2][f]
    kept = 0
    for f in files:
        if not (board / f).exists():
            continue
        ou, osyms, _ = _ids(sexpr.load(board / f))
        nu, nsyms, _ = _ids(sexpr.load(prev / f))
        if f != files[0]:
            mapping[nu] = ou
        for k, u in nsyms.items():
            if k in osyms:
                mapping[u] = osyms[k]
                kept += 1
    texts = {f: UUID.sub(lambda m: f'"{mapping.get(m.group(1), m.group(1))}"', (prev / f).read_text()) for f in files}
    stale = sorted(p.name for p in board.glob("*.kicad_sch") if p.name not in files)
    print(f"draw: {len(files)} sheet file(s) from the plan's preview; {kept} symbol UUID(s) and "
          f"{sum(1 for f in new_root[2] if old_root and f in old_root[2])} sheet UUID(s) carried over"
          + (f"; {', '.join(stale)} not in the plan (left as they are, no longer in the hierarchy)" if stale else ""))
    if dry_run:
        print("(dry run: nothing written)")
        return 0

    # the preview's own ERC, the bar the drawing must meet
    with tempfile.TemporaryDirectory() as td:
        shutil.copytree(prev, Path(td) / proj)
        pe, pw = model.erc(Path(td) / proj / files[0])

    # 2. back up, write
    bak = board / ".sch_backups" / f"draw-{datetime.datetime.now():%Y%m%d-%H%M%S}"
    bak.mkdir(parents=True)
    for p in board.glob("*.kicad_sch"):
        shutil.copy2(p, bak / p.name)
    table = board / "sym-lib-table"
    table_old = table.read_text() if table.exists() else None
    if table_old is not None:
        shutil.copy2(table, bak / "sym-lib-table")

    def restore(why):
        for p in board.glob("*.kicad_sch"):
            if p.name in files and not (bak / p.name).exists():
                p.unlink()
        for p in bak.glob("*.kicad_sch"):
            shutil.copy2(p, board / p.name)
        if table_old is not None:
            table.write_text(table_old)
        print(f"DRAW FAIL: {why}\nthe board's sheets are restored ({bak.relative_to(board)})")
        return 1

    for f, t in texts.items():
        (board / f).write_text(t, encoding="utf-8")
    if not (board / f"{proj}.kicad_pro").exists():
        shutil.copy2(prev / f"{proj}.kicad_pro", board / f"{proj}.kicad_pro")
        print(f"draw: {proj}.kicad_pro created from the preview's (the board had none)")
    if "hw_sch" not in (table_old or ""):
        base = table_old or "(sym_lib_table\n  (version 7)\n)\n"
        entry = '  (lib (name "hw_sch") (type "KiCad") (uri "${KIPRJMOD}/../tools/sch/hw_sch.kicad_sym") (options "") (descr ""))\n'
        table.write_text(base.rstrip()[:-1].rstrip() + "\n" + entry + ")\n")
        print("draw: hw_sch registered in sym-lib-table")

    # 3. resaved by kicad-cli: nothing may move
    try:
        before_nets, _ = model.netlist(board / files[0])
        before_ids = uuid_set([board / f for f in files])
        for f in files:
            resave(board / f)
        after_nets, _ = model.netlist(board / files[0])
        after_ids = uuid_set([board / f for f in files])
    except (RuntimeError, SystemExit) as e:
        return restore(str(e))
    if before_nets != after_nets or before_ids != after_ids:
        return restore("kicad-cli's resave changed the netlist or a UUID")
    print(f"draw: resaved by kicad-cli, netlist and {len(after_ids)} UUID(s) unchanged")

    # 4. the proofs
    ref = json.loads((plan_dir / "netlist.json").read_text())
    miss, extra = emit.parity(ref["nets"], after_nets, ref.get("nc", []))
    if miss or extra:
        return restore(f"netlist parity with the plan: {len(miss)} net(s) not drawn, {len(extra)} drawn otherwise")
    import sch as SCH
    dnp = SCH.dnp_diff(board, set(ref.get("dnp", [])), "the plan")
    if dnp:
        return restore(f"DNP flags not as planned: {dnp[0][1]}")
    print("draw: PARITY OK (the plan)")
    hn = board / "handoff" / "netlist.json"
    if hn.exists() and json.loads(hn.read_text()).get("complete"):
        out, checked, _nl = SCH.handoff_parity(board)
        if out:
            return restore(f"handoff parity: {len(out)} difference(s), first: {out[0][2]}")
        print(f"draw: HANDOFF PARITY OK ({checked} pins)")
    findings, _w = CHECK.run(model.Design(board), SCH.waivers(board))
    if findings:
        return restore(f"check: {len(findings)} finding(s), first: {findings[0][0]} {findings[0][3]}")
    print("draw: CHECKS PASS")
    e, w = model.erc(board / files[0])
    if e is None or (pe is not None and (e > pe or w > pw)):
        return restore(f"ERC {e} error(s), {w} warning(s) against the preview's {pe} / {pw}")
    print(f"draw: ERC {e} error(s), {w} warning(s) (the preview: {pe} / {pw})")

    # 5. the PCB's links
    linked, bad = pcb_links(board, [board / f for f in files])
    if linked is not None:
        print(f"draw: PCB -- {linked} footprint(s) still linked to their symbols"
              + (f"; {len(bad)} not: " + "; ".join(f"{r} {w_}" for r, w_ in bad[:8]) if bad else
                 " (nothing for schupdate.py to re-link)"))
    print(f"DRAW OK: {len(files)} sheet(s) written, zero Konnect calls; backup {bak.relative_to(board)}")
    return 0
