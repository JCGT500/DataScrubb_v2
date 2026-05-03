"""Customer revenue & margin calculations.

The carrier (M3PL) bills us — that's our COST. We bill our customers — that's
our REVENUE. Margin = revenue − cost.

Revenue is derived from a per-customer rate matrix (loaded from
``config/customer_rates.yaml`` or saved into the SQLite ``customer_rates``
table by the dashboard).

NOTE — future intelligent routing
---------------------------------
The weekly trailer / route revenue tables (``compute_trailer_revenue_weekly``,
``compute_route_revenue_weekly``) are designed as the input signal for an
optimizer. Once telemetry GPS lat/lon parsing exists and we have several
months of history, feed:

    - per-route per-day historical demand (cases × stop count)
    - per-route revenue / margin
    - per-trailer last-known location (`trailer_utilization.last_known_state`)
    - per-trailer capacity (`trailer_capacity.yaml` + observed)

into a vehicle-routing solver (e.g. Google OR-tools VRP) to suggest
trailer-to-route assignments that maximise margin per active day.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger("datascrubb.kpi.revenue")

DEFAULT_RATES_FILE = Path(__file__).resolve().parents[2] / "config" / "customer_rates.yaml"


def _normalize_customer(name: str | None) -> str | None:
    if name is None or pd.isna(name):
        return None
    return str(name).strip().upper()


def load_rate_matrix(path: Path | None = None) -> dict[str, dict]:
    """Load the customer rate matrix from YAML.

    Returns ``{"default": {...}, "customers": {"CSL": {...}, ...}}``.
    Customer keys are uppercased.
    """
    p = Path(path) if path else DEFAULT_RATES_FILE
    if not p.exists():
        logger.warning("Rate matrix file not found: %s — using empty matrix", p)
        return {"default": {}, "customers": {}}
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    customers = {
        _normalize_customer(k): v
        for k, v in (data.get("customers") or {}).items()
    }
    return {"default": data.get("default", {}), "customers": customers}


def save_rate_matrix(matrix: dict, path: Path | None = None) -> None:
    p = Path(path) if path else DEFAULT_RATES_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(matrix, f, sort_keys=False)


def rate_for(customer: str | None, matrix: dict) -> dict:
    """Look up the rate dict for a customer, falling back to default.

    Returned dict always includes ``pricing_model`` (defaults to "flat").
    Banded customers carry ``mile_bands``, ``weight_bands``, and ``rate_matrix``
    in addition to the optional flat fields (``rate_per_stop``, ``minimum_charge``).
    """
    cust = _normalize_customer(customer)
    if cust and cust in matrix.get("customers", {}):
        merged = {**matrix.get("default", {}), **matrix["customers"][cust]}
        merged["_source"] = "customer"
    else:
        merged = dict(matrix.get("default", {}))
        merged["_source"] = "default"
    merged.setdefault("pricing_model", "flat")
    return merged


def lookup_banded_rate(
    miles: float,
    weight_lbs: float,
    mile_bands: list[float],
    weight_bands: list[float],
    rate_matrix: list[list[float]],
) -> float:
    """Pick the dollar value from a 2D banded rate matrix.

    Bands are upper-bound inclusive (a route at exactly 50 mi → first row when
    mile_bands[0] == 50). Routes larger than the last band clamp to the last
    row/column.

    Args:
        miles: Total route miles.
        weight_lbs: Total route weight in pounds.
        mile_bands: Upper bounds of mile bands, ascending. e.g. [50, 150, 300, 500]
            yields rows for ≤50, ≤150, ≤300, ≤500, >500.
        weight_bands: Upper bounds of weight bands, ascending.
        rate_matrix: 2D array shaped (len(mile_bands)+1, len(weight_bands)+1).

    Returns:
        Dollar value from the chosen cell, or NaN if miles is null/missing.

    Raises:
        ValueError: If matrix shape doesn't match the band counts.
    """
    if pd.isna(miles):
        return float("nan")
    expected_rows = len(mile_bands) + 1
    expected_cols = len(weight_bands) + 1
    if len(rate_matrix) != expected_rows or any(len(r) != expected_cols for r in rate_matrix):
        raise ValueError(
            f"rate_matrix shape mismatch: expected {expected_rows}×{expected_cols}, "
            f"got {len(rate_matrix)}×{len(rate_matrix[0]) if rate_matrix else 0}"
        )
    # Treat NaN/None weight as 0 — falls into the lowest weight band
    w = 0.0 if pd.isna(weight_lbs) else float(weight_lbs)
    m = float(miles)

    row = next((i for i, ub in enumerate(mile_bands) if m <= ub), len(mile_bands))
    col = next((i for i, ub in enumerate(weight_bands) if w <= ub), len(weight_bands))
    return float(rate_matrix[row][col])


def compute_route_revenue(
    stops_df: pd.DataFrame,
    m3pl_df: pd.DataFrame | None,
    rate_matrix: dict | None = None,
) -> pd.DataFrame:
    """Per-route revenue, cost, and margin.

    Revenue inputs (per route):
        - miles  → from M3PL crst_miles for that PRO# (sum across weeks).
                   Fallback to 0 when no M3PL row exists.
        - stops  → CRST stop count for that order_#.
        - weight → CRST sum_of_weight aggregated.
        - customer → mode (most common) customer over the route's stops.

    Cost: M3PL billed_amount summed by PRO#.

    Output columns:
        route_id, route_name, customer, miles, stop_count, weight_lbs,
        rate_per_mile, rate_per_stop, rate_per_cwt, minimum_charge, rate_source,
        revenue_miles, revenue_stops, revenue_weight, revenue, cost,
        margin, margin_pct.
    """
    if stops_df is None or stops_df.empty:
        return pd.DataFrame()

    matrix = rate_matrix if rate_matrix is not None else load_rate_matrix()

    sm = stops_df.copy()
    sm["route_id"] = sm.get("order_#", sm.get("order_number")).astype(str).str.strip()

    # Per-route stop / weight aggregates
    routes = (
        sm.groupby("route_id", dropna=False)
        .agg(
            stop_count=("transaction_id", "count"),
            weight_lbs=("sum_of_weight", "sum") if "sum_of_weight" in sm.columns else ("transaction_id", "count"),
        )
        .reset_index()
    )

    # Route name + dominant customer per route
    if "route_name" in sm.columns:
        names = (
            sm.dropna(subset=["route_name"])
            .groupby("route_id")["route_name"]
            .agg(lambda s: s.mode().iat[0] if not s.mode().empty else None)
            .reset_index()
        )
        routes = routes.merge(names, on="route_id", how="left")
    else:
        routes["route_name"] = None

    if "customer" in sm.columns:
        custs = (
            sm.dropna(subset=["customer"])
            .groupby("route_id")["customer"]
            .agg(lambda s: s.mode().iat[0] if not s.mode().empty else None)
            .reset_index()
        )
        routes = routes.merge(custs, on="route_id", how="left")
    else:
        routes["customer"] = None

    # Cost + miles from M3PL
    if m3pl_df is not None and not m3pl_df.empty:
        m = m3pl_df.copy()
        m["route_id"] = m["pro_number"].astype(str).str.strip()
        m_agg = (
            m.groupby("route_id")
            .agg(miles=("crst_miles", "sum"), cost=("billed_amount", "sum"))
            .reset_index()
        )
        routes = routes.merge(m_agg, on="route_id", how="left")
    else:
        routes["miles"] = 0.0
        routes["cost"] = 0.0

    routes["miles"] = routes["miles"].fillna(0.0)
    routes["cost"] = routes["cost"].fillna(0.0)
    routes["weight_lbs"] = routes["weight_lbs"].fillna(0.0)

    # Apply rate matrix per row
    rate_rows = routes["customer"].apply(lambda c: rate_for(c, matrix))
    routes["pricing_model"] = [r.get("pricing_model", "flat") for r in rate_rows]
    routes["rate_per_mile"] = [r.get("rate_per_mile", 0) or 0 for r in rate_rows]
    routes["rate_per_stop"] = [r.get("rate_per_stop", 0) or 0 for r in rate_rows]
    routes["rate_per_cwt"] = [r.get("rate_per_cwt", 0) or 0 for r in rate_rows]
    routes["minimum_charge"] = [r.get("minimum_charge", 0) or 0 for r in rate_rows]
    routes["rate_source"] = [r.get("_source", "default") for r in rate_rows]

    # ─── Per-row revenue ─────────────────────────────────────────
    # Flat branch is fully vectorized (the common case). Banded routes are
    # patched in afterward via a small per-row loop.

    # Flat formula applied to every row first
    routes["revenue_miles"] = (routes["miles"] * routes["rate_per_mile"]).round(2)
    routes["revenue_stops"] = (routes["stop_count"] * routes["rate_per_stop"]).round(2)
    routes["revenue_weight"] = (routes["weight_lbs"] / 100.0 * routes["rate_per_cwt"]).round(2)
    routes["revenue_banded"] = np.nan

    # Banded overrides: only iterate the (typically small) subset of banded routes
    is_banded = routes["pricing_model"] == "banded"
    if is_banded.any():
        rev_banded_col = routes["revenue_banded"].copy()
        rev_stops_col = routes["revenue_stops"].copy()
        for i in routes.index[is_banded]:
            r = rate_rows.iloc[routes.index.get_loc(i)]
            if not r.get("rate_matrix"):
                continue
            try:
                base = lookup_banded_rate(
                    routes.at[i, "miles"], routes.at[i, "weight_lbs"],
                    r["mile_bands"], r["weight_bands"], r["rate_matrix"],
                )
            except (ValueError, KeyError, TypeError) as e:
                logger.warning(
                    "Banded rate lookup failed for customer=%s route=%s: %s — falling back to 0",
                    routes.at[i, "customer"], routes.at[i, "route_id"], e,
                )
                base = 0.0
            rev_banded_col.at[i] = round(base, 2) if not np.isnan(base) else np.nan
            stops_dollars = routes.at[i, "stop_count"] * routes.at[i, "rate_per_stop"]
            rev_stops_col.at[i] = round(stops_dollars, 2)
        routes["revenue_banded"] = rev_banded_col
        routes["revenue_stops"] = rev_stops_col
        # Banded routes: zero out the flat-only fields so revenue_calc is correct
        routes.loc[is_banded, "revenue_miles"] = 0.0
        routes.loc[is_banded, "revenue_weight"] = 0.0

    routes["revenue_calc"] = (
        routes["revenue_miles"] + routes["revenue_stops"] + routes["revenue_weight"]
        + routes["revenue_banded"].fillna(0.0)
    ).round(2)
    routes["revenue"] = np.maximum(routes["revenue_calc"], routes["minimum_charge"]).round(2)
    routes["margin"] = (routes["revenue"] - routes["cost"]).round(2)
    routes["margin_pct"] = np.where(
        routes["revenue"].abs() > 0,
        (routes["margin"] / routes["revenue"] * 100).round(1),
        np.nan,
    )

    cols = [
        "route_id", "route_name", "customer",
        "miles", "stop_count", "weight_lbs",
        "pricing_model",
        "rate_per_mile", "rate_per_stop", "rate_per_cwt", "minimum_charge", "rate_source",
        "revenue_miles", "revenue_stops", "revenue_weight", "revenue_banded",
        "revenue", "cost", "margin", "margin_pct",
    ]
    return routes[cols].sort_values("margin", ascending=False)


def compute_trailer_revenue_weekly(
    stops_df: pd.DataFrame, route_revenue_df: pd.DataFrame | None
) -> pd.DataFrame:
    """Per-trailer × ISO-week revenue / cost / margin rollup.

    Attribution: a route's revenue is split across the trailers that ran it,
    weighted by stop_count per trailer (a route handled by one trailer gets
    100% attributed; one split across two trailers 60/40 by stops gets that
    split). Routes with no revenue row contribute zero.

    Output columns: trailer, week, routes, stops, miles, revenue, cost,
    margin, margin_pct.
    """
    if stops_df is None or stops_df.empty or "trailer" not in stops_df.columns:
        return pd.DataFrame()
    if route_revenue_df is None or route_revenue_df.empty:
        return pd.DataFrame()

    df = stops_df.copy()
    df["trailer"] = df["trailer"].astype(str).str.strip().str.upper()
    df = df[df["trailer"].notna() & (df["trailer"] != "") & (df["trailer"] != "NAN")]
    df["arrival_dt"] = pd.to_datetime(df.get("arrival_date"), errors="coerce")
    df = df[df["arrival_dt"].notna()]
    if df.empty:
        return pd.DataFrame()
    df["week"] = df["arrival_dt"].dt.to_period("W").dt.start_time
    if "order_#" in df.columns:
        df["route_id"] = df["order_#"].astype(str).str.strip()
    else:
        df["route_id"] = df.get("order_number", "").astype(str).str.strip()

    # For each (route_id, trailer) — count stops; for each route — total stops
    per_route_trailer = (
        df.groupby(["route_id", "trailer"])
        .agg(stops=("transaction_id", "count"), week=("week", "min"))
        .reset_index()
    )
    per_route_total = (
        df.groupby("route_id")["transaction_id"].count().reset_index(name="route_total_stops")
    )
    per_route_trailer = per_route_trailer.merge(per_route_total, on="route_id", how="left")
    per_route_trailer["share"] = per_route_trailer["stops"] / per_route_trailer["route_total_stops"]

    # Bring revenue / cost from route_revenue
    rev = route_revenue_df.copy()
    rev["route_id"] = rev["route_id"].astype(str).str.strip()
    keep_cols = ["route_id", "miles", "revenue", "cost", "margin"]
    rev = rev[[c for c in keep_cols if c in rev.columns]]

    merged = per_route_trailer.merge(rev, on="route_id", how="left")
    for c in ("miles", "revenue", "cost", "margin"):
        if c in merged.columns:
            merged[c] = pd.to_numeric(merged[c], errors="coerce").fillna(0) * merged["share"]

    out = (
        merged.groupby(["trailer", "week"])
        .agg(
            routes=("route_id", "nunique"),
            stops=("stops", "sum"),
            miles=("miles", "sum"),
            revenue=("revenue", "sum"),
            cost=("cost", "sum"),
            margin=("margin", "sum"),
        )
        .reset_index()
    )
    out["margin_pct"] = (
        out["margin"] / out["revenue"].replace(0, pd.NA) * 100
    ).round(1)
    for c in ("miles", "revenue", "cost", "margin"):
        if c in out.columns:
            out[c] = out[c].round(2)
    return out.sort_values(["trailer", "week"])


def compute_route_revenue_weekly(
    stops_df: pd.DataFrame, route_revenue_df: pd.DataFrame | None
) -> pd.DataFrame:
    """Per-route_name × ISO-week revenue / cost / margin rollup.

    Groups all PRO# instances of a named route (e.g. all "AMES" runs in a
    given week). Output: route_name, week, instances, stops, miles, revenue,
    cost, margin, margin_pct, customer.
    """
    if stops_df is None or stops_df.empty or "route_name" not in stops_df.columns:
        return pd.DataFrame()
    if route_revenue_df is None or route_revenue_df.empty:
        return pd.DataFrame()

    df = stops_df.copy()
    df["arrival_dt"] = pd.to_datetime(df.get("arrival_date"), errors="coerce")
    df = df[df["arrival_dt"].notna() & df["route_name"].notna()]
    if df.empty:
        return pd.DataFrame()
    df["week"] = df["arrival_dt"].dt.to_period("W").dt.start_time
    if "order_#" in df.columns:
        df["route_id"] = df["order_#"].astype(str).str.strip()
    else:
        df["route_id"] = df.get("order_number", "").astype(str).str.strip()

    # Per-route metadata: which week + which route_name was the dominant for that PRO#?
    per_route = (
        df.groupby("route_id")
        .agg(
            route_name=("route_name", lambda s: s.dropna().mode().iat[0] if not s.dropna().mode().empty else None),
            customer=("customer", lambda s: s.dropna().mode().iat[0] if not s.dropna().mode().empty else None),
            week=("week", "min"),
            stops=("transaction_id", "count"),
        )
        .reset_index()
    )

    rev = route_revenue_df.copy()
    rev["route_id"] = rev["route_id"].astype(str).str.strip()
    keep = ["route_id", "miles", "revenue", "cost", "margin"]
    rev = rev[[c for c in keep if c in rev.columns]]
    merged = per_route.merge(rev, on="route_id", how="left")

    out = (
        merged.dropna(subset=["route_name"])
        .groupby(["route_name", "week"])
        .agg(
            instances=("route_id", "nunique"),
            stops=("stops", "sum"),
            miles=("miles", "sum"),
            revenue=("revenue", "sum"),
            cost=("cost", "sum"),
            margin=("margin", "sum"),
            customer=("customer", lambda s: s.dropna().mode().iat[0] if not s.dropna().mode().empty else None),
        )
        .reset_index()
    )
    out["margin_pct"] = (
        out["margin"] / out["revenue"].replace(0, pd.NA) * 100
    ).round(1)
    for c in ("miles", "revenue", "cost", "margin"):
        if c in out.columns:
            out[c] = out[c].round(2)
    return out.sort_values(["route_name", "week"])
