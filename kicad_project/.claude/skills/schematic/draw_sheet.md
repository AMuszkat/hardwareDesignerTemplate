# Prompt for a sheet-drawing agent

Fill `<BOARD_DIR>` (absolute path of the board folder), `<board>` (its
short name, e.g. `board1`), `<SHEET>` (the sheet file, e.g. `emmc.kicad_sch`)
and `<STEM>` (its stem, e.g. `emmc`), load Konnect's `sch_components`,
`sch_wiring` and `sch_batch` toolsets yourself FIRST (an agent's tools are
fixed at spawn; one spawned before the load cannot reach them), then launch
one agent per sub-sheet in the
same message, and draw the root sheet yourself after they report. Proven on
board1 (8 sheets, 787 calls, every sheet `0 missing, 0 extra` on the first
pass, 5–10 minutes a sheet in parallel).

---

You are drawing ONE sheet of a KiCad schematic with the Konnect MCP tools,
exactly as a plan says. Sheet file: `<BOARD_DIR>/<SHEET>`. Plan:
`<BOARD_DIR>/sch_plan/<STEM>.ops.json`, a JSON object whose `"calls"` list
holds `{"tool": NAME, "args": {...}}` in order; NAME maps to the MCP tool
`mcp__konnect__NAME`.

Rules (follow exactly):

1. First load the tool schemas with ToolSearch:
   `select:mcp__konnect__batch_delete,mcp__konnect__batch_delete_no_connect,mcp__konnect__set_schematic_page,mcp__konnect__batch_place_components,mcp__konnect__batch_edit_schematic_components,mcp__konnect__batch_add_wire,mcp__konnect__batch_add_junction,mcp__konnect__batch_add_no_connect,mcp__konnect__add_schematic_net_label,mcp__konnect__update_symbols_from_library,mcp__konnect__reset_schematic_field_positions,mcp__konnect__add_component_annotation`.
2. Print the calls with Bash/python in chunks you can read, e.g.
   `python3 -c "import json;d=json.load(open('<BOARD_DIR>/sch_plan/<STEM>.ops.json'));[print(i,c['tool'],json.dumps(c['args'],separators=(',',':'))) for i,c in enumerate(d['calls'][A:B],A)]"`.
   Make every call in order, with the arguments EXACTLY as written: same
   schematic path, same numbers, same strings, same rotations, label_type and
   shape. Never round, reorder within a call, invent, or skip. A long list
   argument (wires, junctions, no-connect positions, components, uuids) may be
   split into consecutive calls of the same tool (about 100 elements each),
   every element passed exactly once.
3. `add_schematic_net_label` calls may be sent up to 12 per message as
   parallel tool calls; every other call one per message, in order.
4. A call blocked by a transient "classifier gave no verdict" error is retried
   once with the same arguments; any other error is reported verbatim.
5. Touch ONLY this sheet file, through Konnect. Never call save_project, never
   open or close KiCad, never edit any file with a text editor or script,
   never touch another sheet or the root sheet.
6. When all calls are done, run
   `cd <BOARD_DIR>/.. && tools/.venv/bin/python tools/sch/sch.py drawn --board <board> <SHEET>`.
   It must print `0 missing, 0 extra; 0 field(s) not as planned`. If not:
   make the missing items with the same Konnect call types (exact planned
   values), add a missing field with `add_component_annotation`, delete
   extras (`batch_delete` by uuid, `batch_delete_no_connect` by position), and
   re-run until it does.
7. Reply with: the final `drawn` line, the number of calls made, and any call
   that returned an error (verbatim). Nothing else.
