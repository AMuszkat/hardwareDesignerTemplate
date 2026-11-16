#!/usr/bin/env python3
"""kicad_project/tools/par.py (grant, claim, cmap) and tools/minset.py, on cases worked by hand.

    python3 tests/test_par_minset.py          (plain python3; no KiCad, no scipy)

  1. grant: alone it is the whole machine; beside claims it is the free cores and never less than
     an even share; HW_JOBS overrides; a dead claimant's file counts for nothing and is removed.
  2. cmap: results in the order given whatever order they end in; N at a time (measured); a
     timeout is exit 124; `stop` cancels the commands not yet started.
  3. minset.minimal against minset.serial (the deletion filter) on 300 random monotone oracles and
     on the hand cases: the SAME set, with 1 worker and with 8; fewer waves than tests; a set
     that does not fail is refused; `always` is in every test and never dropped.
Exit 0 and `PASS: ...` / 1 and `FAIL: ...`.
"""
import json
import os
import random
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "kicad_project" / "tools"))
os.environ["HW_CORES_DIR"] = tempfile.mkdtemp(prefix="hw-cores-test-")
os.environ.pop("HW_JOBS", None)
import minset  # noqa: E402
import par  # noqa: E402

bad = []


def eq(what, got, want):
    if got != want:
        bad.append(f"{what}: {got!r}, expected {want!r}")


# ---- 1. grant ----------------------------------------------------------------------------------
D = Path(os.environ["HW_CORES_DIR"])
eq("alone: every core", par.grant(total=24), 24)
eq("alone, want 5", par.grant(5, total=24), 5)


def other(name, cores, pid=None):                       # another LIVE process's claim: our parent's pid
    pid = pid or os.getppid()
    (D / name).write_text(json.dumps({"pid": pid, "cores": cores, "start": par._start(pid)}))


other("a.json", 24)
eq("beside one that took all 24: an even share", par.grant(total=24), 12)
other("a.json", 12)
eq("beside one holding 12: the free 12", par.grant(total=24), 12)
other("b.json", 4)
eq("beside 12 and 4: the free 8 (the share is 8 too)", par.grant(total=24), 8)
other("a.json", 2)
eq("beside 2 and 4: the free 18", par.grant(total=24), 18)
os.environ["HW_JOBS"] = "3"
eq("HW_JOBS=3 overrides", par.grant(total=24), 3)
os.environ.pop("HW_JOBS")
(D / "a.json").unlink()
(D / "b.json").unlink()
(D / "dead.json").write_text(json.dumps({"pid": 2 ** 22 + 12345, "cores": 24, "start": "1"}))
eq("a dead claimant: the whole machine", par.grant(total=24), 24)
eq("a dead claimant's file is removed", (D / "dead.json").exists(), False)
with par.claim(6):
    eq("our own claim does not count against us", par.grant(total=24), 24)
    eq("our claim is on file", len(list(D.glob("*.json"))), 1)
eq("the claim is gone after the block", len(list(D.glob("*.json"))), 0)

# ---- 2. cmap -----------------------------------------------------------------------------------
PY = sys.executable
cmds = [[PY, "-c", f"import time; time.sleep({d}); print({i})"] for i, d in enumerate((0.5, 0.1, 0.3, 0.0))]
t0 = time.time()
res = par.cmap(cmds, jobs=4)
wall = time.time() - t0
eq("cmap: job order, not completion order", [r[2].strip() for r in res], ["0", "1", "2", "3"])
eq("cmap: exit codes", [r[0] for r in res], [0, 0, 0, 0])
eq("cmap: four at once (under the serial 0.9 s + start-up)", wall < 0.85 + 4 * 0.02 or wall < 0.9, True)
r = par.cmap([[PY, "-c", "import time; time.sleep(5)"], [PY, "-c", "import sys; sys.exit(7)"]], jobs=2, timeout=0.5)
eq("cmap: a timeout is 124, an exit code is kept", [x[0] for x in r], [124, 7])
r = par.cmap([[PY, "-c", "import os; print(os.environ['HW_JOBS'])"]], jobs=1)
eq("cmap: each command is one core", r[0][2].strip(), "1")
seen = []
r = par.cmap([[PY, "-c", f"print({i})"] for i in range(6)], jobs=1, stop=lambda i, x: seen.append(i) or i == 2)
eq("cmap stop: the rest are not started", [x is None for x in r], [False, False, False, True, True, True])

# ---- 3. minset ---------------------------------------------------------------------------------
calls = []
lock = threading.Lock()


def oracle(cores):
    """fails(S): S holds every item of at least one core -- monotone, with those minimal failing sets."""
    def fails(S):
        with lock:
            calls.append(1)
        S = set(S)
        return any(c <= S for c in cores)
    return fails


items = list("abcdefghij")
f = oracle([{"c", "h"}])
eq("one core {c, h}", minset.minimal(items, f, jobs=8), ["c", "h"])
eq("one core: 3 waves for 10 items", minset.minimal.waves, 3)
eq("two cores {a,b} {i,j}: the serial filter's choice", minset.minimal(items, oracle([{"a", "b"}, {"i", "j"}]), jobs=8),
   minset.serial(items, oracle([{"a", "b"}, {"i", "j"}])))
eq("always: the fixed part completes the core and is not returned",
   minset.minimal(items, oracle([{"c", "X"}]), jobs=4, always=["X"]), ["c"])
eq("the fixed part alone fails: nothing else is needed", minset.minimal(items, oracle([{"X"}]), jobs=4, always=["X"]), [])
try:
    minset.minimal(items, oracle([{"zz"}]), jobs=4)
    bad.append("a set that does not fail: no error raised")
except ValueError:
    pass
rng = random.Random(20261116)
waves = tests = 0
for t in range(300):
    n = rng.randint(1, 30)
    its = list(range(n))
    cores = [set(rng.sample(its, rng.randint(1, min(n, 6)))) for _ in range(rng.randint(1, 4))]
    want = minset.serial(its, oracle(cores))
    for j in (1, 3, 8, 64):
        got = minset.minimal(its, oracle(cores), jobs=j)
        if got != want:
            bad.append(f"random {t}, {j} worker(s): {got}, the serial filter gives {want}")
        if j == 64:
            waves += minset.minimal.waves
            tests += n
            if minset.minimal.waves > len(want) + 1:
                bad.append(f"random {t}: {minset.minimal.waves} waves for {len(want)} kept")
    if not any(c <= set(want) for c in cores) or any(any(c <= set(want) - {x} for c in cores) for x in want):
        bad.append(f"random {t}: {want} is not a minimal failing set")

if bad:
    print("\n".join(bad[:30]))
    print(f"FAIL: {len(bad)} wrong; first: {bad[0]}")
    sys.exit(1)
print(f"PASS: grant shares the machine (free cores, never under an even share, dead claims dropped); cmap returns in "
      f"job order, N at a time, timeout 124, stop cancels; minset equals the serial deletion filter on 300 random "
      f"monotone oracles at 1, 3, 8 and 64 workers -- {waves} waves where the serial filter runs {tests} tests")
