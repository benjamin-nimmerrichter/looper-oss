#!/usr/bin/env python3
"""Entry point for packaged builds (PyInstaller) and `python3 looper_oss.py`.

The desktop app itself lives in `app/`; this file exists so the frozen build and
a plain interpreter agree on one entry point.
"""
import pathlib
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from app.main import main  # noqa: PLC0415

    sys.exit(main())
