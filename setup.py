#!/usr/bin/env python3
"""Compatibility wrapper for setuptools.

Primary project metadata is defined in pyproject.toml.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("No setup command supplied.")
        print("Use one of:")
        print("  python -m pip install -e .")
        print("  python -m build")
        print("  ./setup.py --help")
        raise SystemExit(0)

    from setuptools import setup

    setup()
