#!/usr/bin/env python3
import sys

if sys.version_info < (3, 15):
    raise SystemExit(f"Tinker needs Python 3.15 or newer; this is {sys.version.split()[0]}. "
                     f"On a Mac: brew install python")

from tinkerkit import cli

if __name__ == "__main__":
    cli.main()
