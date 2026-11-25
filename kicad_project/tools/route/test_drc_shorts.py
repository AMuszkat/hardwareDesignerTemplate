#!/usr/bin/env python3
"""A short is never a plan's base, never accepted, and a gate of its own in the audit.

    tools/kpy tools/route/test_drc_shorts.py        # OK, exit 0

The case it was written for: board1 NORTHGAP r192, 2026-11-25 -- a transplant laid an AUD_BCLK via on
R1's DRAM_DATA9_B stub; route.py check took that board as its base (the short in "before" is not new),
passed, and apply and audit followed -- the audit's `drc` row fails anyway on a half-routed board.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import route as R  # noqa: E402


def v(t, *items):
    return {"type": t, "description": t, "items": [{"description": i} for i in items]}


class Shorts(unittest.TestCase):
    def test_only_the_types_that_join_two_nets(self):
        rep = {"violations": [v("shorting_items", "Via [AUD_BCLK]", "Track [DRAM_DATA9_B]"),
                              v("clearance", "a", "b"), v("tracks_crossing", "Track [A]", "Track [B]"),
                              v("via_dangling", "Via [X]")]}
        self.assertEqual([t for t, _ in R.drc_shorts(rep)], ["shorting_items", "tracks_crossing"])

    def test_items_are_order_free(self):
        a = R.drc_shorts({"violations": [v("shorting_items", "Via [A]", "Track [B]")]})
        b = R.drc_shorts({"violations": [v("shorting_items", "Track [B]", "Via [A]")]})
        self.assertEqual(a, b)                      # so a base short is found again with the plan

    def test_none(self):
        self.assertEqual(R.drc_shorts({}), [])
        self.assertEqual(R.drc_shorts({"violations": [v("clearance", "a")]}), [])

    def test_source_wires_both_gates(self):
        src = Path(R.__file__).read_text()
        self.assertIn("k[0] not in SHORT_TYPES", src)            # never accepted
        self.assertIn("a short is never a base", src)            # never inherited by check
        self.assertIn('gate("shorts"', src)                      # its own audit row


if __name__ == "__main__":
    unittest.main(verbosity=1)
