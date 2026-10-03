---
name: render
description: >
  Render the board in 3D and publish the pictures as a gallery page, with the
  engine THE USER chooses (KiCad's ray tracer or Blender Cycles; there is no
  default). Triggers on "render the board", "3D render", "picture of the PCB",
  "photo of the board", "publish a render", "update the render", "Blender
  render", "KiCad render".
---

# Render a board and publish it

The tool is `tools/render/render.py` (`tools/render/README.md` is the whole
story). It reads the board and never writes it, so it needs no backup and no
closed KiCad. **Nothing renders unless the user asks.**

## 0. The engine is the user's choice — every time

There is no default engine. If the request does not name one, ask before
anything else (`AskUserQuestion` when it is available), and do not pick:

- **KiCad** — KiCad's own ray tracer; KiCad's 3D-viewer look; several minutes.
- **Blender** — Cycles on KiCad's export; a studio photograph; several times
  longer (faster with a GPU); needs Blender installed.

"Blender render", "photo-grade", "with Blender" name Blender; "KiCad render"
names KiCad. "Render the board" names neither: ask. A choice made for one
request does not carry over to the next one.

If Blender is chosen and `render.py` says it is not installed, stop and tell
the user: `./setup.sh install-blender` (about 1.2 GB, no root; add
`--amd-gpu` for an AMD GPU on Debian/Ubuntu). Do not install it yourself
unless they ask you to.

## 1. Render

```
tools/.venv/bin/python tools/render/render.py --engine kicad      # or --engine blender
```

KiCad: several minutes — run it in the background (or give the Bash call
the longest timeout). Blender: run it in the background and wait for it. The board
is this session's own; `--board boardN` only for a board this session may
write.

Read what it prints:

- **`WARNING: KiCad had this board open`**: the picture is of the file as last
  saved. If you have unsaved work, save (Konnect `save_project`) and run
  again. Otherwise carry on; the page states it.
- **`NOTE: … drawn as a generated block, height ASSUMED`**: those parts have
  no 3D model anywhere. The render is still complete. If the package drawing
  is in `handoff/datasheets/`, read the body height there, put it in
  `<board>/render.json` under `bodies` (`{"U5": {"height": 0.4}}`) and run
  again. Never guess a height, and do not stop to fetch models: that is
  library work (`footprint` skill).
- **`Cycles on: CPU (no usable GPU …)`** (Blender): it worked, slowly. Say so
  in the report; the GPU runtime is the user's choice (`--amd-gpu`, a driver).
- **It exits with an error**: fix what it names. It is a tool failure, yours
  to solve, not a question for concept.

## 2. Look

Read two files before publishing. Both are small.

- `<board>/renders/<engine>/contact.jpg`: every view on one sheet. Check that
  the board fills each frame, no view is empty or clipped, and every placed
  part has a body. A part showing bare pads is a defect in the tool's body
  resolution; fix it with a `bodies` entry and say so in your report.
- `<board>/renders/<engine>/index.html`: the page you are about to publish.

Do not rewrite the page or restyle it. It is generated to the Artifact page
contract; a change belongs in `tools/render/page.html`.

## 3. Publish

The tool ends by printing a `PUBLISH` block: the exact arguments for the
`Artifact` tool. Pass them as printed.

- **No `url` in the block** (first publish of this engine's render): publish,
  then record the link so the next render updates the same page:
  `tools/.venv/bin/python tools/render/render.py --engine <e> --set-url <the URL>`
- **A `url` in the block**: first `Artifact` with `action: "read"` and that
  `url`, then `action: "list"`, `scope: "files"` and the `url`, then publish
  with the printed arguments. If the update is refused because the page is no
  longer yours to edit, publish without `url` and record the new link.

If this session has no `Artifact` tool, report the folder, and send
`<board>/renders/<engine>/hero.png` with `SendUserFile` if that tool exists.

## 4. Report

Three lines at most: the link, the engine (and, for Blender, the device it
ran on), what the board file was (its save time), and any body that is not the
footprint's own model — name the generated blocks by reference.

## Options

| want | do |
|---|---|
| judge a new angle before spending minutes | `--quick` (seconds). Never publish a `--quick` run |
| one view only | `--views hero` |
| another view, or a different angle | `views` in `<board>/render.json`, then run again |
| the wrong body on a part | `bodies` in `<board>/render.json`, then run again |
| where each body comes from, no picture | `--audit` (needs no engine) |
| Blender on the CPU only, or the GPU only | `--device cpu` / `--device gpu` |

## Limits

- A render is a picture. It is not evidence that a land pattern, a body
  height or a clearance is right; the gates for those are `fp.py check` and
  `place.py drc`.
- "Finished" is the user's word. This skill renders whatever the board file
  holds and does not judge whether the board is done. A routing group still
  stops for the user's review, and a render does not replace it.
