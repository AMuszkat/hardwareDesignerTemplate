# render — the board, photographed (only when asked, with the engine the user picks)

One command takes the board to a set of rendered pictures and a gallery page
ready to publish. The `render` skill is the workflow; this is the tool.

    tools/.venv/bin/python tools/render/render.py --engine kicad              # the full set
    tools/.venv/bin/python tools/render/render.py --engine blender            # the same, photo-grade
    tools/.venv/bin/python tools/render/render.py --engine kicad --quick      # OpenGL, seconds: judge framing
    tools/.venv/bin/python tools/render/render.py --audit                     # where each body comes from; no picture
    tools/.venv/bin/python tools/render/render.py --engine kicad --views hero,top
    tools/.venv/bin/python tools/render/render.py --engine kicad --set-url <artifact URL>

It works on this session's board (`bin/board boardN` sets `HW_BOARD`);
`--board boardN` names another one, and is refused if that board belongs to
another session. It runs in the board venv (Pillow); the one part that needs
`pcbnew` — the scratch board — runs through `tools/kpy` on its own
(`scratch.py`), so KiCad's Python needs nothing extra.

## No render by default, no engine by default

Nothing renders unless the user asks for it, and **`--engine` has no default**:
without it `render.py` stops and lists the two. The user chooses every time —
"render the board" is answered with the question *KiCad or Blender?*, never
with a guess.

| | `--engine kicad` | `--engine blender` |
|---|---|---|
| renderer | KiCad's own ray tracer (`kicad-cli pcb render`) | Blender Cycles, on KiCad's GLB export of the same scratch board |
| look | KiCad's 3D viewer | product photograph: soft studio light, real surface properties, a hint of depth of field |
| time | several minutes for the four views (a 4K three-quarter view is the slow one) | longer on a CPU; a GPU shortens it (run it in the background) |
| needs | KiCad (already required) | Blender ≥ 4.2: `./setup.sh install-blender` (Blender 4.5 LTS, ~1.2 GB, into `~/.local/share/hwdesign/blender/`, no root), or a Blender you have (`BLENDER_BIN`, `blender` on PATH, `/Applications/Blender.app`) |
| GPU | — | the first that works of OptiX / CUDA (NVIDIA driver), HIP (AMD: `./setup.sh install-blender --amd-gpu` unpacks the runtime on Debian/Ubuntu), oneAPI (Intel), Metal (Apple); `--device cpu` or `gpu` forces one, `auto` (default) uses GPU and CPU together. The run prints which |
| writes | `<board>/renders/kicad/` | `<board>/renders/blender/` |
| page link (`render.json`) | `artifact_url_kicad` | `artifact_url_blender` |

The two never overwrite each other: separate folders, separate pages. Bodies
are resolved once, the same way, for both; the Blender engine builds the
generated blocks itself, because KiCad's GLB export carries STEP models only.
`./setup.sh check` reports Blender as optional, with the GPU it would use.

`blender_scene.py` is the Blender half: materials, lights, cameras. KiCad
exports every component colour as dull metal, so it reclassifies them by
colour (gold contacts, tin, black moulding, ceramic). That is a guess about a
surface, good for a picture and nothing else.

## What it writes

Everything goes to `<board>/renders/<engine>/`, which is generated (and
gitignored): do not edit it, delete it freely. Each run replaces the folder.

| file | what |
|---|---|
| `hero.png` `rear.png` `top.png` `bottom.png` | the pictures at full size (about 3 700 px wide), on their backdrop |
| `web/*.webp` | the same, 2 400 px wide — what the page shows |
| `contact.jpg` | every view on one sheet — the one image to look at before publishing |
| `index.html` | the gallery page, written to the Artifact page contract |
| `manifest.json` | board file hash and save time, engine and device, each view's camera, every body's source |

**It never writes the board.** The picture is taken of a scratch copy in a temp
directory; `<board>/render.json` (below) is the only other file it touches, and
only on `--set-url`. Paths on the page and in the manifest are relative to
`kicad_project/` — never a machine path.

## Why a scratch copy: the bodies

KiCad draws a part's body only when the footprint names a 3D model file that
exists. A footprint that names none, or names one that is not installed, is
drawn as bare pads. The tool gives the scratch copy a body for every placed
part, resolved in this order:

| source | meaning |
|---|---|
| `own` | the footprint's own model; the file exists |
| `config` | `<board>/render.json` names a model or a height |
| `alt` | the named file is missing and a near-identical name sits beside it in the same library |
| `stock` | a KiCad library model of the same package: same body size, same pin count, same pitch when both names state one |
| `standin` | a generated block: the F.Fab outline extruded, with a pin-1 dot. **Its height is assumed** |
| `dnp` | not fitted: drawn without a body |
| `none` | nothing to draw: mounting hole, solder jumper, test point, fiducial |

Everything that is not `own` is printed, recorded in the manifest and listed on
the page, so a substituted body is never read as vendor CAD. **A render is not
evidence about a land pattern or a body height** — `tools/fp/` is. The lasting
fix for a part without a model is in the library (`fp.py`, the `footprint`
skill), not here.

## `<board>/render.json` — optional, yours, hand-written

    {
      "artifact_url_kicad": "https://claude.ai/…",
      "artifact_url_blender": "https://claude.ai/…",
      "title": "Board 1 Main",
      "views": {
        "rear": null,
        "detail": {"title": "Connector corner", "rotate": "-40,0,-60", "perspective": true}
      },
      "bodies": {
        "U5": {"height": 0.4},
        "Osc_SMD_2.5x2.0mm": {"model": "${KICAD10_3DMODEL_DIR}/Oscillator.3dshapes/….step"},
        "L1": {"height": 4.0, "style": "ferrite"},
        "TP1": {"none": true}
      }
    }

- **`artifact_url_<engine>`** — the published page of that engine's render.
  `--engine <e> --set-url` writes it; with it, the next publish updates the
  same link instead of making a new one.
- **`views`** — merged over the default four. `null` drops a view, a new name
  adds one. Keys: `title`, `side` (top, bottom, left, right, front, back) or
  `rotate` (`X,Y,Z` degrees, as `kicad-cli pcb render` takes them),
  `perspective`, and `zoom` / `pan` / `pivot` to take the framing away from the
  auto-fit (KiCad engine). The first view is the page's large picture.
- **`bodies`** — keyed by reference or by footprint name (a reference wins).
  `model` (with optional `offset`, `rotate`, `scale`, three numbers each) names
  a file; `height` in mm and `style` (`epoxy`, `metal`, `ferrite`) shape a
  generated block; `none` draws nothing. A body height comes from the package
  drawing in `handoff/datasheets/`, never from a guess.

## How a view is framed (KiCad engine)

The zoom is found, not typed: OpenGL probes of about a second each measure the
board in the frame, and the zoom is adjusted until the board spans 87 % of it
(78 % in a three-quarter view, which needs room for its shadow). The frame is
then grown until the board itself has the pixels of a 4K picture. Any board
shape and size frames itself; nothing is tuned per board.

The ray tracer puts its floor shadows in the alpha channel. A three-quarter
view keeps them. A flat view (top, bottom) would show them as a grey halo of
the outline, one per light, so they are cut away with the same frame rendered
in OpenGL, which has no shadows, and a soft drawn shadow takes their place.
The Blender engine frames with its own camera fit and the same fill targets.

## What it cannot do

- **It photographs the file as last saved.** With KiCad open on this board the
  picture is behind the screen; the run warns, and the page says so.
- **The KiCad engine's appearance is KiCad's own preset** — mask, silk and
  finish colours come from the board, not from this tool.
- **A `stock` match is by name**, not by geometry. If a library model is the
  wrong body, name the right one in `render.json`.
