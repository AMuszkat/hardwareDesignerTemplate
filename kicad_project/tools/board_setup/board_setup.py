#!/usr/bin/env python3
"""Board Setup from handoff/14_fab.md, written by KiCad's own code.

    tools/kpy tools/board_setup/board_setup.py plan  --board board1_main
    tools/kpy tools/board_setup/board_setup.py apply --board board1_main
    tools/kpy tools/board_setup/board_setup.py check --board board1_main

README.md next to this file is the whole story.  In short:

  plan   parse 14_fab.md, resolve its net names on the board, print the rule
         set and what `apply` would change.  Writes nothing.
  apply  the board must be CLOSED in KiCad.  Backs up, then sets layer count,
         the Constraints table, tenting, the net classes and their nets, the
         Track/Via dropdowns, a generated block in <board>.kicad_dru for the
         rules Board Setup has no field for, and the physical stackup.  Saved
         by pcbnew.SaveBoard, then `check` runs in a fresh process.  Refuses a
         page with an UNSET value.
  check  compare the saved board, project and .kicad_dru with 14_fab.md.
         Exit 1 on any difference, including the stackup and UNSET values.

No API writes a physical stackup in KiCad 10 (SWIG does not wrap
BOARD_STACKUP; IPC answers UpdateBoardStackup with "no handler available").
The `stackup` step (run by `apply`) gets there through
BOARD_DESIGN_SETTINGS.CloneFrom from a scratch copy KiCad has parsed, and
proves the rest of the board and the project unchanged -- see cmd_stackup.

Run it through tools/kpy: pcbnew imports only under the interpreter KiCad
was built for.  A conda or venv python cannot import it.
"""

import argparse
import datetime
import hashlib
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # kicad_project/
sys.path.insert(0, str(ROOT / "tools"))
import boardsel                                     # noqa: E402
BEGIN = "# >>> board_setup"
END = "# <<< board_setup"


def die(msg, code=2):
    print(f"board_setup: {msg}", file=sys.stderr)
    sys.exit(code)


@contextmanager
def quiet_stderr():
    """pcbnew prints wx asserts on import and load; hide them."""
    sys.stderr.flush()
    saved = os.dup(2)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 2)
    try:
        yield
    finally:
        os.dup2(saved, 2)
        os.close(null)
        os.close(saved)


def pcbnew_module():
    try:
        with quiet_stderr():
            import pcbnew
    except ImportError:
        die(f"cannot import pcbnew under {sys.executable}; run it as tools/kpy {Path(__file__).name} ...")
    return pcbnew


# ---------------------------------------------------------------------------
# 14_fab.md
# ---------------------------------------------------------------------------

def clean(cell):
    return cell.replace("**", "").replace("`", "").strip()


def md_tables(text):
    """{'## heading': [header, row, row, ...]} for each table in the file."""
    out, head = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            head = line[3:].strip()
        elif line.startswith("|") and head:
            cells = [clean(c) for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            out.setdefault(head, []).append(cells)
    return out


def section(tables, needle, header):
    hits = [h for h in tables if needle.lower() in h.lower()]
    if len(hits) != 1:
        die(f"14_fab.md: expected one table under a heading containing "
            f"'{needle}', found {len(hits)}")
    rows = tables[hits[0]]
    got = [c.lower() for c in rows[0]]
    if got != header:
        die(f"14_fab.md '{hits[0]}': columns are {rows[0]}, this script "
            f"reads {header}. The page changed shape; update the parser.")
    return rows[1:]


def floats(s):
    return [float(x) for x in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", s)]


def first(s, what):
    v = floats(s)
    if not v:
        die(f"14_fab.md: no number in {what}: '{s}'")
    return v[0]


def unset(cell):
    return "UNSET" in cell


def expand_nets(cell):
    """'+5V, VIN/2, EN' -> ['+5V', 'VIN/2', 'EN']: a comma-separated list of
    net names, each taken literally."""
    return [t.strip() for t in cell.split(",") if t.strip()]


# Constraints-table rows that map 1:1 onto BOARD_DESIGN_SETTINGS.
SIMPLE = {
    "minimum clearance": "m_MinClearance",
    "minimum track width": "m_TrackMinWidth",
    "minimum annular width": "m_ViasMinAnnularWidth",
    "minimum via diameter": "m_ViasMinSize",
    "copper to hole clearance": "m_HoleClearance",
    "copper to edge clearance": "m_CopperEdgeClearance",
    "minimum through hole": "m_MinThroughDrill",
    "hole to hole clearance": "m_HoleToHoleMin",
    "solder mask expansion": "m_SolderMaskExpansion",
    "solder mask minimum web": "m_SolderMaskMinWidth",
}

# Order-form finish -> KiCad's copper_finish name.  A finish not here stops
# the parser: add it with KiCad's own spelling (Board Setup -> Board Finish).
FINISH = {"hasl lead-free": "HAL lead-free", "hasl lead free": "HAL lead-free",
          "hasl": "HAL SnPb", "hasl with lead": "HAL SnPb",
          "enig": "ENIG", "immersion gold": "ENIG", "enepig": "ENEPIG",
          "osp": "OSP", "hard gold": "Hard gold", "immersion silver": "Immersion silver",
          "immersion tin": "Immersion tin", "none": "None"}
COLOURS = ("Green", "Red", "Yellow", "Blue", "Purple", "White", "Black")


def parse_fab(path):
    text = path.read_text()
    t = md_tables(text)
    spec = {
        "source": path,
        "sha": hashlib.sha256(text.encode()).hexdigest()[:16],
        "constraints": {},   # attribute -> mm
        "rules": [], "verbatim_rules": [],         # (name, [constraint lines], condition)
        "notes": [], "unset": [], "unverified": [],
        # an EXAMPLE profile (publish.py says so on the page): practice numbers
        "example": "**EXAMPLE profile**" in text,
    }

    # EVERY cell of every table: a value this parser does not read (a
    # material, an impedance option) is still a value the page promises, and
    # the page says nothing is applied while one is UNSET.
    for head, rows in t.items():
        for r in rows[1:]:
            if any(unset(c) for c in r):
                spec["unset"].append(f"{head.split('→')[-1].strip().lower()}: {r[0]}")

    def hole(where, *cells):
        """True (and recorded) when a cell is UNSET -- the concept session owes it."""
        if any(unset(c) for c in cells):
            return True                     # already recorded by the sweep above
        if any("unverified" in c.lower() for c in cells):
            spec["unverified"].append(where)
        return False

    # -- order form -------------------------------------------------------
    need = {"layers", "thickness", "mask_colour", "silk_colour", "finish", "tent"}
    for opt, choice, _why in section(t, "order form", ["option", "choice", "why"]):
        o, c = opt.lower(), choice.lower()
        if o in ("layers", "thickness", "solder mask", "silkscreen", "surface finish", "via process") \
                and hole(f"order form: {opt}", choice):
            need.discard({"layers": "layers", "thickness": "thickness", "solder mask": "mask_colour",
                          "silkscreen": "silk_colour", "surface finish": "finish",
                          "via process": "tent"}[o])
            continue
        if o == "layers":
            spec["layers"] = int(first(choice, "Layers"))
        elif o == "thickness":
            spec["thickness"] = first(choice, "Thickness")
        elif o == "solder mask":
            spec["mask_colour"] = next((k for k in COLOURS if k.lower() in c), None) or die(f"unknown mask colour '{choice}'")
        elif o == "silkscreen":
            spec["silk_colour"] = next((k for k in COLOURS if k.lower() in c), None) or die(f"unknown silk colour '{choice}'")
        elif o == "surface finish":
            spec["finish"] = FINISH.get(c) or die(f"unknown finish '{choice}'; add it to FINISH")
        elif o == "via process":
            if "untented" in c or "not tented" in c or "open" in c:
                spec["tent"] = False
            elif "tent" in c:
                spec["tent"] = True
            else:
                die(f"via process '{choice}': the script knows 'tenting vias' and 'untented vias'")
    for k in need:
        if k not in spec:
            die(f"14_fab.md order form has no row for '{k}'")

    # -- constraints --------------------------------------------------------
    silk_off_pad = None
    seen = set()
    for name, value, basis in section(t, "→ Constraints", ["constraint", "value", "basis"]):
        n = name.lower()
        seen.add(n)
        if n == "solder mask registration":
            # Informational by design: KiCad has no setting for it and no DRC
            # rule tests it.  A margin that depends on it is arithmetic.
            spec["notes"].append(f"solder mask registration: {value} -- nothing typed into "
                                 "Board Setup, no DRC rule tests it")
            continue
        if hole(f"constraints: {name}", value):
            continue
        if n in SIMPLE:
            spec["constraints"][SIMPLE[n]] = first(value, name)
        elif n.startswith("minimum uvia"):
            if "not used" not in value.lower():
                die(f"'{name}' is now '{value}'; microvias need handling in the script")
        elif n.startswith("silkscreen"):
            m_h = re.search(r"(\d+(?:\.\d+)?)\s*mm high", value)
            m_s = re.search(r"(\d+(?:\.\d+)?)\s*stroke", value)
            m_l = re.search(r"(\d+(?:\.\d+)?)\s*line", value)
            m_p = re.search(r"silk\s*>=\s*(\d+(?:\.\d+)?)\s*mm off every pad", basis)
            if not (m_h and m_s and m_l):
                die(f"cannot read the silkscreen row: '{value}'")
            spec["constraints"]["m_MinSilkTextHeight"] = float(m_h.group(1))
            spec["constraints"]["m_MinSilkTextThickness"] = float(m_s.group(1))
            spec["silk_line"] = float(m_l.group(1))
            spec["notes"].append(
                f"silk line {m_l.group(1)} mm: KiCad has no DRC for silk line "
                "width. Draw silk at that width; nothing checks it.")
            if not m_p:
                die("14_fab.md silkscreen row: the basis must say 'silk >= <x> mm off every "
                    f"pad' (it says: '{basis}'). Without it no silk clearance is set -- "
                    "the page is incomplete: a QUESTIONS.md item.")
            silk_off_pad = float(m_p.group(1))
        elif n == "hole to board edge":
            v = first(value, name)
            spec["rules"].append((
                "fab: hole to board edge",
                [f"(constraint physical_hole_clearance (min {v}mm))"],
                "B.Layer == 'Edge.Cuts'"))
        elif n == "allow bridged solder mask apertures between pads within footprints":
            # Board Setup -> Solder Mask/Paste, board-wide; webs BETWEEN footprints are
            # still checked. Strict yes / no: anything else is a page to read again.
            v = value.strip("* ").lower()
            if v not in ("yes", "no"):
                die(f"'{name}': the script reads 'yes' or 'no', not '{value}'")
            spec["bridges_in_fps"] = v == "yes"
        elif n == "board outline":
            nums = floats(value)
            m_tol = re.search(r"\+-\s*(\d+(?:\.\d+)?)", value)
            spec["outline"] = (nums[0], nums[1], float(m_tol.group(1)) if m_tol else 0.2)
        else:
            die(f"14_fab.md has a constraint this script does not know: '{name}'. "
                "Teach it (SIMPLE or a branch in parse_fab); do not skip it.")
    missing = [k for k in list(SIMPLE) + ["board outline"] if k not in seen]
    if missing:
        die(f"14_fab.md Constraints table lacks: {missing}")
    if silk_off_pad is not None and "m_SolderMaskExpansion" in spec["constraints"]:
        # KiCad measures silk against the MASK OPENING (pad + expansion), so
        # "silk >= X off every pad" is a silk clearance of X - expansion.
        exp = spec["constraints"]["m_SolderMaskExpansion"]
        spec["constraints"]["m_SilkClearance"] = round(silk_off_pad - exp, 4)
        spec["notes"].append(
            f"silk clearance {silk_off_pad - exp:g} mm = 'silk >= {silk_off_pad:g} mm off every pad' "
            f"minus the {exp:g} mm mask expansion: KiCad measures silk to the mask opening.")

    # -- physical stackup -------------------------------------------------
    stack, total = [], None
    for layer, mat, _carries in section(t, "Physical Stackup", ["layer", "material / thickness", "carries"]):
        l = layer.lower()
        if hole(f"stackup: {layer}", layer, mat):
            continue
        if re.fullmatch(r"(f|b|in\d+)\.cu", l):
            m_t = re.search(r"(\d+(?:\.\d+)?)\s*mm", mat)     # "1 oz copper, 0.035 mm"
            stack.append({"kind": "copper", "layer": layer,
                          "t": float(m_t.group(1)) if m_t else first(mat, layer)})
        elif l in ("prepreg", "core"):
            m_t = re.search(r"(\d+(?:\.\d+)?)\s*mm", mat)
            m_dk = re.search(r"Dk\s*(\d+(?:\.\d+)?)", mat)
            if not (m_t and m_dk):
                die(f"cannot read dielectric row '{layer} | {mat}'")
            stack.append({"kind": "dielectric", "type": l, "material": mat.split(",")[0].strip(),
                          "t": float(m_t.group(1)), "er": float(m_dk.group(1))})
        elif l == "total":
            m_tol = re.search(r"\+-\s*(\d+(?:\.\d+)?)\s*%", mat)
            total = (first(mat, "total"), float(m_tol.group(1)) if m_tol else 10.0)
        else:
            die(f"unknown stackup row '{layer}'")
    cu = [s for s in stack if s["kind"] == "copper"]
    if "layers" in spec and stack and len(cu) != spec["layers"]:
        die(f"stackup has {len(cu)} copper layers, order form says {spec['layers']}")
    spec["stackup"], spec["stackup_total"] = stack, total

    # -- net classes --------------------------------------------------------
    classes = []
    for prio, (cls, nets, track, clr, via, note) in enumerate(section(
            t, "Net classes", ["class", "nets", "track", "clearance", "via drill / pad", "note"])):
        if hole(f"net class {cls}", track, clr, via):
            continue
        c = {"name": cls, "track": first(track, f"{cls} track"), "clearance": first(clr, f"{cls} clearance"),
             "nets": [], "area_ref": None, "no_vias": False, "priority": prio}
        v = floats(via)
        if len(v) >= 2:
            c["drill"], c["via"] = v[0], v[1]
            if c["drill"] >= c["via"]:
                die(f"class {cls}: via cell '{via}' reads drill {v[0]} >= pad {v[1]}; "
                    "the column is 'drill / pad'")
        elif "none" in via.lower():
            c["no_vias"] = True
        else:
            die(f"cannot read via cell for class {cls}: '{via}'")
        area = re.search(r"inside ([A-Za-z0-9_]+)'s", nets)
        if cls.lower() == "default":
            if "not listed" not in nets:
                die(f"Default class nets cell changed: '{nets}'")
        elif area:
            c["area_ref"] = area.group(1)
        else:
            c["nets"] = expand_nets(nets)
        classes.append(c)
    if not spec["unset"] and (not classes or classes[0]["name"] != "Default"):
        die("the first net class row must be Default")
    spec["classes"] = classes

    # -- custom DRC rules, taken VERBATIM ---------------------------------
    # Each rule sits in a fenced block on the page.  Transcribe those; do NOT
    # re-derive them from the prose: an exception the page states (one net
    # exempted from a no-via class) is lost the moment a script invents its own.
    m_sec = re.search(r"\n## Custom DRC rules[^\n]*\n((?:.|\n)*?)(?=\n## |\Z)", text)
    if m_sec:
        for block in re.findall(r"```\n((?:.|\n)*?)```", m_sec.group(1)):
            i = 0
            while True:                       # balanced scan: a rule ends where its own paren closes
                i = block.find("(rule", i)
                if i < 0:
                    break
                depth, j = 0, i
                while j < len(block):
                    if block[j] == "(":
                        depth += 1
                    elif block[j] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                spec["verbatim_rules"].append(block[i:j + 1].rstrip())
                i = j + 1
        if not spec["verbatim_rules"]:
            die("14_fab.md has a 'Custom DRC rules' section with no (rule ...) blocks in it")

    for c in classes:
        if c["area_ref"]:
            spec["rules"].append((
                f"fab: {c['name']} class inside {c['area_ref']}",
                [f"(constraint clearance (min {c['clearance']}mm))"],
                f"A.intersectsCourtyard('{c['area_ref']}') && B.intersectsCourtyard('{c['area_ref']}')"))
            spec["notes"].append(
                f"class {c['name']} is an area, not a net list: created with no nets, and its "
                f"{c['clearance']} mm clearance is a custom rule inside {c['area_ref']}'s courtyard.")
        if c["no_vias"]:
            spec["rules"].append((
                f"fab: {c['name']} class has no vias",
                ["(constraint disallow via)"],
                f"A.NetClass == '{c['name']}'"))
    for u in spec["unverified"]:
        spec["notes"].append(f"UNVERIFIED on the page: {u} -- applied as written; "
                             "confirm with the fab before ordering")
    return spec


# ---------------------------------------------------------------------------
# board
# ---------------------------------------------------------------------------

def board_paths(board_dir):
    pcb = boardsel.one_pcb(board_dir)
    return pcb, pcb.with_suffix(".kicad_pro"), pcb.with_suffix(".kicad_dru")


def resolve_nets(spec, netnames):
    """Map each class's page net names to board net names.

    Returns ({board net: class}, errors, absent). `errors` are page defects
    (a name matching two board nets, a net in two classes): a QUESTION.
    `absent` are names with no board net YET -- expected before the netlist
    is pushed or the part is drawn; the rest is applied and they are reported
    as the board session's own work."""
    assign, errors, absent = {}, [], []
    for c in spec["classes"]:
        for n in c["nets"]:
            hits = [b for b in netnames if b == n or b.endswith("/" + n)]
            if not hits:
                absent.append(f"class {c['name']}: net '{n}' is not on the board yet")
                continue
            if len(hits) != 1:
                errors.append(f"class {c['name']}: net '{n}' matches {len(hits)} board nets {hits[:4]}")
                continue
            if hits[0] in assign:
                errors.append(f"net '{hits[0]}' is in both {assign[hits[0]]} and {c['name']}")
            assign[hits[0]] = c["name"]
    return assign, errors, absent


def pattern(net):
    """A net-class assignment that matches `net` and nothing else: KiCad reads
    assignments as wildcard/regex patterns, so `D[0]` would also take `D0`."""
    return re.escape(net).replace("/", "/") if re.search(r"[][*?.+()|^$\\{}]", net) else net


def dru_block(spec):
    src = spec["source"]
    rel = src.relative_to(ROOT) if src.is_relative_to(ROOT) else src.name     # never a machine path
    lines = [f"{BEGIN} -- generated from {rel} (sha256 {spec['sha']}).",
             "# Re-run tools/board_setup/board_setup.py apply instead of editing inside this block."]
    if spec.get("verbatim_rules"):
        # Every rule the page states, exactly as it states it.  The derived set
        # is only used to catch a page that dropped one.
        derived = {n for n, _c, _d in spec["rules"]}
        transcribed = "\n".join(spec["verbatim_rules"])
        for n in sorted(derived):
            if f'"{n}"' not in transcribed:
                die(f"14_fab.md's Custom DRC rules section has no rule named '{n}', but the "
                    f"rest of the page still implies it. The page is wrong: that is a "
                    f"QUESTIONS.md item, not a patch to this script.")
        lines += spec["verbatim_rules"]
    else:
        for name, cons, cond in spec["rules"]:
            lines.append(f'(rule "{name}"')
            lines += [f"  {x}" for x in cons]
            lines.append(f'  (condition "{cond}"))')
    lines.append(END)
    return "\n".join(lines) + "\n"


def dru_text(existing, spec):
    block = dru_block(spec)
    if existing is None or not existing.strip():
        return "(version 1)\n" + block
    if BEGIN in existing and END in existing:
        pre, rest = existing.split(BEGIN, 1)
        post = rest.split(END, 1)[1].lstrip("\n")
        return pre + block + post
    return existing.rstrip("\n") + "\n" + block


def sexpr(text, start):
    """Tokenise the balanced s-expression starting at text[start] == '('."""
    toks, depth = [], 0
    rx = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')
    for m in rx.finditer(text, start):
        tok = m.group(0)
        toks.append(tok)
        depth += tok == "("
        depth -= tok == ")"
        if depth == 0:
            break
    stack = [[]]
    for tok in toks:
        if tok == "(":
            stack.append([])
        elif tok == ")":
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(tok[1:-1] if tok.startswith('"') else tok)
    return stack[0][0]


def read_stackup(pcb):
    """The stackup as saved in the .kicad_pcb, read-only. None if absent."""
    text = pcb.read_text()
    i = text.find("\n\t(setup")   # the board's own; footprints may carry a (stackup) too
    if i < 0:
        return None
    setup = sexpr(text, i + 2)
    tree = next((n for n in setup[1:] if isinstance(n, list) and n[0] == "stackup"), None)
    if tree is None:
        return None
    layers, finish = [], None
    for node in tree[1:]:
        if not isinstance(node, list):
            continue
        if node[0] == "copper_finish":
            finish = node[1]
        elif node[0] == "layer":
            d = {"name": node[1]}
            for kid in node[2:]:
                if isinstance(kid, list) and len(kid) > 1:
                    d.setdefault(kid[0], kid[1])
            layers.append(d)
    return {"layers": layers, "finish": finish}


def stackup_entry_list(spec):
    if not spec["stackup"]:
        return ["Physical stackup: none on the page (UNSET)."]
    out = ["Board Setup -> Board Stackup -> Physical Stackup (and Board Finish):",
           f"  copper layers        {spec.get('layers')}"]
    n = 0
    for s in spec["stackup"]:
        if s["kind"] == "copper":
            out.append(f"  {s['layer']:<20} thickness {s['t']} mm")
        else:
            n += 1
            out.append(f"  Dielectric {n:<9} type {s['type']:<8} thickness {s['t']} mm  "
                       f"material {s['material']}  epsilon_r {s['er']}")
    out += [f"  F.Mask / B.Mask      colour {spec.get('mask_colour')} (thickness: KiCad default)",
            f"  F.Silk / B.Silk      colour {spec.get('silk_colour')}",
            f"  Board Finish         copper finish '{spec.get('finish')}'",
            "  Do NOT press 'Adjust dielectric thickness': the layers are the fab's, the total follows."]
    return out


def mm(x, pcbnew):
    return pcbnew.FromMM(x)


# ---------------------------------------------------------------------------
# physical stackup
# ---------------------------------------------------------------------------
#
# No API writes a stackup in KiCad 10 (SWIG does not wrap BOARD_STACKUP; IPC
# has no UpdateBoardStackup handler).  What SWIG does have is
# BOARD_DESIGN_SETTINGS.CloneFrom().  So:
#   1. copy the board to a scratch folder and put the (stackup) block from
#      14_fab.md into THE SCRATCH COPY's (setup);
#   2. let KiCad's own parser load that copy -- a malformed block fails here,
#      before anything real is touched;
#   3. CloneFrom() the parsed design settings onto the real board (identical
#      apart from the stackup, because the scratch copy WAS the real board)
#      and save it with pcbnew.SaveBoard;
#   4. prove it: the saved board with its stackup and thickness removed must
#      equal the board before, and the .kicad_pro must be unchanged.
# The real board file is only ever written by KiCad.

SIDES = {"F": ("Top", "F.SilkS", "F.Paste", "F.Mask"),
         "B": ("Bottom", "B.SilkS", "B.Paste", "B.Mask")}


def stackup_block(spec):
    t = "\t\t"
    out = [f"{t}(stackup"]

    def layer(name, *kids):
        out.append(f'{t}\t(layer "{name}"')
        out.extend(f"{t}\t\t{k}" for k in kids)
        out.append(f"{t}\t)")
    word, silk, paste, mask = SIDES["F"]
    layer(silk, f'(type "{word} Silk Screen")', f'(color "{spec["silk_colour"]}")')
    layer(paste, f'(type "{word} Solder Paste")')
    layer(mask, f'(type "{word} Solder Mask")', f'(color "{spec["mask_colour"]}")', "(thickness 0.01)")
    n = 0
    for s in spec["stackup"]:
        if s["kind"] == "copper":
            layer(s["layer"], '(type "copper")', f'(thickness {s["t"]:g})')
        else:
            n += 1
            layer(f"dielectric {n}", f'(type "{s["type"]}")', f'(thickness {s["t"]:g})',
                  f'(material "{s["material"]}")', f'(epsilon_r {s["er"]:g})')
    word, silk, paste, mask = SIDES["B"]
    layer(mask, f'(type "{word} Solder Mask")', f'(color "{spec["mask_colour"]}")', "(thickness 0.01)")
    layer(paste, f'(type "{word} Solder Paste")')
    layer(silk, f'(type "{word} Silk Screen")', f'(color "{spec["silk_colour"]}")')
    out += [f'{t}\t(copper_finish "{spec["finish"]}")', f"{t}\t(dielectric_constraints no)", f"{t})"]
    return "\n".join(out) + "\n"


def _balanced_end(text, start):
    depth, i, n, q = 0, start, len(text), False
    while i < n:
        c = text[i]
        if q:
            if c == "\\":
                i += 1
            elif c == '"':
                q = False
        elif c == '"':
            q = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    die("unbalanced s-expression while locating the stackup")


def _without_stackup(text):
    """The board text with the setup's (stackup) block and the general
    thickness removed -- what must be unchanged by the stackup step."""
    i = text.find("\n\t(setup")
    if i >= 0:
        s_end = _balanced_end(text, i + 2)
        j = text.find("(stackup", i, s_end)
        if j >= 0:
            k = _balanced_end(text, j)
            line = text.rfind("\n", 0, j)
            text = text[:line] + text[k:]
    return re.sub(r"\(general\s*\(thickness [0-9.]+\)", "(general (thickness X)", text)


def cmd_stackup(spec, board_dir, args):
    import json
    import tempfile
    refuse_unset(spec)
    pcbnew = pcbnew_module()
    pcb, pro, _dru = board_paths(board_dir)
    before, before_pro = pcb.read_text(), pro.read_text()
    i = before.find("\n\t(setup\n")
    if i < 0:
        die("no (setup) section in the board file")
    head = i + len("\n\t(setup\n")
    s_end = _balanced_end(before, i + 2)
    j = before.find("(stackup", i, s_end)
    block = stackup_block(spec)
    if j >= 0:
        line = before.rfind("\n", 0, j) + 1
        scratch = before[:line] + block.rstrip("\n") + before[_balanced_end(before, j):]
    else:
        scratch = before[:head] + block + before[head:]
    total = sum(s["t"] for s in spec["stackup"]) + 0.02        # + both masks
    with tempfile.TemporaryDirectory(prefix="board_setup_") as d:
        sp = Path(d) / pcb.name
        sp.write_text(scratch)
        shutil.copy2(pro, Path(d) / pro.name)
        with quiet_stderr():
            sc = pcbnew.LoadBoard(str(sp))
        if sc is None or not sc.GetDesignSettings().m_HasStackup:
            die("KiCad did not accept the generated stackup; the board was not touched", 4)
        with quiet_stderr():
            b = pcbnew.LoadBoard(str(pcb))
        ds = b.GetDesignSettings()
        ds.CloneFrom(sc.GetDesignSettings())
        ds.m_HasStackup = True
        ds.SetBoardThickness(mm(round(total, 4), pcbnew))
        with quiet_stderr():
            ok = pcbnew.SaveBoard(str(pcb), b)
    after, after_pro = pcb.read_text(), pro.read_text()
    same_pro = json.loads(after_pro) == json.loads(before_pro)
    if not ok or _without_stackup(after) != _without_stackup(before) or not same_pro:
        pcb.write_text(before)
        pro.write_text(before_pro)
        die("the stackup step changed more than the stackup "
            f"(board {'same' if _without_stackup(after) == _without_stackup(before) else 'DIFFERS'}, "
            f"project {'same' if same_pro else 'DIFFERS'}); both files restored", 4)
    print(f"stackup written ({len(spec['stackup'])} layers, {total:.3f} mm with masks); "
          "the rest of the board and the project are byte-for-byte what they were")
    return 0


# Diff kinds: what fixes it.
APPLY, QUESTION, BOARD = "apply", "QUESTION", "board"


def compare(spec, board_dir):
    """Every difference between the saved board and 14_fab.md.

    Returns (diffs, assign, info); each diff is (what, want, have, kind):
    kind 'apply' = this script fixes it, 'QUESTION' = the page is wrong or
    incomplete (QUESTIONS.md), 'board' = the board session's own drawing work.
    """
    pcbnew = pcbnew_module()
    pcb, pro, dru = board_paths(board_dir)
    with quiet_stderr():
        b = pcbnew.LoadBoard(str(pcb))
    ds = b.GetDesignSettings()
    ns = ds.m_NetSettings
    diffs, info = [], []
    for u in spec["unset"]:
        diffs.append((u, "a value", "UNSET on the page", QUESTION))
    if spec.get("example"):
        diffs.append(("fab profile", "the real fab's numbers",
                      "the template's EXAMPLE profile (illustrative values)", QUESTION))

    def cmp(what, want, have, kind=APPLY, tol=1e-6):
        if isinstance(want, float):
            same = have is not None and abs(want - have) <= tol
        else:
            same = want == have
        if not same:
            diffs.append((what, want, have, kind))

    if "layers" in spec:
        cmp("copper layers", spec["layers"], b.GetCopperLayerCount())
    for attr, want in spec["constraints"].items():
        cmp(attr, float(want), pcbnew.ToMM(getattr(ds, attr)))
    if "tent" in spec:
        cmp("m_TentViasFront", spec["tent"], bool(ds.m_TentViasFront))
        cmp("m_TentViasBack", spec["tent"], bool(ds.m_TentViasBack))
    if "bridges_in_fps" in spec:
        cmp("m_AllowSoldermaskBridgesInFPs", spec["bridges_in_fps"], bool(ds.m_AllowSoldermaskBridgesInFPs))

    # net classes
    netnames = [str(n) for n in b.GetNetsByName().keys() if str(n)]
    assign, errors, absent = resolve_nets(spec, netnames)
    for e in errors:
        diffs.append(("net name", "resolves to one board net", e, QUESTION))
    for e in absent:
        diffs.append(("net not on the board", "push the netlist / draw the part, then re-run apply",
                      e, BOARD))
    # copper left on a layer the page no longer has
    names = {b.GetLayerName(i) for i in b.GetEnabledLayers().CuStack()}
    stray = sorted({b.GetLayerName(x.GetLayer()) for x in list(b.GetTracks()) + list(b.Zones())
                    if b.GetLayerName(x.GetLayer()).endswith(".Cu")
                    and b.GetLayerName(x.GetLayer()) not in names})
    if stray:
        diffs.append(("copper on disabled layers", "none", stray, BOARD))
    have_classes = {str(k): v for k, v in ns.GetNetclasses().items()}
    for c in spec["classes"]:
        nc = ns.GetDefaultNetclass() if c["name"] == "Default" else have_classes.get(c["name"])
        if nc is None:
            diffs.append((f"net class {c['name']}", "exists", "missing", APPLY))
            continue
        cmp(f"{c['name']}.track", c["track"], pcbnew.ToMM(nc.GetTrackWidth()))
        cmp(f"{c['name']}.clearance", c["clearance"], pcbnew.ToMM(nc.GetClearance()))
        if "drill" in c:
            cmp(f"{c['name']}.via_drill", c["drill"], pcbnew.ToMM(nc.GetViaDrill()))
            cmp(f"{c['name']}.via_diameter", c["via"], pcbnew.ToMM(nc.GetViaDiameter()))
    extra = sorted(set(have_classes) - {c["name"] for c in spec["classes"]})
    if extra:
        diffs.append(("net classes not in 14_fab.md", [], extra, APPLY))
    wrong = []
    for n in netnames:
        want = assign.get(n, "Default")
        have = ns.GetEffectiveNetClass(n).GetName()
        if have != want:
            wrong.append(f"{n}: {have} (want {want})")
    if wrong:
        diffs.append(("net -> class", "as 14_fab.md", wrong, APPLY))

    # predefined sizes
    tracks = sorted({c["track"] for c in spec["classes"]})
    vias = sorted({(c["via"], c["drill"]) for c in spec["classes"] if "drill" in c})
    cmp("Track dropdown", tracks, [round(pcbnew.ToMM(w), 4) for w in list(ds.m_TrackWidthList)[1:]])
    cmp("Via dropdown", vias, [(round(pcbnew.ToMM(v.m_Diameter), 4), round(pcbnew.ToMM(v.m_Drill), 4))
                               for v in list(ds.m_ViasDimensionsList)[1:]])

    # custom rules
    have_dru = dru.read_text() if dru.exists() else None
    if have_dru is None or dru_block(spec) not in have_dru:
        diffs.append((dru.name, "generated block current", "missing or stale", APPLY))
    for c in spec["classes"]:
        if c["area_ref"] and b.FindFootprintByReference(c["area_ref"]) is None:
            diffs.append((f"class {c['name']} area", c["area_ref"], "no such footprint on the board", BOARD))

    # outline
    if "outline" in spec:
        pts = []
        for d in b.GetDrawings():
            if d.GetLayer() == pcbnew.Edge_Cuts:
                bb = d.GetBoundingBox()
                w = d.GetWidth() // 2
                pts += [(bb.GetLeft() + w, bb.GetTop() + w), (bb.GetRight() - w, bb.GetBottom() - w)]
        if pts:
            W = pcbnew.ToMM(max(p[0] for p in pts) - min(p[0] for p in pts))
            H = pcbnew.ToMM(max(p[1] for p in pts) - min(p[1] for p in pts))
            ow, oh, _tol = spec["outline"]
            if not (abs(W - ow) <= 1e-3 and abs(H - oh) <= 1e-3):
                diffs.append(("board outline", f"{ow} x {oh} mm", f"{W:.3f} x {H:.3f} mm", BOARD))
        else:
            diffs.append(("board outline", "on Edge.Cuts", "none", BOARD))

    # stackup (read back from the saved file; `apply` writes it via cmd_stackup)
    if spec["stackup"]:
        st = read_stackup(pcb)
        if st is None:
            diffs.append(("physical stackup", "as 14_fab.md", "none saved (KiCad default)", APPLY))
        else:
            have = [l for l in st["layers"] if l.get("type") in ("copper", "prepreg", "core")]
            want = spec["stackup"]
            if len(have) != len(want):
                diffs.append(("stackup layers", len(want), len(have), APPLY))
            for w, h in zip(want, have):
                tag = w.get("layer") or f"dielectric ({w['type']})"
                cmp(f"stackup {tag} thickness", w["t"], float(h.get("thickness", "nan")))
                if w["kind"] == "dielectric":
                    cmp(f"stackup {tag} type", w["type"], h.get("type"))
                    cmp(f"stackup {tag} epsilon_r", w["er"], float(h.get("epsilon_r", "nan")), tol=5e-3)
                    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
                    cmp(f"stackup {tag} material", norm(w["material"]), norm(h.get("material")))
            for l in st["layers"]:
                if "Mask" in l["name"]:
                    cmp(f"stackup {l['name']} colour", spec.get("mask_colour"), l.get("color"))
                if "Silk" in l["name"]:
                    cmp(f"stackup {l['name']} colour", spec.get("silk_colour"), l.get("color"))
            cmp("copper finish", spec.get("finish"), st["finish"])
            tot = sum(float(l.get("thickness", 0)) for l in st["layers"])
            if spec["stackup_total"]:
                want_t, pct = spec["stackup_total"]
                info.append(f"stackup sums to {tot:.3f} mm (14_fab.md: {want_t} mm +-{pct:g} %)")
                if abs(tot - want_t) > want_t * pct / 100:
                    diffs.append(("stackup total", f"{want_t} +-{pct:g} %", round(tot, 3), QUESTION))
    return diffs, assign, info


def report(diffs, info):
    for line in info:
        print(f"  note: {line}")
    if not diffs:
        print("  board matches 14_fab.md")
        return
    for what, want, have, kind in diffs:
        if isinstance(have, list) and len(have) > 6:
            have = have[:6] + [f"... {len(have) - 6} more"]
        print(f"  [{kind:>8}] {what}: want {want}, have {have}")
    kinds = {k for *_, k in diffs}
    if QUESTION in kinds:
        print("  QUESTION = the page is wrong or incomplete: a QUESTIONS.md item, quoting MANIFEST hashes.")
    if BOARD in kinds:
        print("  board    = your own drawing work (outline, placing the footprint an area class names).")


def refuse_unset(spec):
    if spec["unset"]:
        die("14_fab.md has UNSET values -- the concept session owes them, and nothing is "
            "applied until they are filled (a QUESTIONS.md item):\n  " + "\n  ".join(spec["unset"]))


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def locks(pcb):
    return [p for p in (pcb.parent / f"~{pcb.name}.lck", pcb.parent / f"~{pcb.with_suffix('.kicad_pro').name}.lck")
            if p.exists()]


def cmd_plan(spec, board_dir, _args):
    print(f"14_fab.md sha256 {spec['sha']}")
    if spec["unset"]:
        print("UNSET on the page (apply refuses until concept fills them):")
        for u in spec["unset"]:
            print(f"  {u}")
    print("Constraints (mm):")
    for k, v in spec["constraints"].items():
        print(f"  {k:<26} {v}")
    print(f"Layers {spec.get('layers')}, thickness {spec.get('thickness')} mm, "
          f"vias {'tented both sides' if spec.get('tent') else 'not tented'}")
    if "bridges_in_fps" in spec:
        print(f"Solder mask: bridged apertures between pads within footprints "
              f"{'allowed' if spec['bridges_in_fps'] else 'not allowed'} (board-wide)")
    print("Net classes (track / clearance / via drill / via pad):")
    for c in spec["classes"]:
        via = f"{c['drill']} / {c['via']}" if "drill" in c else "no vias (custom rule)"
        who = c["nets"] or (f"area: {c['area_ref']} courtyard" if c["area_ref"] else "everything else")
        print(f"  {c['name']:<10} {c['track']} / {c['clearance']} / {via}   {who}")
    print("Custom rules (.kicad_dru, generated block):")
    print("  " + dru_block(spec).replace("\n", "\n  ").rstrip())
    for n in spec["notes"]:
        print(f"  note: {n}")
    print("\n".join(stackup_entry_list(spec)))
    print("Board vs 14_fab.md:")
    diffs, assign, info = compare(spec, board_dir)
    for net, cls in sorted(assign.items(), key=lambda x: x[1]):
        print(f"  net {net!r} -> {cls}")
    report(diffs, info)
    return 0


def cmd_check(spec, board_dir, _args):
    diffs, _assign, info = compare(spec, board_dir)
    print(f"check {board_dir.name} against 14_fab.md ({spec['sha']}):")
    report(diffs, info)
    return 1 if diffs else 0


def is_stackup(what):
    return what == "physical stackup" or what.startswith("stackup") or what == "copper finish"


def cmd_apply(spec, board_dir, args):
    refuse_unset(spec)
    pcb, pro, dru = board_paths(board_dir)
    held = locks(pcb)
    if held and not args.ignore_lock:
        die("KiCad has this board open (" + ", ".join(p.name for p in held) + "). Save, close the "
            "PROJECT in KiCad (not just the PCB editor: it rewrites .kicad_pro on close), then re-run. "
            "--ignore-lock only for a lock left behind by a crash.", 3)
    diffs, assign, _info = compare(spec, board_dir)
    blocking = [d for d in diffs if d[0] == "net name"]
    if blocking:
        report(blocking, [])
        die("net names in 14_fab.md do not resolve on the board; that is a QUESTIONS.md item")
    if not any(k == APPLY for *_, k in diffs):
        print("nothing for apply to change")
        return cmd_check(spec, board_dir, args)
    rules_diff = any(k == APPLY and not is_stackup(w) for w, _, _, k in diffs)
    stack_diff = any(k == APPLY and is_stackup(w) for w, _, _, k in diffs)

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = board_dir / ".board_setup_backups" / stamp
    backup.mkdir(parents=True)
    for p in (pcb, pro, dru):
        if p.exists():
            shutil.copy2(p, backup / p.name)
    print(f"backup: {backup.relative_to(board_dir)}")

    if rules_diff:
        pcbnew = pcbnew_module()
        with quiet_stderr():
            b = pcbnew.LoadBoard(str(pcb))
        n_items = (len(b.GetFootprints()), len(b.GetTracks()), len(b.Zones()), len(b.GetDrawings()))
        ds = b.GetDesignSettings()
        ns = ds.m_NetSettings

        if b.GetCopperLayerCount() > spec["layers"]:
            keep = {"F.Cu", "B.Cu"} | {f"In{i}.Cu" for i in range(1, spec["layers"] - 1)}
            lost = sorted({b.GetLayerName(x.GetLayer()) for x in list(b.GetTracks()) + list(b.Zones())
                           if b.GetLayerName(x.GetLayer()).endswith(".Cu")} - keep)
            if lost:
                die(f"14_fab.md says {spec['layers']} copper layers but the board has copper on "
                    f"{', '.join(lost)}: move or remove it first (nothing was changed)", 4)
        if b.GetCopperLayerCount() != spec["layers"]:
            b.SetCopperLayerCount(spec["layers"])
        for attr, v in spec["constraints"].items():
            setattr(ds, attr, mm(v, pcbnew))
        ds.m_TentViasFront = spec["tent"]
        ds.m_TentViasBack = spec["tent"]
        if "bridges_in_fps" in spec:
            ds.m_AllowSoldermaskBridgesInFPs = spec["bridges_in_fps"]
        if not ds.m_HasStackup:
            ds.SetBoardThickness(mm(spec["thickness"], pcbnew))

        # Classes that already exist are edited IN PLACE: recreating one resets
        # every field this page has no column for (diff-pair width and gap,
        # colour, ...), which are the board session's own.
        have = {str(k): v for k, v in ns.GetNetclasses().items()}
        want_names = {c["name"] for c in spec["classes"]}
        ns.ClearNetclassPatternAssignments()
        if set(have) - want_names:
            ns.ClearNetclasses()            # only way to drop a class the page dropped
            have = {}
        for c in spec["classes"]:
            nc = (ns.GetDefaultNetclass() if c["name"] == "Default"
                  else have.get(c["name"]) or pcbnew.NETCLASS(c["name"]))
            nc.SetTrackWidth(mm(c["track"], pcbnew))
            nc.SetClearance(mm(c["clearance"], pcbnew))
            if "drill" in c:
                nc.SetViaDrill(mm(c["drill"], pcbnew))
                nc.SetViaDiameter(mm(c["via"], pcbnew))
            if c["name"] != "Default":
                nc.SetPriority(c["priority"])
                if c["name"] not in have:
                    ns.SetNetclass(c["name"], nc)
        for net, cls in assign.items():
            ns.SetNetclassPatternAssignment(pattern(net), cls)

        tracks = sorted({c["track"] for c in spec["classes"]})
        vias = sorted({(c["via"], c["drill"]) for c in spec["classes"] if "drill" in c})
        ds.m_TrackWidthList = pcbnew.intVector([0] + [mm(w, pcbnew) for w in tracks])
        vl = ds.m_ViasDimensionsList
        vl.clear()
        vl.append(pcbnew.VIA_DIMENSION(0, 0))
        for dia, drill in vias:
            vl.append(pcbnew.VIA_DIMENSION(mm(dia, pcbnew), mm(drill, pcbnew)))
        b.SynchronizeNetsAndNetClasses(True)

        if n_items != (len(b.GetFootprints()), len(b.GetTracks()), len(b.Zones()), len(b.GetDrawings())):
            die("item counts changed while setting rules; nothing saved", 4)
        with quiet_stderr():
            ok = pcbnew.SaveBoard(str(pcb), b)
        if not ok:
            die(f"pcbnew.SaveBoard failed; originals are in {backup}", 4)
        dru.write_text(dru_text(dru.read_text() if dru.exists() else None, spec))
        print(f"saved {pcb.name}, {pro.name}, {dru.name}")

    if stack_diff:
        # Its own process too: it loads a scratch copy of the board as a second project.
        sys.stdout.flush()
        r = subprocess.run([sys.executable, __file__, "stackup", "--board", str(board_dir),
                            "--fab", str(spec["source"])])
        if r.returncode != 0:
            die(f"stackup step failed (the board keeps whatever it had); originals are in {backup}", 4)

    # A fresh process: the settings manager in this one still holds the project.
    sys.stdout.flush()
    r = subprocess.run([sys.executable, __file__, "check", "--board", str(board_dir),
                        "--fab", str(spec["source"])])
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "apply", "check", "stackup"])
    ap.add_argument("--board", help="boardN, the folder name or a path (default: the folder you "
                    "are in, else this session's board)")
    ap.add_argument("--fab", help="rule page (default: <board>/handoff/14_fab.md)")
    ap.add_argument("--ignore-lock", action="store_true", help="apply despite a KiCad lock file (stale locks only)")
    args = ap.parse_args()

    board_dir = boardsel.resolve(args.board)
    fab = Path(args.fab).resolve() if args.fab else board_dir / "handoff" / "14_fab.md"
    if not fab.exists():
        die(f"{fab} not found -- the handoff of {board_dir.name} carries no fab page yet "
            "(a QUESTIONS.md item)")
    spec = parse_fab(fab)
    sys.exit({"plan": cmd_plan, "apply": cmd_apply, "check": cmd_check,
              "stackup": cmd_stackup}[args.command](spec, board_dir, args))


if __name__ == "__main__":
    main()
