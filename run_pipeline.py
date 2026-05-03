"""Convenience launcher: run the full pipeline against the data files
sitting in the project root.

Usage from a terminal:
    .venv/Scripts/python.exe run_pipeline.py

Or hit F5 in VS Code with the "Pipeline: ..." launch configuration.
"""

from pathlib import Path

from datascrubb.pipeline import Pipeline

ROOT = Path(__file__).resolve().parent


def _first_match(pattern: str) -> Path | None:
    matches = sorted(ROOT.glob(pattern))
    return matches[0] if matches else None


def _all_matches(pattern: str) -> list[Path]:
    return sorted(ROOT.glob(pattern))


def main() -> None:
    sources: dict = {}

    crst_files = _all_matches("CRST data*.xlsx")
    if not crst_files:
        raise SystemExit("No CRST data file found in project root (expected 'CRST data*.xlsx').")
    sources["crst"] = crst_files

    sap_files = _all_matches("SAP_*.xlsx") + _all_matches("SAP*.xlsx")
    sap_files = sorted(set(sap_files))
    if sap_files:
        sources["sap"] = sap_files

    tel_files = _all_matches("AI Troubleshooting*.csv") + _all_matches("*Troubleshooting*.csv")
    tel_files = sorted(set(tel_files))
    if tel_files:
        sources["telemetry"] = tel_files

    m3pl_files = _all_matches("Backup *M3PL*.xlsx")
    if m3pl_files:
        sources["m3pl"] = m3pl_files

    print("Sources discovered:")
    for k, v in sources.items():
        if isinstance(v, list):
            print(f"  {k}:")
            for p in v:
                print(f"    - {p.name}")
        else:
            print(f"  {k}: {v.name}")

    result = Pipeline().run(source_files=sources, export_excel=True)

    print()
    print("=" * 60)
    print(f"Run ID:             {result['run_id']}")
    print(f"Status:             {result['status']}")
    print(f"Stops:              {result['stops_final']:,}")
    print(f"Billing rows:       {result.get('billing_rows', 0):,}")
    print(f"SAP match rate:     {result['sap_match_rate']}")
    print(f"Telemetry coverage: {result['telemetry_coverage']}")
    print(f"M3PL match rate:    {result.get('m3pl_match_rate', 'n/a')}")
    print(f"Errors:             total={result['errors_total']}  hard={result['errors_hard']}  soft={result['errors_soft']}  warn={result['errors_warning']}")
    if result.get("output_path"):
        print(f"Excel output:       {result['output_path']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
