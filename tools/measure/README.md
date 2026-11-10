# `tools/measure` — where the sessions' time went

**Concept's.** `sessions.py` reads a project's Claude Code transcripts and
writes `out/sessions/report.md` (and `report.json`, the same content as
data): per session, how its wall-clock divides between permission prompts,
waiting on a background job, waiting on the other session, waiting on the
user, foreground tools and the model; the same per stage of the work; tool
time by command; review turnaround; what the reviewer agents cost; question
chains. It is the measurement behind a speedup — run it before a change and
after it, on the same kind of work.

    .venv/bin/python tools/measure/sessions.py                         # this project, every session
    .venv/bin/python tools/measure/sessions.py --since 2026-11-06      # sessions and time from that day on (UTC)
    .venv/bin/python tools/measure/sessions.py --since 2026-11-09T16:00 --until 2026-11-10T12:00
    .venv/bin/python tools/measure/sessions.py --project ~/otherProject --out /tmp/x
    .venv/bin/python tools/measure/sessions.py --transcripts DIR       # a folder of *.jsonl, read directly

Standard library only (`python3` runs it as well); read-only on the
transcripts; about two seconds for three sessions of four days.
`tests/test_sessions.py` runs it on a synthetic transcript
(`tests/fixtures/sessions/`).

⚠ **The report holds numbers and the names of tools and commands, nothing
else.** Transcripts are private — prompts, messages, file contents. No
message text, no file path, no command argument reaches the report, so it
can be pasted into a pull request. What a name is:

- a tool's name (`Edit`, `SendMessage`, `mcp konnect`), an agent type
  (`pcb-reviewer`), a skill's name;
- for a Bash call, the script it RAN, by its own file name
  (`place/place.py`, `tools/lint_docs.py`, `build/*.py` with the script's
  name), and a subcommand only when that word is a string literal in that
  script's own source in the project; a script that is no tool of the
  repository is `other script`, unnamed; anything else is the program
  (`grep`, `git status`);
- from a line between sessions, its protocol tokens only: `board1`, `RV7`,
  `PASS`, `Q12`, `pcb`, a revision number, a must-fix count.

Every name passes one filter (`safe()`: short, plain characters) or is
printed as `(other)`. **A board's `build/` script names do appear** — if
those are private in your project, do not publish the report.

## Where the transcripts are

`~/.claude/projects/<slug>/<session id>.jsonl`, the slug being the session's
working directory with every non-alphanumeric character replaced by `-`
(`$CLAUDE_CONFIG_DIR` moves `~/.claude`). A board session runs in
`<project>/kicad_project`, so a project owns several folders: every folder
whose name starts with the project's slug is read, and a session whose
records carry a working directory outside the project is dropped (a sibling
project whose name shares the prefix). Subagent runs lie beside a session in
`<session id>/subagents/agent-*.jsonl` with a `.meta.json` (agent type,
description). `--transcripts DIR` skips the lookup.

A session's role is taken from its name (`bin/board` and `bin/concept` name
the sessions `board1`, `concept`) and its working directory.

## The rule that makes the numbers add up

**Wall-clock is classified by the UNION of the intervals, never by their
sum.** The session's span is cut at every record's timestamp; each piece
takes the highest class that covers it:

    prompt stall > background wait > peer wait > human wait > tool > model

so the classes sum to the span exactly and nothing is counted twice. The
case that made it necessary: the model sends an Edit and a Bash call in one
batch, the Edit waits six hours on a permission prompt, and the Bash call's
result is written when the batch ends. Summed, that is twelve hours, six of
them "tool time" of a command that ran for two seconds. Here it is one six
hour stall, and the report says how many calls returned beside a stalled
prompt and how much of their intervals the stall took.

| class | what it is |
|---|---|
| permission-prompt stall | a call the harness asked the user about: the result record carries a user's decision, or a PreToolUse hook answered `ask`. The WHOLE interval is the stall — the record does not say when the prompt was answered, and the command's own run time, usually seconds, is inside it |
| waiting on a background job | the session idle while a job it launched (a `run_in_background` command, a subagent) still ran, or idle until that job's notification |
| waiting on the other session | idle until a message from another session (named by the kind of line: `verdict`, `answer`, `question`, `request`, `handoff-rev`, `other`) or an idle notice |
| waiting on the user | idle until a typed prompt; `AskUserQuestion` and `ExitPlanMode` (a question is not a permission prompt) |
| foreground tools | a tool call that returned, minus every second a higher class covers |
| model turns | every piece nothing covers: the model generating, a mid-turn compaction |
| no record | an uncovered piece longer than `--gap-min` (15) minutes: an API outage, a suspended machine, a killed session — not the model |

Three rules inside it:

- **A wait is named by the record that ended it — except while the
  session's own background job is running.** The concept session waiting for
  a reviewer agent is woken by board messages and idle notices; that idle
  time is still a wait on the reviewer, so an idle piece between a job's
  launch and its notification is background wait whoever ended it.
- **An idle period that ends in a compaction or a local command's output is
  folded into the wait that follows**, with that wait's cause: a session
  compacted while waiting for a verdict was waiting for the verdict.
- **A wait exists only where the session was idle** (the last record is the
  assistant's, no call pending). After a prompt the model is working until
  it answers.

A background job's run is its launch to its notification — an upper bound,
since a notification is delivered at the next turn boundary.

## Stage and family

**Stage** is taken from the path a command runs or a file tool writes:
`tools/sch/` schematic, `fp/` footprints, `board_setup/` board setup,
`place/` placement, `route/` and `freerouting/` routing, `copper/` copper,
`render/`, `fab/`, and on the concept side `handoff/`, `review/`, `parts/`,
`diagrams/`; `template/` is giveback; a board's `build/` folder and ad-hoc
`pcbnew` scripts are "own scripts"; a tool folder not in the table
(`DIR_STAGE`) is its own stage. An inline script takes the stage of the
first `tools/<dir>/` it mentions. Model time, waits and calls with no path
take the stage of the last call that had one, so a stage's row is the time
spent IN that stage, not the time its tools ran.

**Family** is what a call ran (see the names above). Each second of tool
time has ONE owner — of two overlapping calls the one that takes time beats
one that returns at once (`Read`, `Edit`, `SendMessage`), then the earlier
start — so the family rows sum to the tool class. p50/p90/max are single
calls. A Bash command is cut into its simple commands; the first that runs a
script, an inline script (an interpreter fed a heredoc, `-c` or stdin),
`kicad-cli` or a poll loop (`until ...; do sleep`) names the call. A path
that is only an argument names nothing (`grep x tools/route/route.py` is a
grep), and a heredoc's body is not read for commands, but the commands after
its terminator are (`cat > x.py <<EOF ... EOF` and then a run of `x.py` is
the run).

## Reviews, reviewer runs, questions

Read from the first line of each message between sessions, in the forms
`kicad_project/RELAY_PROTOCOL.md` gives them:

- **Reviews**: `boardN RVn ready — schematic|pcb` paired with the first
  `review RVn: PASS|CHANGES (k must-fix)` after it. In a board session's row
  the minutes are its wait; in the concept session's, its turnaround.
  "Active" is that session's model plus tool time while the review was
  pending — what a board got done while it waited.
- **Subagent runs**: per agent type, and each reviewer run on its own:
  minutes worked (model, tools, its own prompt stalls), output tokens, tool
  calls, inline scripts (Bash calls with a heredoc or an interpreter's `-c`:
  what the reviewer had to compute because no tool handed it over).
- **Questions**: a question's first ring (`Qn BLOCKING|not blocking`,
  `Qn waits on the user`) paired with the line that answers it
  (`answers Qn`, `Qn answered`). A **chain** is three or more questions of
  one board, each within two hours of the one before: one topic the handoff
  did not specify, asked a piece at a time.

## Reading it, and its limits

- **Before and after**: the same command with `--since`/`--until` around
  the change (both take `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM`, UTC). A window
  cuts intervals; the classes still sum to the window.
- A prompt stall is the user's absence, not the tool's cost: the fix is a
  permission rule, and a board session that sits on one all night shows it
  as its largest class.
- Two sessions' rows are not added: both wait at once, and a subagent's
  minutes are inside its parent's background wait.
- A session that is still running ends at its last record; a call with no
  result yet is listed under Notes and not counted.
- The record formats are Claude Code's own and not a published interface:
  after an upgrade, run `python3 tests/test_sessions.py` and one report on a
  real session, and compare the first table with what you remember of the
  day. A record type the parser does not know is skipped, never guessed.
