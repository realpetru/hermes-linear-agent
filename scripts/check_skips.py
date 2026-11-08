#!/usr/bin/env python3
"""Fail CI if a test silently opted out of running.

The point of this gate is that a skip is a *decision*: after the suite grew a FastAPI half,
CI stayed green for weeks while thirty-six tests never executed there, and a green run hid
five real failures. So every skip has to be one of the reasons below.

Usage: python scripts/check_skips.py /path/to/tests.log

One reason is allowed on purpose: the signature test can only run where the `hermes` package
is importable, and CI deliberately does not install Hermes itself.
"""

import re
import sys

ALLOWED = {"hermes not importable here"}


def main(argv):
    if len(argv) != 2:
        print("usage: python scripts/check_skips.py <unittest -v log>")
        return 2
    with open(argv[1], encoding="utf-8", errors="replace") as fh:
        text = fh.read()

    reasons = set(re.findall(r"skipped ['\"]([^'\"]*)['\"]", text))
    unexpected = sorted(reason for reason in reasons if reason not in ALLOWED)
    if unexpected:
        print("::error::tests were skipped for a reason other than the known environmental one")
        for reason in unexpected:
            print(" - %s" % reason)
        return 1

    if reasons:
        print("no unexpected skips (allowed: %s)" % ", ".join(sorted(reasons)))
    else:
        print("no skips: the full suite ran")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
