"""exitlane_dump.py -- a part's exit-lane judgement (exitlane.judge_entry, prelayout.json "exit_lanes") as JSON,
so a search that runs without pcbnew (a board build script's via model) asks exitlane.blocked_by / shuts exactly
what route.py check and pretest ask: the same lanes, the same blocked set, the same width and clearance.

    tools/kpy tools/route/exitlane_dump.py BOARD_DIR OUT.json [--ref U1 ...]      # dump (needs pcbnew)
    import exitlane_dump as ED; r = ED.load(OUT.json)["U1"]                        # load (plain python)
    ED.shut_by(r, [{"k": "via", "net": FULLNET, "x": x, "y": y, "r": 0.2}, ...])   # -> balls newly failing r's gate

BOARD_DIR is a scratch copy as the plan will meet it (every other group already on it).  The dump holds the
lanes, the (ball, key) pairs already blocked, the routed balls, the gate and its balls, and the rule: the area
class's width / clearance when the entry has one, else every lane net's width and every board net's class
clearance (clearance(a, b) = the larger of the two, as exitlane.Rules has it).  Exit 0; 2 on a usage error."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitlane as EL  # noqa: E402  (stdlib only at import time)


class _Rule:
    def __init__(self, d):
        self.d = d

    def width(self, net):
        d = self.d
        return d["width"] if d.get("width") is not None else d["widths"].get(net, d["width_default"])

    def clearance(self, net, other):
        d = self.d
        if d.get("clearance") is not None:
            return d["clearance"]
        c = d["clr"]
        return max(c.get(net, 0.0), c.get(other, 0.0))


def _tup(k):
    return tuple(_tup(x) for x in k) if isinstance(k, list) else k


def load(path):
    """{ref: r} with r as exitlane.blocked_by / shuts read it."""
    out = {}
    for e in json.loads(Path(path).read_text()):
        lanes = [dict(L, key=_tup(L["key"]), seg=tuple(L["seg"])) for L in e["lanes"]]
        out[e["ref"]] = dict(ref=e["ref"], layer=e["layer"], gate=e["gate"], lanes=lanes,
                             blocked={(b, _tup(k)) for b, k in e["blocked"]}, routed=set(e["routed"]),
                             gate_balls=e["gate_balls"], rule=_Rule(e["rule"]))
    return out


def shut_by(r, items):
    """The balls that newly fail r's gate with ITEMS (exitlane item dicts, full net names) added."""
    extra = set()
    for it in items:
        extra |= EL.blocked_by(r, it)
    return EL.shuts(r, extra)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import route as R
    d, out = Path(argv[0]), Path(argv[1])
    refs = [argv[i + 1] for i, a in enumerate(argv) if a == "--ref"]
    B = R.RBoard(d)
    res = []
    for e in R.exitlane_spec(B) or []:
        if refs and e["ref"] not in refs:
            continue
        r = EL.judge_entry(B, e)
        ru = r["rule"]
        rule = dict(width=ru.w if ru.w is not None else (ru.area[1] if ru.area else None),
                    clearance=ru.c if ru.c is not None else (ru.area[2] if ru.area else None), source=ru.source)
        if rule["width"] is None:
            rule["widths"] = {L["net"]: ru.width(L["net"]) for L in r["lanes"]}
            rule["width_default"] = 0.1
        if rule["clearance"] is None:
            rule["clr"] = {n: ru._clr(n) for n in B.b.GetNetsByName().keys() for n in [str(n)]}
        res.append(dict(ref=r["ref"], layer=r["layer"], gate=r["gate"], gate_balls=r["gate_balls"],
                        routed=sorted(r["routed"]), blocked=sorted([b, list(k)] for b, k in r["blocked"]),
                        lanes=[dict(L, key=list(L["key"]), seg=list(L["seg"])) for L in r["lanes"]], rule=rule))
        print(e["ref"], r["layer"], "gate", r["gate"], "lanes", len(r["lanes"]), "blocked", len(r["blocked"]),
              "failing", r["gate_balls"][r["gate"]], "|", ru.source)
    out.write_text(json.dumps(res, indent=0, default=str))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
