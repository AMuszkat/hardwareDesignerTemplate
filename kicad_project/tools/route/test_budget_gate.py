#!/usr/bin/env python3
"""A gate job (`bgwait.py run --group G --gate`) costs time and no round; everything else as before.

    python3 tools/route/test_budget_gate.py        # OK, exit 0

Standard library only (budget.py is): tally() on in-memory groups, close() on a ledger in a
temporary folder.  The case it was written for: board1 NORTHGAP, 2026-11-24 -- two DRC jobs
(12 s, 19 s) and a 7 s dry run were charged three of its four rounds.
"""
import contextlib
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


def job(i, s, gate=False, t=None):
    e = dict(id=i, kind="job", name="G", epoch=1, label=f"j{i}", t=t or T0 + i, s=s, jid=f"j{i}@1")
    if gate:
        e["gate"] = True
    return e


def inner(i, kind, jid, **kw):
    return dict(dict(id=i, kind=kind, name="G", epoch=1, t=T0 + i, s=5.0, job=jid), **kw)


def group(*events, tier="C"):
    return dict(names=["G"], nets=[], tier=tier, basis="", epoch=1, events=list(events), walls=[])


class GateJob(unittest.TestCase):
    def rounds(self, *ev):
        return B.tally(group(*ev), now=T0 + 10_000)["rounds"]

    def test_plain_job_is_still_a_round(self):
        self.assertEqual(self.rounds(job(1, 12.0)), 1)

    def test_gate_job_is_no_round_but_its_time_counts(self):
        t = B.tally(group(job(1, 12.0, gate=True)), now=T0 + 10_000)
        self.assertEqual(t["rounds"], 0)
        self.assertAlmostEqual(t["seconds"], 12.0)

    def test_gate_over_its_limit_is_a_round(self):
        self.assertEqual(self.rounds(job(1, B.GATE_S + 1, gate=True)), 1)
        self.assertEqual(self.rounds(job(1, B.GATE_S, gate=True)), 0)

    def test_race_inside_a_gate_is_counted(self):
        self.assertEqual(self.rounds(job(1, 60.0, gate=True), inner(2, "race", "j1@1", variants=4)), 1)

    def test_router_run_inside_a_gate_is_counted(self):
        self.assertEqual(self.rounds(job(1, 60.0, gate=True), inner(2, "run", "j1@1")), 1)

    def test_check_inside_a_gate_opens_no_round(self):
        a = B.tally(group(inner(1, "check", "j2@1"), job(2, 60.0, gate=True)), now=T0 + 10_000)
        self.assertEqual((a["rounds"], a["checks"]), (0, 0))
        b = B.tally(group(inner(1, "check", "j2@1"), job(2, 60.0)), now=T0 + 10_000)
        self.assertEqual((b["rounds"], b["checks"]), (1, 1))          # a plain job's check: as before

    def test_check_inside_a_gate_after_a_round_is_one_of_its_checks(self):
        ev = [job(1, 600.0), inner(2, "race", "j1@1", variants=4), inner(3, "check", "j4@1"),
              job(4, 60.0, gate=True)]
        t = B.tally(group(*ev), now=T0 + 10_000)
        self.assertEqual((t["rounds"], t["checks"]), (1, 1))

    def test_check_inside_a_gate_over_its_limit_counts(self):
        t = B.tally(group(inner(1, "check", "j2@1"), job(2, B.GATE_S + 1, gate=True)), now=T0 + 10_000)
        self.assertEqual(t["rounds"], 1)

    def test_apply_gate_over_its_limit_is_no_round(self):
        """board1 R1, 2026-11-25: route.py apply of a checked plan ran 6 min as a --gate job."""
        e = job(1, 339.3, gate=True)
        e["label"] = "gate r1Fesc_apply: tools/kpy tools/route/route.py apply routing/R1_Fesc.json --board board1"
        self.assertEqual(self.rounds(e), 0)
        for lab in ("gate x: tools/kpy tools/route/route.py apply a.json && tools/kpy tools/route/netgroup.py b.json",
                    "gate x: bash run.sh", "gate x: tools/kpy tools/route/route.py check a.json"):
            f = dict(e, label=lab)
            self.assertEqual(self.rounds(f), 1, lab)              # a compound, a script, a long check: a round
        g = dict(e)
        g.pop("gate")
        self.assertEqual(self.rounds(g), 1)                       # not a gate: as before

    def test_apply_gate_with_a_race_inside_is_counted(self):
        e = job(1, 600.0, gate=True)
        e["label"] = "gate x: tools/kpy tools/route/route.py apply a.json"
        self.assertEqual(self.rounds(e, inner(2, "race", "j1@1", variants=4)), 1)

    def test_northgap_r192(self):
        """the hand-move gate (199 s, a check inside) and the dry gate: the race after them is round 1."""
        ev = [job(245, 199.0, gate=True), job(246, 24.4, gate=True), inner(247, "check", "j245@1"),
              job(248, 1500.0), inner(249, "race", "j248@1", variants=1)]
        t = B.tally(group(*ev), now=T0 + 10_000)
        self.assertEqual(t["rounds"], 1)
        self.assertEqual(B.tally(group(*ev[:3]), now=T0 + 10_000)["rounds"], 0)

    def test_northgap_r8(self):
        """three gates and the race job: one round, not four."""
        ev = [job(49, 12.4, gate=True), job(50, 19.4, gate=True), job(51, 7.1, gate=True),
              job(52, 1800.0), inner(53, "race", "j52@1", variants=4)]
        t = B.tally(group(*ev), now=T0 + 10_000)
        self.assertEqual(t["rounds"], 1)
        self.assertEqual(t["left_rounds"], 3)
        self.assertAlmostEqual(t["seconds"], 12.4 + 19.4 + 7.1 + 1800.0)
        ev_plain = [dict(e) for e in ev]
        for e in ev_plain:
            e.pop("gate", None)
        self.assertEqual(B.tally(group(*ev_plain), now=T0 + 10_000)["rounds"], 4)   # as before the flag

    def test_running_gate(self):
        now = time.time()
        e = job(1, None, gate=True, t=now - 10)
        e.update(limit=3600, pid=os.getpid())
        self.assertEqual(B.tally(group(e), now=now)["rounds"], 0)
        e["t"] = now - (B.GATE_S + 60)
        self.assertEqual(B.tally(group(e), now=now)["rounds"], 1)

    def test_close_marks_a_gate_over_its_limit(self):
        with tempfile.TemporaryDirectory() as d:
            hb = Path(d)
            (hb / "routing").mkdir()
            g = group(job(1, None, gate=True), job(2, None, gate=True))
            B.ledger_path(hb).write_text(json.dumps({"groups": {"G": g}, "home": str(hb)}))
            B.close(hb, "G", 1, 15.0, "exit 0", ok=True)
            B.close(hb, "G", 2, B.GATE_S + 30, "exit 0", ok=True)
            ev = {e["id"]: e for e in json.loads(B.ledger_path(hb).read_text())["groups"]["G"]["events"]}
            self.assertNotIn("gate_over", ev[1])
            self.assertIn("a round", ev[2]["gate_over"])
            self.assertEqual(B.tally(json.loads(B.ledger_path(hb).read_text())["groups"]["G"])["rounds"], 1)


class GatePath(unittest.TestCase):
    def test_gate_writes_the_flag_bgwait_asks_for(self):
        """budget.gate(..., is_gate=True) -- what `bgwait.py run --group G --gate` calls."""
        with tempfile.TemporaryDirectory() as d:
            hb = Path(d)
            old = os.environ.get("HW_BUDGET_BOARD")
            os.environ["HW_BUDGET_BOARD"] = str(hb)
            try:
                for i, g_ in enumerate((True, False)):
                    tok = B.gate(hb, "G", ["NET_A"], kind="job", label=f"t{i}", jid=f"t{i}@1", is_gate=g_)
                    B.close(tok.home, tok.key, tok.detach()["event"], 3.0, "exit 0", ok=True)
            finally:
                if old is None:
                    os.environ.pop("HW_BUDGET_BOARD", None)
                else:
                    os.environ["HW_BUDGET_BOARD"] = old
            g = json.loads(B.ledger_path(hb).read_text())["groups"]["G"]
            self.assertEqual([bool(e.get("gate")) for e in g["events"]], [True, False])
            self.assertEqual(B.tally(g)["rounds"], 1)                 # the plain job only



class OneRoundOneJob(unittest.TestCase):
    """board1 R1 r201, 2026-11-26: five variants as five `bgwait run --group R1` jobs -- the second was let in past
    the budget, and its tally got the first job's router refused: a granted round spent with 0 s routed."""

    def test_second_job_while_the_first_holds_the_round_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            hb = Path(d)
            (hb / "routing").mkdir()
            (hb / "QUESTIONS.md").write_text("# Questions\n")
            g = group(tier="C")
            g["events"].append(dict(id=1, kind="reopen", epoch=1, t=time.time() - 5, frac=0.25, result="x"))
            B.ledger_path(hb).write_text(json.dumps({"groups": {"G": g}, "home": str(hb)}))
            old = {k: os.environ.get(k) for k in ("HW_BUDGET_BOARD", B.ENV_IN, B.ENV_JOB)}
            os.environ["HW_BUDGET_BOARD"] = str(hb)
            os.environ.pop(B.ENV_IN, None)
            os.environ.pop(B.ENV_JOB, None)
            try:
                with open(os.devnull, "w") as dn, contextlib.redirect_stdout(dn), contextlib.redirect_stderr(dn):
                    B.gate(hb, "G", kind="job", jid="a@1", label="A")          # the round
                    with self.assertRaises(SystemExit) as cm:
                        B.gate(hb, "G", kind="job", jid="b@2", label="B")      # a second job: refused
                    self.assertEqual(cm.exception.code, B.EXIT)
                    B.gate(hb, "G", kind="job", jid="c@3", label="C", is_gate=True)  # a gate job still runs
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

if __name__ == "__main__":
    unittest.main(verbosity=1)
