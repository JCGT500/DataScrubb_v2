"""Reusable Plotly chart builders."""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from datascrubb.utils.geo import lookup_city, normalize_state


def otp_by_s_code(df: pd.DataFrame, otp_col: str = "otp_time_pass") -> go.Figure:
    """Horizontal bar chart of OTP pass rate by S-Code."""
    data = (
        df[df["s_code"].notna()]
        .groupby("s_code")[otp_col]
        .mean()
        .sort_values()
        .reset_index()
    )
    data[otp_col] = (data[otp_col] * 100).round(1)

    fig = px.bar(
        data, x=otp_col, y="s_code", orientation="h",
        labels={otp_col: "OTP %", "s_code": "S-Code"},
        title="On-Time Performance by S-Code",
        color=otp_col,
        color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
        range_color=[0, 100],
    )
    fig.update_layout(height=max(300, len(data) * 25), showlegend=False)
    return fig


def performance_distribution(df: pd.DataFrame) -> go.Figure:
    """Pie chart of stop performance status distribution."""
    if "stop_performance_status" not in df.columns:
        return go.Figure()

    counts = df["stop_performance_status"].value_counts().reset_index()
    counts.columns = ["status", "count"]

    color_map = {
        "On Time": "#22c55e",
        "Late": "#ef4444",
        "Early": "#3b82f6",
        "Missing Arrival": "#9ca3af",
        "Missing Appointment": "#6b7280",
    }

    fig = px.pie(
        counts, names="status", values="count",
        title="Stop Performance Distribution",
        color="status",
        color_discrete_map=color_map,
    )
    return fig


def otp_trend(df: pd.DataFrame, otp_col: str = "otp_time_pass") -> go.Figure:
    """Line chart of OTP pass rate over time."""
    if "arrival_date" not in df.columns:
        return go.Figure()

    daily = (
        df.assign(date=pd.to_datetime(df["arrival_date"], errors="coerce"))
        .dropna(subset=["date"])
        .groupby("date")[otp_col]
        .mean()
        .reset_index()
    )
    daily[otp_col] = (daily[otp_col] * 100).round(1)

    fig = px.line(
        daily, x="date", y=otp_col,
        labels={otp_col: "OTP %", "date": "Date"},
        title="OTP Trend Over Time",
        markers=True,
    )
    fig.update_layout(yaxis_range=[0, 105])
    return fig


def minutes_histogram(df: pd.DataFrame) -> go.Figure:
    """Histogram of minutes from appointment."""
    if "minutes_from_appt" not in df.columns:
        return go.Figure()

    data = df["minutes_from_appt"].dropna()

    fig = px.histogram(
        data, nbins=50,
        labels={"value": "Minutes from Appointment", "count": "Stops"},
        title="Distribution of Arrival vs. Appointment Time",
    )
    fig.add_vline(x=-120, line_dash="dash", line_color="orange", annotation_text="-120 min")
    fig.add_vline(x=120, line_dash="dash", line_color="orange", annotation_text="+120 min")
    fig.add_vline(x=0, line_dash="solid", line_color="green", annotation_text="On Time")
    return fig


def stops_per_day(df: pd.DataFrame) -> go.Figure:
    """Bar chart of stop count per day."""
    if "arrival_date" not in df.columns:
        return go.Figure()

    daily = (
        df.assign(date=pd.to_datetime(df["arrival_date"], errors="coerce"))
        .dropna(subset=["date"])
        .groupby("date")
        .size()
        .reset_index(name="stops")
    )

    fig = px.bar(
        daily, x="date", y="stops",
        labels={"date": "Date", "stops": "Stop Count"},
        title="Stops Per Day",
    )
    return fig


def otp_by_customer(df: pd.DataFrame, otp_col: str = "otp_time_pass", top_n: int = 25) -> go.Figure:
    """Horizontal bar of OTP rate per customer (top_n by stop count)."""
    if "customer" not in df.columns or df.empty:
        return go.Figure()
    grp = (
        df[df["customer"].notna()]
        .groupby("customer")
        .agg(stops=("transaction_id", "count"), otp=(otp_col, "mean"))
        .reset_index()
    )
    if grp.empty:
        return go.Figure()
    grp["otp_pct"] = (grp["otp"] * 100).round(1)
    grp = grp.nlargest(top_n, "stops").sort_values("otp_pct")
    fig = px.bar(
        grp, x="otp_pct", y="customer", orientation="h",
        color="otp_pct",
        color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
        range_color=[0, 100],
        title=f"OTP % by Customer (top {top_n} by volume)",
        labels={"otp_pct": "OTP %", "customer": "Customer"},
        hover_data=["stops"],
    )
    fig.update_layout(height=max(300, len(grp) * 22), showlegend=False)
    return fig


def heatmap_temp_by_route_seq(df: pd.DataFrame) -> go.Figure:
    """Heat map: route_name × stop_seq → min S1 reefer temp.

    Routes that frequently dip out of spec stand out as red columns.
    """
    if df.empty or not {"route_name", "stop_seq", "min_s1"}.issubset(df.columns):
        return go.Figure()
    sub = df[df["min_s1"].notna() & df["route_name"].notna()].copy()
    if sub.empty:
        return go.Figure()
    sub["stop_seq_int"] = pd.to_numeric(sub["stop_seq"], errors="coerce")
    pivot = (
        sub.groupby(["route_name", "stop_seq_int"])["min_s1"]
        .min()
        .reset_index()
        .pivot(index="route_name", columns="stop_seq_int", values="min_s1")
        .sort_index()
    )
    if pivot.empty:
        return go.Figure()
    fig = px.imshow(
        pivot,
        color_continuous_scale="RdYlBu_r",
        aspect="auto",
        labels={"x": "Stop #", "y": "Route", "color": "Min S1 °C"},
        title="Reefer Min S1 Temperature — Route × Stop #",
    )
    fig.update_layout(height=max(400, len(pivot) * 18))
    return fig


def heatmap_door_events_by_dow_hour(df: pd.DataFrame) -> go.Figure:
    """Heat map: hour-of-day × day-of-week → total door open events."""
    if df.empty or not {"actual_arrival", "door_open_events"}.issubset(df.columns):
        return go.Figure()
    sub = df[df["door_open_events"].notna() & df["actual_arrival"].notna()].copy()
    if sub.empty:
        return go.Figure()
    ts = pd.to_datetime(sub["actual_arrival"], errors="coerce")
    sub = sub[ts.notna()]
    ts = ts[ts.notna()]
    sub["hour"] = ts.dt.hour
    sub["dow"] = ts.dt.day_name()
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    pivot = (
        sub.groupby(["dow", "hour"])["door_open_events"]
        .sum()
        .reset_index()
        .pivot(index="dow", columns="hour", values="door_open_events")
        .reindex(order)
        .fillna(0)
    )
    fig = px.imshow(
        pivot,
        color_continuous_scale="Blues",
        aspect="auto",
        labels={"x": "Hour", "y": "Day", "color": "Door Open Events"},
        title="Door-Open Activity — Day of Week × Hour",
    )
    fig.update_layout(height=350)
    return fig


def late_stops_state_choropleth(df: pd.DataFrame) -> go.Figure:
    """Choropleth of late stops per US state.

    A "late" stop = stop_performance_status == "Late". Falls back to OTP failure
    (otp_time_pass == 0) when status isn't present.
    """
    if df.empty or "state" not in df.columns:
        return go.Figure()

    sub = df.copy()
    sub["state_code"] = sub["state"].apply(normalize_state)
    sub = sub[sub["state_code"].notna()]

    if "stop_performance_status" in sub.columns:
        late_mask = sub["stop_performance_status"] == "Late"
    else:
        late_mask = sub["otp_time_pass"] == 0

    state_totals = (
        sub.assign(is_late=late_mask.astype(int))
        .groupby("state_code")
        .agg(late_stops=("is_late", "sum"), total_stops=("is_late", "count"))
        .reset_index()
    )
    state_totals["late_pct"] = (
        state_totals["late_stops"] / state_totals["total_stops"] * 100
    ).round(1)

    fig = px.choropleth(
        state_totals,
        locations="state_code", locationmode="USA-states",
        color="late_stops", scope="usa",
        color_continuous_scale="Reds",
        labels={"late_stops": "Late Stops"},
        hover_data={"state_code": True, "late_stops": True, "total_stops": True, "late_pct": True},
        title="Late Stops by State",
    )
    fig.update_layout(height=500)
    return fig


def late_stops_city_map(df: pd.DataFrame, max_cities: int = 100) -> go.Figure:
    """Scatter-geo of late stops per city, sized by count.

    Uses the offline ``geonamescache`` lookup so no network calls are made.
    Cities that fail to resolve are skipped (they still show on the choropleth).
    """
    if df.empty or "city" not in df.columns or "state" not in df.columns:
        return go.Figure()

    sub = df.copy()
    if "stop_performance_status" in sub.columns:
        late_mask = sub["stop_performance_status"] == "Late"
    else:
        late_mask = sub["otp_time_pass"] == 0

    grouped = (
        sub.assign(is_late=late_mask.astype(int))
        .groupby(["city", "state"], dropna=False)
        .agg(late_stops=("is_late", "sum"), total_stops=("is_late", "count"))
        .reset_index()
    )
    grouped["late_pct"] = (
        grouped["late_stops"] / grouped["total_stops"] * 100
    ).round(1)

    # Geocode
    coords = grouped.apply(
        lambda r: lookup_city(r["city"], r["state"]) or (None, None), axis=1
    )
    grouped["lat"] = [c[0] for c in coords]
    grouped["lon"] = [c[1] for c in coords]

    grouped = grouped.dropna(subset=["lat", "lon"])
    grouped = grouped[grouped["late_stops"] > 0]

    if grouped.empty:
        return go.Figure()

    grouped = grouped.nlargest(max_cities, "late_stops")
    grouped["label"] = grouped["city"] + ", " + grouped["state"].astype(str)

    fig = px.scatter_geo(
        grouped, lat="lat", lon="lon",
        size="late_stops", color="late_pct",
        hover_name="label",
        hover_data={"late_stops": True, "total_stops": True, "late_pct": True, "lat": False, "lon": False},
        color_continuous_scale="Reds", range_color=[0, 100],
        scope="usa", projection="albers usa",
        title=f"Late Stops by City (top {max_cities})",
        labels={"late_pct": "Late %", "late_stops": "Late Stops"},
    )
    fig.update_layout(height=550)
    fig.update_geos(showsubunits=True, subunitcolor="lightgray")
    return fig


def heatmap_excursions_by_customer_dow(df: pd.DataFrame) -> go.Figure:
    """Heat map: customer × day-of-week → reefer excursion stops (min S1 < -30 or > -20)."""
    if df.empty or not {"customer", "actual_arrival", "min_s1"}.issubset(df.columns):
        return go.Figure()
    sub = df[df["min_s1"].notna() & df["customer"].notna()].copy()
    if sub.empty:
        return go.Figure()
    sub["excursion"] = ((sub["min_s1"] < -30) | (sub["min_s1"] > -20)).astype(int)
    ts = pd.to_datetime(sub["actual_arrival"], errors="coerce")
    sub = sub[ts.notna()]
    sub["dow"] = ts[ts.notna()].dt.day_name()
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    # Limit to top 20 customers by stop count
    top_customers = sub["customer"].value_counts().head(20).index.tolist()
    sub = sub[sub["customer"].isin(top_customers)]
    pivot = (
        sub.groupby(["customer", "dow"])["excursion"]
        .sum()
        .reset_index()
        .pivot(index="customer", columns="dow", values="excursion")
        .reindex(columns=order)
        .fillna(0)
    )
    if pivot.empty:
        return go.Figure()
    fig = px.imshow(
        pivot,
        color_continuous_scale="Reds",
        aspect="auto",
        labels={"x": "Day", "y": "Customer", "color": "Excursion stops"},
        title="Reefer Excursions — Customer × Day of Week (top 20 customers)",
    )
    fig.update_layout(height=max(400, len(pivot) * 22))
    return fig
