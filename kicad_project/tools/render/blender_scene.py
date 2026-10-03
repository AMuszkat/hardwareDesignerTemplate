"""blender_scene.py -- the Blender half of `render.py --engine blender`.

Run by render.py, never by hand:

    <blender> -b --factory-startup --python blender_scene.py -- job.json

The job names a GLB that KiCad exported from the scratch board, the generated
stand-in bodies (KiCad's GLB export carries STEP models only), the views and
the output folder.  This script imports the board, gives its materials real
surface properties, lights it like a product shot and renders each view with
Cycles to an RGBA PNG whose alpha holds the floor shadow -- the same contract
as the KiCad ray tracer, so render.py composes both the same way.
"""
import colorsys
import json
import os
import math
import sys

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

job = json.load(open(sys.argv[sys.argv.index("--") + 1]))
MM = 0.001

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=job["glb"])
scene = bpy.context.scene


# ---- materials --------------------------------------------------------------
# KiCad writes board materials with usable values and every component material
# as metallic 1 / roughness 1, which renders as dull grey metal.  Those are
# reclassified by colour.

def bsdf(mat):
    return mat.node_tree.nodes.get("Principled BSDF") if mat.use_nodes else None


HASL = any(k in job.get("finish", "").lower() for k in ("hal", "hasl", "tin", "silver"))


def tune(mat):
    b = bsdf(mat)
    if not b:
        return
    r, g, bl, _ = b.inputs["Base Color"].default_value
    h, s, v = colorsys.rgb_to_hsv(r, g, bl)
    metal, rough, alpha = (b.inputs[k].default_value for k in ("Metallic", "Roughness", "Alpha"))
    put = lambda **kw: [setattr(b.inputs[k.replace("_", " ")], "default_value", x) for k, x in kw.items()]
    colour = lambda *c: setattr(b.inputs["Base Color"], "default_value", (*c, 1.0))
    if alpha < 0.9:                             # solder mask: a glossy lacquer
        # Thinner than KiCad exports it, so copper under the mask reads lighter
        # than bare laminate, as it does on a real board.
        colour(r * 0.55, g * 0.55, bl * 0.55)
        put(Roughness=0.32, Alpha=0.66, Coat_Weight=0.12, Coat_Roughness=0.25)
    elif alpha < 0.999:                         # the laminate itself: opaque, matt
        colour(0.25, 0.23, 0.12)
        put(Alpha=1.0, Roughness=0.75)
    elif metal > 0.99 and rough > 0.99:         # a component colour, unclassified
        if 0.04 < h < 0.18 and s > 0.45 and v > 0.3:
            colour(0.83, 0.62, 0.25)                            # gold contacts
            put(Metallic=1.0, Roughness=0.22)
        elif 0.55 < h < 0.72 and s < 0.4 and v > 0.7:
            colour(0.78, 0.79, 0.80)                            # contacts drawn bluish
            put(Metallic=1.0, Roughness=0.25)
        elif 0.05 < s < 0.30 and v > 0.3:
            put(Metallic=1.0, Roughness=0.35)                   # tin: terminations, leads
        elif v < 0.06:
            colour(0.012, 0.012, 0.013)                         # black moulding
            put(Metallic=0.0, Roughness=0.5, Specular_IOR_Level=0.35)
        elif s < 0.05 and v > 0.3:
            colour(0.85, 0.85, 0.83)                            # natural / white housing
            put(Metallic=0.0, Roughness=0.45)
        else:
            put(Metallic=0.0, Roughness=0.55)                   # ceramic, coloured plastic
    elif metal > 0.99:                          # board copper and pads
        if HASL:                                # tinned, not gold
            colour(0.80, 0.80, 0.77)
        put(Roughness=0.3)
    elif v > 0.9:                               # silkscreen
        put(Roughness=0.7)


for m in bpy.data.materials:
    tune(m)


def material(name, colour, metallic, roughness):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = bsdf(m)
    b.inputs["Base Color"].default_value = (*colour, 1.0)
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = roughness
    return m


STYLE = {
    "epoxy": material("standin_epoxy", (0.012, 0.012, 0.014), 0.0, 0.45),
    "metal": material("standin_metal", (0.62, 0.62, 0.58), 1.0, 0.35),
    "ferrite": material("standin_ferrite", (0.03, 0.03, 0.032), 0.0, 0.7),
}
DOT = material("standin_dot", (0.55, 0.55, 0.52), 0.0, 0.6)


# ---- stand-in bodies --------------------------------------------------------

def block(name, corners, z0, z1, mat):
    """A prism on four (x, y) corners in mm, KiCad frame (y down)."""
    pts = [(x * MM, -y * MM, z * MM) for z in (z0, z1) for x, y in corners]
    faces = [(0, 1, 2, 3), (7, 6, 5, 4)] + [(i, (i + 1) % 4, 4 + (i + 1) % 4, 4 + i) for i in range(4)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(pts, [], faces)
    mesh.materials.append(mat)
    ob = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    bev = ob.modifiers.new("bevel", "BEVEL")
    bev.width, bev.segments = min(0.08, 0.2 * abs(z1 - z0)) * MM, 2
    return ob


top_z, bot_z = job["top_z_mm"], job["bottom_z_mm"]
for s in job["standins"]:
    up = s["side"] == "top"
    z0 = top_z + 0.03 if up else bot_z - 0.03
    z1 = z0 + s["height"] if up else z0 - s["height"]
    block("standin_" + s["ref"], s["corners"], z0, z1, STYLE.get(s["style"], STYLE["epoxy"]))
    if s.get("dot"):
        (cx, cy), r = s["dot"]["at"], s["dot"]["r"]
        ring = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in (2 * math.pi * k / 20 for k in range(20))]
        pts = [(x * MM, -y * MM, (z1 + (0.004 if up else -0.004)) * MM) for x, y in ring]
        mesh = bpy.data.meshes.new("dot_" + s["ref"])
        mesh.from_pydata(pts, [], [tuple(range(20))])
        mesh.materials.append(DOT)
        scene.collection.objects.link(bpy.data.objects.new("dot_" + s["ref"], mesh))


# ---- extent -----------------------------------------------------------------
bpy.context.view_layer.update()
meshes = [o for o in scene.objects if o.type == "MESH"]
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector([min(p[i] for p in pts) for i in range(3)])
hi = Vector([max(p[i] for p in pts) for i in range(3)])
centre, size = (lo + hi) / 2, (hi - lo).length
flat_coords = [c for p in pts for c in p]


# ---- floor, light, world ----------------------------------------------------
bpy.ops.mesh.primitive_plane_add(size=size * 40, location=(centre.x, centre.y, lo.z - 0.00002))
floor = bpy.context.active_object
floor.name = "floor"
floor.is_shadow_catcher = True


def area(name, at, energy, spread):
    data = bpy.data.lights.new(name, "AREA")
    data.shape, data.size, data.energy = "DISK", size * spread, energy * size * size
    ob = bpy.data.objects.new(name, data)
    ob.location = centre + Vector(at) * size
    ob.rotation_euler = (centre - ob.location).to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(ob)
    return ob


# A large key softbox high on one side, a weaker fill opposite, a rim behind.
# Energies are per square metre of scene, so any board size is lit alike.
LIGHTS = [area("key", (-1.1, -1.0, 1.9), 85, 1.9),
          area("fill", (1.5, -0.6, 1.1), 12, 2.2),
          area("rim", (0.3, 1.8, 1.2), 55, 1.0)]

# Blender's own studio environment, for what the metal and the lacquer
# reflect; a flat grey world when the file is not there.
world = bpy.data.worlds.new("studio")
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (0.82, 0.84, 0.86, 1.0)
bg.inputs["Strength"].default_value = 0.16
hdri = os.path.join(bpy.utils.system_resource("DATAFILES"), "studiolights", "world", "studio.exr")
if os.path.exists(hdri):
    env = world.node_tree.nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(hdri)
    world.node_tree.links.new(env.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 0.3
scene.world = world


# ---- render settings --------------------------------------------------------
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"


def pick_gpu(want):
    """The first GPU backend this machine can actually run, in order of speed:
    OptiX and CUDA (NVIDIA, with its driver), HIP (AMD, with the HIP runtime --
    setup.sh --amd-gpu puts one on the path), oneAPI (Intel), Metal (Apple).
    `auto` renders on the GPU and the CPU together; `gpu` on the GPU alone;
    `cpu` never asks.  No usable GPU: the CPU, and the run says so."""
    if want == "cpu":
        return "CPU (asked for)"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except Exception as err:                    # noqa: BLE001
        return f"CPU (no Cycles preferences: {err})"
    tried = []
    for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
        try:
            prefs.compute_device_type = backend
            prefs.get_devices()
        except Exception:                       # noqa: BLE001 -- not built for this platform
            continue
        gpus = [d for d in prefs.devices if d.type == backend]
        tried.append(backend)
        if not gpus:
            continue
        for d in prefs.devices:
            d.use = d.type == backend or (d.type == "CPU" and want != "gpu")
        scene.cycles.device = "GPU"
        return f"{backend}: " + " + ".join(d.name for d in prefs.devices if d.use)
    return "CPU (no usable GPU" + (f"; tried {', '.join(tried)}" if tried else "") + ")"


print("DEVICE", pick_gpu(job.get("device", "auto")))
scene.cycles.samples = job["samples"]
scene.cycles.use_denoising = True
scene.cycles.use_adaptive_sampling = True
scene.cycles.adaptive_threshold = 0.02
scene.render.film_transparent = True
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.render.image_settings.color_depth = "8"
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "Medium High Contrast"
scene.render.threads_mode = "AUTO"

cam_data = bpy.data.cameras.new("cam")
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam


def frame(fill, px):
    """Fit the camera to everything, then set the frame to the content's own
    aspect and back off until it spans `fill` of the frame."""
    scene.render.resolution_x, scene.render.resolution_y = 1600, 1000
    for _ in range(2):
        dg = bpy.context.evaluated_depsgraph_get()
        loc, scale = cam.camera_fit_coords(dg, flat_coords)
        cam.location = loc
        if cam_data.type == "ORTHO":
            cam_data.ortho_scale = scale
        bpy.context.view_layer.update()
        uv = [world_to_camera_view(scene, cam, p) for p in pts]
        w = max(u.x for u in uv) - min(u.x for u in uv)
        h = max(u.y for u in uv) - min(u.y for u in uv)
        aspect = min(max(w * scene.render.resolution_x / (h * scene.render.resolution_y), 0.6), 2.6)
        rx = round(math.sqrt(px * aspect) / 8) * 8
        scene.render.resolution_x, scene.render.resolution_y = rx, round(rx / aspect / 8) * 8
    if cam_data.type == "ORTHO":
        cam_data.ortho_scale /= fill
    else:
        back = (cam.location - centre)
        cam.location = centre + back / fill


for v in job["views"]:
    flat = v["flat"]
    below = v["elevation"] < 0
    el, az = math.radians(v["elevation"]), math.radians(v["azimuth"])
    direction = Vector((math.cos(el) * math.sin(az), -math.cos(el) * math.cos(az), math.sin(el)))
    cam_data.type = "ORTHO" if flat else "PERSP"
    cam_data.lens = 85
    cam_data.clip_start, cam_data.clip_end = size * 0.01, size * 100
    cam.location = centre + direction * size * 4
    if abs(v["elevation"]) > 89.9:               # straight down / up: no horizon to level against
        cam.rotation_euler = (0, 0, 0) if not below else (0, math.pi, 0)
    else:
        cam.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
    for light in LIGHTS:                         # a view from below is lit from below
        if (light.location.z < centre.z) != below:
            light.location.z = 2 * centre.z - light.location.z
            light.rotation_euler = (centre - light.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()
    frame(v["fill"], job["px"])
    cam_data.dof.use_dof = not flat
    if not flat:
        cam_data.dof.focus_distance = (cam.location - centre).length
        cam_data.dof.aperture_fstop = 64        # a hint of depth, every part still sharp
    floor.hide_render = below
    scene.render.filepath = v["out"]
    bpy.ops.render.render(write_still=True)
    print(f"VIEW {v['name']} {scene.render.resolution_x}x{scene.render.resolution_y}")
