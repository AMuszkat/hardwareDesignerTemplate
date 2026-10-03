#!/usr/bin/env python3
"""
PreToolUse hook on SendMessage -- makes the relay switch mechanical.

    python3 <kicad_project>/tools/session/guard.py concept|board   (stdin: hook JSON)

A message to one of this project's relay addresses (the concept session, or
any `<Project>_Kicad_boardN`) is BLOCKED while kicad_project/relay.json says
the relay is off.  While it is on, the guard still enforces the protocol's
shape: a board session rings only the concept session, the concept session
messages only board sessions, and a message is a doorbell (<= 600 characters),
never a carrier -- the content belongs in QUESTIONS.md / the answers / the
CHANGELOG.

Anything else (a subagent this session spawned, a teammate) passes untouched:
SendMessage is also how a session talks to its own subagents.

Exit 2 = block, with the reason on stderr (Claude Code shows it to the model).
"""
import json
import re
import sys
from pathlib import Path

KP = Path(__file__).resolve().parents[2]
MAX_CHARS = 600


def load(name):
    try:
        with open(KP / name) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def block(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)


def main():
    side = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)
    ti = event.get("tool_input") or {}
    to = re.sub(r"\s*\[[^\]]*\]\s*$", "", str(ti.get("to", ""))).strip()
    if not to:
        sys.exit(0)
    proj = load("project.json") or {}
    concept = proj.get("concept_session")
    prefix = proj.get("board_session_prefix")
    if not concept or not prefix:
        # No usable project.json: the names are unknown, so judge by their SHAPE
        # and fail closed -- a broken file must not open the relay.
        if re.fullmatch(r"\w+_(Concept|Kicad_board\d+)", to):
            block(f"kicad_project/project.json is missing or damaged, so {to} cannot be "
                  "checked against the relay switch. Tell the user (./setup.sh init <Name>).")
        sys.exit(0)
    if to != concept and not to.startswith(prefix):
        sys.exit(0)                         # not a relay address: not our business

    relay = load("relay.json") or {}
    if not relay.get("enabled"):
        block(f"Relay is OFF (kicad_project/relay.json), so {to} may not be messaged. "
              "Write the question/answer in its file as usual and tell the user instead. "
              "Only the user turns the relay on (bin/relay on).")
    if side == "board" and to != concept:
        block(f"A board session rings only the concept session ({concept}). "
              "Board sessions never message each other; cross-board facts travel "
              "through the concept session's handoff.")
    if side == "concept" and to == concept:
        block("The concept session does not message itself. A second concept session "
              "is a scratch session: it neither answers the relay nor publishes.")
    msg = str(ti.get("message", ""))
    if len(msg) > MAX_CHARS:
        block(f"Relay messages are doorbells, not carriers ({len(msg)} > {MAX_CHARS} chars). "
              "Put the content in QUESTIONS.md / tools/handoff/answers/ / the CHANGELOG "
              "and send only the pointer line (RELAY_PROTOCOL.md, 'Messages').")
    sys.exit(0)


if __name__ == "__main__":
    main()
