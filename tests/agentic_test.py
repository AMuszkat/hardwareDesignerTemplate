#!/usr/bin/env python3
"""
The agentic test: the pipeline the two sessions drive, run end to end on a
synthetic board, each stage timed and checked.

    python3 tests/agentic_test.py            # every stage; exit 1 on any FAIL
    python3 tests/agentic_test.py --quick    # a smaller router problem
    python3 tests/agentic_test.py --keep     # keep the scratch copy, print its path

It is the measurement harness ROADMAP.md asks for ("Measure before and
after"): run it before and after an item lands and compare the numbers. It is
also a regression test: a stage that fails names what broke.

What it does, in the order the sessions do it, on a scratch COPY of this
repository (nothing here is written; the copy is deleted unless --keep):

  env        what this machine has: kicad-cli, pcbnew, poppler
  handoff    concept side -- new_board, a complete fixture design (12 parts,
             two connectors, every pin as data), publish, publish --check,
             netlist.json complete
  schematic  board side -- sch.py plan from that handoff, with a fixture
             symbol library: the ops.json call lists, the preview sheets,
             the drawing standard (check) on the preview, every pin connected,
             every part placed once; with kicad-cli the full proof (netlist
             parity, ERC) as the skill runs it
  router     tools/route/maze.py on a synthetic window: a bundle of nets
             through a funnel past a via field, negotiated to no overlap;
             every joint 0 or 45 degrees; seconds per iteration
  fanout     fanout.py plan and layers on a synthetic 0.5 mm BGA (board-free)
  snapshot   tools/review/snapshot.py on the scratch board (exit 2 without a
             KiCad project or kicad-cli, by design)
  pcb-tools  tools/kpy imports pcbnew (SKIP where KiCad is not installed)
  lint       tools/lint_docs.py on the scratch copy after the publish

A stage is PASS, FAIL or SKIP (a dependency this machine lacks, named). The
report goes to out/agentic/report.{md,json}; out/ is disposable.
"""
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGES = []


def stage(name, status, seconds, detail="", **numbers):
    STAGES.append(dict(name=name, status=status, seconds=round(seconds, 2), detail=detail, numbers=numbers))
    print(f"  {name:10s} {status:4s} {seconds:7.2f} s  {detail}")


def run(cmd, cwd, timeout=1800):
    r = subprocess.run([str(c) for c in cmd], cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def tail(text, n=3):
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return " | ".join(lines[-n:])[:300]


# == env =====================================================================

def have_kicad_cli():
    return shutil.which("kicad-cli") or (os.path.exists("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
                                         and "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")


def have_pcbnew():
    rc, _ = run([ROOT / "kicad_project" / "tools" / "kpy", "-c", "import pcbnew"], ROOT, timeout=120)
    return rc == 0


def stock_symbols():
    for v in ("KICAD10_SYMBOL_DIR", "KICAD9_SYMBOL_DIR", "KICAD8_SYMBOL_DIR"):
        if os.environ.get(v) and Path(os.environ[v]).exists():
            return Path(os.environ[v])
    for p in (Path("/usr/share/kicad/symbols"), Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols")):
        if p.exists():
            return p
    return None


# == the fixture design ======================================================

COMPONENTS = [
    ("U1", "3.3 V LDO", "3.3 V LDO, 500 mA, SOT-23-5", "set", "fixture"),
    ("C1", "U1 input capacitor", "1 uF, 16 V, X7R, 0603", "set", "fixture"),
    ("C2", "U1 output capacitor", "1 uF, 16 V, X7R, 0603", "set", "fixture"),
    ("U2", "microcontroller", "MCU, 16-pin TSSOP", "set", "fixture"),
    ("C3, C4", "U2 decoupling", "100 nF, 10 V, X7R, 0402", "set", "fixture"),
    ("Y1", "U2 crystal", "8 MHz, 3225", "set", "fixture"),
    ("C5, C6", "Y1 load capacitors", "12 pF, C0G, 0402", "set", "fixture"),
    ("R1, R2", "I2C pull-ups", "4.7k 1%, 0402", "set", "fixture"),
    ("R3", "status LED series resistor", "1k 1%, 0402", "set", "fixture"),
    ("D1", "status LED", "green LED, 0603", "set", "fixture"),
    ("J1", "power and I2C in", "1x4 header, 2.54 mm", "set", "fixture"),
    ("J2", "UART header", "1x3 header, 2.54 mm", "set", "fixture"),
]

BOARD_DATA = '''
# -- agentic test fixture (appended by tests/agentic_test.py) ----------------
PURPOSE = "A fixture board for the agentic test: an LDO, an MCU with its crystal, I2C pull-ups, a status LED, two headers."
RELEASE_NOTE = "Fixture publish by tests/agentic_test.py. Nothing is drawn yet."
NETS = [
    ("+5V", "J1 pin 1", "U1.IN, U1.EN, C1", "Input rail; decouple at U1."),
    ("+3V3", "U1.OUT", "U2.VDD, R1, R2, C2, C3, C4", "Logic rail; a pour, not a trace."),
    ("I2C_SDA", "J1 pin 3", "U2.PB7, R1", "Open-drain; 4.7k pull-up to +3V3."),
    ("I2C_SCL", "J1 pin 4", "U2.PB6, R2", "Open-drain; 4.7k pull-up to +3V3."),
    ("UART_TX", "U2.PA2", "J2 pin 1", "3.3 V logic."),
    ("UART_RX", "J2 pin 2", "U2.PA3", "3.3 V logic."),
    ("LED", "U2.PA0", "R3", "Sinks 2 mA through R3 and D1."),
    ("XTAL_IN", "Y1", "U2.OSC_IN, C5", "Keep short; guard with ground."),
    ("XTAL_OUT", "U2.OSC_OUT", "Y1, C6", "Keep short; guard with ground."),
]
CONNECTORS = [
    ("J1", "FIXED (1x4, 2.54 mm)", "Power and I2C in.",
     [("1", "+5V", "in", ""), ("2", "GND", "", ""), ("3", "I2C_SDA", "bidir", ""), ("4", "I2C_SCL", "bidir", "")]),
    ("J2", "FIXED (1x3, 2.54 mm)", "UART console.",
     [("1", "UART_TX", "out", ""), ("2", "UART_RX", "in", ""), ("3", "GND", "", "")]),
]
LAYOUT = [
    ("Keep the LDO input loop (C1, U1.IN, U1.GND) under 5 mm.", ["fixture"]),
    ("Y1 within 5 mm of U2 pins 3 and 4, C5 and C6 beside it, ground under all three.", ["fixture"]),
    ("Routing workmanship: joints 0 / 45 (no 90, no arcs); via to pad 0.15 mm, own net included; "
     "via links straight on the pad's axis, 45 only as a listed last resort; every copper-shape rule applies.",
     ["fixture"]),
]
PINS = {
    "U1": {"1": "+5V", "2": "GND", "3": "+5V", "4": "NC", "5": "+3V3"},
    "C1": ("+5V", "GND"), "C2": ("+3V3", "GND"),
    "U2": {"1": "+3V3", "2": "GND", "3": "XTAL_IN", "4": "XTAL_OUT", "5": "LED", "6": "UART_TX", "7": "UART_RX",
           "8": "I2C_SCL", "9": "I2C_SDA", "10": "GND", "11": "NC", "12": "NC", "13": "NC", "14": "NC",
           "15": "NC", "16": "NC"},
    "C3": ("+3V3", "GND"), "C4": ("+3V3", "GND"),
    "Y1": {"1": "XTAL_IN", "2": "XTAL_OUT", "_swap": [["1", "2"]]},
    "C5": ("XTAL_IN", "GND"), "C6": ("XTAL_OUT", "GND"),
    "R1": ("+3V3", "I2C_SDA"), "R2": ("+3V3", "I2C_SCL"),
    "R3": ("LED", "LED_K"), "D1": {"1": "GND", "2": "LED_K"},
}
RAILS = ("+5V", "+3V3")
'''


def schematic_json(pwr_flags):
    fp = {"R": "Resistor_SMD:R_0402_1005Metric", "C": "Capacitor_SMD:C_0402_1005Metric"}
    return {
        "sheets": [{"name": "Power", "file": "power.kicad_sch"}, {"name": "MCU", "file": "mcu.kicad_sch"}],
        "symbols": {
            "U1": {"lib_id": "agentic:LDO_SOT23-5", "footprint": "Package_TO_SOT_SMD:SOT-23-5", "sheet": "Power",
                   "group": "3.3 V regulator"},
            "U2": {"lib_id": "agentic:MCU16", "footprint": "Package_SO:TSSOP-16_4.4x5mm_P0.65mm",
                   "units": {"1": "MCU", "2": "MCU"}, "group": "Microcontroller"},
            "J1": {"lib_id": "agentic:CONN_1x4", "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
                   "sheet": "Power", "group": "Power and I2C in"},
            "J2": {"lib_id": "agentic:CONN_1x3", "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
                   "sheet": "MCU", "group": "UART"},
            "Y1": {"lib_id": "agentic:XTAL", "footprint": "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", "sheet": "MCU"},
            "D1": {"lib_id": "agentic:LED", "footprint": "LED_SMD:LED_0603_1608Metric", "sheet": "Power",
                   "group": "Status LED"},
            "R3": {"lib_id": "agentic:R", "footprint": fp["R"], "sheet": "Power", "group": "Status LED"},
            "C1": {"lib_id": "agentic:C", "footprint": "Capacitor_SMD:C_0603_1608Metric", "sheet": "Power"},
            "C2": {"lib_id": "agentic:C", "footprint": "Capacitor_SMD:C_0603_1608Metric", "sheet": "Power"},
            "R*": {"lib_id": "agentic:R", "footprint": fp["R"], "sheet": "MCU", "caption": "I2C pull-ups"},
            "C*": {"lib_id": "agentic:C", "footprint": fp["C"], "sheet": "MCU"},
        },
        "pwr_flags": pwr_flags,
        "waive": [],
    }


# -- a fixture symbol library ---------------------------------------------

FONT = "(effects (font (size 1.27 1.27)))"


def _prop(name, val, x, y, hide=False, just="left"):
    h = " (hide yes)" if hide else ""
    j = f" (justify {just})" if just and not hide else ""
    return f'\t\t(property "{name}" "{val}" (at {x:g} {y:g} 0){h} (effects (font (size 1.27 1.27)){j}))\n'


def _pin(kind, x, y, ang, name, num, length=2.54):
    return (f'\t\t\t(pin {kind} line (at {x:g} {y:g} {ang}) (length {length:g}) (name "{name}" {FONT}) '
            f'(number "{num}" {FONT}))\n')


def _rect(x0, y0, x1, y1):
    return (f"\t\t\t(rectangle (start {x0:g} {y0:g}) (end {x1:g} {y1:g}) (stroke (width 0.254) (type default)) "
            f"(fill (type background)))\n")


def box_symbol(name, ref, units):
    """units: [(left pins, right pins)], each pin (number, name, kind); every
    unit's body with its top-left corner at the origin, fields just above it."""
    s = f'\t(symbol "{name}" (pin_names (offset 1.016)) (exclude_from_sim no) (in_bom yes) (on_board yes)\n'
    s += _prop("Reference", ref, 0, 1.27) + _prop("Value", name, 0, 3.81)
    s += _prop("Footprint", "", 0, 0, hide=True) + _prop("Datasheet", "", 0, 0, hide=True)
    s += _prop("Description", "fixture", 0, 0, hide=True)
    for u, (left, right) in enumerate(units, start=1):
        rows = max(len(left), len(right))
        w = 12.7 if rows > 4 else 10.16
        s += f'\t\t(symbol "{name}_{u}_1"\n' + _rect(0, 0, w, -2.54 * (rows + 1))
        for i, (num, pname, kind) in enumerate(left):
            s += _pin(kind, -2.54, -2.54 * (i + 1), 0, pname, num)
        for i, (num, pname, kind) in enumerate(right):
            s += _pin(kind, w + 2.54, -2.54 * (i + 1), 180, pname, num)
        s += "\t\t)\n"
    return s + "\t)\n"


def two_pin_symbol(name, ref, k1, k2, kind="passive"):
    """A vertical two-pin part, the Value beside the body (never inside it)."""
    s = f'\t(symbol "{name}" (pin_numbers (hide yes)) (pin_names (offset 0) (hide yes)) (exclude_from_sim no) (in_bom yes) (on_board yes)\n'
    s += _prop("Reference", ref, 2.54, 1.27) + _prop("Value", name, 2.54, -1.27)
    s += _prop("Footprint", "", 0, 0, hide=True) + _prop("Datasheet", "", 0, 0, hide=True)
    s += _prop("Description", "fixture", 0, 0, hide=True)
    s += f'\t\t(symbol "{name}_1_1"\n' + _rect(-1.016, 2.54, 1.016, -2.54)
    s += _pin(kind, 0, 3.81, 270, k1, "1", 1.27) + _pin(kind, 0, -3.81, 90, k2, "2", 1.27)
    return s + "\t\t)\n\t)\n"


def fixture_library():
    out = '(kicad_symbol_lib (version 20231120) (generator "agentic_test")\n'
    out += two_pin_symbol("R", "R", "1", "2")
    out += two_pin_symbol("C", "C", "1", "2")
    out += two_pin_symbol("XTAL", "Y", "1", "2")
    out += two_pin_symbol("LED", "D", "K", "A")
    out += box_symbol("LDO_SOT23-5", "U", [([("1", "IN", "power_in"), ("3", "EN", "input"), ("2", "GND", "power_in")],
                                            [("5", "OUT", "power_out"), ("4", "NC", "no_connect")])])
    io = [("3", "OSC_IN", "input"), ("4", "OSC_OUT", "output"), ("5", "PA0", "bidirectional"),
          ("6", "PA2", "bidirectional"), ("7", "PA3", "bidirectional"), ("8", "PB6", "bidirectional"),
          ("9", "PB7", "bidirectional")]
    io2 = [("10", "BOOT0", "input")] + [(str(n), f"PB{n - 11}", "bidirectional") for n in range(11, 17)]
    out += box_symbol("MCU16", "U", [([("1", "VDD", "power_in")], [("2", "VSS", "power_in")]), (io, io2)])
    out += box_symbol("CONN_1x4", "J", [([(str(n), f"Pin_{n}", "passive") for n in range(1, 5)], [])])
    out += box_symbol("CONN_1x3", "J", [([(str(n), f"Pin_{n}", "passive") for n in range(1, 4)], [])])
    return out + ")\n"


# == stages ==================================================================

def stage_handoff(copy):
    t0 = time.time()
    rc, out = run([sys.executable, "tools/handoff/new_board.py", "1", "agentic", "Agentic test board",
                   "--fab", "example_standard"], copy)
    if rc:
        stage("handoff", "FAIL", time.time() - t0, "new_board: " + tail(out))
        return None
    bus = copy / "studies" / "components_board1.py"
    bus.write_text(bus.read_text() + "\nCOMPONENTS = " + repr(COMPONENTS) + "\n")
    mod = copy / "tools" / "handoff" / "boards" / "board1.py"
    mod.write_text(mod.read_text() + BOARD_DATA)
    t1 = time.time()
    rc, out = run([sys.executable, "tools/handoff/publish.py", "--board", "board1"], copy)
    t_pub = time.time() - t1
    if rc:
        stage("handoff", "FAIL", time.time() - t0, "publish: " + tail(out))
        return None
    lint = [ln.strip() for ln in out.splitlines() if "LINT" in ln]
    t2 = time.time()
    rc2, out2 = run([sys.executable, "tools/handoff/publish.py", "--check", "--all"], copy)
    t_chk = time.time() - t2
    board = copy / "kicad_project" / "board1_agentic"
    nl = json.loads((board / "handoff" / "netlist.json").read_text())
    pages = sorted(p.name for p in (board / "handoff").iterdir())
    ok = rc2 == 0 and nl.get("complete") is True
    stage("handoff", "PASS" if ok else "FAIL", time.time() - t0,
          f"rev 2 published in {t_pub:.1f} s, --check {t_chk:.1f} s ({'fresh' if rc2 == 0 else 'STALE'}); "
          f"netlist.json {'complete' if nl.get('complete') else 'INCOMPLETE: ' + str(nl.get('prose_only'))}: "
          f"{len(nl['parts'])} parts, {len(nl['nets'])} nets; {len(pages)} handoff files; {len(lint)} lint note(s)",
          publish_s=round(t_pub, 2), check_s=round(t_chk, 2), parts=len(nl["parts"]), nets=len(nl["nets"]),
          files=len(pages), lint=lint)
    return board if ok else None


def stage_schematic(copy, board, kcli, stock):
    t0 = time.time()
    (board / "lib").mkdir(exist_ok=True)
    (board / "lib" / "agentic.kicad_sym").write_text(fixture_library())
    (board / "sym-lib-table").write_text(
        '(sym_lib_table (version 7)\n'
        '  (lib (name "agentic")(type "KiCad")(uri "${KIPRJMOD}/lib/agentic.kicad_sym")(options "")(descr ""))\n'
        '  (lib (name "hw_sch")(type "KiCad")(uri "${KIPRJMOD}/../tools/sch/hw_sch.kicad_sym")(options "")(descr ""))\n)\n')
    flags = ["+5V", "GND"] if (kcli and stock) else []
    (board / "schematic.json").write_text(json.dumps(schematic_json(flags), indent=1))
    sys.path.insert(0, str(copy / "kicad_project" / "tools" / "sch"))
    import plan, emit, model, check          # noqa: E402  (the copy's tools/sch)
    try:
        src = plan.Source.from_handoff(board)
    except SystemExit as e:
        stage("schematic", "FAIL", time.time() - t0, f"plan source: {e}")
        return
    out = board / "sch_plan"
    report = {}

    def prove_structural(src, out):
        prev = Path(out) / "preview" / src.root_name
        design = model.Design(prev)
        # every (ref, unit) exactly once; every part of the source placed
        placed = {}
        for sh in design.sheets:
            for s in sh.parts:
                placed[(s.ref, s.unit)] = placed.get((s.ref, s.unit), 0) + 1
        want = {(ref, u) for ref, p in src.parts.items() for u in p.units}
        missing = sorted(want - set(placed))
        extra = sorted(set(placed) - want)
        twice = sorted(k for k, n in placed.items() if n > 1)
        # every pin end meets a wire end, a label, a no-connect, a sheet pin or another pin
        dangling = []
        for sh in design.sheets:
            pts = {}
            def add(x, y):
                k = (round(x, 2), round(y, 2)); pts[k] = pts.get(k, 0) + 1
            for a, b, _ in sh.wires:
                add(*a); add(*b)
            for lb in sh.labels:
                add(lb.x, lb.y)
            for x, y, _ in sh.no_connects:
                add(x, y)
            for ss in sh.sheets:
                for p in ss.pins:
                    add(p["x"], p["y"])
            ends = []
            for s in sh.symbols:
                for p, (x, y), _ in s.pins():
                    add(x, y); ends.append((s.ref, p.number, (round(x, 2), round(y, 2))))
            for ref, num, k in ends:
                if pts[k] < 2:
                    dangling.append(f"{sh.name}:{ref}.{num}")
        # the drawing standard on the preview; the nets from the plan when kicad-cli is absent
        real = model.netlist
        if not kcli:
            model.netlist = lambda root: ({n: [tuple(x) for x in v] for n, v in src.nets.items()}, {})
        try:
            findings, waived = check.run(design, [], with_svg=bool(kcli))
        finally:
            model.netlist = real
        report.update(sheets=[(sh.name, sh.paper, len(sh.parts), len(sh.wires), len(sh.labels), len(sh.power_syms))
                              for sh in design.sheets],
                      missing=missing, extra=extra, twice=twice, dangling=dangling,
                      findings=[f"{f[0]} {f[1]} {f[3]}" for f in findings])
        ok = not (missing or extra or twice or dangling or findings)
        if kcli:
            ok = emit.prove(src, out) == 0 and ok
        return 0 if ok else 1

    emit.prove = prove_structural
    t1 = time.time()
    import contextlib, io
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):      # the planner's own table goes into the report
            rc = plan.run(src, out)
    except SystemExit as e:
        stage("schematic", "FAIL", time.time() - t0, f"plan: {e}")
        return
    report["planner_output"] = buf.getvalue()
    t_plan = time.time() - t1
    calls = sum(len(json.loads(p.read_text())["calls"]) for p in out.glob("*.ops.json"))
    sheets = "; ".join(f"{n} {paper}: {parts} parts, {wires} wires, {labels} labels, {pwr} power"
                       for n, paper, parts, wires, labels, pwr in report.get("sheets", []))
    bad = []
    for k in ("missing", "extra", "twice", "dangling"):
        if report.get(k):
            bad.append(f"{k} {report[k][:6]}")
    if report.get("findings"):
        bad.append(f"{len(report['findings'])} finding(s): {report['findings'][:4]}")
    stage("schematic", "PASS" if rc == 0 else "FAIL", time.time() - t0,
          f"plan {t_plan:.1f} s; {calls} Konnect calls replaced by the preview; {sheets}"
          + (f"; proof: {'kicad-cli parity + ERC + check' if kcli else 'structural + check on the plan nets (no kicad-cli)'}")
          + ("; " + "; ".join(bad) if bad else ""),
          plan_s=round(t_plan, 2), konnect_calls=calls, sheets=report.get("sheets"), findings=report.get("findings"),
          dangling=report.get("dangling"), proof="kicad-cli" if kcli else "structural",
          planner_output=report.get("planner_output"))


def stage_router(quick):
    t0 = time.time()
    sys.path.insert(0, str(ROOT / "kicad_project" / "tools" / "route"))
    import maze                                   # noqa: E402
    maze.CLR, maze.PADCLR, maze.W, maze.VIA_R = 0.15, 0.2, 0.2, 0.3
    n = 8 if quick else 16
    W_, H_ = (28.0, 14.0) if quick else (40.0, 20.0)
    G = maze.Grid(0.0, 0.0, W_, H_, ["F.Cu", "B.Cu"], step=0.35, strict=True)
    G.diagonal = True
    G.only45 = True
    # a funnel: two walls on both layers with a gap in the middle, and a via field beside it
    gap = 4.5 if quick else 7.0
    xw = W_ * 0.55
    for L_, T_, R_, B_ in ((xw, -1, xw + 1.4, (H_ - gap) / 2), (xw, (H_ + gap) / 2, xw + 1.4, H_ + 1)):
        cells = G.rect(L_, T_, R_, B_, maze.PADCLR + maze.W / 2)
        G.block(G.layers + ["via"], cells)
    for k in range(6):
        G.static_via(xw - 4.0 + 0.8 * (k % 3), H_ / 2 - 2.0 + 1.6 * (k // 3), 0.3)
    ys = [H_ / 2 - 0.9 * (n - 1) / 2 + 0.9 * k for k in range(n)]
    order = list(range(n))
    order[0], order[1], order[-1], order[-2] = order[1], order[0], order[-2], order[-1]   # four crossings
    jobs = []
    for k in range(n):
        jobs.append(dict(net=f"N{k:02d}", src=(1.5, ys[k]), src_layer="F.Cu",
                         goals={G.cell(W_ - 1.5, ys[order[k]])}, goal_layer="F.Cu", allowed={"F.Cu", "B.Cu"}))
    log = []
    t1 = time.time()
    paths = G.negotiate(jobs, iters=80, pres0=0.5, pres_mul=1.1, hist_inc=0.5, hist_decay=0.7, seed=1,
                        log=log.append, via_cost=2.0, limit=400000)
    t_route = time.time() - t1
    iters = sum(1 for ln in log if ln.strip().startswith("iteration"))
    if paths is None:
        stage("router", "FAIL", time.time() - t0, f"no convergence after {iters} iterations: {tail(chr(10).join(log), 2)}",
              iterations=iters, route_s=round(t_route, 2), nets=n)
        return
    bad_joint, conflicts, nvia, nseg, h = 0, 0, 0, 0, hashlib.sha256()
    for net, (runs, vias) in sorted(paths.items()):
        conflicts += len(G.conflicts(net, runs, vias))
        nvia += len(vias)
        for layer, cells in runs:
            pts = G.polyline(cells)
            h.update(f"{net}{layer}{pts}".encode())
            segs = list(zip(pts, pts[1:]))
            nseg += len(segs)
            heads = [math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360 for a, b in segs]
            for a in heads:
                if abs(a / 45 - round(a / 45)) > 1e-6:
                    bad_joint += 1
            for a, b in zip(heads, heads[1:]):
                d = abs(a - b) % 360
                d = min(d, 360 - d)
                if d > 45 + 1e-6:
                    bad_joint += 1
    ok = conflicts == 0 and bad_joint == 0
    stage("router", "PASS" if ok else "FAIL", time.time() - t0,
          f"{n} nets, {G.nx}x{G.ny} cells x 2 layers; converged in {iters} iteration(s), {t_route:.1f} s "
          f"({t_route / max(iters, 1):.2f} s/iteration); {nseg} segments, {nvia} vias; conflicts {conflicts}; "
          f"joints not 0/45: {bad_joint}; result sha {h.hexdigest()[:10]}",
          nets=n, cells=G.nx * G.ny, iterations=iters, route_s=round(t_route, 2),
          s_per_iteration=round(t_route / max(iters, 1), 3), segments=nseg, vias=nvia, conflicts=conflicts,
          bad_joints=bad_joint, result_sha=h.hexdigest()[:16])


def stage_fanout(tmp):
    t0 = time.time()
    balls, k = [], 0
    for i in range(10):
        for j in range(10):
            ring = min(i, j, 9 - i, 9 - j)
            if ring >= 3:
                continue                      # the inner 4 x 4 is depopulated
            if ring == 2:
                net = "VDD" if (i + j) % 2 else "GND"
            elif ring == 0 and (i in (0, 9) and j in (0, 9)):
                net = "GND"
            else:
                net = f"S{k:02d}"
                k += 1
            balls.append(dict(ball=f"{chr(65 + j)}{i + 1}", x=100 + 0.5 * i, y=100 + 0.5 * j, net=net, d=0.25))
    bf, vf, lf, of = tmp / "balls.json", tmp / "vias.json", tmp / "layers.json", tmp / "layout.json"
    bf.write_text(json.dumps(balls))
    tool = ROOT / "kicad_project" / "tools" / "route" / "fanout.py"
    rc, out = run([sys.executable, tool, "plan", "--balls", bf, "--via", "0.4,0.2", "--clearance", "0.0889",
                   "--width", "0.0889", "--skip-rings", "1", "--in-pad", "^VDD", "--share", "^GND$", "--out", vf], ROOT)
    if rc or not vf.exists():
        stage("fanout", "FAIL", time.time() - t0, "plan: " + tail(out))
        return
    vias = json.loads(vf.read_text())
    mode = next((ln.split("MODE:")[1].strip() for ln in out.splitlines() if "MODE:" in ln), "?")
    signals = [b for b in balls if b["net"].startswith("S")]
    inner = [b for b in signals if min(int(b["ball"][1:]) - 1, ord(b["ball"][0]) - 65, 10 - int(b["ball"][1:]), 74 - ord(b["ball"][0])) == 1]
    with_via = {v["net"] for v in vias}
    missing = [b["net"] for b in inner if b["net"] not in with_via]
    lf.write_text(json.dumps({"layers": [
        {"name": "F.Cu", "side": "component", "width": 0.0889, "clearance": 0.0889},
        {"name": "In2.Cu", "width": 0.1016, "clearance": 0.1016, "nfp": True, "hole_clearance": 0.1778},
        {"name": "B.Cu", "width": 0.0889, "clearance": 0.0889}], "nets": ["^S"]}))
    t1 = time.time()
    rc2, out2 = run([sys.executable, tool, "layers", "--balls", bf, "--vias", vf, "--layers", lf, "--out", of], ROOT)
    t_layers = time.time() - t1
    unplaced = next((ln.strip() for ln in out2.splitlines() if ln.strip().startswith("unplaced")), "unplaced ?")
    ok = not missing and rc2 in (0, 1)
    stage("fanout", "PASS" if ok else "FAIL", time.time() - t0,
          f"{len(balls)} balls, {len(signals)} signals; mode {mode}; {len(vias)} vias planned"
          + (f"; inner-ring signals without a via: {missing}" if missing else "")
          + f"; layers over 3 layers in {t_layers:.1f} s: {unplaced} (exit {rc2})",
          balls=len(balls), vias=len(vias), mode=mode, layers_s=round(t_layers, 2), layers_exit=rc2, unplaced=unplaced)


def stage_snapshot(copy, kcli):
    t0 = time.time()
    rc, out = run([sys.executable, "tools/review/snapshot.py", "--board", "board1", "--kind", "schematic", "--id", "RV0"], copy)
    if not kcli:
        stage("snapshot", "SKIP" if rc == 2 else "FAIL", time.time() - t0,
              f"exit {rc} (2 = nothing to review, by design: no kicad-cli here); " + tail(out, 1), exit=rc)
    else:
        stage("snapshot", "PASS" if rc in (0, 1, 2) else "FAIL", time.time() - t0,
              f"exit {rc} on a board with no KiCad project yet; " + tail(out, 1), exit=rc)


def stage_pcb_tools(have):
    t0 = time.time()
    if not have:
        stage("pcb-tools", "SKIP", time.time() - t0, "tools/kpy cannot import pcbnew: KiCad is not installed here; "
              "route.py check / audit, place.py drc, board_setup.py run only on a machine with KiCad")
        return
    rc, out = run([ROOT / "kicad_project" / "tools" / "kpy", "-c", "import pcbnew; print(pcbnew.Version())"], ROOT)
    stage("pcb-tools", "PASS" if rc == 0 else "FAIL", time.time() - t0, "pcbnew " + tail(out, 1))


def stage_lint(copy):
    t0 = time.time()
    rc, out = run([sys.executable, "tools/lint_docs.py"], copy)
    stage("lint", "PASS" if rc == 0 else "FAIL", time.time() - t0, tail(out, 1))


# == main ====================================================================

IGNORE = shutil.ignore_patterns(".git", ".venv", "out", "__pycache__", "*.pyc", ".claude", "tests")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="a smaller router problem")
    ap.add_argument("--keep", action="store_true", help="keep the scratch copy and print its path")
    ap.add_argument("--out", default=str(ROOT / "out" / "agentic"), help="where the report goes")
    a = ap.parse_args()
    t0 = time.time()
    kcli = have_kicad_cli()
    pcb = have_pcbnew()
    stock = stock_symbols()
    poppler = all(shutil.which(x) for x in ("pdftoppm", "pdftotext", "pdfinfo"))
    print(f"agentic test on {ROOT}")
    stage("env", "PASS", 0.0, f"python {sys.version.split()[0]}; kicad-cli {'yes' if kcli else 'no'}; "
          f"pcbnew {'yes' if pcb else 'no'}; KiCad symbols {'yes' if stock else 'no'}; poppler {'yes' if poppler else 'no'}",
          kicad_cli=bool(kcli), pcbnew=pcb, stock_symbols=bool(stock), poppler=poppler)
    tmp = Path(tempfile.mkdtemp(prefix="agentic-"))
    copy = tmp / ROOT.name
    try:
        t1 = time.time()
        shutil.copytree(ROOT, copy, ignore=IGNORE, symlinks=True)
        print(f"  scratch copy in {time.time() - t1:.1f} s: {copy}")
        board = stage_handoff(copy)
        if board:
            stage_schematic(copy, board, kcli, stock)
        else:
            stage("schematic", "SKIP", 0.0, "no handoff to plan from")
        stage_router(a.quick)
        stage_fanout(tmp)
        stage_snapshot(copy, kcli) if board else stage("snapshot", "SKIP", 0.0, "no board")
        stage_pcb_tools(pcb)
        stage_lint(copy)
    finally:
        if a.keep:
            print(f"  kept: {copy}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    fails = [s for s in STAGES if s["status"] == "FAIL"]
    skips = [s for s in STAGES if s["status"] == "SKIP"]
    total = time.time() - t0
    outd = Path(a.out)
    outd.mkdir(parents=True, exist_ok=True)
    (outd / "report.json").write_text(json.dumps(dict(total_s=round(total, 2), stages=STAGES), indent=1))
    md = ["# Agentic test", "", f"Total {total:.1f} s. " + (f"{len(fails)} FAIL. " if fails else "No failure. ")
          + (f"{len(skips)} skipped ({', '.join(s['name'] for s in skips)})." if skips else ""), "",
          "| stage | result | seconds | detail |", "|---|---|---|---|"]
    md += [f"| {s['name']} | {s['status']} | {s['seconds']} | {s['detail']} |" for s in STAGES]
    (outd / "report.md").write_text("\n".join(md) + "\n")
    print(f"\n{'AGENTIC FAIL' if fails else 'AGENTIC PASS'}: {len(STAGES) - len(fails) - len(skips)} pass, "
          f"{len(fails)} fail, {len(skips)} skip in {total:.1f} s -> {outd.relative_to(ROOT) if outd.is_relative_to(ROOT) else outd}/report.md")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
