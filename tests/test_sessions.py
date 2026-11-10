#!/usr/bin/env python3
"""
tools/measure/sessions.py on a synthetic transcript: the classes add up to the
span, and nothing is counted twice.

    python3 tests/test_sessions.py

The fixture (tests/fixtures/sessions/, 30 records and a 6-record subagent
run, every word invented) is one board session, 09:00:00 to 10:37:30 UTC:

    09:00:20  place.py check                               40 s   tool
    09:01:10  ONE BATCH: an Edit that sits on a permission prompt until
              09:11:10, and a `grep` whose result is written at 09:11:09
                                                          600 s   prompt stall
    09:11:30  route.py sent to the background, notified 09:21:40
                                                          600 s   background wait
    09:22:00  `board1 RV3 ready`; an idle compaction at 09:30; the verdict
              `review RV3: PASS` arrives 09:40:05        1080 s   peer wait
    09:40:25  a question to the user                      300 s   human wait
    09:45:35  an inline script                             15 s   tool
    09:46:00  idle until the next prompt, 10:16:00       1800 s   human wait
    10:16:20  a script written with a heredoc and run in the same call
                                                           30 s   tool
    10:17:30  a prompt, answered 20 minutes later        1200 s   no record

What must hold, and what each assertion is there to catch:

  * the seven classes sum to the span TO THE SECOND -- also inside a
    --since/--until window that cuts intervals in two;
  * the `grep` beside the stalled Edit owns no time: summed, the batch would
    be 600 s of stall plus 599 s of "tool time" of a command that ran for a
    moment, and the tool class would be 687 s instead of 88 s;
  * the per-family tool times sum to the tool class (one owner per second);
  * the background job, its wait, the review request/verdict pair and the
    idle compaction folded into the peer wait are found;
  * NOTHING of the transcript's text is in the report: every message, file
    path and command argument of the fixture carries the word ZEBRA, and the
    report must not.

Standard library only; works on a temp folder; the last line is PASS or FAIL.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "measure", "sessions.py")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "sessions")

EXPECT = {"prompt": 600, "background": 600, "peer": 1080, "human": 2130, "tool": 88, "model": 152, "gap": 1200}
SPAN = 5850

fails = []
checks = [0]


def check(ok, what):
    checks[0] += 1
    if not ok:
        fails.append(what)


def run(out, *args):
    r = subprocess.run([sys.executable, TOOL, "--transcripts", FIXTURE, "--out", out, *args],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(r.stdout + r.stderr)
        print(f"FAIL: sessions.py exited {r.returncode}")
        sys.exit(1)
    with open(os.path.join(out, "report.json")) as f:
        R = json.load(f)
    with open(os.path.join(out, "report.md")) as f:
        md = f.read()
    return R, md, r.stdout


def main():
    if not os.path.isfile(TOOL) or not os.path.isdir(FIXTURE):
        print("FAIL: tools/measure/sessions.py or tests/fixtures/sessions is missing")
        return 1
    with tempfile.TemporaryDirectory(prefix="sessions-test-") as tmp:
        R, md, stdout = run(os.path.join(tmp, "all"))
        check(len(R["sessions"]) == 1, f"1 session expected, {len(R['sessions'])} found")
        if not R["sessions"]:
            print("FAIL: " + "; ".join(fails))
            return 1
        s = R["sessions"][0]
        sec = s["seconds"]

        # the classes sum to the span, to the second, and each is what the fixture holds
        check(round(s["span_s"]) == SPAN, f"span {s['span_s']} s, expected {SPAN}")
        check(abs(sum(sec.values()) - s["span_s"]) < 0.5,
              f"classes sum to {sum(sec.values())} s, the span is {s['span_s']} s")
        for c, want in EXPECT.items():
            check(round(sec[c]) == want, f"{c} {sec[c]} s, expected {want}")
        check(s["role"] == "board1", f"role {s['role']!r}, expected board1")

        # the Bash call beside the stalled Edit is not counted twice
        fam = {r["family"]: r for r in s["families"]}
        grep = {(r["family"], r["detail"]): r for r in s["details"]}.get(("other Bash", "grep"), {})
        check(grep.get("calls") == 1 and grep.get("seconds") == 0,
              f"the grep beside the stalled Edit owns {grep.get('seconds')} s, expected 0")
        check(s["beside_stall"]["calls"] == 1 and round(s["beside_stall"]["seconds"]) == 599,
              f"beside a stalled prompt: {s['beside_stall']}, expected 1 call, 599 s")
        check(abs(sum(r["seconds"] for r in s["families"]) - sec["tool"]) < 0.5,
              "the families' tool seconds do not sum to the tool class")
        st = s["stalls"]
        check(st["n"] == 1 and st["over_1min"] == 1 and st["by_tool"][0]["tool"] == "Edit"
              and round(st["by_tool"][0]["seconds"]) == 600, f"stalls {st}, expected one Edit of 600 s")
        check(abs(sum(r["model"] + r["tool"] + r["background"] + r["prompt"] for r in s["stages"])
                  - (sec["model"] + sec["tool"] + sec["background"] + sec["prompt"])) < 0.5,
              "the stages do not sum to model + tool + background wait + prompt stall")

        # commands are named by what they run
        check(round(fam.get("place/place.py", {}).get("seconds", -1)) == 40, "place/place.py: 40 s expected")
        check(round(fam.get("inline script", {}).get("seconds", -1)) == 15, "inline script: 15 s expected")
        check(round(fam.get("other script", {}).get("seconds", -1)) == 30,
              "a script written with a heredoc and run in the same call is not named by the run")

        # the background job and its wait
        jobs = {r["family"]: r for r in s["background_jobs"]}
        j = jobs.get("route/route.py", {})
        check(j.get("launched") == 1 and j.get("ended") == 1 and round(j.get("run_s", 0)) == 610
              and round(j.get("wait_s", 0)) == 600, f"background job {j}, expected route/route.py 610 s run, 600 s waited")

        # the review pair; the idle compaction is part of the wait for the verdict
        rv = s["relay"]["reviews"]
        check(len(rv) == 1 and rv[0]["board"] == "board1" and rv[0]["id"] == "RV3" and rv[0]["verdict"] == "PASS"
              and rv[0]["what"] == "pcb" and rv[0]["must_fix"] == 0 and abs(rv[0]["minutes"] * 60 - 1085) < 0.5,
              f"reviews {rv}, expected board1 RV3 pcb PASS after 1085 s")
        waits = {(w["cause"], w["ended_by"]): w for w in s["waits"]}
        w = waits.get(("peer", "verdict"), {})
        check(w.get("n") == 1 and round(w.get("seconds", 0)) == 1080,
              f"the wait for the verdict {w}, expected one of 1080 s (the idle compaction folded in)")
        check(s["compactions"]["n"] == 1 and s["compactions"]["idle"] == 1, f"compactions {s['compactions']}")

        # the subagent run
        sub = s["subagents"]
        check(len(sub) == 1 and sub[0]["type"] == "pcb-reviewer" and sub[0]["board"] == "board1"
              and sub[0]["review"] == "RV3" and abs(sub[0]["minutes"] - 2.0) < 0.01 and sub[0]["tool_calls"] == 2
              and sub[0]["inline_scripts"] == 1 and sub[0]["output_tokens"] == 1200,
              f"subagent runs {sub}, expected one pcb-reviewer run of 2.0 min, 2 calls, 1 inline script")
        check(s["turns"] == 12 and s["tokens"]["output"] == 1200,
              f"{s['turns']} requests, {s['tokens']['output']} output tokens; expected 12 and 1200 "
              "(a message written as two records is one request)")

        # nothing of the transcript's text in the report
        for name, text in (("report.md", md), ("report.json", json.dumps(R)), ("stdout", stdout)):
            check("zebra" not in text.lower(), f"{name} holds text of the transcript")
            check("/work/demo" not in text, f"{name} holds a path of the transcript")
        check("## 1. Wall-clock per session" in md and "## 6. Reviews" in md, "report.md lacks its sections")

        # a window that cuts the peer wait, the question and a human wait in two
        R2, _, _ = run(os.path.join(tmp, "window"), "--since", "2026-01-05T09:30", "--until", "2026-01-05T10:00")
        s2 = R2["sessions"][0]
        check(round(s2["span_s"]) == 1800 and abs(sum(s2["seconds"].values()) - 1800) < 0.5,
              f"09:30 to 10:00: span {s2['span_s']} s, classes sum to {sum(s2['seconds'].values())} s; expected 1800")
        check(round(s2["seconds"]["peer"]) == 605 and round(s2["seconds"]["human"]) == 1140
              and round(s2["seconds"]["tool"]) == 15 and round(s2["seconds"]["model"]) == 40,
              f"09:30 to 10:00: {s2['seconds']}, expected peer 605, human 1140, tool 15, model 40")

        # a window with no session in it is an empty report, not an error
        R3, md3, _ = run(os.path.join(tmp, "none"), "--since", "2027-01-01")
        check(R3["sessions"] == [] and "No session" in md3, "a window after the last record is not an empty report")

    # the naming rules, on the cases that went wrong before
    sys.path.insert(0, os.path.dirname(TOOL))
    import sessions as M
    subs = M.Subcommands(None)
    for cmd, want in (
            ("grep -n x tools/route/route.py", "other Bash"),                 # a path that is an argument
            ("cd board1 && ../tools/kpy ../tools/place/place.py check", "place/place.py"),
            ("timeout 600 python3 tools/handoff/publish.py --board board1", "handoff/publish.py"),
            ("python3 - <<'EOF'\nimport os\nos.system('tools/route/route.py')\nEOF", "inline script"),
            ("cat > x.py <<EOF\nprint(1)\nEOF\npython3 x.py", "other script"),  # named by the run
            ("until [ -f done ]; do sleep 5; done", "poll loop"),
            ("git -C . status --short", "git"),
            ("kicad-cli pcb drc board1/x.kicad_pcb", "kicad-cli")):
        got = M.name_bash(cmd, "/work/demo/kicad_project", subs)[0]
        check(got == want, f"name_bash: {want!r} expected, {got!r} found")
    ev = M.relay_event("board2 handoff rev 12 — review RV4: CHANGES (3 must-fix); answers Q5, Q6")
    check(ev.get("board") == "board2" and ev.get("verdict") == "CHANGES" and ev.get("must_fix") == 3
          and ev.get("answers") == ["Q5", "Q6"] and ev.get("rev") == 12, f"relay_event: {ev}")

    if fails:
        for f in fails:
            print("  " + f)
        print(f"FAIL: {len(fails)} of {checks[0]} checks: {fails[0]}")
        return 1
    print(f"PASS: {checks[0]} checks; span {SPAN} s = prompt 600 + background 600 + peer 1080 + human 2130 "
          f"+ tool 88 + model 152 + no record 1200; the Bash call beside the stalled Edit owns 0 s of its 599; "
          f"review RV3 paired after 18.1 min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
