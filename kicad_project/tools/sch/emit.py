"""
A plan out: the Konnect calls that draw it, and a scratch KiCad project of
it that kicad-cli can net up, ERC and render. The scratch project lives under
the plan's output folder and is never the board's own file.
"""
import json
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import check
import model
import sexpr
from plan import uid, nat
from sexpr import esc

FONT = "(effects (font (size 1.27 1.27))"


def junctions(items):
    """Where three or more wire ends and pin ends meet, or a wire end / pin
    end lands on another wire's span: KiCad's junction dot."""
    c = Counter()
    for a, b in items["wires"]:
        c[check.key(*a)] += 1
        c[check.key(*b)] += 1
    for e in items["pin_ends"]:
        c[check.key(*e)] += 1
    out = {k for k, n in c.items() if n >= 3}
    ends = list(c)
    for a, b in items["wires"]:
        for k in ends:
            p = (k[0] / 100, k[1] / 100)
            if check.key(*p) in (check.key(*a), check.key(*b)):
                continue
            if check.on_segment(p, a, b):
                out.add(k)
    return sorted((k[0] / 100, k[1] / 100) for k in out)


def items_of(block):
    parts, power = [], []
    pin_ends = []
    for e in block.els + block.power:
        for num in e.pins:
            pin_ends.append(e.end(num))
        (parts if e.part else power).append(e)
    return dict(parts=parts, power=power, wires=block.wires, ncs=block.ncs, labels=block.labels,
                pin_ends=pin_ends, sections=list(block.sections))


def text_box(title, box, uuid):
    """A KiCad text box: the frame of a section with its title in the top-left
    corner, one object."""
    x0, y0, x1, y1 = box
    return (f"\t(text_box {esc(title)} (exclude_from_sim no) (at {x0:g} {y0:g} 0) (size {x1 - x0:g} {y1 - y0:g})\n"
            f"\t\t(margins 0.9525 0.9525 0.9525 0.9525) (stroke (width 0) (type default)) (fill (type none))\n"
            f"\t\t(effects (font (size {check.TITLE_SIZE:g} {check.TITLE_SIZE:g}) (thickness 0.4) bold) "
            f"(justify left top)) (uuid {esc(uuid)})\n\t)\n")


# == the Konnect calls =======================================================

def power_ref(e, page, i):
    return ("#FLG" if e.lib_id.endswith("PWR_FLAG") else "#PWR") + f"{page:02d}{i + 1:03d}"


def ops_for(src, name, file, paper, items, sch_path, existing, page):
    calls = []
    if existing is not None:
        refs = sorted({s.ref for s in existing.symbols}, key=nat)
        uuids = [w[2] for w in existing.wires] + [l.uuid for l in existing.labels] + \
                [j[2] for j in existing.junctions]
        if refs or uuids:
            calls.append(dict(tool="batch_delete", args=dict(schematic=sch_path, references=refs, uuids=uuids)))
        if existing.no_connects:
            calls.append(dict(tool="batch_delete_no_connect",
                              args=dict(schematic=sch_path, positions=[dict(x=x, y=y) for x, y, _ in existing.no_connects])))
    calls.append(dict(tool="set_schematic_page", args=dict(schematic=sch_path, size=paper)))
    comps, edits = [], []
    for e in sorted(items["parts"], key=lambda e: (nat(e.ref), e.unit)):
        comps.append(dict(lib_id=e.lib_id, reference=e.ref, value=e.part.value, unit=e.unit,
                          x=e.x, y=e.y, rotation=e.rot))
    seen, new_fields = set(), []
    for e in items["parts"]:
        if e.ref in seen:
            continue
        seen.add(e.ref)
        ed = dict(reference=e.ref)
        if e.part.footprint:
            ed["footprint"] = e.part.footprint
        # batch_edit_schematic_components edits only a field the symbol has; a new one is an annotation
        have = getattr(e.part.lib, "fields", {}) or {}
        old = {k: v for k, v in (e.part.fields or {}).items() if k in have or k in ("Datasheet", "Description")}
        new_fields += [(e.ref, k, v) for k, v in (e.part.fields or {}).items() if k not in old]
        if old:
            ed["fields"] = old
        if len(ed) > 1:
            edits.append(ed)
    if comps:
        calls.append(dict(tool="batch_place_components", args=dict(schematic=sch_path, components=comps)))
        # a sheet keeps its own copy of every symbol it ever placed: a symbol rebuilt in the library
        # would keep its old pins here. Re-embed from the library before any wire, then the fields.
        calls.append(dict(tool="update_symbols_from_library", args=dict(schematic=sch_path, allow_pin_moves=True)))
        calls.append(dict(tool="reset_schematic_field_positions", args=dict(schematic=sch_path)))
    if edits:
        calls.append(dict(tool="batch_edit_schematic_components", args=dict(schematic=sch_path, edits=edits)))
    for ref, k, v in new_fields:
        calls.append(dict(tool="add_component_annotation", args=dict(schematic=sch_path, reference=ref, key=k, value=v)))
    if items["wires"]:
        calls.append(dict(tool="batch_add_wire", args=dict(
            schematic=sch_path, wires=[dict(x1=a[0], y1=a[1], x2=b[0], y2=b[1]) for a, b in items["wires"]])))
    if items["junctions"]:
        calls.append(dict(tool="batch_add_junction", args=dict(
            schematic=sch_path, positions=[dict(x=x, y=y) for x, y in items["junctions"]])))
    if items["ncs"]:
        calls.append(dict(tool="batch_add_no_connect", args=dict(
            schematic=sch_path, positions=[dict(x=x, y=y) for x, y in items["ncs"]])))
    if items["power"]:
        calls.append(dict(tool="batch_place_components", args=dict(schematic=sch_path, components=[
            dict(lib_id=e.lib_id, reference=power_ref(e, page, i), value=e.value, unit=1, x=e.x, y=e.y, rotation=e.rot)
            for i, e in enumerate(items["power"])])))
    for kind, text, x, y, rot in items["labels"]:
        calls.append(dict(tool="add_schematic_net_label", args=dict(
            schematic=sch_path, net=text, x=x, y=y, rotation=rot,
            label_type="hierarchical_label" if kind == "hier" else "net_label",
            **({"shape": "bidirectional"} if kind == "hier" else {}))))
    return calls


def root_ops(src, paper, items, syms, sch_path, existing):
    calls = [dict(tool="set_schematic_page", args=dict(schematic=sch_path, size=paper))]
    have = {s.name: s for s in existing.sheets} if existing is not None else {}
    if existing is not None:
        uuids = [w[2] for w in existing.wires] + [l.uuid for l in existing.labels]
        if uuids:
            calls.append(dict(tool="batch_delete", args=dict(schematic=sch_path, references=[], uuids=uuids)))
    for s in syms:
        geo = dict(x=s["x"], y=s["y"], width=s["w"], height=s["h"])
        if s["name"] in have:
            calls.append(dict(tool="edit_sheet", args=dict(schematic=sch_path, sheet_name=s["name"], **geo)))
            for p in have[s["name"]].pins:
                calls.append(dict(tool="delete_sheet_pin", args=dict(schematic=sch_path, sheet_name=s["name"],
                                                                     pin_name=p["name"])))
        else:
            calls.append(dict(tool="add_hierarchical_sheet", args=dict(
                schematic=sch_path, sheet_name=s["name"], sheet_file=s["file"], project_name=src.root_name, **geo)))
        # add_sheet_pin writes every pin facing right (angle 0) and KiCad then puts a left-edge pin on
        # the right edge, onto whatever pin shares its height there. So: the right-edge pins by hand,
        # then import_sheet_pins(side=left) makes the rest facing left, then each is moved to its spot.
        left = [p for p in s["pins"] if abs(p["x"] - s["x"]) < 0.01]
        for p in s["pins"]:
            if p not in left:
                calls.append(dict(tool="add_sheet_pin", args=dict(schematic=sch_path, sheet_name=s["name"],
                                                                  pin_name=p["name"], pin_type="bidirectional",
                                                                  x=p["x"], y=p["y"])))
        if left:
            calls.append(dict(tool="import_sheet_pins", args=dict(schematic=sch_path, sheet_name=s["name"],
                                                                  side="left")))
            for p in left:
                calls.append(dict(tool="edit_sheet_pin", args=dict(schematic=sch_path, sheet_name=s["name"],
                                                                   pin_name=p["name"], x=p["x"], y=p["y"])))
    if items["wires"]:
        calls.append(dict(tool="batch_add_wire", args=dict(
            schematic=sch_path, wires=[dict(x1=a[0], y1=a[1], x2=b[0], y2=b[1]) for a, b in items["wires"]])))
    for kind, text, x, y, rot in items["labels"]:
        calls.append(dict(tool="add_schematic_net_label", args=dict(
            schematic=sch_path, net=text, x=x, y=y, rotation=rot, label_type="net_label")))
    return calls


# == the scratch project =====================================================

def prop(name, val, x, y, angle=0, hide=False, justify=()):
    j = f" (justify {' '.join(justify)})" if justify else ""
    h = " (hide yes)" if hide else ""
    return f"\t\t(property {esc(name)} {esc(val)} (at {x:g} {y:g} {angle:g}){h} {FONT}{j}))\n"


def sym_text(e, ref, path, proj, dnp=False, extra=None):
    lib = e.lib
    out = [f"\t(symbol (lib_id {esc(e.lib_id)}) (at {e.x:g} {e.y:g} {e.rot:g}) (unit {e.unit})"
           f" (exclude_from_sim no) (in_bom {'no' if ref.startswith('#') else 'yes'})"
           f" (on_board {'no' if ref.startswith('#') else 'yes'}) (dnp {'yes' if dnp else 'no'})\n"
           f"\t\t(uuid {esc(uid())})\n"]
    for name, val in (("Reference", ref), ("Value", e.value)):
        f = lib.fields.get(name, dict(x=0, y=0, angle=0, justify=[], hide=False))
        ox, oy = model.xform(f["x"], f["y"], e.rot)
        out.append(prop(name, val, round(e.x + ox, 4), round(e.y + oy, 4), f["angle"], f["hide"], f["justify"]))
    for name, val in (extra or {}).items():
        out.append(prop(name, val, e.x, e.y, 0, True))
    for num in e.pins:
        out.append(f"\t\t(pin {esc(num)} (uuid {esc(uid())}))\n")
    out.append(f"\t\t(instances (project {esc(proj)} (path {esc(path)} (reference {esc(ref)}) (unit {e.unit}))))\n\t)\n")
    return "".join(out)


def sheet_text(src, paper, items, lib_ids, path, file_uuid, proj, sheets=(), root_uuid=None, page=1):
    out = [f"(kicad_sch (version {src.version}) (generator \"sch.py\") (generator_version \"10.0\")\n"
           f"\t(uuid {esc(file_uuid)})\n\t(paper {esc(paper)})\n"
           f"\t(title_block (title {esc(src.title)}) (date {esc(src.date)}) (rev {esc(src.rev)}))\n\t(lib_symbols\n"]
    for lid in sorted(lib_ids):
        out.append("\t\t" + sexpr.dump(src.lib_nodes[lid], 2) + "\n")
    out.append("\t)\n")
    for a, b in items["wires"]:
        out.append(f"\t(wire (pts (xy {a[0]:g} {a[1]:g}) (xy {b[0]:g} {b[1]:g})) (stroke (width 0) (type default))"
                   f" (uuid {esc(uid())}))\n")
    for x, y in items["junctions"]:
        out.append(f"\t(junction (at {x:g} {y:g}) (diameter 0) (color 0 0 0 0) (uuid {esc(uid())}))\n")
    for x, y in items["ncs"]:
        out.append(f"\t(no_connect (at {x:g} {y:g}) (uuid {esc(uid())}))\n")
    for title, box in items.get("sections", []):
        out.append(text_box(title, box, uid()))
    for kind, text, x, y, rot in items["labels"]:
        if kind == "hier":
            j = "left" if rot in (0, 90) else "right"
            out.append(f"\t(hierarchical_label {esc(text)} (shape bidirectional) (at {x:g} {y:g} {rot})"
                       f" {FONT} (justify {j})) (uuid {esc(uid())}))\n")
        else:
            j = "left bottom" if rot in (0, 90) else "right bottom"
            out.append(f"\t(label {esc(text)} (at {x:g} {y:g} {rot}) {FONT} (justify {j})) (uuid {esc(uid())}))\n")
    for e in sorted(items["parts"], key=lambda e: (nat(e.ref), e.unit)):
        extra = {"Footprint": e.part.footprint, **e.part.fields}
        out.append(sym_text(e, e.ref, path, proj, e.part.dnp, extra))
    for i, e in enumerate(items["power"]):
        out.append(sym_text(e, power_ref(e, page, i), path, proj))
    for s in sheets:
        out.append(f"\t(sheet (at {s['x']:g} {s['y']:g}) (size {s['w']:g} {s['h']:g}) (exclude_from_sim no)"
                   f" (in_bom yes) (on_board yes) (dnp no) (fields_autoplaced yes)\n"
                   f"\t\t(stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0000))\n"
                   f"\t\t(uuid {esc(s['uuid'])})\n")
        out.append(prop("Sheetname", s["name"], s["x"], round(s["y"] - 0.7, 4), 0, False, ("left", "bottom")))
        out.append(prop("Sheetfile", s["file"], s["x"], round(s["y"] + s["h"] + 0.6, 4), 0, False, ("left", "top")))
        for p in s["pins"]:
            ang, j = (180, "left") if p["side"] == "left" else (0, "right")
            out.append(f"\t\t(pin {esc(p['name'])} bidirectional (at {p['x']:g} {p['y']:g} {ang}) (uuid {esc(uid())})"
                       f" {FONT} (justify {j})))\n")
        out.append(f"\t\t(instances (project {esc(proj)} (path {esc('/' + root_uuid)} (page {esc(str(s['page']))}))))\n\t)\n")
    if root_uuid and path == "/" + root_uuid:
        out.append("\t(sheet_instances (path \"/\" (page \"1\")))\n")
    out.append("\t(embedded_fonts no)\n)\n")
    return "".join(out)


def write_all(src, plans, root, out, only=None):
    out = Path(out)
    proj = src.root_name
    prev = out / "preview" / proj
    if prev.exists():
        for f in prev.glob("*"):
            f.unlink()
    prev.mkdir(parents=True, exist_ok=True)
    for table in ("sym-lib-table", "fp-lib-table"):
        f = src.board / table
        text = f.read_text() if f.exists() else f"({table.replace('-', '_')}\n  (version 7)\n)\n"
        text = text.replace("${KIPRJMOD}", str(src.board))
        if table == "sym-lib-table":
            hw = Path(__file__).resolve().parent / "hw_sch.kicad_sym"
            text = text.rstrip()[:-1] + (f'  (lib (name "hw_sch") (type "KiCad") (uri "{hw}") (options "") '
                                          f'(descr ""))\n)\n')
        (prev / table).write_text(text)
    pro = src.board / f"{proj}.kicad_pro"
    (prev / f"{proj}.kicad_pro").write_text(pro.read_text() if pro.exists() else "{}\n")
    root_uuid = uid()
    sheet_uuid = {name: uid() for name, _ in src.sheets[1:]}
    existing = {}
    for name, file in src.sheets:
        f = src.board / file
        existing[name] = model.Sheet(f, name, "/") if f.exists() else None
    summary, plan_json = [], {}
    for page, (name, file) in enumerate(src.sheets[1:], start=2):
        if name not in plans:
            continue
        paper, blk = plans[name]
        items = items_of(blk)
        items["junctions"] = junctions(items)
        sch_path = str(src.board / file)
        calls = ops_for(src, name, file, paper, items, sch_path, existing.get(name), page)
        if not only or only == name:
            (out / f"{Path(file).stem}.ops.json").write_text(json.dumps(dict(sheet=name, file=file, calls=calls), indent=1))
        lib_ids = {e.lib_id for e in items["parts"] + items["power"]}
        path = f"/{root_uuid}/{sheet_uuid[name]}"
        (prev / file).write_text(sheet_text(src, paper, items, lib_ids, path, uid(), proj, page=page))
        summary.append((name, paper, items, calls))
        plan_json[name] = dict(file=file, paper=paper,
                               parts=[dict(ref=e.ref, unit=e.unit, x=e.x, y=e.y, rot=e.rot) for e in items["parts"]],
                               wires=len(items["wires"]), labels=len(items["labels"]), power=len(items["power"]),
                               sections=[dict(title=t, box=list(b)) for t, b in items["sections"]])
    if root:
        paper, blk, syms = root
        items = items_of(blk)
        items["junctions"] = junctions(items)
        for page, s in enumerate(syms, start=2):
            s["uuid"], s["page"] = sheet_uuid[s["name"]], page
        sch_path = str(src.board / src.sheets[0][1])
        calls = root_ops(src, paper, items, syms, sch_path, existing.get(src.sheets[0][0]))
        if not only or only == "Root":
            (out / f"{Path(src.sheets[0][1]).stem}.ops.json").write_text(
                json.dumps(dict(sheet="Root", file=src.sheets[0][1], calls=calls), indent=1))
        (prev / f"{proj}.kicad_sch").write_text(
            sheet_text(src, paper, items, set(), "/" + root_uuid, root_uuid, proj, syms, root_uuid))
        summary.insert(0, ("Root", paper, items, calls))
        plan_json["Root"] = dict(file=src.sheets[0][1], paper=paper, parts=[], sections=[])
    plan_json["_title_block"] = dict(title=src.title, date=src.date, rev=src.rev)
    (out / "plan.json").write_text(json.dumps(plan_json, indent=1))
    (out / "netlist.json").write_text(json.dumps(
        dict(nets={n: v for n, v in src.nets.items()}, nc=sorted(map(list, src.nc)),
             dnp=sorted(r for r, p in src.parts.items() if p.dnp)), indent=1))
    print(f"{'sheet':40} {'paper':5} {'parts':>5} {'wires':>5} {'labels':>6} {'power':>5} {'sects':>5} {'calls':>5}")
    for name, paper, items, calls in summary:
        print(f"{name[:40]:40} {paper:5} {len(items['parts']):5} {len(items['wires']):5} "
              f"{len(items['labels']):6} {len(items['power']):5} {len(items['sections']):5} {len(calls):5}")
    print(f"\nKonnect calls: {sum(len(c) for *_, c in summary)}; ops in {out}/")
    table = src.board / "sym-lib-table"
    if "hw_sch" not in (table.read_text() if table.exists() else ""):
        print("BEFORE DRAWING: the board's sym-lib-table has no hw_sch library. Register it (Konnect, library "
              "toolset): name hw_sch, uri ${KIPRJMOD}/../tools/sch/hw_sch.kicad_sym")


# == the frames, written after Konnect drew the parts ========================

def frame(board, plan_dir, remove=False):
    """Write every planned sheet's section frames (text boxes) and the title
    block into the board's files -- graphics only, after Konnect drew the
    parts where the plan put them. The netlist is exported before and after
    and must not change. Returns the number of frames written."""
    import subprocess
    board, plan_dir = Path(board), Path(plan_dir)
    pj = plan_dir / "plan.json"
    if not pj.exists():
        raise SystemExit(f"sch: {pj} not found -- run sch.py plan first")
    plan = json.loads(pj.read_text())
    tb = plan.get("_title_block", {})
    written = plan.setdefault("_frames", {})
    root = board / f"{board.name}.kicad_sch"
    before, _ = model.netlist(root)
    n = 0
    for name, sh in plan.items():
        if name.startswith("_"):
            continue
        f = board / sh["file"]
        if not f.exists():
            raise SystemExit(f"sch: {f} does not exist -- draw the plan first")
        tree = sexpr.load(f)
        placed = {}
        for s in sexpr.every(tree, "symbol"):
            ref = next((str(p[2]) for p in sexpr.every(s, "property") if str(p[1]) == "Reference"), None)
            at = sexpr.value(s, "at")
            placed[(ref, int(sexpr.atom(s, "unit", "1")))] = (float(at[0]), float(at[1]))
        off = [p for p in sh["parts"] if placed.get((p["ref"], p["unit"])) != (p["x"], p["y"])]
        if off and not remove:
            p = off[0]
            raise SystemExit(f"sch: {name}: {p['ref']} unit {p['unit']} is not at ({p['x']}, {p['y']}) as planned "
                             f"({len(off)} part(s)) -- draw {Path(sh['file']).stem}.ops.json first")
        old = set(written.get(name, []))
        tree[:] = [c for c in tree if not (sexpr.is_node(c, "text_box") and str(sexpr.atom(c, "uuid", "")) in old)]
        if tb:
            node = sexpr.first(tree, "title_block")
            if node is None:
                node = ["title_block"]
                i = next((i for i, c in enumerate(tree) if sexpr.is_node(c, "paper")), len(tree) - 1)
                tree.insert(i + 1, node)
            for k in ("title", "date", "rev"):
                node[:] = [c for c in node if not sexpr.is_node(c, k)]
                node.append([k, sexpr.Q(tb[k])])
        ids = []
        if not remove:
            new = []
            for s in sh["sections"]:
                u = uid()
                ids.append(u)
                new.append(sexpr.parse(text_box(s["title"], s["box"], u)))
            i = next((i for i, c in enumerate(tree) if sexpr.is_node(c, "label") or sexpr.is_node(c, "global_label")
                      or sexpr.is_node(c, "hierarchical_label") or sexpr.is_node(c, "symbol")
                      or sexpr.is_node(c, "sheet") or sexpr.is_node(c, "sheet_instances")
                      or sexpr.is_node(c, "embedded_fonts")), len(tree))
            tree[i:i] = new
        f.write_text(sexpr.dump(tree) + "\n", encoding="utf-8")
        written[name] = ids
        n += len(ids)
    after, _ = model.netlist(root)
    if before != after:
        raise SystemExit("sch: the netlist changed while writing frames -- this must not happen; restore the files "
                         "from git and report it")
    pj.write_text(json.dumps(plan, indent=1))
    return n


def groups_of(nets, drop=()):
    out = set()
    for n, nodes in nets.items():
        g = frozenset(tuple(x) for x in nodes if tuple(x) not in drop)
        if len(g) > 1:
            out.add(g)
    return out


def parity(source_nets, drawn_nets, nc=()):
    """Connectivity, names aside: the multi-pin nets of each side as sets of
    (ref, pin). Returns (missing, extra) -- groups only in the source, only
    in the drawing."""
    a, b = groups_of(source_nets, set(map(tuple, nc))), groups_of(drawn_nets, set(map(tuple, nc)))
    return a - b, b - a


def prove(src, out):
    prev = Path(out) / "preview" / src.root_name
    nets, _ = model.netlist(prev / f"{src.root_name}.kicad_sch")
    missing, extra = parity(src.nets, nets, src.nc)
    print(f"\nnetlist parity (preview vs source): {'IDENTICAL' if not missing and not extra else 'DIFFERENT'}")
    for g in sorted(missing, key=lambda g: sorted(g))[:8]:
        print("  only in the source :", sorted(g)[:6])
    for g in sorted(extra, key=lambda g: sorted(g))[:8]:
        print("  only in the preview:", sorted(g)[:6])
    e, w = model.erc(prev / f"{src.root_name}.kicad_sch")
    print(f"ERC on the preview: {e} errors, {w} warnings")
    design = model.Design(prev)
    sj = Path(src.board) / "schematic.json"                 # the board's waivers hold for its preview too
    waive = json.loads(sj.read_text()).get("waive", []) if sj.exists() else []
    findings, waived = check.run(design, waive)
    by = Counter(f[0] for f in findings)
    print(f"check on the preview: {len(findings)} finding(s) {dict(by) if by else ''}")
    for f in findings[:25]:
        print(f"  {f[0]:10} {f[1][:28]:28} {f[3]}")
    for f, w in waived:
        print(f"  waived {f[0]} {f[2]} -- {w['why']}")
    ok = not missing and not extra and not findings and not e
    print("PLAN PASSES" if ok else "PLAN FAILS")
    return 0 if ok else 1
