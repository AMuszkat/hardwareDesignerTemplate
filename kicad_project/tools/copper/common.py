"""Shared by the copper tools: which board, its guide, its project settings.

    import common as C
    a = C.args(ap)                      # parses; a.board is the board folder, a.pcb the .kicad_pcb
    b = C.load(a)                       # the pcbnew board (a.pcb), C.G the guide's rules, C.CLS the classes

Every tool takes `--board boardN|folder|path` (default: this session's board,
tools/boardsel.py) and, for a scratch copy that sits anywhere, `--pcb FILE`
(its guide and project file are then read from beside it, or, with `--guide
DIR`, from that board folder).  Nothing here writes a board.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "route"))
import boardsel                                       # noqa: E402
import guide                                          # noqa: E402

G, CLS, HOLE2HOLE, BOARD_DIR = {}, {}, 0.5, None


def die(msg, code=2):
    print(f"{Path(sys.argv[0]).stem}: {msg}", file=sys.stderr)
    sys.exit(code)


def add_board_args(ap):
    ap.add_argument("--board", help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("--pcb", help="a .kicad_pcb anywhere (a scratch copy); the board folder gives the guide")
    ap.add_argument("--guide", help="read general_routing_guide.md from this board folder (with --pcb)")


def args(ap):
    a = ap.parse_args()
    if a.pcb:
        a.pcb = Path(a.pcb).resolve()
        if not a.pcb.is_file():
            die(f"no such board file: {a.pcb}")
        a.board = Path(a.guide).resolve() if a.guide else a.pcb.parent
    else:
        a.board = boardsel.resolve(a.board)
        a.pcb = boardsel.one_pcb(a.board)
    return a


def pcbnew_module():
    keep, null = os.dup(2), os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 2)
    try:
        import pcbnew
    except ImportError:
        die(f"cannot import pcbnew under {sys.executable}: run this with tools/kpy")
    finally:
        os.dup2(keep, 2)
        os.close(null)
        os.close(keep)
    return pcbnew


def settings(pcb_dir, board_dir=None):
    """The classes and the hole-to-hole minimum from the .kicad_pro beside the
    board (a copy elsewhere must bring its project file, or every net reads as
    Default and the class widths are unknown)."""
    global CLS, HOLE2HOLE
    pros = sorted(Path(pcb_dir).glob("*.kicad_pro")) or (sorted(Path(board_dir).glob("*.kicad_pro")) if board_dir else [])
    if not pros:
        die(f"no .kicad_pro beside {pcb_dir}: the net classes come from it (copy it next to a scratch board)")
    pro = json.loads(pros[0].read_text())
    CLS = {c["name"]: c for c in pro.get("net_settings", {}).get("classes", [])}
    if "Default" not in CLS:
        die(f"{pros[0].name} has no Default net class")
    HOLE2HOLE = float(pro.get("board", {}).get("design_settings", {}).get("rules", {}).get("min_hole_to_hole", 0.5))
    return pro


def load(a):
    """The board, with the module globals set: G (the guide), CLS, HOLE2HOLE."""
    global G, BOARD_DIR
    BOARD_DIR = Path(a.board)
    G = guide.load(a.board)
    settings(Path(a.pcb).parent, a.board)
    pcbnew = pcbnew_module()
    b = pcbnew.LoadBoard(str(a.pcb))
    return b


def netclass_of(b, net):
    """The first class of a net (KiCad names a net in two classes 'A,B')."""
    n = b.FindNet(net) if net else None
    name = (n.GetNetClassName() if n else "") or "Default"
    return name.split(",")[0].strip()


def cls(b, net):
    return CLS.get(netclass_of(b, net), CLS["Default"])


def clr(b, net):
    return cls(b, net).get("clearance", CLS["Default"].get("clearance", 0.2))


def width(b, net):
    return cls(b, net).get("track_width", CLS["Default"].get("track_width", 0.2))


def copper_layers(b, pcbnew):
    return [pcbnew.BOARD.GetStandardLayerName(l) for l in b.GetEnabledLayers().CuStack()]


def exceptions_path(board_dir):
    return Path(board_dir) / "routing" / "vialink_exceptions.json"


def exceptions(board_dir):
    p = exceptions_path(board_dir)
    return json.loads(p.read_text()) if p.exists() else []


def in_board(a, rel):
    """An INPUT path typed on the command line: as typed if it exists from the
    current directory, else inside the board folder (route.py's rule)."""
    return boardsel.in_board(a.board, rel)


def out_in_board(a, rel):
    """An OUTPUT path typed on the command line: absolute, or relative to a
    directory that exists from here, as typed; a bare name or a folder that
    only exists in the board (`routing/x.json`) lands inside the board folder."""
    p = Path(rel)
    if p.is_absolute() or (p.parent != Path(".") and p.parent.is_dir()):
        return p
    q = Path(a.board) / rel
    return q if q.parent.is_dir() else p


def out_path(a, name):
    """Where a tool's output goes: `--out` if given, else <board>/routing/<name>."""
    out = getattr(a, "out", None)
    if out:
        return out_in_board(a, out)
    d = Path(a.board) / "routing"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def short(net):
    return net.rsplit("/", 1)[-1]
