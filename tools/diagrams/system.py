"""
EXAMPLE system sheet -> design/diagrams/system.svg

Replace the blocks with the product's real signal path, keep the pattern:

  * OUTPUT      where the sheet goes (lint check 4 regenerates it and
                compares bytes, so the generator must be deterministic).
  * SYSTEM      True = a system sheet: FUNCTION names only, never a refdes
                (lint check 2).  Board-level sheets set it False.
  * REFLECTS    the id (D1, D2, ...) of the newest design/STATUS.md Decided
                row this generator has been READ AGAINST (lint check 6).  Bump
                it only after opening this file and checking it against that
                decision.
  * OPEN_CLAIMS the {open:<tag>} items of STATUS's Open section that this
                sheet draws as open (lint check 7).  Closing an item breaks
                every sheet still claiming it -- redraw, then drop the tag.

A box is a thing that exists.  An absence gets a line of text, never a box.
Dashed boxes are provisional: the part is not chosen yet.

    .venv/bin/python tools/diagrams/system.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sheet import RS, Sheet                                    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT = "design/diagrams/system.svg"
SYSTEM = True
REFLECTS = "D1"
OPEN_CLAIMS = ["example-sensor"]


def draw():
    s = Sheet(1100, 560)
    s.header("System — signal path (EXAMPLE)",
             "Replace with the product's own blocks; function names only.",
             "Read left to right: input, processing, output; teal is feedback.")
    s.legend([(640, "dig", "digital"), (740, "ana", "analog"),
              (840, "pwr", "power"), (930, "dash", "provisional")], y=112)

    iy = 190
    _, _, _, ih = s.box(60, iy, 240, "Input stage", ["conditions the input",
                                                     "protects the connector"],
                        kind="ana", rail="from: power supply")
    px, py, pw, ph = s.box(400, iy, 260, "Processing", ["the product's core function",
                                                        "configured at boot"],
                           kind="dig", rail="from: power supply")
    ox, oy, ow, oh = s.box(760, iy, 260, "Output stage", ["drives the load"],
                           kind="ana", h=ih, rail="from: power supply")
    mid = iy + ih / 2
    s.seg(300, mid, 400, mid, RS["ana"])
    s.seg(660, mid, 760, mid, RS["dig"])

    # feedback: output back to processing, above the boxes
    s.orth([(890, iy), (890, iy - 30), (560, iy - 30), (560, iy)], RS["fb"])
    s.text(725, iy - 38, "feedback", 10, RS["fb"])

    sy = 400
    s.box(400, sy, 260, "Power supply", ["input rail to local rails"], kind="pwr")
    s.seg(530, sy, 530, py + ph, RS["pwr"])

    s.box(60, sy, 240, "Sensor", ["provisional -- part not chosen",
                                  "STATUS Open: example-sensor"],
          kind="ana", dash=True)
    s.seg(180, sy, 180, iy + ih, RS["ana"])

    s.footer([("EXAMPLE sheet shipped with the template. Delete it, or edit it into "
               "the real system, and keep REFLECTS / OPEN_CLAIMS honest.", RS["sub"])])
    return s


if __name__ == "__main__":
    draw().save(os.path.join(ROOT, OUTPUT))
