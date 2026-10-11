"""
{{TITLE}} -- board data for the handoff publisher.

DATA ONLY. The machinery (hashing, changelog, lint, rendering) lives in
publish.py and is shared by every board; a new board is a new file in
boards/, never a fork of the publisher. Created by new_board.py from
board_template.py -- that file documents every attribute.

    python tools/handoff/publish.py --board {{ID}}

Every attribute below is optional unless marked REQUIRED; an absent one
renders as "none yet" (or its file is not published at all), never as an
invented default. Where it lands in the handoff is given in [brackets].
"""

# -- identity ---------------------------------------------------------------

# REQUIRED. The board folder under kicad_project/: "<id>_<slug>".
BOARD = "{{BOARD}}"

# REQUIRED. Human name, used in headings.               [00_READ_FIRST.md]
TITLE = "{{TITLE}}"

# The value bus: studies/<COMPONENTS_MODULE>.py, holding
#   COMPONENTS = [(refdes, function, value, status, note), ...]
# with status "set" / "proposed" / "tbd" ("tbd" renders as PLACEHOLDER), and
# optionally ACCEPTANCE = {"node": ..., "<quantity>": ..., "note": ...}.
#                                                       [01_components.md]
COMPONENTS_MODULE = "components_{{ID}}"

# What the board exists to prove or deliver, in a paragraph. A board session
# that knows WHY a rule exists breaks it less.             [00_READ_FIRST.md]
PURPOSE = None

# One note per publish, written by hand, appended to the CHANGELOG entry
# under "What actually changed": WHICH OF THE BOARD SESSION'S DRAWN WORK IS
# NOW WRONG, and what to do. Set or clear it on every publish -- an unchanged
# note is warned about and not repeated. An AMENDED revision (a republish
# within 20 minutes of the revision's FIRST publish that changes no new page)
# must describe every change since the previous revision, not just the
# latest.                                                  [CHANGELOG.md]
RELEASE_NOTE = """\
First publish of {{TITLE}}. Nothing is drawn yet, so nothing is wrong yet.
"""

# -- the design -------------------------------------------------------------

# (net, source, loads, constraint) -- every net that carries a constraint.
# The constraint is the requirement as a wiring rule, with its reason.
#                                                       [02_nets.md]
NETS = [
    # ("+3V3", "U1 (LDO) output", "every logic supply pin",
    #  "Decouple at each pin; a pour, not a trace."),
]

# (refdes, status, intro, pins) -- every connector that leaves the board.
# pins: [(pin, net, dir, note), ...]; a pin's net must be in NETS, be a ground
# net (GROUND_NETS, default ("GND",)), or be a pinout signal (the lint checks). The board that OWNS a connector
# defines its pin order; another board mirrors it and says so here.
#                                                       [09_connectors.md]
CONNECTORS = [
    # ("J1", "FIXED (1x4, 2.54 mm)", "Power and UART in.",
    #  [("1", "+5V", "in", ""), ("2", "GND", "", ""),
    #   ("3", "UART_RX", "in", ""), ("4", "UART_TX", "out", "")]),
    # ("J2", "NOT DEFINED", "Do not invent this connector.", []),
]

# A connector whose table uses a system's numbering on a part numbered from 1
# (a module's connector B, pins 101-200, on a 100-pin part): the offset from
# the table's pin to the PART's pin. 09_connectors says so beside the table and
# netlist.json carries the part's pins.             [09_connectors.md, netlist.json]
PIN_OFFSET = {
    # "J2": -100,
}

# (constraint, sources) -- every layout rule, each with where it came from.
# Must be COMPLETE before layout starts.                [10_layout.md]
LAYOUT = [
    # ("Keep the regulator's input loop (C1, U1.VIN, U1.GND) under 5 mm.",
    #  ["U1 datasheet p.12"]),
]

# Choices a review will flag that are intentional, with the reason. The
# board session does not "fix" these.            Markdown. [04_deliberate.md]
DELIBERATE = ""

# What is not decided yet: what each open item blocks and what is safe to
# start meanwhile.                                   Markdown. [05_open.md]
OPEN = ""

# Board-specific rules, appended to the generic rules every board session
# works under.                                          [00_READ_FIRST.md]
FORBIDDEN = [
    # "Do NOT fit both band-selected resistors.",
]

# The ordered power-up sequence and what drives every enable. None = no
# 12_boot.md.                                        Markdown. [12_boot.md]
BOOT = None

# -- pin accounting (a board with a programmable device or a big IC) --------

# Repo-relative CSV, generated on the concept side, with at least the columns
# Pin Number, Pin Name, Signal Name, Function. Setting it publishes
# 03_pinout.csv and 08_pin_accounting.md: every pin assigned, decided, OPEN,
# spare, reserved or UNKNOWN, reconciled to a total.
#                                     [03_pinout.csv, 08_pin_accounting.md]
PINOUT = None
PINOUT_REF = None         # the refdes the pinout belongs to (e.g. "U1"): its pins go into netlist.json
PKG_TOTAL = None          # the package's pin count; a mismatch is flagged

# Unassigned pins are classified by PIN NAME, never by the Function column:
# exact name, then the name without trailing digits (VCC1, VCC2 -> VCC), then
# the prefix up to the first underscore (a hard macro's pads, one answer).
PIN_TREATMENT = {
    # "VCCIO0": "+3V3, 100 nF at the pin",
}
PIN_OPEN = {
    # "VREF": "OPEN -- depends on the ADC choice (05_open.md #2)",
}
SPARE_PINS = None         # regex on PIN NAME for spare user I/O, e.g. r"^P[TBLR]\d+[AB]$"
SPARE_FUNCTION = None     # regex on Function, for vendors whose names don't say
SHARED_PADS = None        # (regex on Function, note) -- signals on dual-use pads
RESERVED_FUNCTION = None  # regex on Function for pins left empty ON PURPOSE (an
                          # escape channel); default: starts with "RESERVED EMPTY"
GROUND_NETS = ("GND",)    # net names the connector lint accepts without a NETS row

# -- connectivity as data ----------------------------------------------------

# Pin -> net for every part whose connections are fixed here and are not in
# the pinout or a connector table: {refdes: {pin: net}}, "NC" for a pin left
# unconnected. With every part covered, handoff/netlist.json is COMPLETE and
# the board session can lay its schematic out from it (`tools/sch/sch.py plan
# --handoff`); short of that it still checks the drawing pin for pin
# (`sch.py parity --handoff`). 02_nets.md stays the place for the WHY.
#                                                       [netlist.json]
PINS = {
    # "C1": {"1": "+3V3", "2": "GND"},
    # "U2": {"1": "+3V3", "2": "GND", "3": "I2C_SCL", "4": "NC"},
}
RAILS = ()                # power nets beyond GROUND_NETS, drawn as power symbols ("+3V3", ...)

# -- generated drawings and other sources -----------------------------------

DIAGRAM = None            # e.g. "design/diagrams/{{ID}}_block.svg"   [06_block_diagram.svg]
FLOORPLAN = None          # e.g. "design/diagrams/{{ID}}_floorplan.svg" [11_floorplan.svg]
ANSWERS = "tools/handoff/answers/{{ID}}.md"           # [07_answers.md]

# Anything else this handoff is derived from, repo-relative -- a module this
# file or the bus imports, studies/designpoint.py if a value is read from it.
# Recorded as provenance; freshness itself is judged on the rendered pages, so
# a source that moves a page is caught whether it is listed here or not.
#                                                       [MANIFEST.json]
SOURCES = []

# Datasheets to publish that the handoff text does not name by their index
# key (an application note, a family datasheet filed under the family name,
# a passive whose DC-bias curve matters).           [13_datasheets.md]
DATASHEETS_EXTRA = []

# -- fabrication ------------------------------------------------------------
#
# None = no 14_fab.md yet. Otherwise a dict that picks a profile from fabs/
# and makes this board's choices. Every unfilled value renders as UNSET and
# board_setup.py refuses to apply the page until it is filled.
#                                                       [14_fab.md]
#
# FAB = {
#     "profile": "myfab_standard",       # fabs/<profile>.py (copy _blank.py)
#     "layers": 4,                       # a stackup the profile has; None until decided
#     "outline": (80, 50),               # mm, W x H; None until decided
#     "quantity": 5,                     # None until decided
#     "size_why": "5 is the prototype minimum.",
#     "layers_why": "A solid ground plane under the switching loop.",
#     # Override or add order-form rows by option name: (option, choice, why)
#     "order": [],
#     # Override Constraints rows by name: (constraint, value, basis). A
#     # board needs finer geometry -> override the tier AND the arithmetic.
#     "constraints": [],
#     # What each stackup layer carries: {"In1.Cu": "GND -- one solid pour"}
#     "carries": {},
#     # (class, nets, track, clearance, via drill / pad, note). nets is a
#     # list or "inside <REF>'s courtyard" (an AREA class: no nets, a custom
#     # clearance rule inside that footprint). via "none" = no vias (a
#     # custom rule). A "Default" row replaces the profile's.
#     "netclasses": [
#         ("Power", ["+5V", "+3V3"], "0.5", "0.2", "0.3 / 0.6", "pours preferred"),
#     ],
#     # Custom DRC rules: (title, rule text, why). Same (rule "fab: <name>")
#     # as a derived rule replaces it -- that is how an exception is stated.
#     "rules": [],
#     "why": [],                         # numbered arithmetic behind the numbers
#     "sources": [],                     # (source, where, what it gave)
#     "order_notes": None,               # None = generated from the order rows
# }
FAB = None
