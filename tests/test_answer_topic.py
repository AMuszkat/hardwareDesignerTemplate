#!/usr/bin/env python3
"""A third answer on one topic does not publish without an `Escalated:` line
(ROADMAP Q1b; the contract is kicad_project/RELAY_PROTOCOL.md section 5).

    python3 tests/test_answer_topic.py [--keep]

On a scratch copy of the repo, a board made by tools/handoff/new_board.py and
its answers file, driven the way the concept session meets the check:

  1  an answers file in the OLD format (no `Topic:` line), already published,
     keeps publishing -- one lint note, no failure: the merge breaks no live
     project;
  2  a NEW answer without a topic does not publish, and the line says what to
     add;
  3  two answers on one topic publish (the topic compares case- and
     space-insensitively, and may be bold); the second is a lint note;
  4  a third on that topic does not publish, the line names the protocol
     section and the `Escalated:` line, and the handoff is untouched;
  5  with `Escalated:` it publishes, and `publish.py --check` is fresh;
  6  the fourth needs its own `Escalated:` line; an answer on another topic
     does not;
  7  a `Topic:` inside a fenced block is not read.

Needs no KiCad. Exit 0 pass / 1 fail; the last stdout line is PASS / FAIL.
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import agentic_test as A                             # noqa: E402  (the copy's ignore list, run, tail)


def answer(q, title, *lines):
    return f"## Q{q} — {title}\n\n" + "\n".join(lines) + ("\n\n" if lines else "") + "Body of the answer.\n\n"


def main():
    keep = "--keep" in sys.argv
    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="answer-topic-"))
    fails, pubs = [], []

    def expect(ok, what, out=""):
        print(("  ok    " if ok else "  FAIL  ") + what)
        if not ok:
            fails.append(what)
            if out:
                print("        " + "\n        ".join(out.strip().splitlines()[-6:]))

    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=A.IGNORE, symlinks=True)
        n = A.free_board(copy)
        bid = f"board{n}"
        rc, out = A.run([sys.executable, "tools/handoff/new_board.py", str(n), "topics", "Answer topic test board"],
                        copy)
        if rc:
            print("FAIL: new_board: " + A.tail(out))
            return 1
        ans = copy / "tools" / "handoff" / "answers" / f"{bid}.md"
        head = ans.read_text().replace("Nothing answered yet.\n", "")
        handoff = copy / "kicad_project" / f"{bid}_topics" / "handoff"
        rel = f"tools/handoff/answers/{bid}.md"

        def publish(*extra):
            t = time.time()
            r = A.run([sys.executable, "tools/handoff/publish.py", *(extra or ("--board", bid))], copy)
            pubs.append(time.time() - t)
            return r

        def rev():
            return json.loads((handoff / "MANIFEST.json").read_text())["rev"]

        def errline(out):
            return [ln for ln in out.splitlines() if ln.startswith("publish:")]

        # 1 -- the old format, already published: three answers, no topic
        old = answer(1, "J1: no drawing") + answer(2, "J3 pads only") + answer(3, "TP1 size")
        ans.write_text(head + old)
        page = handoff / "07_answers.md"
        page.write_text(page.read_text() + "\n" + old)          # as a publisher before this check left it
        rc, out = publish()
        expect(rc == 0 and "published rev" in out and "3 published answer(s) name no topic" in out,
               "1 an old-format answers file keeps publishing, with one note", out)
        rc, out = publish("--check", "--board", bid)
        expect(rc == 0 and "fresh" in out and "name no topic" in out, "1 --check: fresh, the note stays", out)

        # 2 -- a new answer must name its topic
        r0 = rev()
        ans.write_text(head + old + answer(4, "U1 escape: which layer"))
        rc, out = publish()
        line = errline(out)
        expect(rc == 2 and len(line) == 1 and rel in line[0] and "names no topic" in line[0]
               and "'Topic: " in line[0], "2 a new answer without a topic does not publish", out)
        expect(rev() == r0, "2 the published handoff is untouched")

        # 3 -- two answers on one topic
        a4 = answer(4, "U1 escape: which layer", "Topic: U1 escape")
        a5 = answer(5, "U1 escape: the inner row", "**Topic:** u1  ESCAPE")
        ans.write_text(head + old + a4)
        rc, out = publish()
        expect(rc == 0 and "answers (Q4" not in out, "3 the first answer on a topic publishes", out)
        ans.write_text(head + old + a4 + a5)
        rc, out = publish()
        expect(rc == 0 and "topic 'u1 escape' has 2 answers (Q4, Q5)" in out,
               "3 the second publishes, and the note says the next one is the user's", out)

        # 4 -- the third does not
        r0 = rev()
        a6 = answer(6, "U1 escape: again", "Topic: U1 escape")
        ans.write_text(head + old + a4 + a5 + a6)
        rc, out = publish()
        line = errline(out)
        expect(rc == 2 and len(line) == 1 and "topic 'u1 escape' has 3 answers (Q4, Q5, Q6)" in line[0]
               and "RELAY_PROTOCOL.md section 5" in line[0] and "'Escalated: " in line[0],
               "4 a third answer on the topic does not publish; the line names section 5 and the Escalated line", out)
        expect(rev() == r0 and "Q6" not in (handoff / "07_answers.md").read_text(),
               "4 the published handoff is untouched")

        # 5 -- escalated, it does
        a6e = answer(6, "U1 escape: again", "Topic: U1 escape",
                     "Escalated: 2026-11-10, the user chose the six-layer escape (D12)")
        ans.write_text(head + old + a4 + a5 + a6e)
        rc, out = publish()
        expect(rc == 0 and "published rev" in out and "## Q6" in (handoff / "07_answers.md").read_text()
               and "has 3 answers (Q4, Q5, Q6; 1 escalated)" in out, "5 with an Escalated line it publishes", out)
        rc, out = publish("--check", "--all")
        expect(rc == 0, "5 --check --all: fresh", out)

        # 6 -- the fourth needs its own; another topic does not
        a7 = answer(7, "U1 escape: once more", "Topic: U1 escape")
        a8 = answer(8, "J3 land", "Topic: J3 land")
        ans.write_text(head + old + a4 + a5 + a6e + a7 + a8)
        rc, out = publish()
        expect(rc == 2 and "has 4 answers" in out and "1 'Escalated:' line(s)" in out,
               "6 the fourth answer needs its own Escalated line", out)
        ans.write_text(head + old + a4 + a5 + a6e + a8)
        rc, out = publish()
        expect(rc == 0, "6 an answer on another topic publishes", out)

        # 7 -- a fenced block is text, not fields
        a9 = answer(9, "the format, quoted", "```", "Topic: U1 escape", "```", "Topic: answers format")
        ans.write_text(head + old + a4 + a5 + a6e + a8 + a9)
        rc, out = publish()
        expect(rc == 0, "7 a Topic line inside a fenced block is not counted", out)
    finally:
        if keep:
            print(f"  kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails)[:300])
        return 1
    print(f"PASS: answer topics: 7 cases, {len(pubs)} publisher runs at {sum(pubs) / len(pubs):.2f} s each, "
          f"{time.time() - t0:.0f} s in all")
    return 0


if __name__ == "__main__":
    sys.exit(main())
