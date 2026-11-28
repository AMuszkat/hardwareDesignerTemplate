#!/usr/bin/env python3
"""A group that lays copper after its terms count: the old table is stale and one new window opens.

    python3 tools/route/test_budget_copper.py        # OK, exit 0

Standard library only.  The case it was written for: board1 R1, 2026-11-25 -- its one terms window
went at 23:20 to a single variant, three applies followed, the table was stale, and the group could
neither count again nor write its wall.  Also: a wall's ordinal (the 14th came out "the THIRD").
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import budget as B  # noqa: E402

NOW = time.time()


def ev(i, kind, t, **kw):
    return dict(dict(id=i, kind=kind, name=kw.pop("name", "G"), epoch=kw.pop("epoch", 1), t=t, s=kw.pop("s", 0.0)), **kw)


def group(*events, tier="C"):
    return dict(names=["G"], nets=["A", "B"], tier=tier, basis="", epoch=1, events=list(events), walls=[])


class Recount(unittest.TestCase):
    def test_a_recount_replaces_every_earlier_claim(self):
        g = group(ev(1, "apply", NOW - 4000, nets=["A", "B"]), ev(2, "recount", NOW - 3000, nets=["A"]))
        self.assertEqual(B.tally(g, NOW)["nets_applied"], 1)
        self.assertFalse(B.tally(g, NOW)["complete"])

    def test_an_apply_after_a_recount_adds(self):
        g = group(ev(1, "apply", NOW - 4000, nets=["A", "B"]), ev(2, "recount", NOW - 3000, nets=[]),
                  ev(3, "apply", NOW - 2000, name="G2", nets=["B"]))
        self.assertEqual(B.tally(g, NOW)["nets_applied"], 1)


class Window(unittest.TestCase):
    def test_a_window_before_copper_is_over_and_a_new_one_opens(self):
        g = group(ev(1, "terms", NOW - 4000, s=150.0), ev(2, "apply", NOW - 3000, nets=["A"]))
        t = B.tally(g, now=NOW)
        self.assertEqual((t["terms"], t["terms_end"]), ([], None))
        g["events"].append(ev(3, "terms", NOW - 100, s=None))
        t = B.tally(g, now=NOW)
        self.assertEqual([e["id"] for e in t["terms"]], [3])
        self.assertAlmostEqual(t["terms_end"], NOW - 100 + B.TERMS_S["C"])

    def test_copper_before_the_window_changes_nothing(self):
        g = group(ev(1, "apply", NOW - 4000, nets=["A"]), ev(2, "terms", NOW - 3000, s=150.0))
        self.assertEqual([e["id"] for e in B.tally(g, now=NOW)["terms"]], [2])

    def test_copper_ripped_since_leaves_the_window_used(self):
        """the board is back where the count found it: its table still describes it"""
        g = group(ev(1, "terms", NOW - 4000, s=150.0), ev(2, "apply", NOW - 3000, name="G2", nets=["A"]),
                  ev(3, "rip", NOW - 2000, name="G2"))
        self.assertEqual(B.last_copper(g), 0.0)
        self.assertEqual([e["id"] for e in B.tally(g, now=NOW)["terms"]], [1])

    def test_an_earlier_epochs_copper_is_not_this_epochs(self):
        g = group(ev(1, "apply", NOW - 5000, nets=["A"]), ev(2, "terms", NOW - 4000, s=150.0, epoch=2))
        g["epoch"] = 2
        self.assertEqual(B.last_copper(g), 0.0)
        self.assertEqual(B.count_base(g), NOW - 4000)

    def test_count_base_is_the_later_of_the_two(self):
        g = group(ev(1, "worker", NOW - 5000), ev(2, "apply", NOW - 3000, nets=["A"]))
        self.assertEqual(B.count_base(g), NOW - 3000)


class StaleRows(unittest.TestCase):
    def test_a_row_counted_before_the_groups_copper_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            hb = Path(d)
            (hb / "routing" / "terms").mkdir(parents=True)
            (hb / "tmpWork").mkdir()
            f = hb / "tmpWork" / "ceil.json"
            f.write_text("{}")
            os.utime(f, (NOW - 4000, NOW - 4000))
            (hb / "routing" / "terms" / "G.json").write_text(
                '{"terms": {"T1": {"kind": "placement", "text": "a part moved"}}, "rows": '
                '[{"moved": ["T1"], "closes": false, "left": 2, "contested": ["N1", "N2", "N3"], "evidence": "tmpWork/ceil.json", '
                '"blocked_by": [{"what": "a via site", "part": null, "wall": "fixed", "why": "#4"}]}]}')
            g = group(ev(1, "terms", NOW - 4100, s=150.0), ev(2, "apply", NOW - 3000, nets=["A"]))
            TT, errs = B.read_terms(hb, "G", B.count_base(g))
            self.assertIsNone(TT)
            self.assertTrue(any("older than the board it describes" in e for e in errs), errs)
            os.utime(f, (NOW - 100, NOW - 100))                # counted again, on today's board
            TT, errs = B.read_terms(hb, "G", B.count_base(g))
            self.assertEqual(errs, [])


class Ordinal(unittest.TestCase):
    def test_ordinals(self):
        self.assertEqual([B.ordinal(n) for n in (2, 3, 4, 14, 20)],
                         ["SECOND", "THIRD", "FOURTH", "FOURTEENTH", "TWENTIETH"])
        self.assertEqual([B.ordinal(n) for n in (21, 22, 23, 111)], ["21st", "22nd", "23rd", "111th"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
