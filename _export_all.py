#!/usr/bin/env python
"""Dump ALL Vistas data to fast, portable files under ./export.

  export/parquet/  — load instantly in pandas (and R / DuckDB / Power BI / Excel Power Query)
  export/excel/    — multi-sheet workbooks for hand analysis
  export/README.txt, manifest.json

Run:  python _export_all.py    (or double-click "Export All Data.bat")
"""
from __future__ import annotations
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    from vistas import exporter
    exporter.build_all()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
