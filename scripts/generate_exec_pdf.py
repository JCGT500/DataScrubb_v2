"""CLI entry point: generate the executive PDF from the current SQLite DB.

Usage:
    .venv/Scripts/python.exe scripts/generate_exec_pdf.py [output_path]

If no path is given, writes to output/Exec_Report_<YYYYMMDD_HHMMSS>.pdf.
"""

import sys
from pathlib import Path

from datascrubb.export.pdf import generate_executive_pdf


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else None
    path = generate_executive_pdf(out)
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
