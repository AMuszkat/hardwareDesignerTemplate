"""A drawn sheet against the same sheet in the plan's preview: the gate that
runs after EACH sheet is drawn, before the next one starts.

    tools/.venv/bin/python tools/sch/sch.py drawn --board board1 [SHEET.kicad_sch ...]

Two comparisons per sheet, both as sets so order and UUIDs do not matter:

  items   every symbol (reference, unit, position, rotation, lib_id, Value),
          wire, label, junction, no-connect and caption text -- the drawing
          is the plan's preview, item for item, or the sheet is not done;
  fields  every field the sheet's ops.json asked for (Footprint, Datasheet,
          MPN ...) is on the drawn symbol with that value -- Konnect's
          batch edit silently skips a field the symbol does not have yet,
          and no netlist check sees a missing MPN;
  DNP     KiCad's own DNP flag on exactly the parts the preview has it on
          (the board's "dnp": true). No Konnect call sets it, so it is the
          one difference left after drawing until it is ticked in eeschema.

Power symbols compare by kind (#PWR / #FLG), not number: Konnect numbers them
as it goes. A missing item is listed with its planned values, ready to be
made with the same Konnect call; an extra one with its position, ready for
batch_delete / batch_delete_no_connect.
"""
import json
from pathlib import Path

import sexpr

R = lambda v: round(float(v), 2)


def items(path):
    t = sexpr.load(path)
    out = set()
    for s in sexpr.every(t, "symbol"):
        at = sexpr.value(s, "at")
        props = {str(p[1]): str(p[2]) for p in sexpr.every(s, "property")}
        ref = props.get("Reference", "")
        ref = "#PWR" if ref.startswith("#PWR") else "#FLG" if ref.startswith("#FLG") else ref
        out.add(("symbol", ref, str(sexpr.atom(s, "unit")), R(at[0]), R(at[1]), R(at[2]) % 360,
                 str(sexpr.atom(s, "lib_id")), props.get("Value", "")))
    for w in sexpr.every(t, "wire"):
        pts = sorted((R(p[1]), R(p[2])) for p in sexpr.every(sexpr.first(w, "pts"), "xy"))
        out.add(("wire",) + tuple(pts))
    for kind in ("label", "hierarchical_label", "global_label"):
        for l in sexpr.every(t, kind):
            at = sexpr.value(l, "at")
            out.add((kind, str(l[1]), R(at[0]), R(at[1]), R(at[2]) % 360))
    for j in sexpr.every(t, "junction"):
        at = sexpr.value(j, "at")
        out.add(("junction", R(at[0]), R(at[1])))
    for n in sexpr.every(t, "no_connect"):
        at = sexpr.value(n, "at")
        out.add(("no_connect", R(at[0]), R(at[1])))
    for n in sexpr.every(t, "text"):
        at = sexpr.value(n, "at")
        out.add(("text", str(n[1]), R(at[0]), R(at[1])))
    for sh in sexpr.every(t, "sheet"):
        name = {str(p[1]): str(p[2]) for p in sexpr.every(sh, "property")}.get("Sheetname", "")
        at, size = sexpr.value(sh, "at"), sexpr.value(sh, "size")
        out.add(("sheet", name, R(at[0]), R(at[1]), R(size[0]), R(size[1])))
        for p in sexpr.every(sh, "pin"):
            at = sexpr.value(p, "at")
            out.add(("sheet_pin", name, str(p[1]), R(at[0]), R(at[1]), R(at[2]) % 360))
    return out


def wanted_fields(ops_path):
    """{(ref, field): value} from the sheet's planned calls."""
    want = {}
    if not ops_path.exists():
        return want
    for c in json.loads(ops_path.read_text())["calls"]:
        if c["tool"] == "batch_edit_schematic_components":
            for e in c["args"]["edits"]:
                if e.get("footprint"):
                    want[(e["reference"], "Footprint")] = e["footprint"]
                for k, v in e.get("fields", {}).items():
                    want[(e["reference"], k)] = v
        elif c["tool"] == "add_component_annotation":
            a = c["args"]
            want[(a["reference"], a["key"])] = a["value"]
    return want


def drawn_fields(path):
    have = {}
    for s in sexpr.every(sexpr.load(path), "symbol"):
        props = {str(p[1]): str(p[2]) for p in sexpr.every(s, "property")}
        for k, v in props.items():
            have.setdefault((props.get("Reference"), k), set()).add(v)
    return have


def dnp_refs(path):
    """References whose KiCad DNP flag is set -- the flag, not a field named DNP."""
    return {str({str(p[1]): str(p[2]) for p in sexpr.every(s, "property")}.get("Reference", ""))
            for s in sexpr.every(sexpr.load(path), "symbol") if sexpr.flag(s, "dnp")}


def run(board, plan_dir, sheets=None):
    """Prints a report per sheet; returns the number of sheets that differ."""
    board, plan_dir = Path(board), Path(plan_dir)
    preview = next(plan_dir.glob("preview/*/"))
    files = sheets or sorted(p.name for p in preview.glob("*.kicad_sch"))
    bad = 0
    for f in files:
        drawn, plan = items(board / f), items(preview / f)
        missing, extra = sorted(plan - drawn, key=str), sorted(drawn - plan, key=str)
        want = wanted_fields(plan_dir / f"{Path(f).stem}.ops.json")
        have = drawn_fields(board / f)
        fields = [(r, k, v, sorted(have.get((r, k), []))) for (r, k), v in sorted(want.items())
                  if have.get((r, k)) != {v}]
        # KiCad's own DNP flag: no Konnect call sets it, so the ops cannot carry it -- the plan's
        # preview does, and a part the board marks DNP is not drawn until someone ticks it
        dnp_want, dnp_have = dnp_refs(preview / f), dnp_refs(board / f)
        print(f"{f}: {len(drawn)} items drawn, {len(plan)} planned; {len(missing)} missing, {len(extra)} extra; "
              f"{len(fields)} field(s) not as planned; DNP flag on {len(dnp_have)} of {len(dnp_want)} planned")
        for x in missing[:20]:
            print("  planned, not drawn:", x)
        for x in extra[:20]:
            print("  drawn, not planned:", x)
        for r, k, v, got in fields[:20]:
            print(f"  field {r}.{k}: planned {v!r}, drawn {got}")
        if dnp_want - dnp_have:
            print(f"  DNP flag not set on {', '.join(sorted(dnp_want - dnp_have))} -- no Konnect call sets it: "
                  "in eeschema, Symbol Properties -> 'Do not populate', after the last redraw")
        if dnp_have - dnp_want:
            print(f"  DNP flag set on {', '.join(sorted(dnp_have - dnp_want))}, which the plan fits")
        bad += bool(missing or extra or fields or dnp_want != dnp_have)
    print("DRAWN AS PLANNED" if not bad else f"NOT AS PLANNED: {bad} sheet(s)")
    return bad
