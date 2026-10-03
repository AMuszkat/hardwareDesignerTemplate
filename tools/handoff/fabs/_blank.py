"""
Fab profile -- BLANK. Copy to <fab>_<service>.py and fill every field.

Fill each value from the fab's OWN pages (capabilities, the quote form, the
stackup page, the solder-mask page) and name the page in SOURCES; a value
that is yours rather than the fab's says "ours" and gives the reason. Never
fill a field from memory or a search snippet: a wrong fab number passes DRC
and is found at the fab's review, or at assembly.

Every None renders as UNSET in handoff/14_fab.md, and board_setup.py refuses
to apply a page with an UNSET value -- so an unfilled field stops the board
session at Board Setup instead of becoming a silent KiCad default.

example_standard.py is a filled example of the same shape (illustrative
values, not any fab's). Do NOT set EXAMPLE = True in a real profile. Keep the row
NAMES: board_setup.py reads them, and a new name is a parser change.
"""

NAME = "_blank"
HOUSE = None            # "Fab, 'Service' -- how it is ordered"
READ_NOTE = None        # "values as read YYYY-MM; verify before ordering"

SOURCES = [
    # ("<page title>", "<URL>", "<what it gave, quoted>"),
]

# (option, choice, why). The engine adds "Board size / quantity" and
# "Layers" from the board's FAB block. board_setup.py needs these rows:
# Thickness, Solder mask, Silkscreen, Surface finish, Via process.
ORDER = [
    ("Material", None, ""),
    ("Thickness", None, ""),            # "1.6 mm"
    ("Min track / spacing", None, ""),  # "6/6 mil (0.1524 mm)"
    ("Min hole size", None, ""),        # "0.3 mm"
    ("Solder mask", None, ""),          # a colour: green, red, blue, black ...
    ("Silkscreen", None, ""),           # white / black
    ("Surface finish", None, ""),       # HASL lead-free, ENIG, OSP ...
    ("Finished copper, outer", None, ""),
    ("Inner copper", None, ""),         # dropped on a 2-layer board
    ("Via process", None, ""),          # "tenting vias" or "untented vias"
    ("Impedance control", None, ""),
    ("E-test", None, ""),
]

# (constraint, value in mm, basis). board_setup.py knows exactly these names.
CONSTRAINTS = [
    ("Minimum clearance", None, ""),
    ("Minimum track width", None, ""),
    ("Minimum annular width", None, ""),
    ("Minimum via diameter", None, ""),
    ("Copper to hole clearance", None, ""),
    ("Copper to edge clearance", None, ""),
    ("Minimum through hole", None, ""),
    ("Hole to hole clearance", None, ""),
    ("Minimum uVia diameter / drill", None, ""),   # "not used", or handle it
    ("Solder mask registration", None, ""),        # informational only
    ("Solder mask expansion", None, ""),
    ("Solder mask minimum web", None, ""),
    # "<line> line; <h> mm high, <stroke> stroke (fab minimum ...)" with
    # "keep silk >= <x> mm off every pad" in the basis
    ("Silkscreen line / text", None, ""),
    ("Hole to board edge", None, ""),
]

# The "Board outline" Constraints row: "<W> x <H> mm, " + OUTLINE[0].
OUTLINE = (None, "")     # ("CNC routed, +-0.2 mm, no V-score", "<source>")

# {copper layer count: [(layer, material / thickness, carries), ...]}.
# Copper rows "F.Cu" / "In1.Cu" / "B.Cu": "copper 0.035 mm".
# Dielectric rows "prepreg" / "core": "<material>, <t> mm, Dk <er>".
# Last row ("total", "<t> mm +-<p> %", "<source>").
STACKUPS = {}

# The Default net class: (track, clearance, via drill / pad, note).
DEFAULT_CLASS = (None, None, None, "")

# Fab-wide custom DRC rules: (title, rule text, why).
RULES = []
