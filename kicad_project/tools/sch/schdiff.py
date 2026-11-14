"""What a redraw re-wrote: the board's sheets against an older drawing of them,
as SETS -- for the review request's Scope (a `draw` for a small change
re-writes every sheet; its text differs everywhere, so a text diff says
nothing).

    tools/.venv/bin/python tools/sch/schdiff.py --board board1 [OLD_DIR]

OLD_DIR is a folder of .kicad_sch files: by default the newest
<board>/.sch_backups/draw-*/ (the backup `sch.py draw` made). It prints every
part whose lib_id, Value, Footprint, DNP flag, fields or unit sheets differ,
whether nets / no-connects / rails / PWR_FLAGs are identical, and per sheet
how many symbols, wires, labels and frames sit elsewhere, the paper, and the
title block's rev and date. Exit 1 when a connection differs (parity is the
gate for that; this only reports), else 0.

A backup folder is not named after the board, and `plan.Source.from_sch`
finds the root sheet by the folder's name: the old sheets are copied to a
temporary folder of the board's name first (with the board's lib/ and
sym-lib-table, which a sheet's symbols resolve against).
"""
import argparse
import collections
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import boardsel                                     # noqa: E402
import plan                                         # noqa: E402

_WS = re.compile(r"\s+")


def _sets(path):
    t = Path(path).read_text()
    norm = lambda found: collections.Counter(_WS.sub(" ", m) for m in found)
    title = lambda k: (re.search(r'\(%s "([^"]*)"\)' % k, t) or [None, ""])[1]
    return {
        "symbols": norm(re.findall(r'\(symbol\s+\(lib_id "[^"]+"\)\s+\(at [-\d. ]+\)', t)),
        "wires": norm(re.findall(r'\(wire\s+\(pts[^)]*\)[^)]*\)\s*\)', t)),
        "labels": norm(re.findall(r'\((?:label|hierarchical_label|global_label) "[^"]+"\s+(?:\(shape \w+\)\s+)?\(at [-\d. ]+\)', t)),
        "frames": norm(re.findall(r'\(text_box "[^"]*"\s+(?:\(exclude_from_sim \w+\)\s+)?\(at [-\d. ]+\)\s+\(size [-\d. ]+\)', t)),
        "paper": (re.search(r'\(paper "([^"]+)"', t) or [None, "?"])[1],
        "rev": title("rev"), "date": title("date"),
    }


def run(board, old):
    board, old = Path(board), Path(old)
    tmp = Path(tempfile.mkdtemp(prefix="schdiff-"))
    try:
        stage = tmp / board.name
        stage.mkdir()
        for f in old.iterdir():
            if f.is_file():
                shutil.copy2(f, stage / f.name)
        for extra in ("lib", "sym-lib-table"):
            src, dst = board / extra, stage / extra
            if src.exists() and not dst.exists():
                shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)
        a, b = plan.Source.from_sch(str(stage)), plan.Source.from_sch(str(board))
        print(f"schdiff: {old} -> {board}")
        print(f"  parts {len(a.parts)} -> {len(b.parts)}, nets {len(a.nets)} -> {len(b.nets)}, "
              f"no-connects {len(a.nc)} -> {len(b.nc)}")
        n = 0
        for r in sorted(set(a.parts) | set(b.parts)):
            pa, pb = a.parts.get(r), b.parts.get(r)
            if not pa or not pb:
                print(f"  {r}: only in the {'old' if pa else 'new'} drawing"); n += 1
                continue
            for k in ("lib_id", "value", "footprint", "dnp"):
                if getattr(pa, k) != getattr(pb, k):
                    print(f"  {r} {k}: {getattr(pa, k)!r} -> {getattr(pb, k)!r}"); n += 1
            fa, fb = dict(pa.fields), dict(pb.fields)
            for k in sorted(set(fa) | set(fb)):
                if fa.get(k) != fb.get(k):
                    print(f"  {r} field {k}: {fa.get(k)!r} -> {fb.get(k)!r}"); n += 1
            if dict(pa.units) != dict(pb.units):
                print(f"  {r} units: {dict(pa.units)} -> {dict(pb.units)}"); n += 1
        print(f"  {n} part difference(s)")
        key = lambda nets: {k: sorted(map(tuple, v)) for k, v in nets.items()}
        same = {"nets": key(a.nets) == key(b.nets),
                "no-connects": sorted(map(tuple, a.nc)) == sorted(map(tuple, b.nc)),
                "rails": sorted(a.rails) == sorted(b.rails),
                "PWR_FLAGs": sorted(a.flags) == sorted(b.flags)}
        print("  " + ", ".join(f"{k} {'identical' if v else 'DIFFER'}" for k, v in same.items()))
        for f in sorted(p.name for p in board.glob("*.kicad_sch")):
            if not (stage / f).exists():
                print(f"  {f}: new sheet"); continue
            A, B = _sets(stage / f), _sets(board / f)
            moved = lambda k: sum((A[k] - B[k]).values()) + sum((B[k] - A[k]).values())
            line = ", ".join(f"{k} {sum(A[k].values())} -> {sum(B[k].values())} ({moved(k)} differ)"
                             for k in ("symbols", "wires", "labels", "frames"))
            head = "; ".join(f"{k} {A[k]} -> {B[k]}" if A[k] != B[k] else f"{k} {B[k]}" for k in ("paper", "rev", "date"))
            print(f"  {f}: {line}; {head}")
        for f in sorted(p.name for p in stage.glob("*.kicad_sch")):
            if not (board / f).exists():
                print(f"  {f}: sheet gone")
        return 0 if same["nets"] and same["no-connects"] else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--board")
    ap.add_argument("old", nargs="?", help="folder of the older sheets (default: the newest .sch_backups/draw-*)")
    a = ap.parse_args()
    board = Path(boardsel.resolve(a.board))
    old = a.old
    if not old:
        backs = sorted((board / ".sch_backups").glob("draw-*"))
        if not backs:
            sys.exit("schdiff: no .sch_backups/draw-* folder -- name the older drawing's folder")
        old = backs[-1]
    old = Path(old) if os.path.isabs(str(old)) else Path.cwd() / old
    if not old.is_dir():
        sys.exit(f"schdiff: {old} is not a folder")
    sys.exit(run(board, old))


if __name__ == "__main__":
    main()
