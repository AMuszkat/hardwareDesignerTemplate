#!/usr/bin/env python3
"""layerpics.py -- every copper layer of a board as a picture, read-only.

    tools/.venv/bin/python tools/route/layerpics.py BOARD_DIR OUT_DIR [--zoom LAYER:X0,Y0,X1,Y1 ...] [--scale 20]

Writes OUT_DIR/<layer>.svg and .png for each copper layer (with Edge.Cuts), OUT_DIR/overview.png (all layers in
one grid, in stack order) and, per --zoom, OUT_DIR/<layer>_zoom_<n>.png of that window in board mm. kicad-cli
does the export from the file on disk; the board is never opened for writing. Exit 0 when every layer was
written, 1 otherwise. A picture is not a gate: it proves nothing about a clearance.
"""
import glob, os, re, subprocess, sys
import pymupdf
from PIL import Image, ImageDraw

PT = 72 / 25.4


def copper_layers(text):
    head = text[:text.index('(setup')] if '(setup' in text else text[:20000]
    return re.findall(r'\(\d+ "([^"]+\.Cu)" ', head)


def edge_centre(text):
    xs, ys = [], []
    for m in re.finditer(r'\(gr_(?:line|rect|arc|poly|circle)\b', text):
        d, i = 0, m.start()
        while True:
            c = text[i]
            d += c == '('
            d -= c == ')'
            i += 1
            if d == 0:
                break
        blk = text[m.start():i]
        if '"Edge.Cuts"' not in blk:
            continue
        for x, y in re.findall(r'\((?:start|end|mid|center|xy) (-?[\d.]+) (-?[\d.]+)\)', blk):
            xs.append(float(x)); ys.append(float(y))
    if not xs:
        return None
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def main(a):
    if len(a) < 2:
        print(__doc__); return 2
    bd, out = os.path.abspath(a[0]), os.path.abspath(a[1])
    zooms, scale, i = [], 20, 2
    while i < len(a):
        if a[i] == '--zoom':
            lay, box = a[i + 1].split(':'); zooms.append((lay, [float(v) for v in box.split(',')])); i += 2
        elif a[i] == '--scale':
            scale = float(a[i + 1]); i += 2
        else:
            print('unknown argument', a[i]); return 2
    pcb = glob.glob(os.path.join(bd, '*.kicad_pcb'))
    if len(pcb) != 1:
        print('layerpics: need exactly one .kicad_pcb in', bd); return 1
    text = open(pcb[0]).read()
    layers = copper_layers(text)
    os.makedirs(out, exist_ok=True)
    ims, bad = [], 0
    for L in layers:
        n = L[:-3]
        svg = os.path.join(out, n + '.svg')
        r = subprocess.run(['kicad-cli', 'pcb', 'export', 'svg', '--layers', L + ',Edge.Cuts', '--mode-single',
                            '--exclude-drawing-sheet', '--page-size-mode', '2', '--black-and-white', '-o', svg, pcb[0]],
                           capture_output=True, text=True)
        if r.returncode or not os.path.exists(svg):
            print('layerpics: FAIL', L, r.stderr.strip()[-200:]); bad += 1; continue
        pg = pymupdf.open(svg)[0]
        png = os.path.join(out, n + '.png')
        pg.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).save(png)
        ims.append((n, Image.open(png).convert('RGB')))
    if ims:
        w = 1200
        th = [(n, im.resize((w, int(im.height * w / im.width)), Image.LANCZOS)) for n, im in ims]
        h = th[0][1].height; cols = 5 if len(th) > 4 else len(th); rows = -(-len(th) // cols)
        G = Image.new('RGB', (cols * (w + 10) + 10, rows * (h + 50) + 10), 'white'); dr = ImageDraw.Draw(G)
        for k, (n, t) in enumerate(th):
            x, y = 10 + (k % cols) * (w + 10), 40 + (k // cols) * (h + 50)
            G.paste(t, (x, y)); dr.text((x + 4, y - 28), n + '.Cu', fill='black')
        G.save(os.path.join(out, 'overview.png'))
    c = edge_centre(text)
    for k, (lay, (x0, y0, x1, y1)) in enumerate(zooms, 1):
        svg = os.path.join(out, lay + '.svg')
        if c is None or not os.path.exists(svg):
            print('layerpics: no zoom for', lay); bad += 1; continue
        pg = pymupdf.open(svg)[0]
        ox, oy = c[0] - pg.rect.width / PT / 2, c[1] - pg.rect.height / PT / 2
        clip = pymupdf.Rect((x0 - ox) * PT, (y0 - oy) * PT, (x1 - ox) * PT, (y1 - oy) * PT)
        z = min(60, 3400 / max(clip.width, clip.height))
        f = os.path.join(out, '%s_zoom_%d.png' % (lay, k))
        pg.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip, alpha=False).save(f)
    print('layerpics: %d layer(s), %d zoom(s) in %s%s' % (len(ims), len(zooms), out, '' if not bad else '; %d FAILED' % bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
