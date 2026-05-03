"""Data Load page — upload files and trigger the pipeline."""

import sys
from pathlib import Path

import streamlit as st

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def render():
    st.header("Load Data")
    st.markdown("Upload your data files and run the pipeline to process, match, and validate.")

    st.subheader("1. Upload Source Files")

    st.caption("All file types accept multiple files — drag/drop or click to add. Files of the same type are concatenated; duplicate stops are de-duped by transaction_id.")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**CRST Data** (Required, multiple files allowed)")
        crst_files = st.file_uploader(
            "Upload one or more CRST Excel files",
            type=["xlsx", "xls"],
            accept_multiple_files=True,
            key="crst_upload",
        )

        st.markdown("**SAP Data** (Optional, multiple files allowed)")
        sap_files = st.file_uploader(
            "Upload one or more SAP Excel files",
            type=["xlsx", "xls"],
            accept_multiple_files=True,
            key="sap_upload",
        )

    with col2:
        st.markdown("**Telemetry Data** (Optional, multiple files allowed)")
        telemetry_files = st.file_uploader(
            "Upload one or more Telemetry CSV files",
            type=["csv"],
            accept_multiple_files=True,
            key="telemetry_upload",
        )

        st.markdown("**M3PL Billing** (Optional, multiple weekly files allowed)")
        m3pl_files = st.file_uploader(
            "Upload one or more M3PL Excel invoice files",
            type=["xlsx", "xls"],
            accept_multiple_files=True,
            key="m3pl_upload",
        )

    st.markdown("---")
    st.subheader("2. Run Pipeline")

    output_name = st.text_input(
        "Output filename (optional)",
        placeholder="Trans_KPI_Validation.xlsx",
    )

    have_crst = bool(crst_files)
    if st.button("Run Pipeline", type="primary", disabled=not have_crst):
        _run_pipeline(crst_files, sap_files, telemetry_files, m3pl_files, output_name)
    elif not have_crst:
        st.info("Upload at least one CRST file to enable the pipeline.")


def _run_pipeline(crst_files, sap_files, telemetry_files, m3pl_files, output_name):
    """Save uploaded files (one or many per source) to temp dir, then run the pipeline."""
    import tempfile

    from datascrubb.pipeline import Pipeline

    def _save_many(file_list, tmpdir):
        """Write Streamlit UploadedFile objects to disk; return list of Paths."""
        paths = []
        for f in file_list or []:
            p = tmpdir / f.name
            p.write_bytes(f.getvalue())
            paths.append(p)
        return paths

    with st.spinner("Running pipeline..."):
        progress = st.progress(0, text="Saving uploaded files...")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            source_files: dict = {}

            crst_paths = _save_many(crst_files, tmpdir)
            if not crst_paths:
                st.error("Need at least one CRST file.")
                return
            source_files["crst"] = crst_paths
            progress.progress(10, text=f"CRST files saved ({len(crst_paths)})")

            sap_paths = _save_many(sap_files, tmpdir)
            if sap_paths:
                source_files["sap"] = sap_paths
                progress.progress(20, text=f"SAP files saved ({len(sap_paths)})")

            tel_paths = _save_many(telemetry_files, tmpdir)
            if tel_paths:
                source_files["telemetry"] = tel_paths
                progress.progress(30, text=f"Telemetry files saved ({len(tel_paths)})")

            m3pl_paths = _save_many(m3pl_files, tmpdir)
            if m3pl_paths:
                source_files["m3pl"] = m3pl_paths
                progress.progress(35, text=f"M3PL files saved ({len(m3pl_paths)})")

            progress.progress(40, text="Processing pipeline...")

            try:
                pipeline = Pipeline()
                result = pipeline.run(
                    source_files=source_files,
                    export_excel=True,
                    output_filename=output_name if output_name else None,
                )
                progress.progress(100, text="Complete!")

                st.success(f"Pipeline completed! Run ID: {result['run_id']}")

                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("Stops", result["stops_final"])
                col2.metric("Billing Rows", result.get("billing_rows", 0))
                col3.metric("SAP Match", result["sap_match_rate"])
                col4.metric("Telemetry", result["telemetry_coverage"])
                col5.metric("M3PL Match", result.get("m3pl_match_rate", "0%"))

                if result.get("output_path"):
                    st.info(f"Excel output: `{result['output_path']}`")

                if result["errors_total"] > 0:
                    st.subheader("Error Summary")
                    ecol1, ecol2, ecol3 = st.columns(3)
                    ecol1.metric("Hard Errors", result["errors_hard"])
                    ecol2.metric("Soft Errors", result["errors_soft"])
                    ecol3.metric("Warnings", result["errors_warning"])
                    if result["errors_hard"] > 0:
                        st.error("Hard errors detected! Review the Validation Report page.")

            except Exception as e:
                progress.progress(100, text="Failed")
                st.error(f"Pipeline failed: {e}")
                st.exception(e)
