#!/usr/bin/env python3
"""🔧 Tinker — your friendly project builder. Tinker itself is tinkerkit/cli.py; this file only makes sure the
Python running it is new enough to read it.

It keeps to syntax that Python 3.9 can read (no lazy import, no match): Python reads a whole file before it runs
a line of it, so newer syntax here would fail before the check below could explain why."""

import sys

if sys.version_info < (3, 15):
    raise SystemExit(f"Tinker needs Python 3.15 or newer; this is {sys.version.split()[0]}. "
                     f"On a Mac: brew install python")

from tinkerkit import cli


def __getattr__(name):  # the tests still import Tinker's names from here, until they import them from tinkerkit.cli
    return getattr(cli, name)


if __name__ == "__main__":
    cli.main()
