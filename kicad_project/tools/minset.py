"""minset.py -- the smallest set that still fails, found with the tests run in parallel.

    sys.path.insert(0, <kicad_project>/tools)
    import minset
    keep = minset.minimal(items, fails)                 # fails(subset: list) -> bool

WHAT FOR.  A loop that solves a model, checks the answer with a slower tool (a router, a DRC run)
and turns each failure into a cut wants the SMALLEST part of the answer that fails: a cut on
three nets removes far more of the model than a cut on thirty.  The deletion filter finds one --
drop each item whose absence still leaves the rest failing -- at one test an item, one after the
other: thirty router runs on one core while the others idle (the first project's fanout loop:
36 % of 14.5 h, checks of up to 18 minutes a round).

THE SAME ANSWER, IN WAVES.  `fails` must be MONOTONE: a set that fails still fails with more
items in it (dropping an item only relaxes the check).  Then for the items not yet decided,
r1 .. rm in order, "does it still fail without r1 .. rk?" is true up to some k* and false after
it.  One wave asks every k at once: r1 .. rk* are dropped together, and r(k*+1) is KEPT for good
-- exactly what the serial filter decides, item by item.  So the result is the serial filter's
own, whatever the number of workers, in as many waves as items are kept (plus one) instead of as
many tests as there are items.  With one worker it IS the serial filter.

`fails` runs in threads: it must be safe to call concurrently (each call its own scratch files)
and should spend its time in a child process -- a pure-Python test gains nothing here (the GIL).
A test that raises stops the search: a crash is no verdict.

    minimal(items, fails, jobs=None, always=(), log=None)
      always: items in every test and never dropped (the fixed part of the check);
      jobs:   tests at once (default: par.grant());
      log:    callable(str) for one line a wave.
Raises ValueError when the whole set does not fail (nothing to minimise).
Tests: tests/test_par_minset.py.
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import par  # noqa: E402


def minimal(items, fails, jobs=None, always=(), log=None):
    items, always = list(items), list(always)
    jobs = max(1, jobs or par.grant())
    if not fails(always + items):
        raise ValueError("minset: the whole set does not fail -- nothing to minimise")
    kept, rest, tests, waves = [], items, 1, 0
    with par.claim(min(jobs, max(1, len(items)))), ThreadPoolExecutor(max_workers=jobs) as pool:
        while rest:
            win = rest[:jobs]                                  # prefixes r1..rk, k = 1 .. len(win)
            # the set tested for k: everything kept so far, and the undecided items after rk
            res = list(pool.map(lambda k: fails(always + kept + rest[k:]), range(1, len(win) + 1)))
            tests += len(win)
            waves += 1
            k = 0
            while k < len(win) and res[k]:
                k += 1                                         # r1..rk dropped: still failing without them
            if k < len(win):
                kept.append(rest[k])                           # without r(k+1) too it passes: r(k+1) stays
                rest = rest[k + 1:]
            else:
                rest = rest[k:]
            if log:
                log(f"minset: wave {waves}, {len(win)} test(s) -> {len(kept)} kept, {len(rest)} undecided")
    minimal.tests, minimal.waves = tests, waves
    return kept


def serial(items, fails, always=()):
    """The deletion filter itself, one test at a time -- what minimal() must equal."""
    keep, always = list(items), list(always)
    if not fails(always + keep):
        raise ValueError("minset: the whole set does not fail -- nothing to minimise")
    for it in list(keep):
        trial = [x for x in keep if x != it]
        if fails(always + trial):
            keep = trial
    return keep
