"""Weekly executive PDF report.

Reads the populated SQLite DB and writes a one-page (or short multi-page)
PDF summary suitable for emailing to leadership: KPI cards, top customers,
top issues, top risks, top revenue and margin.

Usage:
    from datascrubb.export.pdf import generate_executive_pdf
    generate_executive_pdf("output/exec_report.pdf")
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak,
)

from datascrubb.config import load_config
from datascrubb.db import get_engine

logger = logging.getLogger("datascrubb.export.pdf")


# ─────────────────────── helpers ───────────────────────

def _safe_read(engine, table: str) -> pd.DataFrame:
    try:
        return pd.read_sql(f"SELECT * FROM {table}", engine)
    except Exception:
        return pd.DataFrame()


def _styles():
    base = getSampleStyleSheet()
    base.add(ParagraphStyle(
        name="H1", parent=base["Heading1"],
        fontSize=20, textColor=colors.HexColor("#1f2937"), spaceAfter=8,
    ))
    base.add(ParagraphStyle(
        name="H2", parent=base["Heading2"],
        fontSize=13, textColor=colors.HexColor("#374151"), spaceAfter=4, spaceBefore=10,
    ))
    base.add(ParagraphStyle(
        name="Caption", parent=base["BodyText"],
        fontSize=9, textColor=colors.HexColor("#6b7280"), spaceAfter=4,
    ))
    base.add(ParagraphStyle(
        name="KpiLabel", parent=base["BodyText"],
        fontSize=8, textColor=colors.HexColor("#6b7280"), alignment=1,
    ))
    base.add(ParagraphStyle(
        name="KpiValue", parent=base["BodyText"],
        fontSize=14, textColor=colors.HexColor("#111827"), alignment=1, spaceAfter=2,
    ))
    return base


def _kpi_card(label: str, value: str, styles) -> Table:
    t = Table(
        [[Paragraph(value, styles["KpiValue"])], [Paragraph(label, styles["KpiLabel"])]],
        colWidths=[1.6 * inch],
        rowHeights=[0.35 * inch, 0.18 * inch],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f9fafb")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _kpi_row(cards: list[tuple[str, str]], styles) -> Table:
    cells = [[_kpi_card(label, value, styles) for label, value in cards]]
    t = Table(cells, colWidths=[1.6 * inch] * len(cards))
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _df_table(
    df: pd.DataFrame, columns: list[str], col_widths: list[float] | None = None,
    max_rows: int = 10, header_fill: str = "#1f2937",
) -> Table:
    if df.empty:
        return Table([["(no data)"]], colWidths=[6 * inch])
    sub = df[[c for c in columns if c in df.columns]].head(max_rows).copy()

    # Format common numeric / dollar columns
    money_cols = {"revenue", "cost", "margin", "billed_amount", "total_billed"}
    for col in sub.columns:
        if col in money_cols or col.endswith("_amount"):
            sub[col] = sub[col].apply(
                lambda v: f"${v:,.0f}" if pd.notna(v) and v != 0 else "—"
            )
        elif col.endswith("_pct") or col.endswith("_rate"):
            sub[col] = sub[col].apply(
                lambda v: f"{v:.1f}%" if pd.notna(v) else "—"
            )
        else:
            sub[col] = sub[col].apply(
                lambda v: "—" if pd.isna(v) else (f"{v:,.0f}" if isinstance(v, (int, float)) and abs(v) >= 100 else str(v))
            )

    data = [sub.columns.tolist()] + sub.values.tolist()
    table = Table(data, colWidths=col_widths, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_fill)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
    ]))
    return table


# ─────────────────────── main ───────────────────────

def generate_executive_pdf(
    output_path: str | Path | None = None,
    db_path: str | Path | None = None,
) -> Path:
    """Build a multi-section executive PDF from the current DB.

    Returns the path to the PDF written. If ``output_path`` is None,
    writes to ``output/Exec_Report_<YYYYMMDD_HHMMSS>.pdf``.
    """
    cfg = load_config()
    db = Path(db_path) if db_path else cfg.db_path
    if not db.exists():
        raise FileNotFoundError(f"DB not found: {db}. Run the pipeline first.")

    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = cfg.root / cfg.export.output_dir / f"Exec_Report_{ts}.pdf"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    engine = get_engine(db)
    stops = _safe_read(engine, "stop_master")
    revenue = _safe_read(engine, "route_revenue")
    cust = _safe_read(engine, "customer_scorecard")
    churn = _safe_read(engine, "customer_churn")
    conc = _safe_read(engine, "customer_concentration")
    risk = _safe_read(engine, "claims_risk")
    drivers = _safe_read(engine, "driver_scorecard")
    trailers = _safe_read(engine, "trailer_utilization")
    detention = _safe_read(engine, "detention_audit")
    lanes = _safe_read(engine, "lane_profitability")

    styles = _styles()
    story = []

    # Title block
    story.append(Paragraph("DataScrubb — Executive Report", styles["H1"]))
    if not stops.empty:
        d = pd.to_datetime(stops["arrival_date"], errors="coerce").dropna()
        period = f"{d.min().date()} to {d.max().date()}" if not d.empty else "—"
        story.append(Paragraph(f"Reporting period: <b>{period}</b>", styles["Caption"]))
    story.append(Paragraph(f"Generated: {datetime.now():%Y-%m-%d %H:%M}", styles["Caption"]))
    story.append(Spacer(1, 8))

    # Headline KPI cards (two rows of 4)
    if not stops.empty:
        total_stops = len(stops)
        otp = stops["otp_time_pass"].mean() * 100 if "otp_time_pass" in stops.columns and stops["otp_time_pass"].notna().any() else 0
        routes = stops["order_number"].nunique() if "order_number" in stops.columns else 0
        customers = stops["customer"].nunique() if "customer" in stops.columns else 0
    else:
        total_stops = otp = routes = customers = 0

    if not revenue.empty:
        total_cost = revenue["cost"].sum()
        total_rev = revenue["revenue"].sum()
        total_margin = total_rev - total_cost
        margin_pct = total_margin / total_rev * 100 if total_rev else 0
    else:
        total_cost = total_rev = total_margin = margin_pct = 0

    high_risk = int((risk["risk_band"] == "HIGH").sum()) if not risk.empty else 0
    medium_risk = int((risk["risk_band"] == "MEDIUM").sum()) if not risk.empty else 0

    story.append(Paragraph("Headline metrics", styles["H2"]))
    story.append(_kpi_row([
        ("Total stops", f"{total_stops:,}"),
        ("OTP (time)", f"{otp:.1f}%"),
        ("Routes", f"{routes:,}"),
        ("Customers", f"{customers:,}"),
    ], styles))
    story.append(_kpi_row([
        ("Total revenue", f"${total_rev:,.0f}"),
        ("Total cost", f"${total_cost:,.0f}"),
        ("Margin", f"${total_margin:,.0f}"),
        ("Margin %", f"{margin_pct:.1f}%"),
    ], styles))
    story.append(Spacer(1, 8))
    story.append(_kpi_row([
        ("HIGH-risk routes", f"{high_risk}"),
        ("MEDIUM-risk routes", f"{medium_risk}"),
        ("Drivers", f"{len(drivers):,}" if not drivers.empty else "0"),
        ("Trailers", f"{len(trailers):,}" if not trailers.empty else "0"),
    ], styles))

    # Top customers
    story.append(Paragraph("Top 10 customers by margin", styles["H2"]))
    if not cust.empty and "margin" in cust.columns:
        story.append(_df_table(
            cust.sort_values("margin", ascending=False),
            ["customer", "stops", "otp_rate", "revenue", "cost", "margin", "margin_pct"],
            col_widths=[1.5 * inch, 0.6 * inch, 0.7 * inch, 0.9 * inch, 0.9 * inch, 0.9 * inch, 0.7 * inch],
            max_rows=10,
        ))
    else:
        story.append(Paragraph("(customer margin data not available)", styles["Caption"]))

    # Customers at risk
    story.append(Paragraph("Customers trending down (latest week)", styles["H2"]))
    if not churn.empty:
        risky = churn[churn["churn_band"].isin(["CHURN_RISK", "DECLINING"])]
        if not risky.empty:
            story.append(_df_table(
                risky.sort_values("delta_pct"),
                ["customer", "pros", "prev_pros", "delta_pros", "delta_pct", "churn_band"],
                col_widths=[1.8 * inch, 0.7 * inch, 0.8 * inch, 0.8 * inch, 0.8 * inch, 1.1 * inch],
                max_rows=10,
            ))
        else:
            story.append(Paragraph("No customers in CHURN_RISK or DECLINING bands.", styles["Caption"]))
    else:
        story.append(Paragraph("(churn data requires >= 2 weeks of history)", styles["Caption"]))

    story.append(PageBreak())

    # Top revenue concentration
    story.append(Paragraph("Revenue concentration — top 10 customers", styles["H2"]))
    if not conc.empty:
        story.append(_df_table(
            conc.head(10),
            ["rank", "customer", "revenue", "cost", "margin", "share_pct", "cumulative_share_pct"],
            col_widths=[0.4 * inch, 1.6 * inch, 0.9 * inch, 0.9 * inch, 0.9 * inch, 0.8 * inch, 1.0 * inch],
            max_rows=10,
        ))

    # Top claims-risk routes
    story.append(Paragraph("Highest claims-risk routes", styles["H2"]))
    if not risk.empty:
        story.append(_df_table(
            risk.sort_values("risk_score", ascending=False),
            ["route_id", "route_name", "customer", "risk_score", "risk_band", "short_cases", "excursion_stops"],
            col_widths=[0.9 * inch, 1.4 * inch, 1.1 * inch, 0.7 * inch, 0.7 * inch, 0.8 * inch, 0.8 * inch],
            max_rows=10,
        ))

    # Detention exposure
    story.append(Paragraph("Top 10 customers by billable detention hours", styles["H2"]))
    if not detention.empty:
        story.append(_df_table(
            detention.head(10),
            ["customer", "detention_stops", "billable_hours", "avg_dwell_min", "max_dwell_min"],
            col_widths=[1.8 * inch, 1.0 * inch, 1.0 * inch, 1.0 * inch, 1.0 * inch],
            max_rows=10,
        ))
    else:
        story.append(Paragraph("(no stops over the dwell threshold)", styles["Caption"]))

    # Lane profitability
    story.append(Paragraph("Top profitable lanes", styles["H2"]))
    if not lanes.empty:
        story.append(_df_table(
            lanes.sort_values("margin", ascending=False),
            ["origin_state", "dest_state", "routes", "revenue", "cost", "margin", "margin_pct"],
            col_widths=[0.7 * inch, 0.7 * inch, 0.6 * inch, 1.0 * inch, 1.0 * inch, 1.0 * inch, 0.8 * inch],
            max_rows=10,
        ))

    # Driver leaders
    story.append(Paragraph("Top 10 drivers by composite score", styles["H2"]))
    if not drivers.empty:
        story.append(_df_table(
            drivers.head(10),
            ["rank", "driver", "score", "total_stops", "otp_rate", "late_rate", "avg_dwell"],
            col_widths=[0.5 * inch, 1.4 * inch, 0.6 * inch, 0.8 * inch, 0.7 * inch, 0.7 * inch, 0.8 * inch],
            max_rows=10,
        ))

    # Footer
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "Methodology — see <b>LOGIC.md</b> in the repo for the formula behind every metric. "
        "Source data: CRST stops + SAP segments + reefer telemetry + M3PL weekly invoices.",
        styles["Caption"],
    ))

    doc = SimpleDocTemplate(
        str(output_path), pagesize=LETTER,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        title="DataScrubb Executive Report",
    )
    doc.build(story)
    logger.info("Executive PDF written: %s (%d KB)", output_path, output_path.stat().st_size // 1024)
    return output_path
