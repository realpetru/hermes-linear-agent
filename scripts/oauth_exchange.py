#!/usr/bin/env python3
"""Thin wrapper kept for the documented `scripts/` path. Prefer:
    python -m linear_hermes.exchange <code>
"""
import sys

from linear_hermes.exchange import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
