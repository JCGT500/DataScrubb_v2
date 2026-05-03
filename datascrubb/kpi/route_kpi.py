"""Route-level KPI computations.

All functions take normalized DataFrames as input and return new DataFrames.
None of them mutate inputs or reach the database.

The canonical "route" key is ``order_#`` from CRST (== ``pro_number`` in
M3PL). Each route is one or more stops, all sharing the same order number.
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger("datascrubb.kpi")

PLASMA_TEMP_SETPOINT_C = -25.0
PLASMA_TEMP_TOLERANCE_C = 5.0
TEMP_EXCURSION_MIN_MINUTES = 15


def _coerce_route_key(stops_df: pd.DataFrame) -> pd.Series:
    """Return a string Series with the route key (order_#)."""
    if "order_#" in stops_df.columns:
        return stops_df["order_#"].astype(str).str.strip()
    if "order_number" in stops_df.columns:
        return stops_df["order_number"].astype(str).str.strip()
    raise KeyError("stops_df must have either 'order_#' or 'order_number' column")


def _filter_warehouses(stops_df: pd.DataFrame, include_warehouses: bool) -> pd.DataFrame:
    """Filter to PLASMA_CENTER stops only when include_warehouses is False.

    Falls back to ``stop_type == 'PLASMA_CENTER'`` if the new ``stop_class``
    column isn't present (backwards-compat with old data).
    """
    if include_warehouses or stops_df is None or stops_df.empty:
        return stops_df
    if "stop_class" in stops_df.columns:
        return stops_df[stops_df["stop_class"] == "PLASMA_CENTER"]
    if "stop_type" in stops_df.columns:
        return stops_df[stops_df["stop_type"] == "PLASMA_CENTER"]
    return stops_df


def compute_route_otp(stops_df: pd.DataFrame) -> pd.DataFrame:
    """Roll up stop-level OTP into one row per route.

    Returns columns: route_id, route_name, stop_count, pickup_count,
    delivery_count, otp_time_pass_rate, otp_day_pass_rate, avg_minutes_late,
    dwell_minutes_total, dwell_minutes_avg, first_arrival, last_departure.
    """
    if stops_df is None or stops_df.empty:
        return pd.DataFrame()

    df = stops_df.copy()
    df["route_id"] = _coerce_route_key(df)

    # Stop-type counts (PU = Pickup, DO = Delivery in CRST data; stop_type column already
    # holds PLASMA_CENTER / WAREHOUSE — pickup/delivery is in the source 'stop_type' raw col,
    # which we mirror via the CRST 'stop_type' free-text column when present)
    raw_stop_type = df["stop_type"] if "stop_type" in df.columns else pd.Series(index=df.index)

    # Build aggregations
    grouped = df.groupby("route_id", dropna=False)

    out = grouped.agg(
        stop_count=("transaction_id", "count"),
        otp_time_pass_rate=("otp_time_pass", "mean"),
        otp_day_pass_rate=("otp_day_pass", "mean"),
        avg_minutes_late=("minutes_from_appt", "mean"),
        first_arrival=("actual_arrival", "min"),
        last_departure=("actual_departure", "max") if "actual_departure" in df.columns else ("actual_arrival", "max"),
    ).reset_index()

    if "dwell_minutes" in df.columns:
        dwell = grouped["dwell_minutes"].agg(["sum", "mean"]).reset_index()
        dwell.columns = ["route_id", "dwell_minutes_total", "dwell_minutes_avg"]
        out = out.merge(dwell, on="route_id", how="left")
    else:
        out["dwell_minutes_total"] = np.nan
        out["dwell_minutes_avg"] = np.nan

    # Route name (mode of route_name within the group)
    if "route_name" in df.columns:
        names = (
            df.dropna(subset=["route_name"])
            .groupby("route_id")["route_name"]
            .agg(lambda s: s.mode().iat[0] if not s.mode().empty else None)
            .reset_index()
        )
        out = out.merge(names, on="route_id", how="left")
    else:
        out["route_name"] = None

    # Round percentage / minute columns
    for col in ("otp_time_pass_rate", "otp_day_pass_rate"):
        if col in out.columns:
            out[col] = (out[col] * 100).round(1)
    out["avg_minutes_late"] = out["avg_minutes_late"].round(1)
    if "dwell_minutes_total" in out.columns:
        out["dwell_minutes_total"] = out["dwell_minutes_total"].round(1)
        out["dwell_minutes_avg"] = out["dwell_minutes_avg"].round(1)

    return out.sort_values("route_id")


def compute_miles_variance(
    stops_df: pd.DataFrame, m3pl_df: pd.DataFrame | None
) -> pd.DataFrame:
    """Compare M3PL-billed miles per PRO# to CRST stop counts as a sanity check.

    CRST does not carry per-route mileage directly, so the "actual" comparison
    only makes sense once a separate odometer/PC*Miler source is wired in. For
    now we surface M3PL-billed miles alongside the CRST stop count so users
    can spot routes with disproportionately high billed miles per stop.
    """
    if m3pl_df is None or m3pl_df.empty:
        return pd.DataFrame()

    m3pl = m3pl_df.copy()
    m3pl["route_id"] = m3pl["pro_number"].astype(str).str.strip()

    out = (
        m3pl.groupby(["route_id", "billing_week_end"], dropna=False)
        .agg(
            legacy_route=("legacy_route", "first"),
            lane=("lane", "first"),
            m3pl_billed_miles=("crst_miles", "sum"),
            m3pl_stop_count=("stop_count", "sum"),
            billed_amount=("billed_amount", "sum"),
        )
        .reset_index()
    )

    if stops_df is not None and not stops_df.empty:
        stops = stops_df.copy()
        stops["route_id"] = _coerce_route_key(stops)
        crst_counts = (
            stops.groupby("route_id")
            .agg(crst_stop_count=("transaction_id", "count"))
            .reset_index()
        )
        out = out.merge(crst_counts, on="route_id", how="left")
        out["stop_variance"] = out["crst_stop_count"] - out["m3pl_stop_count"]
        out["miles_per_crst_stop"] = (
            out["m3pl_billed_miles"] / out["crst_stop_count"].replace(0, np.nan)
        ).round(1)
    else:
        out["crst_stop_count"] = np.nan
        out["stop_variance"] = np.nan
        out["miles_per_crst_stop"] = np.nan

    return out.sort_values(["billing_week_end", "route_id"])


def compute_billing_recon(m3pl_df: pd.DataFrame | None) -> pd.DataFrame:
    """Reconcile per-PRO billed_amount vs the recomputed expected amount."""
    if m3pl_df is None or m3pl_df.empty:
        return pd.DataFrame()

    df = m3pl_df.copy()
    df["expected_amount"] = (
        df["team_miles"] * df["team_rate"]
        + df["solo_miles"] * df["solo_rate"]
        + df["team_deficit_miles"] * df["team_deficit_rate"]
        + df["solo_deficit_miles"] * df["solo_deficit_rate"]
        + df["stop_count"] * df["stop_rate"]
        + df["tolls"]
    ).round(2)
    df["delta"] = (df["billed_amount"] - df["expected_amount"]).round(2)
    df["delta_pct"] = np.where(
        df["expected_amount"].abs() > 0,
        (df["delta"] / df["expected_amount"] * 100).round(2),
        np.nan,
    )

    cols = [
        "pro_number", "legacy_route", "lane", "billing_week_end",
        "billed_amount", "expected_amount", "delta", "delta_pct",
        "team_miles", "solo_miles", "team_deficit_miles", "solo_deficit_miles",
        "stop_count", "tolls", "source_file",
    ]
    return df[[c for c in cols if c in df.columns]].sort_values(
        ["billing_week_end", "lane", "pro_number"]
    )


def compute_equipment_util(
    stops_df: pd.DataFrame, m3pl_df: pd.DataFrame | None = None
) -> dict[str, pd.DataFrame]:
    """Per-tractor / per-trailer / per-driver utilization summary.

    Returns dict keyed by ``tractor``, ``trailer``, ``driver`` with one
    DataFrame each.
    """
    out: dict[str, pd.DataFrame] = {}
    if stops_df is None or stops_df.empty:
        return out

    df = stops_df.copy()
    df["route_id"] = _coerce_route_key(df)

    # Per-PRO miles map from M3PL (sum across weeks; first-occurrence wins on dups)
    miles_by_pro: dict[str, float] = {}
    if m3pl_df is not None and not m3pl_df.empty:
        miles_by_pro = (
            m3pl_df.groupby(m3pl_df["pro_number"].astype(str).str.strip())["crst_miles"]
            .sum()
            .to_dict()
        )

    df["route_miles"] = df["route_id"].map(miles_by_pro).fillna(0)

    def _build(group_col: str, normalize_upper: bool = False) -> pd.DataFrame:
        if group_col not in df.columns:
            return pd.DataFrame()
        sub = df.copy()
        sub[group_col] = sub[group_col].astype(str).str.strip()
        if normalize_upper:
            sub[group_col] = sub[group_col].str.upper()
        sub = sub[sub[group_col].notna() & (sub[group_col] != "") & (sub[group_col] != "nan")]
        if sub.empty:
            return pd.DataFrame()
        # Distinct route miles per group — sum miles across distinct (group, route) pairs
        per_route = (
            sub.drop_duplicates(subset=[group_col, "route_id"])
            .groupby(group_col)["route_miles"]
            .sum()
            .reset_index(name="total_miles")
        )
        agg = (
            sub.groupby(group_col)
            .agg(
                total_stops=("transaction_id", "count"),
                distinct_routes=("route_id", "nunique"),
                otp_rate=("otp_time_pass", "mean"),
                active_days=("arrival_date", "nunique"),
            )
            .reset_index()
        )
        agg = agg.merge(per_route, on=group_col, how="left")
        agg["otp_rate"] = (agg["otp_rate"] * 100).round(1)
        agg["total_miles"] = agg["total_miles"].round(0)
        return agg.sort_values("total_stops", ascending=False)

    out["tractor"] = _build("truck")
    out["trailer"] = _build("trailer")
    out["driver"] = _build("drivers", normalize_upper=True)
    return out


def compute_loaded_miles(
    stops_df: pd.DataFrame, m3pl_df: pd.DataFrame | None
) -> pd.DataFrame:
    """Per-route loaded vs deadhead mile estimates.

    CRST does not record per-segment miles, so we approximate by stop-to-stop
    transitions. For each route:
        - Sort stops in physical execution order (by ``stop_seq``).
        - For each transition (stop_n -> stop_n+1) the trailer was carrying
          ``current_cases_at_n`` cases. If that's > 0 the segment is "loaded".
        - loaded_pct = loaded_segments / total_segments
        - Apply that ratio to the route's M3PL miles to estimate
          loaded_miles vs deadhead_miles.

    Returns columns: route_id, route_name, customer, total_segments,
    loaded_segments, deadhead_segments, loaded_pct, total_miles,
    estimated_loaded_miles, estimated_deadhead_miles.
    """
    if stops_df is None or stops_df.empty:
        return pd.DataFrame()

    df = stops_df.copy()
    df["route_id"] = _coerce_route_key(df)

    # Build per-route segment counts
    df["stop_seq_int"] = pd.to_numeric(df.get("stop_seq"), errors="coerce")
    df = df.sort_values(["route_id", "stop_seq_int", "actual_arrival"], na_position="last")

    cur_cases = pd.to_numeric(df.get("current_cases"), errors="coerce").fillna(0)
    # A segment originating from this stop is loaded if cases_on_board > 0
    df["_segment_loaded"] = (cur_cases > 0).astype(int)

    # Per route: count stops (= n), segments = n-1, loaded segments = sum of _segment_loaded
    # for the first n-1 stops (last stop has no outgoing segment)
    def _per_route(group: pd.DataFrame) -> pd.Series:
        n = len(group)
        if n <= 1:
            return pd.Series({
                "total_segments": 0,
                "loaded_segments": 0,
                "deadhead_segments": 0,
                "loaded_pct": pd.NA,
            })
        loaded = int(group["_segment_loaded"].iloc[: n - 1].sum())
        return pd.Series({
            "total_segments": n - 1,
            "loaded_segments": loaded,
            "deadhead_segments": (n - 1) - loaded,
            "loaded_pct": round(loaded / (n - 1) * 100, 1),
        })

    seg = df.groupby("route_id", dropna=False, group_keys=False).apply(
        _per_route, include_groups=False
    ).reset_index()

    # Route name + customer (mode)
    if "route_name" in df.columns:
        names = (
            df.dropna(subset=["route_name"])
            .groupby("route_id")["route_name"]
            .agg(lambda s: s.mode().iat[0] if not s.mode().empty else None)
            .reset_index()
        )
        seg = seg.merge(names, on="route_id", how="left")
    if "customer" in df.columns:
        custs = (
            df.dropna(subset=["customer"])
            .groupby("route_id")["customer"]
            .agg(lambda s: s.mode().iat[0] if not s.mode().empty else None)
            .reset_index()
        )
        seg = seg.merge(custs, on="route_id", how="left")

    # Driver attribution (mode)
    if "drivers" in df.columns:
        drv = (
            df.dropna(subset=["drivers"])
            .groupby("route_id")["drivers"]
            .agg(lambda s: s.mode().iat[0] if not s.mode().empty else None)
            .reset_index()
        )
        drv["drivers"] = drv["drivers"].astype(str).str.strip().str.upper()
        seg = seg.merge(drv, on="route_id", how="left")

    # Apply M3PL miles
    if m3pl_df is not None and not m3pl_df.empty:
        miles = (
            m3pl_df.assign(route_id=m3pl_df["pro_number"].astype(str).str.strip())
            .groupby("route_id")["crst_miles"].sum()
            .reset_index(name="total_miles")
        )
        seg = seg.merge(miles, on="route_id", how="left")
    else:
        seg["total_miles"] = 0.0

    seg["total_miles"] = pd.to_numeric(seg["total_miles"], errors="coerce").fillna(0.0)
    pct = pd.to_numeric(seg["loaded_pct"], errors="coerce") / 100
    seg["estimated_loaded_miles"] = (seg["total_miles"] * pct.fillna(0)).round(0)
    seg["estimated_deadhead_miles"] = (seg["total_miles"] - seg["estimated_loaded_miles"]).round(0)

    return seg.sort_values("estimated_deadhead_miles", ascending=False, na_position="last")


def compute_driver_scorecard(
    stops_df: pd.DataFrame,
    *,
    weight_otp: float = 0.40,
    weight_late_rate: float = 0.20,
    weight_dwell: float = 0.20,
    weight_cases_variance: float = 0.20,
) -> pd.DataFrame:
    """Per-driver composite score (0-100) blending OTP, dwell, late rate, cases variance.

    Score = w_otp * otp_norm + w_late * (1-late_norm) + w_dwell * (1-dwell_norm)
          + w_var * (1-variance_norm), all min-max scaled, then × 100.
    """
    if stops_df is None or stops_df.empty or "drivers" not in stops_df.columns:
        return pd.DataFrame()

    df = stops_df.copy()
    df["driver"] = df["drivers"].astype(str).str.strip().str.upper()
    df = df[df["driver"].notna() & (df["driver"] != "") & (df["driver"] != "NAN")]
    if df.empty:
        return pd.DataFrame()

    grouped = df.groupby("driver")
    base_aggs: dict = dict(
        total_stops=("transaction_id", "count"),
        otp_rate=("otp_time_pass", "mean"),
        late_count=("stop_performance_status", lambda s: int((s == "Late").sum())),
        avg_dwell=("dwell_minutes", "mean"),
        cases_variance_total=("cases_variance", lambda s: float(pd.to_numeric(s, errors="coerce").abs().sum())),
        active_days=("arrival_date", "nunique"),
    )
    if "max_speed" in df.columns:
        base_aggs["max_speed_mph"] = ("max_speed", "max")
        base_aggs["avg_speed_mph"] = ("avg_speed", "mean")
    if "idle_minutes" in df.columns:
        base_aggs["idle_minutes_total"] = ("idle_minutes", "sum")
    if "door_open_while_moving" in df.columns:
        base_aggs["door_moving_events"] = ("door_open_while_moving", "sum")
    out = grouped.agg(**base_aggs).reset_index()

    # Compute distinct_routes properly (the agg above is inconsistent due to series naming)
    if "order_#" in df.columns:
        rt = df.groupby("driver")["order_#"].nunique().reset_index(name="distinct_routes_x")
        out = out.drop(columns=["distinct_routes"], errors="ignore").merge(rt, on="driver", how="left")
        out = out.rename(columns={"distinct_routes_x": "distinct_routes"})

    out["otp_rate"] = (out["otp_rate"] * 100).round(1)
    out["avg_dwell"] = out["avg_dwell"].round(0)
    out["late_rate"] = (out["late_count"] / out["total_stops"] * 100).round(1)
    out["cases_variance_per_stop"] = (out["cases_variance_total"] / out["total_stops"]).round(1)

    # Normalise inputs for composite score
    def _norm_lower_better(s: pd.Series) -> pd.Series:
        s = s.fillna(s.median())
        if s.max() == s.min():
            return pd.Series(1.0, index=s.index)
        return 1 - (s - s.min()) / (s.max() - s.min())

    def _norm_higher_better(s: pd.Series) -> pd.Series:
        s = s.fillna(s.median())
        if s.max() == s.min():
            return pd.Series(1.0, index=s.index)
        return (s - s.min()) / (s.max() - s.min())

    n_otp = _norm_higher_better(out["otp_rate"])
    n_late = _norm_lower_better(out["late_rate"])
    n_dwell = _norm_lower_better(out["avg_dwell"])
    n_var = _norm_lower_better(out["cases_variance_per_stop"])

    out["score"] = (
        (weight_otp * n_otp + weight_late_rate * n_late
         + weight_dwell * n_dwell + weight_cases_variance * n_var) * 100
    ).round(1)

    out["rank"] = out["score"].rank(method="min", ascending=False).astype(int)

    cols = [
        "rank", "driver", "score",
        "total_stops", "distinct_routes", "active_days",
        "otp_rate", "late_count", "late_rate",
        "avg_dwell", "cases_variance_total", "cases_variance_per_stop",
        "max_speed_mph", "avg_speed_mph", "idle_minutes_total", "door_moving_events",
    ]
    return out[[c for c in cols if c in out.columns]].sort_values("rank")


def compute_lane_profitability(
    revenue_df: pd.DataFrame, stops_df: pd.DataFrame
) -> pd.DataFrame:
    """Origin-state × destination-state margin matrix.

    Origin = state of the first PU stop on the route.
    Destination = state of the last SO stop on the route.
    Aggregates route_revenue (cost, revenue, margin) by (origin, destination).
    """
    if revenue_df is None or revenue_df.empty or stops_df is None or stops_df.empty:
        return pd.DataFrame()

    if not {"state", "stop_direction"}.issubset(stops_df.columns):
        return pd.DataFrame()

    df = stops_df.copy()
    df["route_id"] = _coerce_route_key(df)
    df["stop_seq_int"] = pd.to_numeric(df.get("stop_seq"), errors="coerce")
    df = df.sort_values(["route_id", "stop_seq_int", "actual_arrival"], na_position="last")

    # First PU state per route
    pu = df[df["stop_direction"].astype(str).str.upper() == "PU"]
    origin = (
        pu.groupby("route_id")["state"]
        .agg(lambda s: s.dropna().iloc[0] if not s.dropna().empty else None)
        .reset_index(name="origin_state")
    )
    # Last SO state per route
    so = df[df["stop_direction"].astype(str).str.upper() == "SO"]
    dest = (
        so.groupby("route_id")["state"]
        .agg(lambda s: s.dropna().iloc[-1] if not s.dropna().empty else None)
        .reset_index(name="dest_state")
    )

    rev = revenue_df.copy()
    rev["route_id"] = rev["route_id"].astype(str).str.strip()
    rev = rev.merge(origin, on="route_id", how="left").merge(dest, on="route_id", how="left")

    lane = (
        rev.dropna(subset=["origin_state", "dest_state"])
        .groupby(["origin_state", "dest_state"])
        .agg(
            routes=("route_id", "nunique"),
            cost=("cost", "sum"),
            revenue=("revenue", "sum"),
            margin=("margin", "sum"),
        )
        .reset_index()
    )
    lane["margin_pct"] = (
        lane["margin"] / lane["revenue"].replace(0, np.nan) * 100
    ).round(1)
    return lane.sort_values("margin", ascending=False)


def compute_claims_risk(
    stops_df: pd.DataFrame,
    *,
    setpoint_c: float = PLASMA_TEMP_SETPOINT_C,
    tolerance_c: float = PLASMA_TEMP_TOLERANCE_C,
    weight_short_cases: float = 0.40,
    weight_excursion: float = 0.40,
    weight_door_events: float = 0.20,
    door_event_count_threshold: int = 5,
    band_high: float = 70,
    band_medium: float = 40,
) -> pd.DataFrame:
    """Per-route claims-risk index (0-100).

    Combines:
        - case shortage magnitude (sum of |cases_variance| where < 0)
        - reefer temp excursion stop count (loaded only)
        - excessive door-open events (sum)

    Each component is min-max scaled across routes; weighted average × 100.
    All thresholds and weights are tunable via kwargs (driven by
    config/default.yaml at pipeline runtime).
    """
    if stops_df is None or stops_df.empty:
        return pd.DataFrame()

    df = stops_df.copy()
    df["route_id"] = _coerce_route_key(df)

    cv = pd.to_numeric(df.get("cases_variance"), errors="coerce").fillna(0)
    df["_short_cases"] = cv.where(cv < 0, 0).abs()

    is_loaded = df.get("loaded_at_stop", pd.Series(0, index=df.index)).fillna(0).astype(int) == 1
    min_s1 = pd.to_numeric(df.get("min_s1"), errors="coerce")
    max_s1 = pd.to_numeric(df.get("max_s1"), errors="coerce")
    excursion = (
        is_loaded
        & ((min_s1 < (setpoint_c - tolerance_c))
           | (max_s1 > (setpoint_c + tolerance_c)))
    )
    df["_excursion_loaded"] = excursion.astype(int)

    door = pd.to_numeric(df.get("door_open_events"), errors="coerce").fillna(0)
    df["_door_excess"] = door.where(door > door_event_count_threshold, 0)

    grouped = df.groupby("route_id")
    agg = grouped.agg(
        stop_count=("transaction_id", "count"),
        short_cases=("_short_cases", "sum"),
        excursion_stops=("_excursion_loaded", "sum"),
        excess_door_events=("_door_excess", "sum"),
        customer=("customer", lambda s: s.dropna().mode().iat[0] if not s.dropna().mode().empty else None),
        route_name=("route_name", lambda s: s.dropna().mode().iat[0] if not s.dropna().mode().empty else None),
    ).reset_index()

    def _scale(s: pd.Series) -> pd.Series:
        s = s.fillna(0)
        if s.max() == s.min():
            return pd.Series(0.0, index=s.index)
        return (s - s.min()) / (s.max() - s.min())

    n_short = _scale(agg["short_cases"])
    n_exc = _scale(agg["excursion_stops"])
    n_door = _scale(agg["excess_door_events"])

    agg["risk_score"] = (
        (weight_short_cases * n_short
         + weight_excursion * n_exc
         + weight_door_events * n_door) * 100
    ).round(1)

    def _band(score: float) -> str:
        if score >= band_high:
            return "HIGH"
        if score >= band_medium:
            return "MEDIUM"
        if score > 0:
            return "LOW"
        return "NONE"

    agg["risk_band"] = agg["risk_score"].apply(_band)
    cols = [
        "route_id", "route_name", "customer", "risk_score", "risk_band",
        "stop_count", "short_cases", "excursion_stops", "excess_door_events",
    ]
    return agg[cols].sort_values("risk_score", ascending=False)


def compute_cycle_time_consistency(stops_df: pd.DataFrame) -> pd.DataFrame:
    """Per-route-name cycle time consistency.

    cycle_time = last_departure − first_arrival in minutes.
    Returns aggregates per route_name: avg / median / std-dev / min / max
    cycle time across all PRO# instances of that named route. Low std dev =
    predictable, high = chaotic. Useful for promised-windows + capacity.
    """
    if stops_df is None or stops_df.empty:
        return pd.DataFrame()
    if "route_name" not in stops_df.columns:
        return pd.DataFrame()

    df = stops_df.copy()
    df["route_id"] = _coerce_route_key(df)

    arr = pd.to_datetime(df.get("actual_arrival"), errors="coerce")
    dep = pd.to_datetime(df.get("actual_departure"), errors="coerce")

    per_route = (
        df.assign(_arr=arr, _dep=dep)
        .groupby("route_id", dropna=False)
        .agg(
            route_name=("route_name", lambda s: s.dropna().mode().iat[0] if not s.dropna().mode().empty else None),
            customer=("customer", lambda s: s.dropna().mode().iat[0] if not s.dropna().mode().empty else None),
            stops=("transaction_id", "count"),
            first_arrival=("_arr", "min"),
            last_departure=("_dep", "max"),
        )
        .reset_index()
    )
    per_route["cycle_minutes"] = (
        (per_route["last_departure"] - per_route["first_arrival"]).dt.total_seconds() / 60
    )
    per_route = per_route[per_route["cycle_minutes"].notna() & (per_route["cycle_minutes"] > 0)]
    if per_route.empty:
        return pd.DataFrame()

    out = (
        per_route.dropna(subset=["route_name"])
        .groupby("route_name")
        .agg(
            instances=("route_id", "nunique"),
            customer=("customer", lambda s: s.dropna().mode().iat[0] if not s.dropna().mode().empty else None),
            avg_stops=("stops", "mean"),
            avg_cycle_min=("cycle_minutes", "mean"),
            median_cycle_min=("cycle_minutes", "median"),
            std_cycle_min=("cycle_minutes", "std"),
            min_cycle_min=("cycle_minutes", "min"),
            max_cycle_min=("cycle_minutes", "max"),
        )
        .reset_index()
    )
    for c in ("avg_cycle_min", "median_cycle_min", "std_cycle_min", "min_cycle_min", "max_cycle_min", "avg_stops"):
        if c in out.columns:
            out[c] = out[c].round(0)
    out["consistency_pct"] = (
        100 - (out["std_cycle_min"] / out["avg_cycle_min"].replace(0, pd.NA) * 100)
    ).round(1)
    out["consistency_pct"] = out["consistency_pct"].clip(lower=0, upper=100)
    out = out[out["instances"] >= 2]  # need >= 2 instances to compute std
    return out.sort_values("std_cycle_min", ascending=False, na_position="last")


def compute_late_code_analysis(stops_df: pd.DataFrame) -> pd.DataFrame:
    """Rank late_code values by impact — count, late stops, customers, distinct routes."""
    if stops_df is None or stops_df.empty or "late_code" not in stops_df.columns:
        return pd.DataFrame()

    df = stops_df.copy()
    df["late_code"] = df["late_code"].astype(str).str.strip().str.upper()
    df = df[df["late_code"].notna() & (df["late_code"] != "") & (df["late_code"] != "NAN")]
    if df.empty:
        return pd.DataFrame()

    out = (
        df.groupby("late_code")
        .agg(
            occurrences=("transaction_id", "count"),
            distinct_customers=("customer", "nunique"),
            distinct_routes=("order_#", "nunique") if "order_#" in df.columns else ("transaction_id", "nunique"),
            distinct_drivers=("drivers", "nunique"),
            avg_minutes_late=("minutes_from_appt", "mean"),
        )
        .reset_index()
    )
    out["avg_minutes_late"] = out["avg_minutes_late"].round(0)
    return out.sort_values("occurrences", ascending=False)


def compute_detention_audit(
    stops_df: pd.DataFrame, threshold_minutes: int = 120
) -> pd.DataFrame:
    """Stops where dwell exceeded threshold — billable detention candidates.

    Returns one row per stop with dwell > threshold; per-customer rollup
    shows hours of detention by customer (the conversation to have with
    sales / billing).
    """
    if stops_df is None or stops_df.empty or "dwell_minutes" not in stops_df.columns:
        return pd.DataFrame()

    df = stops_df.copy()
    df["dwell_minutes"] = pd.to_numeric(df["dwell_minutes"], errors="coerce")
    long_dwells = df[df["dwell_minutes"] > threshold_minutes].copy()
    if long_dwells.empty:
        return pd.DataFrame()

    out = (
        long_dwells.dropna(subset=["customer"])
        .groupby("customer")
        .agg(
            detention_stops=("transaction_id", "count"),
            total_dwell_min=("dwell_minutes", "sum"),
            avg_dwell_min=("dwell_minutes", "mean"),
            max_dwell_min=("dwell_minutes", "max"),
            distinct_routes=("order_#", "nunique") if "order_#" in long_dwells.columns else ("transaction_id", "nunique"),
        )
        .reset_index()
    )
    out["billable_hours"] = (out["total_dwell_min"] / 60).round(1)
    out["avg_dwell_min"] = out["avg_dwell_min"].round(0)
    out["max_dwell_min"] = out["max_dwell_min"].round(0)
    out["threshold_minutes"] = threshold_minutes
    return out.sort_values("billable_hours", ascending=False)


def compute_demand_forecast(
    stops_df: pd.DataFrame,
    horizon_weeks: int = 4,
    *,
    alpha: float = 0.5,
    min_weeks_history: int = 3,
) -> pd.DataFrame:
    """Simple exponential-smoothing forecast of weekly stops per customer.

    For each customer with >= ``min_weeks_history`` weeks of history, fit
    a simple ES model and project ``horizon_weeks`` weeks of stops.
    """
    if stops_df is None or stops_df.empty or "customer" not in stops_df.columns:
        return pd.DataFrame()

    df = stops_df.copy()
    df["customer"] = df["customer"].astype(str).str.strip().str.upper()
    df["arrival_dt"] = pd.to_datetime(df["arrival_date"], errors="coerce")
    df = df[df["arrival_dt"].notna() & df["customer"].notna() & (df["customer"] != "")]
    if df.empty:
        return pd.DataFrame()
    df["week"] = df["arrival_dt"].dt.to_period("W").dt.start_time

    weekly = df.groupby(["customer", "week"]).size().reset_index(name="stops")

    forecasts: list[dict] = []
    for cust, g in weekly.groupby("customer"):
        g = g.sort_values("week")
        if len(g) < min_weeks_history:
            continue
        # Simple exponential smoothing in pure pandas
        s_prev = float(g["stops"].iloc[0])
        for v in g["stops"].iloc[1:]:
            s_prev = alpha * float(v) + (1 - alpha) * s_prev
        last_week = g["week"].iloc[-1]
        avg_stops_per_wk = float(g["stops"].mean())
        for h in range(1, horizon_weeks + 1):
            forecasts.append({
                "customer": cust,
                "forecast_week": last_week + pd.Timedelta(weeks=h),
                "horizon": h,
                "forecast_stops": round(s_prev, 1),
                "history_avg_per_week": round(avg_stops_per_wk, 1),
                "weeks_of_history": int(len(g)),
            })

    if not forecasts:
        return pd.DataFrame(columns=["customer", "forecast_week", "horizon", "forecast_stops", "history_avg_per_week", "weeks_of_history"])
    return pd.DataFrame(forecasts).sort_values(["customer", "horizon"])


def compute_customer_scorecard(
    stops_df: pd.DataFrame, revenue_df: pd.DataFrame | None = None
) -> pd.DataFrame:
    """One row per customer with the metrics you'd put in front of an account exec.

    Combines: stop volume, OTP, dwell, claims (cases short), excursion stops,
    plus revenue/cost/margin if route_revenue is provided.
    """
    if stops_df is None or stops_df.empty or "customer" not in stops_df.columns:
        return pd.DataFrame()

    df = stops_df.copy()
    df["customer"] = df["customer"].astype(str).str.strip().str.upper()
    df = df[df["customer"].notna() & (df["customer"] != "") & (df["customer"] != "NAN")]
    if df.empty:
        return pd.DataFrame()

    df["arrival_dt"] = pd.to_datetime(df["arrival_date"], errors="coerce")
    cv = pd.to_numeric(df.get("cases_variance"), errors="coerce").fillna(0)
    df["_short"] = cv.where(cv < 0, 0).abs()
    df["_late"] = (df.get("stop_performance_status") == "Late").astype(int)
    is_loaded = df.get("loaded_at_stop", pd.Series(0, index=df.index)).fillna(0).astype(int) == 1
    min_s1 = pd.to_numeric(df.get("min_s1"), errors="coerce")
    df["_excursion_loaded"] = (
        is_loaded & ((min_s1 < (PLASMA_TEMP_SETPOINT_C - PLASMA_TEMP_TOLERANCE_C)))
    ).astype(int)

    out = (
        df.groupby("customer")
        .agg(
            stops=("transaction_id", "count"),
            distinct_routes=("order_#", "nunique") if "order_#" in df.columns else ("transaction_id", "nunique"),
            active_days=("arrival_date", "nunique"),
            otp_rate=("otp_time_pass", "mean"),
            late_stops=("_late", "sum"),
            avg_dwell_min=("dwell_minutes", "mean"),
            short_cases_total=("_short", "sum"),
            excursion_stops=("_excursion_loaded", "sum"),
            first_seen=("arrival_dt", "min"),
            last_seen=("arrival_dt", "max"),
        )
        .reset_index()
    )
    out["otp_rate"] = (out["otp_rate"] * 100).round(1)
    out["avg_dwell_min"] = out["avg_dwell_min"].round(0)
    out["late_rate_pct"] = (out["late_stops"] / out["stops"] * 100).round(1)
    out["claims_per_stop"] = (out["short_cases_total"] / out["stops"]).round(1)

    # Revenue / margin overlay
    if revenue_df is not None and not revenue_df.empty and "customer" in revenue_df.columns:
        rev = (
            revenue_df.dropna(subset=["customer"])
            .assign(customer=lambda d: d["customer"].astype(str).str.strip().str.upper())
            .groupby("customer")
            .agg(
                cost=("cost", "sum"),
                revenue=("revenue", "sum"),
                margin=("margin", "sum"),
            )
            .reset_index()
        )
        out = out.merge(rev, on="customer", how="left")
        out["margin_pct"] = (
            out["margin"] / out["revenue"].replace(0, pd.NA) * 100
        ).round(1)

    cols = [
        "customer", "stops", "distinct_routes", "active_days",
        "otp_rate", "late_rate_pct", "avg_dwell_min",
        "short_cases_total", "claims_per_stop", "excursion_stops",
        "revenue", "cost", "margin", "margin_pct",
        "first_seen", "last_seen",
    ]
    return out[[c for c in cols if c in out.columns]].sort_values(
        "revenue" if "revenue" in out.columns else "stops", ascending=False, na_position="last"
    )


def compute_customer_churn_signal(
    stops_df: pd.DataFrame,
    *,
    band_churn_risk_pct: float = -50.0,
    band_declining_pct: float = -20.0,
    band_growing_pct: float = 50.0,
) -> pd.DataFrame:
    """Week-over-week PRO# count per customer; flag drops >= threshold."""
    if stops_df is None or stops_df.empty or "customer" not in stops_df.columns:
        return pd.DataFrame()

    df = stops_df.copy()
    df["customer"] = df["customer"].astype(str).str.strip().str.upper()
    df["arrival_dt"] = pd.to_datetime(df["arrival_date"], errors="coerce")
    df = df[df["arrival_dt"].notna() & df["customer"].notna() & (df["customer"] != "")]
    if df.empty:
        return pd.DataFrame()
    df["week"] = df["arrival_dt"].dt.to_period("W").dt.start_time

    by_week = (
        df.groupby(["customer", "week"])
        .agg(pros=("order_#", "nunique") if "order_#" in df.columns else ("transaction_id", "nunique"),
             stops=("transaction_id", "count"))
        .reset_index()
    )
    if by_week.empty:
        return pd.DataFrame()

    by_week = by_week.sort_values(["customer", "week"])
    by_week["prev_pros"] = by_week.groupby("customer")["pros"].shift(1)
    by_week["delta_pros"] = by_week["pros"] - by_week["prev_pros"]
    by_week["delta_pct"] = (
        by_week["delta_pros"] / by_week["prev_pros"].replace(0, pd.NA) * 100
    ).round(1)

    # Latest week per customer
    latest = (
        by_week.sort_values("week").groupby("customer").tail(1).copy()
    )

    def _band(p):
        if pd.isna(p):
            return "NEW"
        if p <= band_churn_risk_pct:
            return "CHURN_RISK"
        if p <= band_declining_pct:
            return "DECLINING"
        if p >= band_growing_pct:
            return "GROWING"
        return "STABLE"

    latest["churn_band"] = latest["delta_pct"].apply(_band)

    cols = ["customer", "week", "pros", "prev_pros", "delta_pros", "delta_pct", "churn_band"]
    return latest[cols].sort_values("delta_pct", ascending=True, na_position="last")


def compute_customer_concentration(revenue_df: pd.DataFrame | None) -> pd.DataFrame:
    """Pareto / concentration view: revenue share by customer with cumulative %."""
    if revenue_df is None or revenue_df.empty or "customer" not in revenue_df.columns:
        return pd.DataFrame()
    df = revenue_df.copy()
    df = df.dropna(subset=["customer"])
    df["customer"] = df["customer"].astype(str).str.strip().str.upper()
    grouped = (
        df.groupby("customer")
        .agg(revenue=("revenue", "sum"), cost=("cost", "sum"), margin=("margin", "sum"))
        .reset_index()
        .sort_values("revenue", ascending=False)
        .reset_index(drop=True)
    )
    total_rev = grouped["revenue"].sum() or 1
    grouped["share_pct"] = (grouped["revenue"] / total_rev * 100).round(1)
    grouped["cumulative_share_pct"] = grouped["share_pct"].cumsum().round(1)
    grouped["rank"] = grouped.index + 1
    return grouped


def compute_route_reefer_cost(stops_df: pd.DataFrame) -> pd.DataFrame:
    """Per-route reefer runtime, gallons, and fuel cost rollup."""
    if stops_df is None or stops_df.empty:
        return pd.DataFrame()
    if "reefer_fuel_cost" not in stops_df.columns:
        return pd.DataFrame()

    df = stops_df.copy()
    df["route_id"] = _coerce_route_key(df)
    out = (
        df.groupby("route_id", dropna=False)
        .agg(
            reefer_runtime_minutes=("reefer_runtime_minutes", "sum"),
            reefer_gallons=("reefer_gallons", "sum"),
            reefer_fuel_cost=("reefer_fuel_cost", "sum"),
            stops_with_telem=("telem_events", lambda s: int((s.fillna(0) > 0).sum())),
            customer=("customer", lambda s: s.dropna().mode().iat[0] if not s.dropna().mode().empty else None),
            route_name=("route_name", lambda s: s.dropna().mode().iat[0] if not s.dropna().mode().empty else None),
        )
        .reset_index()
    )
    out["reefer_runtime_hours"] = (out["reefer_runtime_minutes"] / 60).round(1)
    out["reefer_gallons"] = out["reefer_gallons"].round(1)
    out["reefer_fuel_cost"] = out["reefer_fuel_cost"].round(2)
    return out.sort_values("reefer_fuel_cost", ascending=False)


def compute_alarm_log(stops_df: pd.DataFrame) -> pd.DataFrame:
    """Per-trailer alarm counts and last-seen timestamp from stop-aggregated data."""
    if stops_df is None or stops_df.empty:
        return pd.DataFrame()
    if "alarm_events" not in stops_df.columns or "trailer" not in stops_df.columns:
        return pd.DataFrame()

    df = stops_df.copy()
    df = df[df["alarm_events"].fillna(0) > 0]
    if df.empty:
        return pd.DataFrame()

    df["arrival_dt"] = pd.to_datetime(df["arrival_date"], errors="coerce")
    out = (
        df.groupby("trailer", dropna=False)
        .agg(
            alarm_event_total=("alarm_events", "sum"),
            stops_with_alarms=("transaction_id", "count"),
            first_alarm_date=("arrival_dt", "min"),
            last_alarm_date=("arrival_dt", "max"),
            last_known_state=("state", lambda s: s.dropna().iloc[-1] if not s.dropna().empty else None),
        )
        .reset_index()
    )
    return out.sort_values("alarm_event_total", ascending=False)


def compute_trailer_utilization(
    stops_df: pd.DataFrame, m3pl_df: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Per-trailer asset utilization summary.

    For each trailer that appears in CRST stops, compute:
        - first_seen / last_seen dates
        - active_days  (distinct dates with at least one stop)
        - period_days  (max - min date across the WHOLE dataset)
        - idle_days = period_days - active_days
        - utilization_pct = active_days / period_days * 100
        - max_consecutive_idle_days  (longest streak with no activity)
        - total_stops, loaded_stops, loaded_pct
        - stops_per_active_day, distinct_routes, distinct_drivers,
          distinct_customers
        - total_miles (sum of M3PL miles for routes this trailer ran)
        - miles_per_active_day
        - last_known_state, last_known_city
    """
    if stops_df is None or stops_df.empty or "trailer" not in stops_df.columns:
        return pd.DataFrame()

    df = stops_df.copy()
    df["trailer"] = df["trailer"].astype(str).str.strip()
    df = df[df["trailer"].notna() & (df["trailer"] != "") & (df["trailer"] != "nan")]
    if df.empty:
        return pd.DataFrame()

    df["arrival_dt"] = pd.to_datetime(df["arrival_date"], errors="coerce")
    df = df[df["arrival_dt"].notna()]
    if df.empty:
        return pd.DataFrame()

    period_start = df["arrival_dt"].min()
    period_end = df["arrival_dt"].max()
    period_days = max((period_end - period_start).days + 1, 1)

    # Per-trailer base aggregates
    df["route_id"] = _coerce_route_key(df)
    is_loaded = df.get("loaded_at_stop", pd.Series(0, index=df.index)).fillna(0).astype(int)

    base = (
        df.groupby("trailer")
        .agg(
            first_seen=("arrival_dt", "min"),
            last_seen=("arrival_dt", "max"),
            total_stops=("transaction_id", "count"),
            loaded_stops=("loaded_at_stop", lambda s: int(pd.to_numeric(s, errors="coerce").fillna(0).sum())),
            distinct_routes=("route_id", "nunique"),
            distinct_drivers=("drivers", "nunique"),
            distinct_customers=("customer", "nunique"),
        )
        .reset_index()
    )

    # active_days = distinct dates per trailer
    active = (
        df.assign(d=df["arrival_dt"].dt.normalize())
        .groupby("trailer")["d"]
        .nunique()
        .reset_index(name="active_days")
    )
    base = base.merge(active, on="trailer", how="left")

    # max consecutive idle days
    def _max_idle(group: pd.DataFrame) -> int:
        days = sorted(group["arrival_dt"].dt.normalize().unique())
        if len(days) <= 1:
            return 0
        gaps = [
            (days[i + 1] - days[i]).days - 1 for i in range(len(days) - 1)
        ]
        return int(max(gaps)) if gaps else 0

    idle = df.groupby("trailer", group_keys=False).apply(
        _max_idle, include_groups=False
    )
    if isinstance(idle, pd.Series):
        idle = idle.reset_index(name="max_consecutive_idle_days")
    base = base.merge(idle, on="trailer", how="left")

    base["period_days"] = period_days
    base["idle_days"] = (base["period_days"] - base["active_days"]).clip(lower=0)
    base["utilization_pct"] = (base["active_days"] / base["period_days"] * 100).round(1)
    base["loaded_pct"] = (base["loaded_stops"] / base["total_stops"] * 100).round(1)
    base["stops_per_active_day"] = (base["total_stops"] / base["active_days"]).round(2)

    # Miles via M3PL
    if m3pl_df is not None and not m3pl_df.empty:
        m = m3pl_df.copy()
        m["route_id"] = m["pro_number"].astype(str).str.strip()
        miles_per_route = m.groupby("route_id")["crst_miles"].sum().to_dict()
        df["route_miles"] = df["route_id"].map(miles_per_route).fillna(0)
        per_route = (
            df.drop_duplicates(subset=["trailer", "route_id"])
            .groupby("trailer")["route_miles"]
            .sum()
            .reset_index(name="total_miles")
        )
        base = base.merge(per_route, on="trailer", how="left")
        base["total_miles"] = base["total_miles"].fillna(0).round(0)
    else:
        base["total_miles"] = 0.0

    base["miles_per_active_day"] = (
        base["total_miles"] / base["active_days"].replace(0, pd.NA)
    ).round(0)

    # Last known location (state, city) at the most recent stop
    df_sorted = df.sort_values(["trailer", "arrival_dt"], ascending=[True, False])
    last_loc = df_sorted.drop_duplicates(subset=["trailer"], keep="first")[
        ["trailer", "state", "city"]
    ].rename(columns={"state": "last_known_state", "city": "last_known_city"})
    base = base.merge(last_loc, on="trailer", how="left")

    # Health signals from telemetry aggregations on stop_master
    health_aggs: dict = {}
    for col, fn, out_col in [
        ("alarm_events", "sum", "alarm_event_total"),
        ("min_battery", "min", "min_battery_seen"),
        ("avg_battery", "mean", "avg_battery"),
        ("max_engine_hours", "max", "max_engine_hours"),
        ("max_total_hours", "max", "max_total_hours"),
        ("reefer_fuel_cost", "sum", "reefer_fuel_cost_total"),
        ("reefer_gallons", "sum", "reefer_gallons_total"),
        ("idle_minutes", "sum", "idle_minutes_total"),
        ("door_open_while_moving", "sum", "door_open_while_moving_total"),
        ("setpoint_changes", "sum", "setpoint_changes_total"),
        ("fill_pct_cases", "mean", "avg_fill_pct_cases"),
        ("fill_pct_cases", "max", "max_fill_pct_cases"),
        ("fill_pct_weight", "mean", "avg_fill_pct_weight"),
        ("fill_pct_weight", "max", "max_fill_pct_weight"),
    ]:
        if col in df.columns:
            health_aggs[out_col] = (col, fn)

    if health_aggs:
        health = df.groupby("trailer").agg(**health_aggs).reset_index()
        for c in ("min_battery_seen", "avg_battery"):
            if c in health.columns:
                health[c] = health[c].round(2)
        for c in ("reefer_fuel_cost_total", "reefer_gallons_total", "idle_minutes_total"):
            if c in health.columns:
                health[c] = health[c].round(1)
        for c in ("max_engine_hours", "max_total_hours"):
            if c in health.columns:
                health[c] = pd.to_numeric(health[c], errors="coerce").round(0)
        for c in ("avg_fill_pct_cases", "max_fill_pct_cases",
                  "avg_fill_pct_weight", "max_fill_pct_weight"):
            if c in health.columns:
                health[c] = health[c].round(1)
        # Pct of stops over 90% capacity (over-loaded warning signal)
        if "fill_pct_cases" in df.columns:
            over90 = (
                df.assign(_over=(pd.to_numeric(df["fill_pct_cases"], errors="coerce") >= 90).astype(int))
                .groupby("trailer")["_over"]
                .mean()
                .reset_index(name="pct_stops_over_90")
            )
            over90["pct_stops_over_90"] = (over90["pct_stops_over_90"] * 100).round(1)
            health = health.merge(over90, on="trailer", how="left")
        base = base.merge(health, on="trailer", how="left")

    cols = [
        "trailer", "utilization_pct", "active_days", "idle_days",
        "max_consecutive_idle_days", "first_seen", "last_seen", "period_days",
        "total_stops", "loaded_stops", "loaded_pct", "stops_per_active_day",
        "distinct_routes", "distinct_drivers", "distinct_customers",
        "total_miles", "miles_per_active_day",
        "alarm_event_total", "min_battery_seen", "avg_battery",
        "max_engine_hours", "max_total_hours",
        "reefer_fuel_cost_total", "reefer_gallons_total",
        "idle_minutes_total", "door_open_while_moving_total", "setpoint_changes_total",
        "avg_fill_pct_cases", "max_fill_pct_cases",
        "avg_fill_pct_weight", "max_fill_pct_weight",
        "pct_stops_over_90",
        "last_known_state", "last_known_city",
    ]
    return base[[c for c in cols if c in base.columns]].sort_values(
        "utilization_pct", ascending=False, na_position="last"
    )


def compute_temp_compliance(
    stops_df: pd.DataFrame,
    setpoint_c: float = PLASMA_TEMP_SETPOINT_C,
    tolerance_c: float = PLASMA_TEMP_TOLERANCE_C,
    min_excursion_minutes: int = TEMP_EXCURSION_MIN_MINUTES,
) -> pd.DataFrame:
    """Per-route reefer temperature compliance summary.

    A stop only counts as an "excursion" if BOTH:
      - The trailer was loaded with product at the stop (``loaded_at_stop == 1``).
      - ``min_s1`` or ``max_s1`` falls outside ``setpoint_c ± tolerance_c``.

    Empty-trailer stops are tracked separately (``empty_stops_skipped``) so the
    user can audit them, but they do not pull a route's compliance flag down.
    """
    if stops_df is None or stops_df.empty:
        return pd.DataFrame()

    df = stops_df.copy()
    df["route_id"] = _coerce_route_key(df)

    has_telem = df["telem_events"].fillna(0) > 0 if "telem_events" in df.columns else pd.Series(False, index=df.index)
    is_loaded = df["loaded_at_stop"].fillna(0).astype(int) == 1 if "loaded_at_stop" in df.columns else pd.Series(False, index=df.index)

    if "min_s1" in df.columns and "max_s1" in df.columns:
        out_of_range = has_telem & (
            (df["min_s1"] < (setpoint_c - tolerance_c))
            | (df["max_s1"] > (setpoint_c + tolerance_c))
        )
    else:
        out_of_range = pd.Series(False, index=df.index)

    # Excursion only counts when loaded
    df["_excursion"] = (out_of_range & is_loaded).astype(int)
    df["_excursion_minutes"] = (df["_excursion"] * min_excursion_minutes).astype(float)
    df["_loaded_with_telem"] = (has_telem & is_loaded).astype(int)
    df["_empty_skipped"] = (out_of_range & ~is_loaded).astype(int)

    grouped = df.groupby("route_id", dropna=False)

    agg = grouped.agg(
        stops_with_telemetry=("telem_events", lambda s: int((s.fillna(0) > 0).sum())),
        loaded_stops_with_telemetry=("_loaded_with_telem", "sum"),
        empty_stops_skipped=("_empty_skipped", "sum"),
        min_temp=("min_s1", "min"),
        max_temp=("max_s1", "max"),
        avg_temp=("avg_amb_temp", "mean"),
        excursion_count=("_excursion", "sum"),
        excursion_minutes=("_excursion_minutes", "sum"),
        door_open_events_total=("door_open_events", "sum"),
    ).reset_index()

    agg["compliance_flag"] = np.where(agg["excursion_count"] > 0, "EXCURSION", "OK")
    agg["min_temp"] = agg["min_temp"].round(1)
    agg["max_temp"] = agg["max_temp"].round(1)
    agg["avg_temp"] = agg["avg_temp"].round(1)
    agg["setpoint_c"] = setpoint_c
    agg["tolerance_c"] = tolerance_c
    return agg.sort_values("excursion_count", ascending=False)
