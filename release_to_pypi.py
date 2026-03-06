#!/usr/bin/env python3
"""Backward-compatible wrapper for the packaged CLI."""

from pypi_release_tool.release_tool import main

if __name__ == "__main__":
    raise SystemExit(main())
