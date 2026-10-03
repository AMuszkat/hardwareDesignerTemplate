"""
Study N -- <title: the question in a few words>.

Question  <the design question, answerable with a number or a yes/no>
Decides   <what the answer will set: a SPECS row, a value-bus line, a STATUS
          decision, a handoff layout rule -- or "nothing yet, it informs Study M">
Inputs    designpoint: <the PARAMS keys this study reads>
Headline  <tag>.<metric>_<unit>, ...                    -> SPECS section <n>

Copy to studies/sNN_<name>.py (the next free number; run_all.py finds it by
that pattern), fill the header, delete what you do not need.  Inputs come from
designpoint.kw(); a value that is not there gets a PARAMS row with its source
first -- never a local constant.  Make it deterministic (fixed seeds).
"""
import designpoint as DP
from common import out, plt          # noqa: F401  (plots go to out/)


def analyse():
    """The computation, returning plain numbers.  Shared by main() and
    headline() so the printed result and the guarded result cannot differ."""
    k = DP.kw()
    raise NotImplementedError("the study's computation")


def headline():
    """The numbers SPECS quotes, keyed <tag>.<metric>_<unit>.  --check allows
    each one a millionth of its value (baseline.REL_DEFAULT).  Delete this
    function if the study produces no published number."""
    return {}


def main():
    result = analyse()
    print(result)
    print("-> <the conclusion in one line, and where it goes>")


if __name__ == "__main__":
    main()
