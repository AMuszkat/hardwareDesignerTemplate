#!/usr/bin/env python3
"""`budget.py lay`: a spent group lays a closing row the handoff already allows -- LAY_CHECKS checks, nothing else.

    python3 tools/route/test_budget_lay.py        # OK, exit 0

Standard library only.  The case it was written for: board1 NORTHGAP r192, 2026-11-25 -- T2 (the
group's own net re-laid) closed by hand after the round and its three checks were spent, and its
reserve gates were refused at the door.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import budget as B  # noqa: E402

NOW = time.time()


def run(i, t):
    return dict(id=i, kind="run", name="G", epoch=1, t=t, s=60.0, round=True, label=f"r{i}")


def check(i, t):
    return dict(id=i, kind="check", name="G", epoch=1, t=t, s=20.0, label=f"c{i}")


def spent_group():
    """tier A: 2 rounds; both run, then the round's three checks."""
    t0 = NOW - 3000
    ev = [run(1, t0), run(2, t0 + 100), check(3, t0 + 200), check(4, t0 + 300), check(5, t0 + 400)]
    return dict(names=["G"], nets=["A", "B"], tier="A", basis="", epoch=1, events=ev, walls=[])


class Tally(unittest.TestCase):
    def test_spent_without_a_lay(self):
        t = B.tally(spent_group(), now=NOW)
        self.assertTrue(t["no_more"])
        self.assertEqual((t["lay"], t["lay_left"]), (None, 0))

    def test_a_lay_gives_its_checks_and_nothing_else(self):
        g = spent_group()
        g["events"].append(dict(id=6, kind="lay", name="G", epoch=1, t=NOW - 2000, s=0))
        t = B.tally(g, now=NOW)
        self.assertFalse(t["no_more"])
        self.assertEqual((t["lay_left"], t["left_rounds"]), (B.LAY_CHECKS, 0))
        for i in range(B.LAY_CHECKS):
            g["events"].append(check(7 + i, NOW - 1900 + i))
        t = B.tally(g, now=NOW)
        self.assertTrue(t["no_more"])                           # its checks used: spent again
        self.assertEqual(t["rounds"], 2)                        # and they opened no round

    def test_a_router_run_after_a_lay_still_spends(self):
        g = spent_group()
        g["events"] += [dict(id=6, kind="lay", name="G", epoch=1, t=NOW - 2000, s=0), run(7, NOW - 1900)]
        t = B.tally(g, now=NOW)
        self.assertLess(t["left_rounds"], 0)
        self.assertTrue(t["no_more"])

    def test_a_lay_of_an_earlier_epoch_counts_for_nothing(self):
        g = spent_group()
        g["events"].append(dict(id=6, kind="lay", name="G", epoch=0, t=NOW - 2000, s=0))
        self.assertTrue(B.tally(g, now=NOW)["no_more"])


class Cmd(unittest.TestCase):
    def board(self, d, allows):
        hb = Path(d)
        (hb / "routing" / "terms").mkdir(parents=True)
        (hb / "tmpWork").mkdir()
        for v in ("ceil", "t1", "t2"):
            (hb / "tmpWork" / f"{v}.json").write_text("{}")
        terms = {"T1": {"kind": "placement", "text": "the group's own net re-laid on In7"},
                 "T2": {"kind": "rule", "text": "a polygon notched"}}
        if allows:
            terms["T1"]["handoff_allows"] = "the group's own net, on a layer of its own spec"
        (hb / "routing" / "terms" / "G.json").write_text(json.dumps({"terms": terms, "rows": [
            {"moved": ["T1", "T2"], "closes": None, "left": None, "evidence": "tmpWork/ceil.json"},
            {"moved": ["T1"], "closes": True, "left": 0, "proof": "check", "evidence": "tmpWork/t1.json"},
            {"moved": ["T2"], "closes": False, "left": 2, "contested": ["N1", "N2", "N3"], "evidence": "tmpWork/t2.json"}]}))
        B.ledger_path(hb).write_text(json.dumps({"groups": {"G": spent_group()}, "home": str(hb)}))
        return hb

    def lay(self, hb):
        old = os.environ.get("HW_BUDGET_BOARD")
        os.environ["HW_BUDGET_BOARD"] = str(hb)
        err = io.StringIO()
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                rc = B.cmd_lay(hb, "G")
        finally:
            if old is None:
                os.environ.pop("HW_BUDGET_BOARD", None)
            else:
                os.environ["HW_BUDGET_BOARD"] = old
        return rc, err.getvalue()

    def test_lay_a_row_the_handoff_allows_once(self):
        with tempfile.TemporaryDirectory() as d:
            hb = self.board(d, allows=True)
            self.assertEqual(self.lay(hb)[0], 0)
            g = json.loads(B.ledger_path(hb).read_text())["groups"]["G"]
            e = g["events"][-1]
            self.assertEqual((e["kind"], e["moved"], e["proof"]), ("lay", ["T1"], "check"))
            self.assertFalse(B.tally(g)["no_more"])
            rc, err = self.lay(hb)
            self.assertEqual(rc, B.EXIT)                        # one lay an epoch
            self.assertIn("already", err)

    def test_no_lay_when_the_row_needs_a_grant(self):
        with tempfile.TemporaryDirectory() as d:
            hb = self.board(d, allows=False)
            rc, err = self.lay(hb)
            self.assertEqual(rc, 1)
            self.assertIn("handoff_allows", err)
            g = json.loads(B.ledger_path(hb).read_text())["groups"]["G"]
            self.assertTrue(B.tally(g)["no_more"])

    def test_no_lay_while_the_group_is_open(self):
        with tempfile.TemporaryDirectory() as d:
            hb = self.board(d, allows=True)
            L = json.loads(B.ledger_path(hb).read_text())
            L["groups"]["G"]["events"] = L["groups"]["G"]["events"][:2]      # two rounds, no check: open
            B.ledger_path(hb).write_text(json.dumps(L))
            self.assertEqual(self.lay(hb)[0], 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
