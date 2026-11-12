#!/usr/bin/env python3
"""stepbox.py -- an ENVELOPE 3D model: one box, as a STEP file KiCad and an MCAD tool both load.

    python3 tools/fp/stepbox.py OUT.step --size X Y Z [--center CX CY] [--name NAME]

For a part whose vendor publishes no model a script may fetch (a login wall, a site that refuses
automated clients): the body's envelope from the datasheet -- X by Y by Z mm, its bottom on z = 0,
centred on (CX, CY) in MODEL axes (y up: a footprint's y is the model's -y).  It claims nothing about
pins, chamfers or a pin-1 mark; record in the footprint's note that it is an envelope and where the
three numbers come from.  Pure text (AP214, one MANIFOLD_SOLID_BREP), no CAD kernel needed.
Proof it loads: `kicad-cli pcb export step` on a board that uses it reports no model it could not read.
"""
import argparse, itertools, os


def box(name, sx, sy, sz, cx=0.0, cy=0.0):
    L = []; n = [0]

    def add(txt):
        n[0] += 1; L.append(f"#{n[0]}={txt};"); return n[0]
    f = lambda v: ("%.6f" % v).rstrip("0").rstrip(".") + ("." if "." not in ("%.6f" % v).rstrip("0").rstrip(".") else "")
    pt = lambda p: add("CARTESIAN_POINT('',(%s,%s,%s))" % tuple(f(v) for v in p))
    dr = lambda d: add("DIRECTION('',(%s,%s,%s))" % tuple(f(v) for v in d))
    ac = add("APPLICATION_CONTEXT('core data for automotive mechanical design processes')")
    add(f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#{ac})")
    pc = add(f"PRODUCT_CONTEXT('',#{ac},'mechanical')")
    pr = add(f"PRODUCT('{name}','{name}','',(#{pc}))")
    pf = add(f"PRODUCT_DEFINITION_FORMATION('','',#{pr})")
    dc = add(f"PRODUCT_DEFINITION_CONTEXT('part definition',#{ac},'design')")
    pd = add(f"PRODUCT_DEFINITION('design','',#{pf},#{dc})")
    ps = add(f"PRODUCT_DEFINITION_SHAPE('','',#{pd})")
    add(f"PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(#{pr}))")
    lu = add("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))")
    au = add("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
    su = add("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
    um = add(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#{lu},'distance_accuracy_value','confusion accuracy')")
    ctx = add(f"(GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{um}))"
              f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{lu},#{au},#{su}))REPRESENTATION_CONTEXT('Context #1','3D Context with UNIT and UNCERTAINTY'))")
    org = add(f"AXIS2_PLACEMENT_3D('',#{pt((0, 0, 0))},#{dr((0, 0, 1))},#{dr((1, 0, 0))})")
    lo = (cx - sx / 2, cy - sy / 2, 0.0); hi = (cx + sx / 2, cy + sy / 2, sz)
    xyz = lambda v: tuple(hi[a] if v[a] else lo[a] for a in range(3))
    V = {v: add(f"VERTEX_POINT('',#{pt(xyz(v))})") for v in itertools.product((0, 1), repeat=3)}
    E = {}
    for v in V:
        for a in range(3):
            if v[a] == 0:
                w = tuple(1 if k == a else v[k] for k in range(3))
                d = tuple(1.0 if k == a else 0.0 for k in range(3))
                ln = add(f"LINE('',#{pt(xyz(v))},#{add('VECTOR(%s,#%d,1.)' % (chr(39) * 2, dr(d)))})")
                E[(v, w)] = add(f"EDGE_CURVE('',#{V[v]},#{V[w]},#{ln},.T.)")
    faces = []
    for a in range(3):
        b, c = (a + 1) % 3, (a + 2) % 3
        for side in (0, 1):
            def vx(ib, ic):
                v = [0, 0, 0]; v[a] = side; v[b] = ib; v[c] = ic; return tuple(v)
            loop = [vx(0, 0), vx(1, 0), vx(1, 1), vx(0, 1)]          # counter-clockwise about +a
            if side == 0:
                loop.reverse()                                     # the outward normal is -a
            oe = []
            for p, q in zip(loop, loop[1:] + loop[:1]):
                e, sense = (E[(p, q)], ".T.") if (p, q) in E else (E[(q, p)], ".F.")
                oe.append(add(f"ORIENTED_EDGE('',*,*,#{e},{sense})"))
            el = add("EDGE_LOOP('',(%s))" % ",".join(f"#{k}" for k in oe))
            fb = add(f"FACE_OUTER_BOUND('',#{el},.T.)")
            nrm = tuple((1.0 if side else -1.0) if k == a else 0.0 for k in range(3))
            ref = tuple(1.0 if k == b else 0.0 for k in range(3))
            pl = add(f"PLANE('',#{add('AXIS2_PLACEMENT_3D(%s,#%d,#%d,#%d)' % (chr(39) * 2, pt(xyz(vx(0, 0))), dr(nrm), dr(ref)))})")
            faces.append(add(f"ADVANCED_FACE('',(#{fb}),#{pl},.T.)"))
    sh = add("CLOSED_SHELL('',(%s))" % ",".join(f"#{k}" for k in faces))
    so = add(f"MANIFOLD_SOLID_BREP('{name}',#{sh})")
    rep = add(f"ADVANCED_BREP_SHAPE_REPRESENTATION('{name}',(#{org},#{so}),#{ctx})")
    add(f"SHAPE_DEFINITION_REPRESENTATION(#{ps},#{rep})")
    head = ["ISO-10303-21;", "HEADER;", "FILE_DESCRIPTION(('envelope %s x %s x %s mm'),'2;1');" % (f(sx), f(sy), f(sz)),
            f"FILE_NAME('{name}.step','2000-01-01T00:00:00',(''),(''),'stepbox.py','stepbox.py','');",
            "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));", "ENDSEC;", "DATA;"]
    return "\n".join(head + L + ["ENDSEC;", "END-ISO-10303-21;"]) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out"); ap.add_argument("--size", nargs=3, type=float, required=True, metavar=("X", "Y", "Z"))
    ap.add_argument("--center", nargs=2, type=float, default=(0.0, 0.0), metavar=("CX", "CY"))
    ap.add_argument("--name")
    a = ap.parse_args()
    name = a.name or os.path.splitext(os.path.basename(a.out))[0]
    open(a.out, "w").write(box(name, *a.size, *a.center))
    print(f"{a.out}: envelope {a.size[0]:g} x {a.size[1]:g} x {a.size[2]:g} mm, centre ({a.center[0]:g}, {a.center[1]:g}), bottom on z = 0")


if __name__ == "__main__":
    main()
