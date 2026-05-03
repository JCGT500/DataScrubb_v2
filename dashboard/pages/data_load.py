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

    # ─── Source toggle ────────────────────────────────────────
    sp_available = _sharepoint_available()
    options = ["Local upload"]
    if sp_available:
        options.append("SharePoint folder")
    source_mode = st.radio(
        "Source", options,
        horizontal=True,
        help=(
            "Local: drag-and-drop files from your machine. "
            "SharePoint: pull this week's files from the configured SharePoint folder. "
            "(Configure SharePoint in Admin → SharePoint.)"
        ),
    )

    if source_mode == "SharePoint folder":
        _render_sharepoint_load()
    else:
        _render_local_upload()


@st.cache_data(ttl=10, show_spinner=False)
def _sharepoint_available() -> bool:
    """Cached check (10s TTL) so we don't read YAML on every page render."""
    try:
        from datascrubb.config import load_config
        cfg = load_config()
        sp = getattr(cfg, "sharepoint", None)
        if sp is None:
            return False
        return bool(
            getattr(sp, "enabled", False)
            and getattr(sp, "tenant_id", "")
            and getattr(sp, "client_id", "")
            and getattr(sp, "site_url", "")
        )
    except Exception:
        return False


@st.cache_data(ttl=10, show_spinner=False)
def _db_status() -> dict:
    """Cached DB-source status (10s TTL)."""
    try:
        from datascrubb.config import load_config
        from datascrubb.db import get_engine
        from datascrubb.db_cache import db_source_status
        cfg = load_config()
        return db_source_status(get_engine(cfg.db_path))
    except Exception:
        return {}


def _hint(source: str, status: dict, friendly: str) -> str:
    """Render a one-line 'DB has N rows from <ts>' hint for a source."""
    info = status.get(source) or {}
    rows = int(info.get("rows", 0) or 0)
    ts = info.get("last_run_ts")
    if rows == 0:
        return f"ⓘ DB has no cached {friendly} yet."
    if ts:
        return f"ⓘ DB has {rows:,} cached {friendly} (last run: {ts[:19]})."
    return f"ⓘ DB has {rows:,} cached {friendly}."


def _render_local_upload():
    st.subheader("1. Upload Source Files")

    status = _db_status()

    st.caption(
        "All file types accept multiple files — drag/drop or click to add. "
        "**Anything you don't upload is reused from the SQLite DB** (the most "
        "recent run's data). Tick **Force fresh rebuild** below to disable that."
    )

    force_fresh = st.checkbox(
        "Force fresh rebuild (ignore cached DB data for sources you don't upload)",
        value=False,
        help="Strict from-scratch run. Sources you don't upload will produce NaN / zero "
             "downstream — useful for sanity checks but usually NOT what you want.",
    )

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**CRST Data** (Required, multiple files allowed)")
        st.caption(_hint("crst", status, "stops"))
        crst_files = st.file_uploader(
            "Upload one or more CRST Excel files",
            type=["xlsx", "xls"],
            accept_multiple_files=True,
            key="crst_upload",
        )

        st.markdown("**SAP Data** (Optional)")
        st.caption(_hint("sap", status, "SAP segments") + " — leave empty to reuse.")
        sap_files = st.file_uploader(
            "Upload one or more SAP Excel files",
            type=["xlsx", "xls"],
            accept_multiple_files=True,
            key="sap_upload",
        )

    with col2:
        st.markdown("**Telemetry Data** (Optional)")
        st.caption(_hint("telemetry", status, "stop telemetry aggregations") + " — leave empty to reuse.")
        telemetry_files = st.file_uploader(
            "Upload one or more Telemetry CSV files",
            type=["csv"],
            accept_multiple_files=True,
            key="telemetry_upload",
        )

        st.markdown("**M3PL Billing** (Optional)")
        st.caption(_hint("m3pl", status, "billing rows") + " — leave empty to reuse.")
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
        _run_pipeline(crst_files, sap_files, telemetry_files, m3pl_files, output_name,
                      reuse_cached=not force_fresh)
    elif not have_crst:
        st.info("Upload at least one CRST file to enable the pipeline.")


def _render_sharepoint_load():
    """List + pull source files from the configured SharePoint folder, then run pipeline."""
    import tempfile
    from datascrubb.config import load_config
    from datascrubb.sharepoint import GraphClient, GraphError, list_source_files, download_source_files
    from datascrubb.sharepoint.auth import SharepointAuthError, signed_in_account

    cfg = load_config()
    sp = cfg.sharepoint

    # Sign-in gate
    try:
        acct = signed_in_account(sp.tenant_id, sp.client_id)
    except Exception as e:
        st.error(f"Auth check failed: {e}")
        return
    if not acct:
        st.warning("Not signed in to SharePoint. Open **Admin → SharePoint** to sign in first.")
        return
    st.caption(f"Signed in as **{acct.get('username')}** · site: `{sp.site_url}` · folder: `{sp.source_folder}`")

    if "sp_loaded_classified" not in st.session_state:
        st.session_state["sp_loaded_classified"] = None

    if st.button("Refresh file list from SharePoint"):
        try:
            client = GraphClient(sp.tenant_id, sp.client_id, sp.site_url)
            classified = list_source_files(client, sp.source_folder)
            st.session_state["sp_loaded_classified"] = classified
        except (GraphError, SharepointAuthError) as e:
            st.error(f"Failed to list SharePoint folder: {e}")
            return

    classified = st.session_state["sp_loaded_classified"]
    if not classified:
        st.info("Click **Refresh file list from SharePoint** to discover files.")
        return

    # Show what's in the folder + per-source selection
    st.subheader("1. Files found in SharePoint")
    selected: dict[str, list[dict]] = {}
    for source in ("crst", "sap", "telemetry", "m3pl"):
        items = classified.get(source, [])
        if not items:
            st.caption(f"**{source.upper()}**: 0 files")
            continue
        names = [it["name"] for it in items]
        # Default-select all files for the source
        picks = st.multiselect(
            f"**{source.upper()}** ({len(items)} found)",
            options=names,
            default=names,
            key=f"sp_pick_{source}",
        )
        selected[source] = [it for it in items if it["name"] in picks]

    n_crst = len(selected.get("crst", []))
    if n_crst == 0:
        st.warning("Need at least one CRST file selected to run the pipeline.")

    st.markdown("---")
    st.subheader("2. Run Pipeline")
    output_name = st.text_input(
        "Output filename (optional)",
        placeholder="Trans_KPI_Validation.xlsx",
        key="sp_output_name",
    )

    if st.button("Pull selected & Run Pipeline", type="primary", disabled=(n_crst == 0)):
        try:
            client = GraphClient(sp.tenant_id, sp.client_id, sp.site_url)
            with st.spinner("Downloading files from SharePoint..."):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = Path(tmp)
                    source_files = download_source_files(client, selected, tmp_path)
                    _run_pipeline_from_paths(source_files, output_name)
        except (GraphError, SharepointAuthError) as e:
            st.error(f"SharePoint pull failed: {e}")
        except Exception as e:
            st.error(f"Pipeline failed: {e}")
            st.exception(e)


def _run_pipeline_from_paths(source_files: dict[str, list[Path]], output_name: str | None,
                              reuse_cached: bool = True) -> None:
    """Same as _run_pipeline() but accepts Paths directly (already downloaded)."""
    from datascrubb.pipeline import Pipeline

    if not source_files.get("crst"):
        st.error("Need at least one CRST file.")
        return

    progress = st.progress(40, text="Processing pipeline...")
    try:
        pipeline = Pipeline()
        result = pipeline.run(
            source_files=source_files,
            export_excel=True,
            output_filename=output_name if output_name else None,
            reuse_cached=reuse_cached,
        )
        progress.progress(100, text="Complete!")

        st.success(f"Pipeline completed! Run ID: {result['run_id']}")

        used = result.get("sources_used", {})
        def _label(metric: str, source: str) -> str:
            tag = used.get(source, "")
            return f"{metric} (reused)" if tag.startswith("cached") else metric

        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Stops", result["stops_final"])
        col2.metric(_label("Billing Rows", "m3pl"), result.get("billing_rows", 0))
        col3.metric(_label("SAP Match", "sap"), result["sap_match_rate"])
        col4.metric(_label("Telemetry", "telemetry"), result["telemetry_coverage"])
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


def _run_pipeline(crst_files, sap_files, telemetry_files, m3pl_files, output_name, reuse_cached: bool = True):
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
                    reuse_cached=reuse_cached,
                )
                progress.progress(100, text="Complete!")

                st.success(f"Pipeline completed! Run ID: {result['run_id']}")

                used = result.get("sources_used", {})
                def _label(metric: str, source: str) -> str:
                    tag = used.get(source, "")
                    return f"{metric} (reused)" if tag.startswith("cached") else metric

                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("Stops", result["stops_final"])
                col2.metric(_label("Billing Rows", "m3pl"), result.get("billing_rows", 0))
                col3.metric(_label("SAP Match", "sap"), result["sap_match_rate"])
                col4.metric(_label("Telemetry", "telemetry"), result["telemetry_coverage"])
                col5.metric("M3PL Match", result.get("m3pl_match_rate", "0%"))

                # Sources-used summary (per-source line)
                fresh = [s for s, t in used.items() if t == "fresh"]
                cached = [f"{s} ({t.split('(')[1].rstrip(')')})" for s, t in used.items() if t.startswith("cached")]
                lines = []
                if fresh:
                    lines.append(f"Fresh from upload: {', '.join(fresh)}")
                if cached:
                    lines.append(f"Reused from DB: {', '.join(cached)}")
                if lines:
                    st.caption(" · ".join(lines))

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
