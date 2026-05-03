"""Configuration page — customer rate matrix + trailer capacity matrix."""

import io
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datascrubb.kpi.capacity import (
    DEFAULT_CAPACITY_FILE,
    load_trailer_capacity,
    save_trailer_capacity,
)
from datascrubb.kpi.revenue import (
    DEFAULT_RATES_FILE,
    load_rate_matrix,
    save_rate_matrix,
)

EXPECTED_COLS = ["customer", "rate_per_mile", "rate_per_stop", "rate_per_cwt", "minimum_charge"]
NUMERIC_COLS = ["rate_per_mile", "rate_per_stop", "rate_per_cwt", "minimum_charge"]


def _build_template() -> pd.DataFrame:
    """Empty rate-table template with the headers users should fill in."""
    return pd.DataFrame(
        [
            {"customer": "CSL", "rate_per_mile": 2.50, "rate_per_stop": 100.00, "rate_per_cwt": 0.00, "minimum_charge": 300.00},
            {"customer": "BIOLIFE", "rate_per_mile": 2.65, "rate_per_stop": 105.00, "rate_per_cwt": 0.00, "minimum_charge": 300.00},
            {"customer": "<add your customers here>", "rate_per_mile": 2.25, "rate_per_stop": 95.00, "rate_per_cwt": 0.00, "minimum_charge": 250.00},
        ],
        columns=EXPECTED_COLS,
    )


def _read_uploaded(file) -> pd.DataFrame:
    """Read a CSV or Excel upload into a DataFrame, normalising column names."""
    name = file.name.lower()
    if name.endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file)
    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.lower()
        .str.replace(r"\s+", "_", regex=True)
        .str.replace("$/", "rate_per_", regex=False)
        .str.replace("$", "", regex=False)
    )
    # Synonym mapping (be lenient with common headers)
    synonyms = {
        "name": "customer",
        "client": "customer",
        "account": "customer",
        "rate_mile": "rate_per_mile",
        "per_mile": "rate_per_mile",
        "mile_rate": "rate_per_mile",
        "rate_stop": "rate_per_stop",
        "per_stop": "rate_per_stop",
        "stop_rate": "rate_per_stop",
        "rate_cwt": "rate_per_cwt",
        "per_cwt": "rate_per_cwt",
        "cwt_rate": "rate_per_cwt",
        "min_charge": "minimum_charge",
        "minimum": "minimum_charge",
        "min": "minimum_charge",
    }
    df = df.rename(columns={k: v for k, v in synonyms.items() if k in df.columns})
    return df


def _validate(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Return (cleaned, warnings, errors)."""
    warnings: list[str] = []
    errors: list[str] = []

    if df is None or df.empty:
        errors.append("Uploaded file is empty.")
        return df, warnings, errors

    if "customer" not in df.columns:
        errors.append("Required column `customer` is missing.")
        return df, warnings, errors

    # Add any missing rate columns as 0
    for col in NUMERIC_COLS:
        if col not in df.columns:
            warnings.append(f"Column `{col}` not found — defaulting to 0 for all rows.")
            df[col] = 0.0

    cleaned = df.copy()
    cleaned["customer"] = cleaned["customer"].astype(str).str.strip().str.upper()
    cleaned = cleaned[cleaned["customer"].notna() & (cleaned["customer"] != "") & (cleaned["customer"] != "NAN")]

    for col in NUMERIC_COLS:
        cleaned[col] = pd.to_numeric(cleaned[col], errors="coerce").fillna(0.0)

    # Drop placeholder rows
    cleaned = cleaned[~cleaned["customer"].str.contains("ADD YOUR CUSTOMER", case=False, na=False)]

    dupes = cleaned[cleaned["customer"].duplicated(keep=False)]
    if not dupes.empty:
        warnings.append(
            f"{dupes['customer'].nunique()} duplicate customer name(s) detected — "
            "later row will win on save: " + ", ".join(sorted(dupes['customer'].unique())[:5])
        )

    if cleaned.empty:
        errors.append("No usable rows after cleaning (empty customer names or all placeholders).")

    return cleaned[EXPECTED_COLS], warnings, errors


def render():
    st.header("Configuration")
    st.caption(
        "Configure the customer rate matrix (drives Revenue / Margin) and "
        "trailer capacity matrix (drives Fill % calculations)."
    )

    rates_tab, capacity_tab = st.tabs(["Customer Rates", "Trailer Capacity"])

    with rates_tab:
        _render_rate_matrix()

    with capacity_tab:
        _render_capacity_matrix()


def _render_rate_matrix():
    st.subheader("Customer Rate Matrix")
    st.caption(
        "What we **charge customers** per mile / stop / hundredweight. "
        "Drives the Revenue / Margin numbers on the Cost vs Revenue page."
    )
    st.caption(f"Stored at: `{DEFAULT_RATES_FILE}`")

    matrix = load_rate_matrix()

    # ───────────────────── Upload section ─────────────────────
    st.subheader("Upload your current rate table")
    st.caption(
        "CSV or Excel file. Required column: `customer`. Optional: "
        "`rate_per_mile`, `rate_per_stop`, `rate_per_cwt`, `minimum_charge`. "
        "Common header variants (e.g. `Per Mile`, `$/Stop`, `Min Charge`) are recognised."
    )

    ucol1, ucol2 = st.columns([3, 1])
    with ucol1:
        upload = st.file_uploader(
            "Choose a CSV or Excel file",
            type=["csv", "xlsx", "xls"],
            key="rate_matrix_upload",
        )
    with ucol2:
        # Template download
        tmpl = _build_template()
        csv_bytes = tmpl.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download CSV template",
            data=csv_bytes,
            file_name="customer_rates_template.csv",
            mime="text/csv",
            help="Empty template with the column headers we expect.",
        )
        xl_buf = io.BytesIO()
        with pd.ExcelWriter(xl_buf, engine="openpyxl") as w:
            tmpl.to_excel(w, sheet_name="Rates", index=False)
        st.download_button(
            "Download Excel template",
            data=xl_buf.getvalue(),
            file_name="customer_rates_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    if upload is not None:
        try:
            raw = _read_uploaded(upload)
        except Exception as e:
            st.error(f"Could not read file: {e}")
            return

        cleaned, warnings, errors = _validate(raw)

        if errors:
            for e in errors:
                st.error(e)
            with st.expander("Raw upload preview (first 20 rows)"):
                st.dataframe(raw.head(20), use_container_width=True)
            return

        for w in warnings:
            st.warning(w)

        st.markdown("**Preview** — review before saving:")
        st.dataframe(cleaned, use_container_width=True, height=300)

        m1, m2 = st.columns(2)
        new_rows = len(cleaned)
        existing = set((matrix.get("customers") or {}).keys())
        added = sum(1 for c in cleaned["customer"] if c not in existing)
        updated = new_rows - added
        m1.metric("Rows in upload", f"{new_rows:,}")
        m2.metric("Existing / new", f"{updated} updated · {added} new")

        save_mode = st.radio(
            "Save mode",
            ["Replace entire customer list", "Merge into existing list (upload wins on duplicates)"],
            horizontal=False,
            key="rate_save_mode",
        )

        if st.button("Apply upload to rate matrix", type="primary", key="rate_apply_btn"):
            new_customers: dict = {}
            if save_mode.startswith("Merge"):
                # Start from existing
                new_customers = {
                    k: dict(v) for k, v in (matrix.get("customers") or {}).items()
                }
            for _, r in cleaned.iterrows():
                cust = str(r["customer"]).strip().upper()
                if not cust:
                    continue
                entry: dict = {}
                for col in NUMERIC_COLS:
                    v = float(r[col])
                    if v != 0:
                        entry[col] = v
                new_customers[cust] = entry
            new_matrix = {
                "default": matrix.get("default", {}) or {},
                "customers": new_customers,
            }
            save_rate_matrix(new_matrix)
            st.success(
                f"Saved {len(new_customers)} customer rates. Re-run the pipeline "
                "(Load Data → Run) to recompute revenue and margin."
            )
            st.rerun()

    st.markdown("---")

    # ───────────────────── Default rates editor ─────────────────────
    st.subheader("Default rates (used when no customer-specific entry exists)")
    default = matrix.get("default", {}) or {}
    dcols = st.columns(4)
    rate_per_mile = dcols[0].number_input(
        "$/mile (default)", value=float(default.get("rate_per_mile", 2.25) or 0), step=0.05, format="%.2f",
    )
    rate_per_stop = dcols[1].number_input(
        "$/stop (default)", value=float(default.get("rate_per_stop", 95.0) or 0), step=1.00, format="%.2f",
    )
    rate_per_cwt = dcols[2].number_input(
        "$/cwt (default)", value=float(default.get("rate_per_cwt", 0) or 0), step=0.05, format="%.2f",
    )
    minimum_charge = dcols[3].number_input(
        "Min charge $ (default)", value=float(default.get("minimum_charge", 250.0) or 0), step=10.00, format="%.2f",
    )

    # ───────────────────── Per-customer table ─────────────────────
    st.subheader("Per-customer rates")
    st.caption("Customer name (UPPERCASED automatically) → rate fields. Add or remove rows directly.")

    custs = matrix.get("customers", {}) or {}
    if custs:
        rows = [
            {
                "customer": k,
                "rate_per_mile": v.get("rate_per_mile", 0) or 0,
                "rate_per_stop": v.get("rate_per_stop", 0) or 0,
                "rate_per_cwt": v.get("rate_per_cwt", 0) or 0,
                "minimum_charge": v.get("minimum_charge", 0) or 0,
            }
            for k, v in custs.items()
        ]
    else:
        rows = []
    df = pd.DataFrame(rows, columns=EXPECTED_COLS)

    edited = st.data_editor(
        df, num_rows="dynamic", use_container_width=True,
        column_config={
            "customer": st.column_config.TextColumn("Customer", required=True),
            "rate_per_mile": st.column_config.NumberColumn("$/mile", format="%.2f"),
            "rate_per_stop": st.column_config.NumberColumn("$/stop", format="%.2f"),
            "rate_per_cwt": st.column_config.NumberColumn("$/cwt", format="%.2f"),
            "minimum_charge": st.column_config.NumberColumn("Min $", format="%.2f"),
        },
        key="rate_matrix_editor",
    )

    sc1, sc2, sc3 = st.columns([1, 1, 3])
    with sc1:
        if st.button("Save changes", type="primary", key="rate_save_inline"):
            new_customers: dict = {}
            for _, r in edited.iterrows():
                cust = str(r.get("customer", "")).strip().upper()
                if not cust:
                    continue
                entry: dict = {}
                for k in NUMERIC_COLS:
                    v = r.get(k)
                    if v is not None and pd.notna(v) and float(v) != 0:
                        entry[k] = float(v)
                new_customers[cust] = entry
            new_matrix = {
                "default": {
                    "rate_per_mile": float(rate_per_mile),
                    "rate_per_stop": float(rate_per_stop),
                    "rate_per_cwt": float(rate_per_cwt),
                    "minimum_charge": float(minimum_charge),
                },
                "customers": new_customers,
            }
            save_rate_matrix(new_matrix)
            st.success("Rate matrix saved. Re-run the pipeline to recompute revenue.")
    with sc2:
        # Export current matrix as CSV
        export_rows = [
            {
                "customer": k,
                **{col: v.get(col, 0) for col in NUMERIC_COLS},
            }
            for k, v in custs.items()
        ]
        export_df = pd.DataFrame(export_rows, columns=EXPECTED_COLS)
        st.download_button(
            "Export current matrix (CSV)",
            data=export_df.to_csv(index=False).encode("utf-8"),
            file_name="customer_rates_current.csv",
            mime="text/csv",
        )
    with sc3:
        st.caption(
            "Saving updates `config/customer_rates.yaml`. Revenue is recomputed "
            "the next time the pipeline runs (Load Data → Run Pipeline)."
        )


def _render_capacity_matrix():
    st.subheader("Trailer Capacity Matrix")
    st.caption(
        "Max cases / max weight (lbs) per trailer. Drives the `fill_pct_*` columns "
        "on Stop Explorer and the **Trailer Fill % to Capacity** section on the "
        "Trailer Utilization page."
    )
    st.caption(
        "**Capacity priority:** explicit config row → observed 95th-percentile of "
        "historical loads (when ≥ 5 stops) → default."
    )
    st.caption(f"Stored at: `{DEFAULT_CAPACITY_FILE}`")

    matrix = load_trailer_capacity()

    # ───── Default ─────
    st.markdown("**Default capacity** (used when no per-trailer override + insufficient history)")
    default = matrix.get("default", {}) or {}
    dc1, dc2 = st.columns(2)
    default_max_cases = dc1.number_input(
        "Default max cases",
        value=float(default.get("max_cases", 800) or 800), step=50.0, format="%.0f",
        key="cap_default_cases",
    )
    default_max_weight = dc2.number_input(
        "Default max weight (lbs)",
        value=float(default.get("max_weight_lbs", 44000) or 44000), step=500.0, format="%.0f",
        key="cap_default_weight",
    )

    # ───── Per-trailer ─────
    st.markdown("**Per-trailer overrides** (UPPERCASE trailer IDs match `stop_master.trailer`)")
    trailers = matrix.get("trailers", {}) or {}
    rows = [
        {
            "trailer": k,
            "max_cases": v.get("max_cases", 0) or 0,
            "max_weight_lbs": v.get("max_weight_lbs", 0) or 0,
        }
        for k, v in trailers.items()
    ]
    df = pd.DataFrame(rows, columns=["trailer", "max_cases", "max_weight_lbs"])
    edited = st.data_editor(
        df, num_rows="dynamic", use_container_width=True,
        column_config={
            "trailer": st.column_config.TextColumn("Trailer", required=True),
            "max_cases": st.column_config.NumberColumn("Max cases", format="%.0f"),
            "max_weight_lbs": st.column_config.NumberColumn("Max weight (lbs)", format="%.0f"),
        },
        key="capacity_matrix_editor",
    )

    cc1, cc2, cc3 = st.columns([1, 1, 3])
    with cc1:
        if st.button("Save capacity changes", type="primary", key="cap_save_inline"):
            new_trailers: dict = {}
            for _, r in edited.iterrows():
                t = str(r.get("trailer", "")).strip().upper()
                if not t:
                    continue
                entry: dict = {}
                for k in ("max_cases", "max_weight_lbs"):
                    v = r.get(k)
                    if v is not None and pd.notna(v) and float(v) != 0:
                        entry[k] = float(v)
                new_trailers[t] = entry
            new_matrix = {
                "default": {
                    "max_cases": float(default_max_cases),
                    "max_weight_lbs": float(default_max_weight),
                },
                "trailers": new_trailers,
            }
            save_trailer_capacity(new_matrix)
            st.success("Capacity matrix saved. Re-run the pipeline to recompute fill %.")
    with cc2:
        export_rows = [
            {"trailer": k, "max_cases": v.get("max_cases", 0), "max_weight_lbs": v.get("max_weight_lbs", 0)}
            for k, v in trailers.items()
        ]
        export_df = pd.DataFrame(export_rows, columns=["trailer", "max_cases", "max_weight_lbs"])
        st.download_button(
            "Export capacity (CSV)",
            data=export_df.to_csv(index=False).encode("utf-8"),
            file_name="trailer_capacity_current.csv",
            mime="text/csv",
        )
    with cc3:
        st.caption(
            "Saving updates `config/trailer_capacity.yaml`. Fill % is recomputed "
            "the next time the pipeline runs."
        )
