"""A kicad-cli PDF page to a PNG, cropped to its ink or to a box in board mm.

    import pdfcrop
    pdfcrop.raster(pdf, png, scale=6)                      # cropped to the ink, 4 pt margin
    pdfcrop.raster(pdf, png, scale=6, box=(x0, y0, x1, y1))  # board mm

`kicad-cli pcb export pdf` has no fit-to-board option, and it plots the board at
its own coordinates on the page: board mm x is page point x * 72 / 25.4, from
the page's top-left corner, so a box in board mm is a clip rectangle on the
page. PyMuPDF does the raster. pcbnew pins the board tools to KiCad's own
interpreter and PyMuPDF lives in kicad_project/tools/.venv (tools/setup.sh), so
when it is not importable here the crop runs out of process in that venv.
Returns the PNG path, or None with the reason printed.
"""
import subprocess
import sys
from pathlib import Path

VENV_PY = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python"
PT = 72 / 25.4

_CODE = r"""
import sys
try:
    import pymupdf as fitz
except ImportError:
    import fitz
pdf, png, scale = sys.argv[1], sys.argv[2], float(sys.argv[3])
box = [float(v) for v in sys.argv[4].split(",")] if len(sys.argv) > 4 else None
p = fitz.open(pdf)[0]
if box:
    r = fitz.Rect(*box)
else:
    ink = [v[1] for v in p.get_bboxlog()]
    if not ink:
        sys.exit(3)
    r = fitz.Rect(min(q[0] for q in ink) - 4, min(q[1] for q in ink) - 4,
                  max(q[2] for q in ink) + 4, max(q[3] for q in ink) + 4)
p.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=r).save(png)
"""


def raster(pdf, png, scale=6.0, box=None):
    args = [str(pdf), str(png), str(scale)]
    if box is not None:
        args.append(",".join(f"{v * PT:.3f}" for v in box))
    try:
        try:
            import pymupdf  # noqa: F401
        except ImportError:
            import fitz  # noqa: F401
        exes = [sys.executable]
    except ImportError:
        exes = [str(VENV_PY)] if VENV_PY.exists() else []
        exes.append("python3")
    for exe in exes:
        r = subprocess.run([exe, "-c", _CODE, *args], capture_output=True, text=True)
        if r.returncode == 0 and Path(png).exists():
            return Path(png)
        if r.returncode == 3:
            print(f"  raster {Path(png).name}: the page has no ink")
            return None
    print(f"  raster {Path(png).name}: no interpreter with PyMuPDF -- run tools/setup.sh (the PDF is beside it)")
    return None
