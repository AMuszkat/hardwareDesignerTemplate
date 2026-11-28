#!/usr/bin/env python3
"""A wall carries its count, and a reopen is not a refill (budget.py's two sections of that name).

    python3 tools/route/test_budget_terms.py        # OK, exit 0

Standard library only (budget.py is): tally() on in-memory groups; gate(), cmd_wall() and
read_terms() on a ledger in a temporary folder.  The case it was written for: board1,
2026-11-23 and 24 -- 28 walls on five groups in 32 hours, each offering changes nobody had
counted and every answer a full budget again; one group of 33 nets reached its tenth wall
with 13.5 h of tool time against a 6 h budget and no net on copper.
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

T0 = 1_800_000_000.0


def job(i, s, ep=1, t=None):
    return dict(id=i, kind="job", name="G", epoch=ep, label=f"j{i}", t=t or T0 + i, s=s, jid=f"j{i}@1")


def group(*events, tier="C", epoch=1, walls=()):
    return dict(names=["G"], nets=[], tier=tier, basis="test", epoch=epoch, events=list(events), walls=list(walls))


def wall(n, ep, q, rev=1, t=None):
    return dict(t=t or T0 + 100 * n, epoch=ep, n=n, q=q, rev=rev, parts="", blocks="x", changes=[])


@contextlib.contextmanager
def board(g=None, rev=None, answers=""):
    """A board folder with a ledger (group G), QUESTIONS.md and, when asked, a handoff."""
    with tempfile.TemporaryDirectory() as d:
        hb = Path(d)
        (hb / "routing").mkdir()
        (hb / "QUESTIONS.md").write_text("# Questions\n")
        if g is not None:
            B.ledger_path(hb).write_text(json.dumps({"groups": {"G": g}, "home": str(hb)}))
        if rev is not None:
            (hb / "handoff").mkdir()
            (hb / "handoff" / "MANIFEST.json").write_text(json.dumps({"rev": rev}))
            (hb / "handoff" / "07_answers.md").write_text(answers)
        old = {k: os.environ.get(k) for k in ("HW_BUDGET_BOARD", B.ENV_IN, B.ENV_JOB)}
        os.environ["HW_BUDGET_BOARD"] = str(hb)
        os.environ.pop(B.ENV_IN, None)
        os.environ.pop(B.ENV_JOB, None)
        try:
            yield hb
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def quiet(fn, *a, **kw):
    """-> (what fn returned or the exit code it left with, everything it printed)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            r = fn(*a, **kw)
        except SystemExit as e:
            r = ("exit", e.code)
    return r, out.getvalue()


def ledger(hb):
    return json.loads(B.ledger_path(hb).read_text())["groups"]["G"]


def table(hb, terms, rows, key="G", touch=True, blocked=None, extra=None):
    """Write routing/terms/<key>.json and an evidence file per row."""
    p = B.terms_path(hb, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    full = []
    for i, r in enumerate(rows):
        r = dict(r)
        if "evidence" not in r:
            r["evidence"] = f"tmpWork/terms/v{i}.json"
            if touch:
                f = hb / r["evidence"]
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("{}")
        if r.get("closes") is False and "contested" not in r:
            r["contested"] = ["N1", "N2", "N3"]           # three nets left overlapping: not one crossing
        if (blocked is not False and set(r.get("moved") or []) == set(terms) and r.get("closes") is not True
                and "blocked_by" not in r):
            r["blocked_by"] = blocked or [{"what": "the lane end is boxed by a via site", "part": None,
                                    "wall": "fixed", "why": "10_layout #4: the via site is reserved"}]
        full.append(r)
    p.write_text(json.dumps(dict({"terms": terms, "rows": full}, **(extra or {}))))
    return p


TERMS3 = {"T1": {"kind": "rule", "text": "the reserve ends 0.6 mm further south"},
          "T2": {"kind": "placement", "text": "R215..R217 move east"},
          "T3": {"kind": "layer", "text": "the straps change layer west of U200"}}


class Allowance(unittest.TestCase):
    def test_first_epoch_has_the_tier(self):
        for tier in "ABC":
            a = B.allowance(group(tier=tier))
            self.assertEqual((a["rounds"], a["seconds"], a["frac"]),
                             (B.TIERS[tier]["rounds"], B.TIERS[tier]["seconds"], 1.0))

    def test_a_reopen_gets_its_share(self):
        ev = [dict(kind="reopen", epoch=2, t=T0, frac=0.5)]
        self.assertEqual(B.allowance(group(*ev, tier="C", epoch=2))["rounds"], 2)
        self.assertEqual(B.allowance(group(*ev, tier="C", epoch=2))["seconds"], 3 * 3600)
        ev = [dict(kind="reopen", epoch=2, t=T0, frac=0.25)]
        self.assertEqual(B.allowance(group(*ev, tier="C", epoch=2))["rounds"], 1)
        self.assertEqual(B.allowance(group(*ev, tier="C", epoch=2))["seconds"], 1.5 * 3600)

    def test_never_less_than_one_round_and_fifteen_minutes(self):
        ev = [dict(kind="reopen", epoch=2, t=T0, frac=0.25)]
        a = B.allowance(group(*ev, tier="A", epoch=2))
        self.assertEqual((a["rounds"], a["seconds"]), (1, B.MIN_S))
        b = B.allowance(group(*ev, tier="B", epoch=2))
        self.assertEqual((b["rounds"], b["seconds"]), (1, 1800))

    def test_an_epoch_opened_before_the_rule_keeps_its_budget(self):
        ev = [dict(kind="reopen", epoch=11, t=T0, result="handoff rev 187 answers Q125")]
        a = B.allowance(group(*ev, tier="C", epoch=11))
        self.assertEqual((a["rounds"], a["seconds"], a["frac"]), (4, 6 * 3600, 1.0))

    def test_the_share_is_this_epochs_not_an_earlier_ones(self):
        ev = [dict(kind="reopen", epoch=2, t=T0, frac=0.25), dict(kind="reopen", epoch=3, t=T0 + 1)]
        self.assertEqual(B.allowance(group(*ev, tier="C", epoch=3))["rounds"], 4)

    def test_tally_spends_against_the_share(self):
        ev = [dict(kind="reopen", epoch=2, t=T0, frac=0.5), job(2, 60.0, ep=2), job(3, 60.0, ep=2)]
        t = B.tally(group(*ev, tier="C", epoch=2), now=T0 + 10_000)
        self.assertEqual((t["rounds"], t["allow_rounds"], t["left_rounds"]), (2, 2, 0))
        self.assertEqual(t["allow_seconds"], 3 * 3600)
        t = B.tally(group(*ev, job(4, 60.0, ep=2), tier="C", epoch=2), now=T0 + 10_000)
        self.assertTrue(t["no_more"])
        self.assertEqual(t["state"], "spent")


class Reopen(unittest.TestCase):
    def reopened(self, walls, epoch):
        g = group(job(1, 60.0, ep=epoch), tier="C", epoch=epoch, walls=walls)
        with board(g, rev=2, answers=f"## {walls[-1]['q']} -- answered\n") as hb:
            tok, said = quiet(B.gate, hb, "G", kind="job", label="lay", jid="lay@1")
            return ledger(hb), said, tok

    def test_after_the_first_wall_half(self):
        g, said, _ = self.reopened([wall(1, 1, "Q7")], 1)
        self.assertEqual(g["epoch"], 2)
        e = next(e for e in g["events"] if e["kind"] == "reopen")
        self.assertEqual(e["frac"], 0.5)
        self.assertIn("2 round(s) and 3.0 h, half of tier C", said)
        self.assertEqual(B.tally(g)["allow_rounds"], 2)

    def test_after_a_later_wall_a_quarter(self):
        g, said, _ = self.reopened([wall(1, 1, "Q7"), wall(2, 2, "Q9")], 2)
        self.assertEqual(g["epoch"], 3)
        e = [e for e in g["events"] if e["kind"] == "reopen"][-1]
        self.assertEqual(e["frac"], 0.25)
        self.assertIn("1 round(s) and 1.5 h, a quarter of tier C", said)

    def test_an_unanswered_wall_stays_shut(self):
        g = group(job(1, 60.0), tier="C", walls=[wall(1, 1, "Q7")])
        with board(g, rev=2, answers="## Q6 -- another\n") as hb:
            r, said = quiet(B.gate, hb, "G", kind="job", label="x", jid="x@1")
            self.assertEqual(r, ("exit", B.EXIT))
            self.assertEqual(ledger(hb)["epoch"], 1)

    def test_work_after_every_net_is_on_copper_is_a_whole_budget(self):
        ev = [job(1, 60.0), dict(id=2, kind="apply", name="G", epoch=1, t=T0 + 5, s=0, nets=["N1"])]
        g = dict(group(*ev, tier="C"), nets=["N1"])
        with board(g) as hb:
            quiet(B.gate, hb, "G", kind="job", label="x", jid="x@1")
            g2 = ledger(hb)
            self.assertEqual(g2["epoch"], 2)
            self.assertNotIn("frac", next(e for e in g2["events"] if e["kind"] == "reopen"))
            self.assertEqual(B.tally(g2)["allow_rounds"], 4)


class TermsTable(unittest.TestCase):
    def read(self, terms, rows, **kw):
        with board(group(job(1, 60.0))) as hb:
            table(hb, terms, rows, **kw)
            return B.read_terms(hb, "G")

    def test_no_table(self):
        with board(group(job(1, 60.0))) as hb:
            TT, errs = B.read_terms(hb, "G")
            self.assertIsNone(TT)
            self.assertIn("there is no routing/terms/G.json", errs[0])

    def test_a_table_needs_its_ceiling(self):
        TT, errs = self.read(TERMS3, [dict(moved=["T1"], closes=False, left=3)])
        self.assertIsNone(TT)
        self.assertTrue(any("no CEILING row" in e for e in errs), errs)

    def test_a_crossing_is_counted_at_its_ends_first(self):
        rows = [dict(moved=["T1", "T2", "T3"], closes=False, left=2, contested=["A", "B"]),
                dict(moved=["T1"], closes=False, left=2, contested=["A", "B"])]
        TT, errs = self.read(TERMS3, rows)
        self.assertIsNone(TT)
        self.assertTrue(any("the bind is a crossing, A x B" in e for e in errs), errs)

    def test_a_crossing_with_its_swap_row_passes_that_check(self):
        terms = dict(TERMS3, T4={"kind": "swap", "nets": ["A", "B"], "text": "A's and B's U500 sites exchanged"})
        rows = [dict(moved=["T1", "T2", "T3", "T4"], closes=False, left=2, contested=["A", "B"]),
                dict(moved=["T4"], closes=False, left=2, contested=["C", "D"], crossings=[["C", "D"]])]
        TT, errs = self.read(terms, rows)
        self.assertFalse(any("A x B" in e for e in errs), errs)
        self.assertTrue(any("C x D" in e for e in errs), errs)          # the swap's own crossing is next

    def test_a_row_that_does_not_close_names_what_it_left(self):
        rows = [dict(moved=["T1", "T2", "T3"], closes=False, left=2, contested=[])]
        TT, errs = self.read(TERMS3, rows)
        self.assertTrue(any('"contested"' in e for e in errs), errs)

    def test_a_closing_ceiling_needs_every_term_counted(self):
        rows = [dict(moved=["T1", "T2", "T3"], closes=True, left=0, proof="check"), dict(moved=["T1"], closes=False, left=3)]
        TT, errs = self.read(TERMS3, rows)
        self.assertIsNone(TT)
        self.assertEqual(sum("has no row of its own" in e for e in errs), 2, errs)      # T2 and T3

    def test_alone_or_left_out_both_count(self):
        rows = [dict(moved=["T1", "T2", "T3"], closes=True, left=0, proof="check"),
                dict(moved=["T1"], closes=False, left=3),
                dict(moved=["T1", "T3"], closes=True, left=0, proof="check"),            # every term but T2
                dict(moved=["T3"], closes=None, left=None, note="no answer")]
        TT, errs = self.read(TERMS3, rows)
        self.assertEqual(errs, [])
        self.assertEqual(TT["ceiling"]["moved"], ["T1", "T2", "T3"])

    def test_a_ceiling_that_does_not_close_says_what_stops_it(self):
        row = dict(moved=["T1", "T2", "T3"], closes=False, left=1)
        TT, errs = self.read(TERMS3, [row], blocked=False)
        self.assertIsNone(TT)
        self.assertIn("does not say what stops it", errs[0])
        self.assertIn("no row closes", errs[0])
        # a part that stops it and that no term moves: the count is missing (board1 R1, 2026-11-25)
        by = [{"what": "pad C100.1 over the lane end (33.5, 14.2)", "part": "C100"}, {"what": "a via site", "part": None, "wall": "fixed", "why": "#4"}]
        TT, errs = self.read(TERMS3, [dict(row, blocked_by=by)])
        self.assertIsNone(TT)
        self.assertIn("C100 stops the ceiling", errs[0])
        self.assertIn("EVERY TERM THAT COULD MOVE", errs[0])
        # ... unless a rule keeps it there, and the wall says so
        TT, errs = self.read(TERMS3, [dict(row, blocked_by=by)], extra={"fixed": {"C100": "10_layout #9: at its pin"}})
        self.assertEqual(errs, [])
        self.assertIn("- pad C100.1 over the lane end (33.5, 14.2) -- C100 stays: 10_layout #9: at its pin",
                      B.closing_lines(TT, "G"))
        # ... or a term moves it
        terms = dict(TERMS3, T4={"kind": "placement", "text": "C100 moves 0.5 mm west"})
        TT, errs = self.read(terms, [dict(row, moved=["T1", "T2", "T3", "T4"], blocked_by=by)])
        self.assertEqual(errs, [])
        # a blocker that reads like a reference says whether it is a part
        TT, errs = self.read(TERMS3, [dict(row, blocked_by=[{"what": "pad R66.1 north of the lane"}])])
        self.assertIn("it names R66", errs[0])
        TT, errs = self.read(TERMS3, [dict(row, blocked_by=[{"what": "DRAM_DATA9_B's lane end on In7.Cu", "wall": "fixed",
                                                                  "why": "another group's copper"}])])
        self.assertEqual(errs, [])
        # a ceiling that closes owes none
        TT, errs = self.read({"T1": TERMS3["T1"]}, [dict(moved=["T1"], closes=True, left=0, proof="check")], blocked=False)
        self.assertEqual(errs, [])

    def test_only_a_gap_between_fixed_walls_does_not_fit(self):
        """board1 R1, Q147 (rev 212): DOES NOT FIT with a router lead and its own lays uncounted."""
        row = dict(moved=["T1", "T2", "T3"], closes=False, left=1)
        own = {"what": "the pocket's walls: CA4_A's lay, NCS1_A's In2 track", "part": None, "wall": "own"}
        # a blocker that says no wall, or a fixed one with no rule
        TT, errs = self.read(TERMS3, [dict(row, blocked_by=[{"what": "a pocket", "part": None}])])
        self.assertIn("which wall is it?", errs[0])
        TT, errs = self.read(TERMS3, [dict(row, blocked_by=[{"what": "a pocket", "part": None, "wall": "fixed"}])])
        self.assertIn("fixed by what?", errs[0])
        # the group's own copper, uncounted: a lead, the table refused
        TT, errs = self.read(TERMS3, [dict(row, blocked_by=[own])])
        self.assertIsNone(TT)
        self.assertIn("A LEAD, UNCOUNTED", errs[0])
        # counted by a row that moved every term and re-laid it: the table stands
        relaid = dict(row, evidence="tmpWork/terms/relaid.json", lead="CA4_A, NCS1_A re-laid",
                      blocked_by=[{"what": "the board edge", "part": None, "wall": "fixed", "why": "the outline"}])
        with board(group(job(1, 60.0))) as hb:
            (hb / "tmpWork/terms").mkdir(parents=True, exist_ok=True)
            (hb / relaid["evidence"]).write_text("{}")
            table(hb, TERMS3, [dict(row, blocked_by=[dict(own, counted=relaid["evidence"])]), relaid])
            TT, errs = B.read_terms(hb, "G")
            self.assertEqual(errs, [])
            self.assertIn("every term, and CA4_A, NCS1_A re-laid", "\n".join(B.terms_lines(TT)))
            self.assertIn("counted: `tmpWork/terms/relaid.json`", "\n".join(B.closing_lines(TT, "G")))
            # ... but a count its window cut is no count
            table(hb, TERMS3, [dict(row, blocked_by=[dict(own, counted=relaid["evidence"])]),
                               dict(relaid, closes=None, left=None)])
            TT, errs = B.read_terms(hb, "G")
            self.assertIn("no answer in its window", errs[0])
            # the router's own line in the ceiling's evidence is a router lead, whatever the entries say
            ev = hb / "tmpWork/terms/ceil.log"
            ev.write_text("DMI0_B: the channel is shut by routed nets, not by the board alone -- another "
                          "--seed or --full may open it without a change to the spec")
            table(hb, TERMS3, [dict(row, evidence="tmpWork/terms/ceil.log")])
            TT, errs = B.read_terms(hb, "G")
            self.assertIsNone(TT)
            self.assertIn("the router's own lead", errs[0])
            # ... counted by another seed: the table stands
            seed = dict(relaid, lead="--seed 2")
            table(hb, TERMS3, [dict(row, evidence="tmpWork/terms/ceil.log", blocked_by=[
                {"what": "DMI0_B's cut point", "part": None, "wall": "router", "counted": seed["evidence"]}]), seed])
            TT, errs = B.read_terms(hb, "G")
            self.assertEqual(errs, [])

    def test_a_ceiling_that_does_not_close_is_a_whole_table(self):
        TT, errs = self.read(TERMS3, [dict(moved=["T3", "T2", "T1"], closes=False, left=5)])
        self.assertEqual(errs, [])
        self.assertEqual(TT["closing"], [])

    def test_one_term_is_its_own_ceiling(self):
        one = {"T1": TERMS3["T1"]}
        TT, errs = self.read(one, [dict(moved=["T1"], closes=True, left=0, proof="check")])
        self.assertEqual(errs, [])
        self.assertEqual(len(TT["closing"]), 1)

    def test_cheapest_first(self):
        rows = [dict(moved=["T1", "T2", "T3"], closes=True, left=0, proof="check"),
                dict(moved=["T3"], closes=True, left=0, proof="check"),                  # layer
                dict(moved=["T1"], closes=True, left=0, proof="check"),                  # rule
                dict(moved=["T2"], closes=True, left=0, proof="check"),                  # placement
                dict(moved=[], closes=True, left=0, proof="check")]                      # no term: more effort only
        TT, errs = self.read(TERMS3, rows)
        self.assertEqual(errs, [])
        self.assertEqual([r["moved"] for r in TT["closing"]], [[], ["T2"], ["T1"], ["T3"], ["T1", "T2", "T3"]])

    def test_a_closing_row_says_how_it_is_known(self):
        rows = [dict(moved=["T1", "T2", "T3"], closes=True, left=0)]
        TT, errs = self.read(TERMS3, rows)
        self.assertTrue(any('"proof"' in e for e in errs), errs)
        rows = [dict(moved=["T1", "T2", "T3"], closes=True, left=0, proof="looks fine")]
        TT, errs = self.read(TERMS3, rows)
        self.assertTrue(any('"proof"' in e for e in errs), errs)
        rows = [dict(moved=["T1", "T2", "T3"], closes=False, left=4)]            # a row that does not close needs none
        TT, errs = self.read(TERMS3, rows)
        self.assertEqual(errs, [])

    def test_closed_means_nothing_left(self):
        rows = [dict(moved=["T1", "T2", "T3"], closes=True, left=2, proof="check")]
        TT, errs = self.read(TERMS3, rows)
        self.assertTrue(any("0 left" in e for e in errs), errs)

    def test_not_closed_says_how_many(self):
        TT, errs = self.read(TERMS3, [dict(moved=["T1", "T2", "T3"], closes=False)])
        self.assertTrue(any("how many nets" in e for e in errs), errs)

    def test_a_term_has_a_kind_and_a_text(self):
        TT, errs = self.read({"T1": {"kind": "effort", "text": "more rounds"}}, [dict(moved=["T1"], closes=True, left=0, proof="check")])
        self.assertTrue(any("term T1" in e for e in errs), errs)

    def test_a_row_moves_only_named_terms(self):
        TT, errs = self.read({"T1": TERMS3["T1"]}, [dict(moved=["T1"], closes=True, left=0, proof="check"),
                                                     dict(moved=["T9"], closes=False, left=1)])
        self.assertTrue(any("T9 is not in" in e for e in errs), errs)

    def test_evidence_is_a_file_of_the_board(self):
        TT, errs = self.read(TERMS3, [dict(moved=["T1", "T2", "T3"], closes=False, left=5)], touch=False)
        self.assertTrue(any("no such file" in e for e in errs), errs)
        TT, errs = self.read(TERMS3, [dict(moved=["T1", "T2", "T3"], closes=False, left=5, evidence="../x.json")])
        self.assertTrue(any("inside the board folder" in e for e in errs), errs)

    def test_evidence_counted_for_an_earlier_wall_is_refused(self):
        with board(group(job(1, 60.0))) as hb:
            table(hb, TERMS3, [dict(moved=["T1", "T2", "T3"], closes=False, left=5)])
            TT, errs = B.read_terms(hb, "G", time.time() + 3600)          # the epoch began after the count
            self.assertIsNone(TT)
            self.assertTrue(any("older than the board it describes" in e for e in errs), errs)


class Wall(unittest.TestCase):
    CLOSING = [dict(moved=["T1", "T2", "T3"], closes=True, left=0, proof="check"),
               dict(moved=["T1"], closes=False, left=3),
               dict(moved=["T2"], closes=True, left=0, proof="check"),
               dict(moved=["T3"], closes=None, left=None, note="4 knots at it 170")]

    def test_a_big_groups_first_wall_needs_the_table(self):
        with board(group(job(1, 60.0, t=time.time() - 100), tier="C")) as hb:
            r, said = quiet(B.cmd_wall, hb, "G", "U1's field is full", ["rule: open In5"])
            self.assertEqual(r, 2)
            self.assertIn("WALL REFUSED", said)
            self.assertIn("--terms", said)
            self.assertEqual(ledger(hb)["walls"], [])
            self.assertNotIn("## Q", (hb / "QUESTIONS.md").read_text())

    def test_a_small_groups_first_wall_may_still_name_its_changes(self):
        with board(group(job(1, 60.0, t=time.time() - 100), tier="A")) as hb:
            r, said = quiet(B.cmd_wall, hb, "G", "the pad row is shut", ["placement: C5 0.3 mm west"])
            self.assertEqual(r, 0, said)
            q = (hb / "QUESTIONS.md").read_text()
            self.assertIn("1. **placement** — C5 0.3 mm west", q)
            self.assertIn("**When it reopens:** 1 round(s) and 22 min, half", q)

    def test_a_small_groups_second_wall_needs_the_table(self):
        g = group(dict(kind="reopen", epoch=2, t=time.time() - 200, frac=0.5), job(2, 60.0, ep=2, t=time.time() - 100),
                  tier="A", epoch=2, walls=[wall(1, 1, "Q1")])
        with board(g) as hb:
            r, said = quiet(B.cmd_wall, hb, "G", "still shut", ["placement: C5 0.6 mm west"])
            self.assertEqual(r, 2)
            self.assertIn("WALL REFUSED", said)

    def test_with_a_table_the_changes_are_its_rows(self):
        with board(group(job(1, 60.0, t=time.time() - 100), tier="C")) as hb:
            table(hb, TERMS3, self.CLOSING)
            r, said = quiet(B.cmd_wall, hb, "G", "U1's field is full", ["rule: something nobody counted"])
            self.assertEqual(r, 2)
            self.assertIn("no --change with a terms table", said)
            self.assertEqual(ledger(hb)["walls"], [])

    def test_the_wall_quotes_the_table(self):
        with board(group(job(1, 60.0, t=time.time() - 100), tier="C")) as hb:
            p = table(hb, TERMS3, self.CLOSING)
            r, said = quiet(B.cmd_wall, hb, "G", "U1's field is full: 3 nets have no via site", [])
            self.assertEqual(r, 0, said)
            q = (hb / "QUESTIONS.md").read_text()
            self.assertIn("## Q1 — WALL G: U1's field is full", q)
            self.assertIn("| every term (T1, T2, T3): the ceiling | yes | 0 |", q)
            self.assertIn("| T1 | NO | 3 |", q)
            self.assertIn("| T3 | no answer in the window | ? |", q)
            self.assertIn("1. **placement** — T2: R215..R217 move east", q)
            self.assertIn("2. **placement + rule + layer** — T1 + T2 + T3", q)
            self.assertIn("**When it reopens:** 2 round(s) and 3.0 h, half", q)
            w = ledger(hb)["walls"][0]
            self.assertEqual(w["terms"]["closing"], ["T2", "T1 + T2 + T3"])
            self.assertTrue(w["terms"]["ceiling"])
            self.assertEqual((hb / w["terms"]["path"]).read_bytes(), p.read_bytes())
            self.assertIn("ring: ", said)

    def test_a_row_that_only_routed_or_counted_is_a_lead(self):
        rows = [dict(moved=["T1", "T2", "T3"], closes=True, left=0, proof="router"),
                dict(moved=["T1"], closes=False, left=3),
                dict(moved=["T2"], closes=True, left=0, proof="count"),
                dict(moved=["T3"], closes=True, left=0, proof="check")]
        with board(group(job(1, 60.0, t=time.time() - 100), tier="C")) as hb:
            table(hb, TERMS3, rows)
            r, said = quiet(B.cmd_wall, hb, "G", "U1's field is full: 3 nets have no via site", [])
            self.assertEqual(r, 0, said)
            q = (hb / "QUESTIONS.md").read_text()
            self.assertIn("| every term (T1, T2, T3): the ceiling | routes, plan not gated | 0 |", q)
            self.assertIn("| T2 | counts, no plan | 0 |", q)
            self.assertIn("| T3 | yes | 0 |", q)
            self.assertRegex(q, r"1\. \*\*placement\*\* — T2: .* -- A LEAD: a counting model")
            self.assertRegex(q, r"2\. \*\*layer\*\* — T3: .* -- PROVED: `route.py check` PASS")
            self.assertRegex(q, r"3\. \*\*placement \+ rule \+ layer\*\* — T1 \+ T2 \+ T3: .* -- A LEAD: every net routed")

    def test_a_ceiling_that_does_not_close_is_a_capacity_wall(self):
        with board(group(job(1, 60.0, t=time.time() - 100), tier="C")) as hb:
            table(hb, TERMS3, [dict(moved=["T1", "T2", "T3"], closes=False, left=4)])
            r, said = quiet(B.cmd_wall, hb, "G", "no via band south of U200", [])
            self.assertEqual(r, 0, said)
            q = (hb / "QUESTIONS.md").read_text()
            self.assertIn("WALL G -- DOES NOT FIT: no via band south of U200", q)
            self.assertIn("**No change the board can name closes it.**", q)
            self.assertIn("left 4 net(s) open", q)
            w = ledger(hb)["walls"][0]
            self.assertEqual(w["terms"]["closing"], [])
            self.assertIs(w["terms"]["ceiling"], False)
            self.assertIn("**What stops the ceiling**", q)
            self.assertIn("- the lane end is boxed by a via site", q)

    def test_a_ceiling_its_window_cut_is_not_a_capacity_wall(self):
        with board(group(job(1, 60.0, t=time.time() - 100), tier="C")) as hb:
            table(hb, TERMS3, [dict(moved=["T1", "T2", "T3"], closes=None, left=None)], blocked=False)
            r, said = quiet(B.cmd_wall, hb, "G", "no via band south of U200", [])
            self.assertEqual(r, 0, said)
            q = (hb / "QUESTIONS.md").read_text()
            self.assertIn("WALL G -- NO ANSWER IN ITS WINDOW: no via band south of U200", q)
            self.assertNotIn("DOES NOT FIT:", q)
            self.assertIn("**The count did not end.**", q)

    def test_a_table_from_before_this_epoch_is_refused(self):
        g = group(dict(kind="reopen", epoch=2, t=time.time() + 50, frac=0.5), job(2, 60.0, ep=2, t=time.time() + 60),
                  tier="C", epoch=2, walls=[wall(1, 1, "Q1")])
        with board(g) as hb:
            table(hb, TERMS3, [dict(moved=["T1", "T2", "T3"], closes=False, left=4)])    # written before the reopen
            r, said = quiet(B.cmd_wall, hb, "G", "x", [])
            self.assertEqual(r, 2)
            self.assertIn("older than the board it describes", said)

    def test_cmd_terms(self):
        with board(group(job(1, 60.0, t=time.time() - 100), tier="C")) as hb:
            r, said = quiet(B.cmd_terms, hb, "G")
            self.assertEqual(r, 1)
            self.assertIn("TERMS FAIL", said)
            table(hb, TERMS3, self.CLOSING)
            r, said = quiet(B.cmd_terms, hb, "G")
            self.assertEqual(r, 0, said)
            self.assertIn("TERMS OK -- 4 row(s), 2 close(s)", said)


class TermsWindow(unittest.TestCase):
    def spent(self, *more, **kw):
        ev = [job(i, 60.0) for i in range(1, 6)]                          # five rounds of a tier C's four
        return group(*ev, *more, tier="C", **kw)

    def test_a_spent_group_may_still_count_its_terms(self):
        with board(self.spent()) as hb:
            r, _ = quiet(B.gate, hb, "G", kind="job", label="x", jid="x@1")
            self.assertEqual(r, ("exit", B.EXIT))
            tok, said = quiet(B.gate, hb, "G", kind="terms", label="ceiling", jid="c@1")
            self.assertTrue(tok)
            self.assertAlmostEqual(tok.detach()["limit"], B.TERMS_S["C"], delta=5)
            self.assertIn("opens now", said)
            e = ledger(hb)["events"][-1]
            self.assertEqual((e["kind"], e["jid"]), ("terms", "c@1"))

    def test_a_later_variant_gets_what_is_left_of_the_window(self):
        first = dict(id=9, kind="terms", name="G", epoch=1, t=time.time() - 1000, s=900.0, jid="c@1")
        with board(self.spent(first)) as hb:
            tok, said = quiet(B.gate, hb, "G", kind="terms", label="t1", jid="t1@1")
            self.assertAlmostEqual(tok.detach()["limit"], B.TERMS_S["C"] - 1000, delta=5)
            self.assertIn("is open", said)

    def test_after_the_window_the_table_is_written(self):
        first = dict(id=9, kind="terms", name="G", epoch=1, t=time.time() - B.TERMS_S["C"] - 5, s=900.0, jid="c@1")
        with board(self.spent(first)) as hb:
            r, said = quiet(B.gate, hb, "G", kind="terms", label="t1", jid="t1@1")
            self.assertEqual(r, ("exit", B.EXIT))
            self.assertIn("its terms count is over", said)
            self.assertEqual(len(ledger(hb)["events"]), 6)

    def test_a_new_epoch_has_a_new_window(self):
        old = dict(id=9, kind="terms", name="G", epoch=1, t=time.time() - 9 * 3600, s=900.0, jid="c@1")
        g = group(old, dict(kind="reopen", epoch=2, t=time.time() - 3600, frac=0.5), job(11, 60.0, ep=2),
                  tier="C", epoch=2, walls=[wall(1, 1, "Q1")])
        with board(g) as hb:
            tok, _ = quiet(B.gate, hb, "G", kind="terms", label="c", jid="c@2")
            self.assertAlmostEqual(tok.detach()["limit"], B.TERMS_S["C"], delta=5)

    def test_not_at_the_wall(self):
        with board(self.spent(walls=[wall(1, 1, "Q1")])) as hb:
            r, said = quiet(B.gate, hb, "G", kind="terms", label="c", jid="c@1")
            self.assertEqual(r, ("exit", B.EXIT))
            self.assertIn("at its wall", said)

    def test_terms_cost_no_round_and_none_of_the_groups_time(self):
        ev = [job(1, 60.0), dict(id=2, kind="terms", name="G", epoch=1, t=T0 + 5, s=3000.0, jid="c@1"),
              dict(id=3, kind="terms", name="G", epoch=1, t=T0 + 6, s=None, limit=5000.0, jid="d@1")]
        t = B.tally(group(*ev, tier="C"), now=T0 + 4000)
        self.assertEqual((t["rounds"], t["seconds"]), (1, 60.0))
        self.assertEqual(len(t["terms"]), 2)
        self.assertEqual(t["terms_end"], T0 + 5 + B.TERMS_S["C"])
        self.assertEqual(t["state"], "open")

    def test_the_users_grant_reopens_the_window_from_now(self):
        first = dict(id=9, kind="terms", name="G", epoch=1, t=time.time() - B.TERMS_S["C"] - 3600, s=900.0, jid="c@1")
        why = "the user, 2026-11-28: 45 more min (Recommended)"
        with board(self.spent(first)) as hb:
            r, said = quiet(B.cmd_grant, hb, "G", 45, why)
            self.assertEqual(r, 0, said)
            e = ledger(hb)["events"][-1]
            self.assertEqual((e["kind"], e["minutes"], e["why"]), ("terms_grant", 45, why))
            t = B.tally(ledger(hb))
            self.assertEqual(t["rounds"], 5)                              # it gives no round
            tok, said = quiet(B.gate, hb, "G", kind="terms", label="x", jid="x@1")
            self.assertTrue(tok, said)
            self.assertAlmostEqual(tok.detach()["limit"], 45 * 60, delta=5)

    def test_a_grant_is_the_users_and_bounded(self):
        first = dict(id=9, kind="terms", name="G", epoch=1, t=time.time() - B.TERMS_S["C"] - 5, s=900.0, jid="c@1")
        with board(self.spent(first)) as hb:
            n = len(ledger(hb)["events"])
            for m, why in ((45, "the session thinks so, more time"), (B.GRANT_MAX_MIN + 1, "the user, 2026-11-28: yes"),
                           (0, "the user, 2026-11-28: yes please")):
                r, said = quiet(B.cmd_grant, hb, "G", m, why)
                self.assertEqual(r, 2, said)
                self.assertIn("GRANT REFUSED", said)
            self.assertEqual(len(ledger(hb)["events"]), n)
        with board(self.spent()) as hb:                                   # no window yet: nothing to extend
            r, said = quiet(B.cmd_grant, hb, "G", 45, "the user, 2026-11-28: 45 more min")
            self.assertEqual(r, 1, said)

    def test_what_runs_inside_a_terms_job_is_charged_nothing(self):
        with board(self.spent()) as hb:
            os.environ[B.ENV_IN] = "terms"                                # what bgwait.py sets for its command
            try:
                tok, _ = quiet(B.gate, hb, "G", kind="run", label="variant's router run")
            finally:
                os.environ.pop(B.ENV_IN, None)
            self.assertFalse(tok)
            self.assertEqual(len(ledger(hb)["events"]), 5)


if __name__ == "__main__":
    unittest.main()
