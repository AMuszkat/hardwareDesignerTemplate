"""Diagram helper regressions:  python -m unittest discover -s tools/diagrams"""
import contextlib
import io
from pathlib import Path
import unittest

from check_diagrams import text_problems
from sheet import RS, Sheet


class SheetTests(unittest.TestCase):
    def assert_lint_fails(self, sheet, message):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            sheet.lint()
        self.assertIn(message, output.getvalue())

    def test_frame_stays_behind_existing_wire(self):
        s = Sheet(400, 300)
        s.seg(100, 50, 100, 200, RS["pwr"])
        s.frame(40, 100, 300, 150, "supplies", "pwr")
        svg = "".join(s.o)
        self.assertLess(svg.index('<rect x="40"'), svg.index('<line x1="100"'))
        self.assertNotIn('stroke-dasharray=', svg)

    def test_provisional_block_remains_dashed(self):
        s = Sheet(400, 300)
        s.box(40, 40, 250, "candidate", ["not yet committed"], dash=True)
        self.assertIn('stroke-dasharray="6 4"', "".join(s.o))

    def test_footer_wraps_without_reducing_type(self):
        s = Sheet(400, 400)
        s.footer([("A readable note with enough words to require wrapping. " * 2,
                   RS["sub"])], y0=100)
        self.assertGreater(len(s.texts), 1)
        self.assertTrue(all(t[2] == 11.5 for t in s.texts))
        self.assertEqual(sorted(t[1] for t in s.texts), [t[1] for t in s.texts])

    def test_clipped_footer_is_rejected(self):
        s = Sheet(400, 200)
        s.footer([("first", RS["sub"]), ("clipped", RS["sub"])], y0=190)
        self.assert_lint_fails(s, "text anchor off canvas")

    def test_off_canvas_wire_is_rejected(self):
        s = Sheet(400, 300)
        s.seg(100, 100, 410, 100, RS["pwr"])
        self.assert_lint_fails(s, "wire off canvas")

    def test_off_canvas_frame_is_rejected(self):
        s = Sheet(400, 300)
        s.frame(350, 50, 100, 100, "overflow", "pwr")
        self.assert_lint_fails(s, "frame off canvas")

    def test_box_overlap_is_still_rejected(self):
        s = Sheet(400, 300)
        s.box(40, 40, 200, "first", [])
        s.box(100, 40, 200, "second", [])
        self.assert_lint_fails(s, "box overlap")

    def test_wire_through_box_is_still_rejected(self):
        s = Sheet(400, 300)
        s.box(100, 100, 200, "part", [], h=100)
        s.seg(40, 150, 350, 150, RS["pwr"])
        self.assert_lint_fails(s, "wire through")


class RenderedBoundsTests(unittest.TestCase):
    def test_detects_collision_and_clipping(self):
        problems = text_problems({"a": "first", "b": "second"},
                                 {"a": (10, 10, 30, 10),
                                  "b": (20, 15, 100, 10)}, (0, 0, 100, 100))
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("text overlap" in p for p in problems))
        self.assertTrue(any("text off canvas" in p for p in problems))

    def test_touching_text_is_not_overlap(self):
        self.assertEqual(text_problems({"a": "first", "b": "second"},
                                      {"a": (10, 10, 30, 10),
                                       "b": (40, 10, 30, 10)},
                                      (0, 0, 100, 100)), [])

    def test_missing_measurement_fails(self):
        self.assertEqual(text_problems({"a": "missing"}, {}, (0, 0, 100, 100)),
                         ["unmeasured text: missing"])


class GeneratorTests(unittest.TestCase):
    """Every generator declares the lint contract and runs cleanly."""

    def test_generators_declare_contract(self):
        for gen in sorted(Path(__file__).parent.glob("*.py")):
            if gen.name in ("sheet.py", "check_diagrams.py", Path(__file__).name):
                continue
            src = gen.read_text()
            with self.subTest(generator=gen.name):
                for name in ("OUTPUT", "SYSTEM", "REFLECTS", "OPEN_CLAIMS"):
                    self.assertRegex(src, rf"(?m)^{name}\s*=", f"{gen.name} lacks {name}")


if __name__ == "__main__":
    unittest.main()
