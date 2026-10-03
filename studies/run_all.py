"""
Run the studies, check the headline numbers, or lint the docs.

    python studies/run_all.py                 # every study, in order
    python studies/run_all.py --list          # what each study answers
    python studies/run_all.py --only s02_divider_tolerance   # or just: --only s02
    python studies/run_all.py --check         # headline numbers vs baseline
    python studies/run_all.py --check --bless # accept them (a claim you looked)
    python studies/run_all.py --docs          # tools/lint_docs.py

A study is `studies/sNN_<name>.py` with a `main()`; it may also define
`headline()` for `--check` (see regression.py).  Plots and text output go to
`out/` (disposable); a number that matters goes into design/SPECS.md with the
study as its source, and into the baseline.  studies/README.md is the guide;
studies/_template.py is the skeleton.
"""
import argparse
import importlib
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "out")
sys.path.insert(0, HERE)


def studies():
    """Study module names in numeric order (s01_x, s02_y, ... s10_z)."""
    names = [f[:-3] for f in os.listdir(HERE)
             if re.fullmatch(r"s\d+_\w+\.py", f)]
    return sorted(names, key=lambda n: int(re.match(r"s(\d+)", n).group(1)))


def run(name):
    t = time.time()
    print(f"\n===== {name} =====", flush=True)
    importlib.import_module(name).main()
    sys.stdout.flush()
    print(f"[{name}: {time.time() - t:.0f} s]", file=sys.stderr, flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", metavar="NAME", help="run a single study by module name")
    ap.add_argument("--list", action="store_true",
                    help="list the studies and the question each one answers")
    ap.add_argument("--docs", action="store_true",
                    help="lint the authored docs and generated artefacts")
    ap.add_argument("--check", action="store_true",
                    help="recompute the headline numbers and diff them "
                         "against studies/baselines/")
    ap.add_argument("--bless", action="store_true",
                    help="with --check, accept the current values as the "
                         "new baseline -- a claim that you looked at them")
    args = ap.parse_args()
    if args.bless and not args.check:
        ap.error("--bless goes with --check (it accepts what --check just compared)")
    if args.only:
        have = studies()
        hits = [n for n in have if n == args.only.removesuffix(".py") or n.startswith(args.only + "_")]
        if len(hits) != 1:
            ap.error(f"--only {args.only}: " + ("no such study" if not hits else "ambiguous")
                     + f" (have: {', '.join(have)})")
        args.only = hits[0]

    if args.list:
        for name in studies():
            with open(os.path.join(HERE, name + ".py")) as f:
                src = f.read()
            doc = re.search(r'"""\s*(.*?)\n\s*\n', src, re.S)
            title = " ".join(doc.group(1).split()) if doc else "(no docstring)"
            title = re.sub(r"^Study \d+\s*--\s*", "", title)
            print(f"  {name:<28} {title}")
        return
    if args.docs:
        sys.exit(subprocess.call([sys.executable,
                                  os.path.join(ROOT, "tools", "lint_docs.py")]))
    if args.check:
        import regression
        ok, _ = regression.run(bless=args.bless)
        sys.exit(0 if ok else 1)

    os.makedirs(OUT, exist_ok=True)
    for name in ([args.only] if args.only else studies()):
        run(name)


if __name__ == "__main__":
    main()
