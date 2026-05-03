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

    # ───────────────────── Per-customer FLAT-rate table ─────────────────────
    st.subheader("Per-customer rates (flat pricing)")
    st.caption(
        "Customers using the simple `miles*$/mi + stops*$/stop + (lbs/100)*$/cwt` formula. "
        "Banded customers are managed in the next section."
    )

    custs = matrix.get("customers", {}) or {}
    flat_custs = {k: v for k, v in custs.items() if (v or {}).get("pricing_model", "flat") == "flat"}
    banded_custs = {k: v for k, v in custs.items() if (v or {}).get("pricing_model") == "banded"}

    if flat_custs:
        rows = [
            {
                "customer": k,
                "rate_per_mile": v.get("rate_per_mile", 0) or 0,
                "rate_per_stop": v.get("rate_per_stop", 0) or 0,
                "rate_per_cwt": v.get("rate_per_cwt", 0) or 0,
                "minimum_charge": v.get("minimum_charge", 0) or 0,
            }
            for k, v in flat_custs.items()
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

    # ───────────────────── Banded customers ─────────────────────
    st.markdown("---")
    st.subheader(f"Per-customer rates (banded pricing) — {len(banded_custs)} customer(s)")
    st.caption(
        "Banded customers price by a 2D rate matrix indexed by mile band × weight band. "
        "Each cell is the **total dollars for the route**. Bands are upper-bound inclusive; "
        "routes above the largest band clamp to the last row/column. "
        "Use the **Banded Rate Import Wizard** below to add a new banded customer from an Excel sheet."
    )

    edited_banded: dict[str, dict] = {}
    for cust_name, cust_data in banded_custs.items():
        with st.expander(f"📊 {cust_name}", expanded=False):
            edited_banded[cust_name] = _render_banded_customer_editor(cust_name, cust_data)

    # Add-new-banded-customer (manual entry) ──
    with st.expander("➕ Add new banded customer (manual entry)", expanded=False):
        new_name = st.text_input("Customer name", key="new_banded_cust_name").strip().upper()
        if new_name and new_name not in banded_custs and st.button("Create blank banded entry", key="new_banded_create"):
            blank = {
                "pricing_model": "banded",
                "mile_bands": [50, 150, 300, 500],
                "weight_bands": [500, 1000, 2000, 5000],
                "rate_matrix": [[0.0] * 5 for _ in range(5)],
                "rate_per_stop": 0.0,
                "minimum_charge": 0.0,
            }
            full_custs = {**custs, new_name: blank}
            save_rate_matrix({"default": matrix.get("default", {}) or {}, "customers": full_custs})
            st.success(f"Created blank banded entry for **{new_name}**. Edit the bands above.")
            st.rerun()

    # ───────────────────── Save / Export ─────────────────────
    st.markdown("---")
    sc1, sc2, sc3 = st.columns([1, 1, 3])
    with sc1:
        if st.button("Save all rates", type="primary", key="rate_save_inline"):
            new_customers: dict = {}
            # Flat customers from the data_editor
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
            # Banded customers from the per-expander editors
            for cust, banded_entry in edited_banded.items():
                new_customers[cust] = banded_entry
            new_matrix = {
                "default": {
                    "pricing_model": "flat",
                    "rate_per_mile": float(rate_per_mile),
                    "rate_per_stop": float(rate_per_stop),
                    "rate_per_cwt": float(rate_per_cwt),
                    "minimum_charge": float(minimum_charge),
                },
                "customers": new_customers,
            }
            save_rate_matrix(new_matrix)
            st.success(
                f"Saved {len(new_customers)} customer rates "
                f"({sum(1 for v in new_customers.values() if v.get('pricing_model') == 'banded')} banded). "
                "Re-run the pipeline to recompute revenue."
            )
    with sc2:
        # Export current matrix as CSV (flat customers only — banded export is per-customer XLSX)
        export_rows = [
            {
                "customer": k,
                **{col: v.get(col, 0) for col in NUMERIC_COLS},
            }
            for k, v in flat_custs.items()
        ]
        export_df = pd.DataFrame(export_rows, columns=EXPECTED_COLS)
        st.download_button(
            "Export flat matrix (CSV)",
            data=export_df.to_csv(index=False).encode("utf-8"),
            file_name="customer_rates_flat.csv",
            mime="text/csv",
        )
    with sc3:
        st.caption(
            "Saving updates `config/customer_rates.yaml`. Revenue is recomputed "
            "the next time the pipeline runs (Load Data → Run Pipeline)."
        )

    # ───────────────────── Banded Rate Import Wizard ─────────────────────
    st.markdown("---")
    with st.expander("🧙 Banded Rate Import Wizard — upload an Excel rate sheet", expanded=False):
        _render_banded_import_wizard(matrix)


def _render_banded_customer_editor(cust_name: str, cust_data: dict) -> dict:
    """One expander's worth of UI for editing a single banded customer.
    Returns the edited dict (to be merged into the YAML on save).
    """
    mile_bands = list(cust_data.get("mile_bands", [50, 150, 300, 500]) or [50, 150, 300, 500])
    weight_bands = list(cust_data.get("weight_bands", [500, 1000, 2000, 5000]) or [500, 1000, 2000, 5000])
    rate_matrix = cust_data.get("rate_matrix") or [[0.0] * (len(weight_bands) + 1) for _ in range(len(mile_bands) + 1)]

    # Mile bands (comma-separated for easy edit)
    c1, c2 = st.columns(2)
    mb_str = c1.text_input(
        "Mile band upper bounds (comma-separated, ascending)",
        value=", ".join(str(int(b) if float(b).is_integer() else b) for b in mile_bands),
        key=f"mb_{cust_name}",
    )
    wb_str = c2.text_input(
        "Weight band upper bounds (lbs, comma-separated, ascending)",
        value=", ".join(str(int(b) if float(b).is_integer() else b) for b in weight_bands),
        key=f"wb_{cust_name}",
    )

    try:
        new_mile_bands = [float(x.strip()) for x in mb_str.split(",") if x.strip()]
        new_weight_bands = [float(x.strip()) for x in wb_str.split(",") if x.strip()]
    except ValueError:
        st.error("Bands must be numbers separated by commas.")
        return cust_data

    # Resize matrix if shape changed
    expected_rows = len(new_mile_bands) + 1
    expected_cols = len(new_weight_bands) + 1
    cur_rows = len(rate_matrix)
    cur_cols = len(rate_matrix[0]) if rate_matrix else 0
    if cur_rows != expected_rows or cur_cols != expected_cols:
        new_rm = [[0.0] * expected_cols for _ in range(expected_rows)]
        for r in range(min(cur_rows, expected_rows)):
            for c in range(min(cur_cols, expected_cols)):
                new_rm[r][c] = rate_matrix[r][c]
        rate_matrix = new_rm
        st.info(f"Matrix resized to {expected_rows}×{expected_cols} to match band counts.")

    # Build a labeled DataFrame for editing
    mile_labels = [f"≤{int(b) if float(b).is_integer() else b}" for b in new_mile_bands] + [f">{int(new_mile_bands[-1]) if new_mile_bands and float(new_mile_bands[-1]).is_integer() else (new_mile_bands[-1] if new_mile_bands else 0)}"]
    weight_labels = [f"≤{int(b) if float(b).is_integer() else b}" for b in new_weight_bands] + [f">{int(new_weight_bands[-1]) if new_weight_bands and float(new_weight_bands[-1]).is_integer() else (new_weight_bands[-1] if new_weight_bands else 0)}"]

    rm_df = pd.DataFrame(rate_matrix, index=mile_labels, columns=weight_labels)
    rm_df.index.name = "miles \\ weight"

    st.caption("Edit cells (in $). Rows = mile bands, columns = weight bands. Upper-bound inclusive.")
    edited_df = st.data_editor(
        rm_df,
        use_container_width=True,
        column_config={c: st.column_config.NumberColumn(c, format="$%.2f", min_value=0) for c in weight_labels},
        key=f"rm_{cust_name}",
    )

    # Optional add-ons
    a1, a2 = st.columns(2)
    rate_per_stop = a1.number_input(
        "Per-stop add-on $/stop", value=float(cust_data.get("rate_per_stop", 0) or 0),
        step=1.0, format="%.2f", key=f"rps_{cust_name}",
    )
    minimum_charge = a2.number_input(
        "Minimum charge $", value=float(cust_data.get("minimum_charge", 0) or 0),
        step=10.0, format="%.2f", key=f"min_{cust_name}",
    )

    if st.button(f"🗑️ Delete {cust_name}", key=f"del_{cust_name}"):
        # Mark for deletion via session_state — handled by Save All
        st.session_state.setdefault("_banded_to_delete", set()).add(cust_name)
        st.warning(f"{cust_name} marked for deletion. Click **Save all rates** to apply.")

    return {
        "pricing_model": "banded",
        "mile_bands": new_mile_bands,
        "weight_bands": new_weight_bands,
        "rate_matrix": [[float(x) for x in row] for row in edited_df.values.tolist()],
        "rate_per_stop": float(rate_per_stop),
        "minimum_charge": float(minimum_charge),
    }


def _render_banded_import_wizard(matrix: dict) -> None:
    """Interactive wizard: upload Excel → pick sheet → map layout → confirm → save."""
    st.caption(
        "Upload an Excel rate sheet of any layout — the wizard walks you through "
        "mapping it to the canonical bands × matrix structure. Mappings are saved per "
        "workbook so the next upload of the same shape is one click."
    )

    upload = st.file_uploader(
        "Upload rate sheet (Excel)", type=["xlsx", "xls"], key="banded_wizard_upload",
    )
    if upload is None:
        return

    # Step 1: Read all sheets
    try:
        xls = pd.ExcelFile(upload)
    except Exception as e:
        st.error(f"Could not open workbook: {e}")
        return

    st.markdown("**Step 1.** Pick the sheet that holds the rate matrix.")
    sheet_name = st.selectbox("Sheet", xls.sheet_names, key="bw_sheet")

    raw = pd.read_excel(upload, sheet_name=sheet_name, header=None)
    st.caption(f"Preview ({raw.shape[0]} rows × {raw.shape[1]} cols):")
    st.dataframe(raw.head(20), use_container_width=True, height=300)

    # Step 2: Layout mapping
    st.markdown("**Step 2.** Tell the wizard where to find the bands and the dollar matrix.")
    l1, l2, l3, l4 = st.columns(4)
    weight_row = l1.number_input("Row with weight bands (0-indexed)", min_value=0, max_value=max(raw.shape[0] - 1, 0), value=0, key="bw_wrow")
    mile_col = l2.number_input("Col with mile bands (0-indexed)", min_value=0, max_value=max(raw.shape[1] - 1, 0), value=0, key="bw_mcol")
    matrix_first_row = l3.number_input("First matrix data row", min_value=0, max_value=max(raw.shape[0] - 1, 0), value=1, key="bw_frow")
    matrix_first_col = l4.number_input("First matrix data col", min_value=0, max_value=max(raw.shape[1] - 1, 0), value=1, key="bw_fcol")

    # Step 3: Customer attribution
    st.markdown("**Step 3.** Which customer does this matrix apply to?")
    attr_mode = st.radio(
        "Customer attribution",
        ["Use this sheet name as the customer", "Type a customer name"],
        key="bw_attr_mode",
        horizontal=True,
    )
    if attr_mode == "Type a customer name":
        cust_name = st.text_input("Customer name", value="", key="bw_cust_name").strip().upper()
    else:
        cust_name = sheet_name.strip().upper()

    if not cust_name:
        st.info("Provide a customer name to continue.")
        return

    # Step 4: Parse + preview
    st.markdown("**Step 4.** Preview the parsed bands & matrix.")
    try:
        weight_bands_raw = raw.iloc[weight_row, matrix_first_col:].tolist()
        mile_bands_raw = raw.iloc[matrix_first_row:, mile_col].tolist()
        matrix_raw = raw.iloc[matrix_first_row:, matrix_first_col:].values.tolist()

        # Parse band labels — accept "0-50", "≤50", "<50", "50", or just "50"
        weight_bands = _parse_band_labels(weight_bands_raw)
        mile_bands = _parse_band_labels(mile_bands_raw)

        # Coerce matrix cells to floats (drop trailing all-NaN rows/cols)
        rate_matrix = []
        for row in matrix_raw:
            parsed_row = []
            for cell in row:
                try:
                    parsed_row.append(float(cell))
                except (TypeError, ValueError):
                    parsed_row.append(0.0)
            rate_matrix.append(parsed_row)

        # If the last row / col is all zeros (likely empty trailing band slot), trim
        while rate_matrix and all(v == 0 for v in rate_matrix[-1]):
            rate_matrix.pop()
        while rate_matrix and all(row[-1] == 0 for row in rate_matrix):
            for row in rate_matrix:
                row.pop()

        # Trim bands to match the matrix shape (last band = "and up", so bands has one less than rows/cols)
        n_rows = len(rate_matrix)
        n_cols = len(rate_matrix[0]) if rate_matrix else 0
        mile_bands = mile_bands[:max(n_rows - 1, 0)]
        weight_bands = weight_bands[:max(n_cols - 1, 0)]

        st.write(f"**Parsed for {cust_name}:**")
        st.write(f"- Mile bands: `{mile_bands}` → matrix has {n_rows} rows")
        st.write(f"- Weight bands: `{weight_bands}` → matrix has {n_cols} cols")
        preview_idx = [f"≤{b}" for b in mile_bands] + ([f">{mile_bands[-1]}"] if mile_bands else [])
        preview_cols = [f"≤{b}" for b in weight_bands] + ([f">{weight_bands[-1]}"] if weight_bands else [])
        if len(preview_idx) == n_rows and len(preview_cols) == n_cols:
            st.dataframe(pd.DataFrame(rate_matrix, index=preview_idx, columns=preview_cols), use_container_width=True)
        else:
            st.warning(
                f"Band count vs matrix shape mismatch: bands suggest {len(preview_idx)}×{len(preview_cols)} "
                f"but matrix is {n_rows}×{n_cols}. Adjust the row/col mapping above."
            )
            st.dataframe(pd.DataFrame(rate_matrix), use_container_width=True)
    except Exception as e:
        st.error(f"Parse failed: {e}")
        return

    # Step 5: Confirm + save
    st.markdown("**Step 5.** Save to `customer_rates.yaml`.")
    if st.button(f"💾 Add {cust_name} as banded customer", key="bw_save", type="primary"):
        if not mile_bands or not weight_bands or not rate_matrix:
            st.error("Cannot save — bands or matrix is empty. Adjust the mapping above.")
            return
        new_entry = {
            "pricing_model": "banded",
            "mile_bands": mile_bands,
            "weight_bands": weight_bands,
            "rate_matrix": rate_matrix,
            "rate_per_stop": 0.0,
            "minimum_charge": 0.0,
        }
        cur_custs = matrix.get("customers", {}) or {}
        cur_custs[cust_name] = new_entry
        save_rate_matrix({"default": matrix.get("default", {}) or {}, "customers": cur_custs})
        st.success(f"Added **{cust_name}** as a banded customer. Re-run the pipeline to apply.")
        st.balloons()


def _parse_band_labels(raw_labels: list) -> list[float]:
    """Best-effort parse of band labels into upper-bound numbers.

    Accepts: 50, "50", "0-50", "≤50", "<=50", "<50", "Up to 50", "50 lbs", etc.
    Returns the upper bounds (sorted ascending). Drops anything non-numeric.
    """
    import re
    out: list[float] = []
    for label in raw_labels:
        if label is None or pd.isna(label):
            continue
        s = str(label).strip()
        if not s:
            continue
        # Match a range "a-b" → take b; otherwise grab the largest number in the string
        m = re.match(r"^\s*\d+\.?\d*\s*[-–]\s*(\d+\.?\d*)\s*", s)
        if m:
            out.append(float(m.group(1)))
            continue
        nums = re.findall(r"\d+\.?\d*", s)
        if nums:
            out.append(float(nums[-1]))
    return sorted(out)


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
