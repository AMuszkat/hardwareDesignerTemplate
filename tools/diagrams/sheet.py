"""
Shared drawing primitives for the design/diagrams/ sheets.

House style and the rules that keep the sheets readable (a sheet that ignores
them comes out 3000 px wide with 8 pt text, and nobody reads it):

  * At most THREE body lines per box, at most 34 characters each; both are
    asserted, not advised.  A box is a name plus the two facts that
    identify the part.  What will not fit belongs in design/SPECS.md.
  * Typography is fixed: 21 pt sheet title, 13.5 pt box title, 11 pt body,
    10 pt annotations.  Size the CANVAS to the content, never the type.
  * Colour is the domain (blue digital, green analog, amber power, teal
    sense, red protection). Solid frames group functions; DASHED part
    boxes mean provisional implementation (a candidate may be named).
  * A box may carry a `rail=` tag: a small amber footer strip naming the
    supply it runs from.  That is how the system sheet says "powered by"
    without a power tree on the same page.

Self-lint: every box and wire is registered as it is drawn.  Sheet.save()
fails loudly on a box overlap, a wire through a box interior, or anything
off canvas.  Edit the generator, never the SVG.
"""

import textwrap


RS = dict(ink="#10243E", sub="#5A6472", body="#3A4552",
          dig="#38598B", digbg="#F0F4FA", txt="#1F3A5F",
          ana="#3E7C4F", anabg="#F0F7F1",
          pwr="#B97A1F", pwrbg="#FDF3E3",
          fb="#0E7C7B", grey="#8A8F98",
          red="#B3423A", redbg="#FBEFEE", redtx="#8E2F28", redbd="#6E4441")

FILL = {"dig": "#FFFFFF", "ana": RS["anabg"], "pwr": RS["pwrbg"],
        "red": RS["redbg"], "use": "#F7F8FA"}
EDGE = {"dig": RS["dig"], "ana": RS["ana"], "pwr": RS["pwr"],
        "red": RS["red"], "use": RS["grey"]}
TTL = {"dig": RS["txt"], "ana": "#2E5E3C", "pwr": "#8A5A12",
       "red": RS["redtx"], "use": "#4A5560"}
BDY = {"dig": RS["body"], "ana": RS["body"], "pwr": RS["body"],
       "red": RS["redbd"], "use": RS["sub"]}

MK = {RS["dig"]: "aD", RS["ana"]: "aA", RS["pwr"]: "aP",
      RS["fb"]: "aF", RS["red"]: "aR", RS["grey"]: "aG"}

RAIL_STRIP = 20          # height of a box's "powered from" footer


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Sheet:
    def __init__(self, w, h):
        self.W, self.H = w, h
        self.boxes, self.wires, self.texts, self.frames = [], [], [], []
        self.o = [f'<svg xmlns="http://www.w3.org/2000/svg" '
                  f'viewBox="0 0 {w} {h}" '
                  f'font-family="Helvetica, Arial, sans-serif">',
                  f'<rect width="{w}" height="{h}" fill="#FFFFFF"/>', "<defs>"]
        for c, mid in MK.items():
            self.o.append(
                f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5"'
                f' markerWidth="7" markerHeight="7"'
                f' orient="auto-start-reverse">'
                f'<path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>')
        self.o.append("</defs>")
        # Frames are painted into a BACKGROUND layer that grows here, behind
        # everything else -- see frame().
        self._bg = len(self.o)

    # ---- text ------------------------------------------------------------
    def text(self, x, y, s, fs=11, col=None, anchor="middle",
             bold=False, italic=False):
        self.texts.append((x, y, fs, s))
        a = ' font-weight="bold"' if bold else ""
        a += ' font-style="italic"' if italic else ""
        self.o.append(f'<text x="{x}" y="{y}" font-size="{fs}" '
                      f'fill="{col or RS["sub"]}" text-anchor="{anchor}"{a}>'
                      f'{esc(s)}</text>')

    def paragraph(self, x, y, s, width, fs=11.5, col=None, dy=18):
        """Wrap prose without shrinking type; return the next free baseline.

        The conservative character budget is for prose, not precision box
        sizing. Rendered glyph bounds can be checked with check_diagrams.py.
        """
        cols = max(1, int(width / (fs * 0.62)))
        for line in textwrap.wrap(s, cols, break_long_words=False,
                                  break_on_hyphens=False):
            self.text(x, y, line, fs, col, "start")
            y += dy
        return y

    # ---- blocks ----------------------------------------------------------
    def box(self, x, y, w, title, lines, kind="dig", h=None, dash=False,
            rail=None):
        """Part box: bold title, <=3 body lines, optional 'powered from' tag."""
        assert len(lines) <= 3, f"{title}: {len(lines)} body lines -- max 3"
        for ln in lines:
            assert len(ln) <= 34, f"{title}: line too long ({len(ln)}): {ln}"
        strip = RAIL_STRIP if rail else 0
        # an explicit height means the box is being aligned with others in a
        # row: reserve the tag strip either way so their body text lines up
        reserve = RAIL_STRIP if (rail or h is not None) else 0
        h = h or 46 + 18 * len(lines) + strip
        d = ' stroke-dasharray="6 4"' if dash else ""
        self.o.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
            f'fill="{FILL[kind]}" stroke="{EDGE[kind]}" stroke-width="1.6"'
            f' rx="8"{d}/>')
        self.text(x + w / 2, y + 27, title, 13.5, TTL[kind], bold=True)
        top, bot, blk = y + 36, y + h - 8 - reserve, 18 * len(lines)
        first = top + (bot - top - blk) / 2 + 13
        for i, ln in enumerate(lines):
            self.text(x + w / 2, first + 18 * i, ln, 11, BDY[kind])
        if rail:
            yl = y + h - strip
            self.o.append(
                f'<line x1="{x + 12}" y1="{yl}" x2="{x + w - 12}" y2="{yl}" '
                f'stroke="{RS["pwr"]}" stroke-width="0.9" '
                f'stroke-dasharray="3 3" opacity="0.7"/>')
            self.text(x + w / 2, y + h - 6, rail, 10, RS["pwr"])
        self.boxes.append((x, y, w, h, title[:26]))
        return x, y, w, h

    def frame(self, x, y, w, h, label, kind, dash=False, lx=None, ly=None,
              anchor="start", fill=None):
        """Container / functional lane. NOT registered: wires may cross it.

        `fill` overrides the domain tint -- a lane holding boxes of its own
        colour needs a neutral ground or the boxes disappear into it.

        ⚠ THE RECT IS INSERTED BEHIND EVERYTHING ALREADY DRAWN, not appended.
        A lane is FILLED, so appending it paints out whatever it covers, and
        the failure is silent: wires drawn before their lane lose whatever
        the lane covers, arrowheads included, and the lint checks geometry,
        not z-order. Inserting makes the mistake impossible rather than
        catchable; frames still stack in call order among themselves, and the
        label stays on top where it belongs.
        """
        bg = fill or {"dig": RS["digbg"], "ana": RS["anabg"], "pwr": RS["pwrbg"],
                      "red": RS["redbg"], "use": "#F7F8FA"}[kind]
        ec = EDGE[kind]
        d = ' stroke-dasharray="6 5"' if dash else ""
        self.o.insert(self._bg,
                      f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
                      f'fill="{bg}" stroke="{ec}" stroke-width="1.6" rx="12"'
                      f'{d}/>')
        self._bg += 1
        self.frames.append((x, y, w, h, label))
        self.text(lx if lx is not None else x + 20, ly or y + 25, label,
                  14, ec, anchor, bold=True)

    # ---- wires -----------------------------------------------------------
    def seg(self, x1, y1, x2, y2, col, head=True, dash=False):
        self.wires.append((x1, y1, x2, y2))
        m = f' marker-end="url(#{MK[col]})"' if head else ""
        d = ' stroke-dasharray="5 4"' if dash else ""
        self.o.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                      f'stroke="{col}" stroke-width="2"{d}{m}/>')

    def orth(self, pts, col, head=True, dash=False):
        for a, b in zip(pts, pts[1:]):
            self.wires.append((*a, *b))
        d = " ".join(f"{a},{b}" for a, b in pts)
        m = f' marker-end="url(#{MK[col]})"' if head else ""
        s_ = ' stroke-dasharray="5 4"' if dash else ""
        self.o.append(f'<polyline points="{d}" fill="none" stroke="{col}" '
                      f'stroke-width="2"{s_}{m}/>')

    def dot(self, x, y, col):
        self.o.append(f'<circle cx="{x}" cy="{y}" r="4" fill="{col}"/>')

    def sumnode(self, cx, cy, r=18):
        self.o.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#FFFFFF" '
                      f'stroke="{RS["ink"]}" stroke-width="2"/>')
        self.text(cx, cy + 6, "Σ", 16, RS["ink"], bold=True)

    # ---- furniture -------------------------------------------------------
    def header(self, title, subtitle, ribbon=None):
        self.o.append(f'<title>{esc(title)}</title><desc>'
                      f'{esc(subtitle)} {esc(ribbon or "")}</desc>')
        self.text(40, 48, title, 21, RS["ink"], "start", True)
        self.text(40, 72, subtitle, 12.5, RS["sub"], "start")
        if ribbon:
            self.text(40, 96, ribbon, 11.5, RS["ink"], "start")

    def legend(self, items, y=38):
        """items: (x, kind|'fb'|'dash', label)."""
        for x, kind, lab in items:
            if kind == "fb":
                self.seg(x, y + 8, x + 34, y + 8, RS["fb"])
                self.text(x + 42, y + 13, lab, 11, RS["body"], "start")
                continue
            d = ' stroke-dasharray="4 3"' if kind == "dash" else ""
            k = "use" if kind == "dash" else kind
            self.o.append(
                f'<rect x="{x}" y="{y}" width="16" height="16" '
                f'fill="{"#FFFFFF" if kind == "dash" else FILL[k]}" '
                f'stroke="{EDGE[k]}" stroke-width="1.5" rx="3"{d}/>')
            self.text(x + 22, y + 13, lab, 11, RS["body"], "start")

    def footer(self, lines, y0=None, dy=22):
        y0 = y0 if y0 is not None else self.H - 42
        for txt, col in lines:
            y0 = self.paragraph(40, y0, txt, self.W - 80, col=col,
                                dy=dy) + 4

    # ---- lint + write ----------------------------------------------------
    def lint(self):
        bad = []
        for x, y, w, h, label in self.frames:
            if x < 0 or y < 0 or x + w > self.W or y + h > self.H:
                bad.append(f"frame off canvas: {label}")
        for x, y, fs, label in self.texts:
            if not (0 <= x <= self.W and fs <= y <= self.H - 4):
                bad.append(f"text anchor off canvas: {label}")
        for i, a in enumerate(self.boxes):
            if (a[0] < 0 or a[1] < 0 or a[0] + a[2] > self.W
                    or a[1] + a[3] > self.H):
                bad.append(f"off canvas: {a[4]}")
            for b in self.boxes[i + 1:]:
                if (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
                        and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]):
                    bad.append(f"box overlap: {a[4]} <-> {b[4]}")
        for x1, y1, x2, y2 in self.wires:
            if not (0 <= x1 <= self.W and 0 <= x2 <= self.W
                    and 0 <= y1 <= self.H and 0 <= y2 <= self.H):
                bad.append(f"wire off canvas: ({x1},{y1})-({x2},{y2})")
            n = max(2, int(max(abs(x2 - x1), abs(y2 - y1)) / 3))
            for k in range(n + 1):
                px = x1 + (x2 - x1) * k / n
                py = y1 + (y2 - y1) * k / n
                for b in self.boxes:
                    if (b[0] + 2 < px < b[0] + b[2] - 2
                            and b[1] + 2 < py < b[1] + b[3] - 2):
                        bad.append(
                            f"wire through {b[4]}: ({x1},{y1})-({x2},{y2})")
                        break
                else:
                    continue
                break
        if bad:
            for m in dict.fromkeys(bad):
                print("LINT FAIL:", m)
            raise SystemExit(1)
        print(f"lint OK: {len(self.boxes)} boxes, {len(self.wires)} segments, "
              f"no overlaps, no wires through boxes, inside {self.W}x{self.H}")

    def save(self, path):
        self.lint()
        self.o.append("</svg>")
        svg = "".join(self.o)
        open(path, "w").write(svg)
        print(f"wrote {path} ({len(svg)} bytes, {self.W}x{self.H})")
