"""Download/export button component."""

import io

import pandas as pd
import streamlit as st


def export_dataframe(df: pd.DataFrame, filename: str = "export", label: str = "Download"):
    """Render Excel and CSV download buttons for a DataFrame."""
    col1, col2 = st.columns(2)

    with col1:
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label=f"{label} (CSV)",
            data=csv,
            file_name=f"{filename}.csv",
            mime="text/csv",
        )

    with col2:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Data")
        st.download_button(
            label=f"{label} (Excel)",
            data=buffer.getvalue(),
            file_name=f"{filename}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
