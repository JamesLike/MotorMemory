#!/usr/bin/env python3
"""
MotorMemory - save and restore named motor positions.

Usage:
    motormemory.py [--file PRESETS.json] [--hutch MFX]

Columns are EPICS motor records, rows are named positions.  See README.md.
"""

from __future__ import annotations

import argparse
import os
import sys

# Allow running from any cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

import pv_backend  # noqa: E402
from main_window import MainWindow  # noqa: E402
from presets_model import DEFAULT_FILE, PresetStore  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "-f",
        "--file",
        default=os.environ.get("MOTORMEMORY_FILE", DEFAULT_FILE),
        help=f"presets JSON file (default: $MOTORMEMORY_FILE or {DEFAULT_FILE})",
    )
    ap.add_argument("--style", default="Windows", help="Qt style (default: Windows, as camviewer)")
    ap.add_argument(
        "--hutch",
        default=os.environ.get("MOTORMEMORY_HUTCH"),
        help="instrument for eLog posting, e.g. MFX (default: $MOTORMEMORY_HUTCH, else derived from the motor PV prefix)",
    )
    args = ap.parse_args(argv)

    app = QApplication(sys.argv[:1])
    if args.style:
        app.setStyle(args.style)

    if pv_backend.BACKEND is None:
        QMessageBox.warning(
            None,
            "No EPICS library",
            "Neither pyca/psp nor pyepics could be imported.\nThe GUI will run but nothing will connect.",
        )

    store = PresetStore(args.file)
    try:
        store.load()
    except Exception as exc:
        QMessageBox.critical(None, "Load failed", f"Could not read {args.file}:\n{exc}\n\nStarting empty.")
        store.tables = []

    win = MainWindow(store, hutch=args.hutch)
    win.show()
    rc = app.exec_()
    return rc


if __name__ == "__main__":
    sys.exit(main())
