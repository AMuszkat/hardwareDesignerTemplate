#!/usr/bin/env python3
"""bgwait.py watch -- the worker's watchdog: DONE (0), STALLED (3), OVER ITS CAP (4), still working (124).

    python3 tools/test_bgwait_watch.py        # 5 tests, about 15 s; standard library only
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

BG = Path(__file__).resolve().parent / "bgwait.py"


def watch(*args, timeout=60):
    env = dict(os.environ, HW_BGWAIT_DIR=tempfile.mkdtemp(prefix="bgw_jobs_"))
    r = subprocess.run([sys.executable, str(BG), "watch", *map(str, args), "--poll", "0.2"],
                       capture_output=True, text=True, timeout=timeout, env=env)
    return r.returncode, r.stdout + r.stderr


def writer(d, every, n):
    def go():
        for i in range(n):
            (Path(d) / f"w{i}.txt").write_text("x")
            time.sleep(every)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


class Watch(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="bgw_watch_"))

    def test_done(self):
        (self.d / "STATE.md").write_text("s")
        (self.d / "DONE").write_text("")
        rc, out = watch(self.d, "--idle", 30, "--timeout", 10)
        self.assertEqual(rc, 0, out)
        self.assertIn("DONE", out)

    def test_stalled(self):
        (self.d / "STATE.md").write_text("s")
        rc, out = watch(self.d, "--idle", 2, "--timeout", 20)
        self.assertEqual(rc, 3, out)
        self.assertIn("STALLED", out)

    def test_writing_is_not_stalled_but_capped(self):
        writer(self.d, 0.5, 40)
        rc, out = watch(self.d, "--idle", 2, "--max", 4, "--timeout", 20)
        self.assertEqual(rc, 4, out)
        self.assertIn("OVER ITS CAP", out)

    def test_timeout_while_writing(self):
        writer(self.d, 0.5, 20)
        rc, out = watch(self.d, "--idle", 3, "--max", 60, "--timeout", 3)
        self.assertEqual(rc, 124, out)

    def test_cache_writes_do_not_count(self):
        (self.d / "__pycache__").mkdir()
        os.utime(self.d, (time.time() - 100, time.time() - 100))
        (self.d / "a.txt").write_text("a")
        os.utime(self.d / "a.txt", (time.time() - 100, time.time() - 100))

        def touch():
            for _ in range(20):
                (self.d / "__pycache__" / "x.pyc").write_text("x")
                time.sleep(0.3)
        threading.Thread(target=touch, daemon=True).start()
        rc, out = watch(self.d, "--idle", 2, "--timeout", 20)
        self.assertEqual(rc, 3, out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
