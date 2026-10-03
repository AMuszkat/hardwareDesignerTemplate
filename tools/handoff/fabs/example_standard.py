"""
Fab profile -- EXAMPLE: a conservative "standard prototype" service.

⚠ NOT ANY FAB'S NUMBERS.  Every value below is illustrative: a conservative
set that typical low-cost 2- and 4-layer prototype services meet, written so
the template runs end to end (publish -> 14_fab.md -> board_setup.py).  It is
not a source.  Before Board Setup is trusted or anything is ordered, replace
it with a profile of YOUR fab: copy _blank.py to <fab>_<service>.py, fill every
field from that fab's own pages (capabilities, quote form, stackup, solder
mask), name each page in SOURCES, and point the board's FAB "profile" at it.

Because EXAMPLE = True, publish.py prints a warning on 14_fab.md and in its
lint, and board_setup.py `check` reports a QUESTION until the profile is
replaced -- so an example number can be practised with but never reach an
order unnoticed.

A profile is DATA ONLY: what the fab can build and what costs nothing extra
to order.  The board module chooses (layers, outline, net classes, its own
custom rules) and may override any row here by name -- see board_template.py,
FAB.  Keep the row NAMES: board_setup.py reads them.

The defaults are deliberately LOOSE -- 6/6 mil and a 0.3 mm hole -- because
tighter tiers and smaller holes can only add cost.  A board whose parts need
finer geometry (a fine-pitch BGA escape, a 0.4 mm QFN) overrides those rows in
its own FAB block, with the arithmetic.
"""

NAME = "example_standard"
EXAMPLE = True
HOUSE = "EXAMPLE -- no particular fab; a typical standard prototype service"
READ_NOTE = ("illustrative values, not any fab's own: replace this profile with "
             "your fab's (copy fabs/_blank.py) before ordering")

# (source, where, what it gave).  A real profile names the fab's own pages;
# these rows say which page each kind of number comes from.
SOURCES = [
    ("your fab's capabilities page", "<URL>",
     "min track/space, min drill, annular ring, copper and hole to edge, "
     "silkscreen minimums, outline tolerance, mask and silk colours"),
    ("your fab's quote / order form", "<URL>",
     "the tiers offered (track/space, hole size), default thickness and "
     "copper, which options carry a surcharge"),
    ("your fab's standard stackup page", "<URL>",
     "prepreg and core thicknesses and their Dk, the finished thickness and "
     "its tolerance"),
    ("your fab's solder mask page", "<URL>",
     "minimum mask bridge (web) per colour and copper weight, suggested mask "
     "clearance"),
]

# What to choose on the order form: (option, choice, why).  The engine adds
# "Board size / quantity" and "Layers" from the board's own FAB block, and
# drops "Inner copper" on a 2-layer board.  A board overrides any row by name.
ORDER = [
    ("Material", "FR-4", "The standard laminate (illustrative)."),
    ("Thickness", "1.6 mm", "The usual default thickness (illustrative)."),
    ("Min track / spacing", "6/6 mil (0.1524 mm)",
     "A loose tier most services offer at no charge (illustrative). A board "
     "that needs finer geometry overrides this row and the two Constraints "
     "rows that go with it."),
    ("Min hole size", "0.3 mm", "A hole size most services drill at no charge (illustrative)."),
    ("Solder mask", "green", "Usually no extra charge, and usually the narrowest web allowed."),
    ("Silkscreen", "white", "Usually no extra charge."),
    ("Surface finish", "HASL lead-free",
     "Lead-free, usually without an area charge (illustrative). ENIG if the "
     "assembler wants a flatter fine-pitch site."),
    ("Finished copper, outer", "1 oz", "Typical standard (illustrative)."),
    ("Inner copper", "1 oz", "Typical standard for 4 layers (illustrative)."),
    ("Via process", "tenting vias", "Keeps solder off vias at assembly (illustrative)."),
    ("Impedance control", "no",
     "A board with a controlled-impedance net overrides this row and its "
     "stackup together."),
    ("Castellated holes, edge plating, via-in-pad, blind/buried vias, "
     "custom stackup, plugged vias", "none",
     "Each is usually a surcharge; a board that needs one overrides this row "
     "and says why."),
    ("E-test", "the prototype default", ""),
]

# KiCad Board Setup -> Constraints: (constraint, value in mm, basis).
# The row NAMES are what board_setup.py reads; keep them.
CONSTRAINTS = [
    ("Minimum clearance", "0.1524", "the 6/6 mil order-form tier (illustrative)"),
    ("Minimum track width", "0.1524", "the 6/6 mil order-form tier (illustrative)"),
    ("Minimum annular width", "0.15",
     "illustrative: a typical guaranteed FINISHED ring, absorbing the fab's "
     "own hole-size tolerance"),
    ("Minimum via diameter", "0.6", "0.3 mm hole + 2 x 0.15 mm ring"),
    ("Copper to hole clearance", "0.25",
     "illustrative: keeps a drill's wander off neighbouring copper"),
    ("Copper to edge clearance", "0.5",
     "illustrative arithmetic: a typical 0.3 mm copper-to-edge minimum PLUS a "
     "typical +-0.2 mm routed-outline tolerance"),
    ("Minimum through hole", "0.3", "the order form's hole row"),
    ("Hole to hole clearance", "0.5", "illustrative: keeps two drills apart"),
    ("Minimum uVia diameter / drill", "not used", "no microvias, no HDI"),
    ("Solder mask registration", "not published here",
     "Nothing to type: KiCad has no field for it and no DRC rule tests it. A "
     "design margin that depends on registration is checked by arithmetic, "
     "on this page, not by a rule"),
    ("Solder mask expansion", "0.05", "illustrative: 2 mil, inside a typical 2-4 mil suggestion"),
    ("Solder mask minimum web", "0.15",
     "illustrative, green at 1 oz. A pitch that cannot give it: GANG that "
     "opening rather than draw a thinner web the fab will delete anyway"),
    ("Silkscreen line / text", "0.2 line; 1.0 mm high, 0.2 stroke (typical minimum 0.15 / 0.8 x 0.15)",
     "illustrative; keep silk >= 0.2 mm off every pad"),
    ("Hole to board edge", "0.5", "illustrative: a typical hole-to-outline minimum"),
]

# The "Board outline" Constraints row: "<W> x <H> mm, " + OUTLINE[0].
OUTLINE = ("routed, +-0.2 mm, no V-score", "illustrative outline tolerance; single board")

# Physical stackups by copper layer count: (layer, material / thickness,
# carries).  The board's FAB "carries" fills the third column per layer.
STACKUPS = {
    2: [
        ("F.Cu", "copper 0.035 mm (1 oz)", ""),
        ("core", "FR-4, 1.51 mm, Dk 4.5", "illustrative; ask the fab for any impedance-controlled net"),
        ("B.Cu", "copper 0.035 mm (1 oz)", ""),
        ("total", "1.6 mm +-10 %", "illustrative"),
    ],
    4: [
        ("F.Cu", "copper 0.035 mm (1 oz finished)", ""),
        ("prepreg", "7628, 0.2 mm, Dk 4.4", "illustrative"),
        ("In1.Cu", "copper 0.035 mm", ""),
        ("core", "FR-4, 1.06 mm, Dk 4.6", "illustrative"),
        ("In2.Cu", "copper 0.035 mm", ""),
        ("prepreg", "7628, 0.2 mm, Dk 4.4", "illustrative"),
        ("B.Cu", "copper 0.035 mm (1 oz finished)", ""),
        ("total", "1.6 mm +-10 %", "illustrative"),
    ],
}

# The Default net class: (track, clearance, via drill / pad, note).  A board's
# own "Default" row replaces it.
DEFAULT_CLASS = ("0.2", "0.2", "0.3 / 0.6",
                 "illustrative: comfortably above the 0.1524 floor, which is for "
                 "the places that need it, not the default")

# Fab-wide custom DRC rules beyond the ones the engine derives (hole to board
# edge, area classes, no-via classes): (title, rule text, why).
RULES = []
