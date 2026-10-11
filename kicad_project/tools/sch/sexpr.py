"""
A reader for KiCad's s-expression files -- read-only. Nothing in tools/sch
writes a board's KiCad file through this module.

    tree = sexpr.load(path)              # ['kicad_sch', ['version', '20250610'], ...]
    sexpr.first(tree, "paper")           # ['paper', 'A3'] or None
    sexpr.every(tree, "wire")            # every direct child named "wire"
    sexpr.value(node, "at")              # the atoms after the head: ['10.16', '20.32', '0']

Atoms come back as str; a quoted atom is a Q (a str subclass) so a caller can
tell `(hide yes)` from `"yes"` when it matters.
"""


class Q(str):
    """A quoted string atom."""


def parse(text):
    stack, cur, i, n = [], [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "(":
            stack.append(cur)
            cur = []
            i += 1
        elif c == ")":
            done = cur
            cur = stack.pop()
            cur.append(done)
            i += 1
        elif c == '"':
            j, buf = i + 1, []
            while text[j] != '"':
                if text[j] == "\\":
                    nxt = text[j + 1]
                    buf.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            cur.append(Q("".join(buf)))
            i = j + 1
        elif c in " \t\r\n":
            i += 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()"':
                j += 1
            cur.append(text[i:j])
            i = j
    if stack or len(cur) != 1:
        raise ValueError("unbalanced s-expression")
    return cur[0]


def load(path):
    with open(path, encoding="utf-8") as f:
        return parse(f.read())


def is_node(x, head=None):
    return isinstance(x, list) and x and (head is None or x[0] == head)


def every(node, head):
    return [c for c in node[1:] if is_node(c, head)]


def first(node, head):
    for c in node[1:]:
        if is_node(c, head):
            return c
    return None


def value(node, head, default=None):
    """The atoms after `head` in the first child named `head`."""
    c = first(node, head)
    if c is None:
        return default
    return [a for a in c[1:] if not isinstance(a, list)]


def atom(node, head, default=None):
    v = value(node, head)
    return v[0] if v else default


def flag(node, head):
    """`(hide yes)` / `(hide)` / a bare `hide` atom -> True."""
    if head in [a for a in node[1:] if not isinstance(a, list)]:
        return True
    v = value(node, head)
    return v is not None and (not v or v[0] in ("yes", "true"))


def dump(node, ind=0):
    """A parsed node back to text (for copying a library symbol into a scratch
    file -- never for rewriting a board's file)."""
    if not isinstance(node, list):
        return esc(node) if isinstance(node, Q) else str(node)
    if not any(isinstance(c, list) for c in node):
        return "(" + " ".join(dump(c) for c in node) + ")"
    pad = "\t" * (ind + 1)
    out = "("
    for i, c in enumerate(node):
        if isinstance(c, list):
            out += "\n" + pad + dump(c, ind + 1)
        else:
            out += ("" if i == 0 else " ") + dump(c)
    return out + "\n" + "\t" * ind + ")"


def esc(s):
    """A string as a quoted KiCad atom."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
