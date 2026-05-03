"""Live Map — interactive Folium map of stops, routes, trailers, customers.

Layers (toggleable via Folium's LayerControl):
    - Stops      circles per stop, colored by chosen metric, popups with detail
    - Routes     polylines connecting stops on the same route in stop_seq order
    - Trailers   markers at each trailer's last_known_city
    - Customers  bubbles per customer city, sized by stop count

Time-slider variant (separate tab): folium.plugins.TimestampedGeoJson scrubs
through stops in chronological order with play/pause controls.

Future GIS hook
---------------
The page positions stops/trailers via city/state geocoding (see
``datascrubb/utils/geo.py::lookup_city``). Once telemetry's free-text
``Position`` column is parsed into raw lat/lon, swap the trailer layer to
real-time GPS pings + breadcrumb polylines. The next step after that is
feeding the weekly trailer / route revenue tables and trailer last-known
state into a routing optimizer (e.g. OR-tools VRP) for trailer-to-route
suggestions.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from folium.plugins import MarkerCluster, TimestampedGeoJson
from streamlit_folium import st_folium

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datascrubb.config import load_config
from datascrubb.db import get_engine
from datascrubb.utils.geo import lookup_city


# ─────────────── data loaders ───────────────

def _load(table: str) -> pd.DataFrame:
    cfg = load_config()
    if not cfg.db_path.exists():
        return pd.DataFrame()
    engine = get_engine(cfg.db_path)
    try:
        return pd.read_sql(f"SELECT * FROM {table}", engine)
    except Exception:
        return pd.DataFrame()


@st.cache_data(show_spinner=False)
def _geocode_stops(stops: pd.DataFrame) -> pd.DataFrame:
    """Add lat/lon columns to a stops DataFrame using offline geocoding."""
    if stops.empty or "city" not in stops.columns or "state" not in stops.columns:
        return stops
    out = stops.copy()
    coords = out.apply(
        lambda r: lookup_city(r.get("city"), r.get("state")) or (None, None), axis=1
    )
    out["lat"] = [c[0] for c in coords]
    out["lon"] = [c[1] for c in coords]
    return out


# ─────────────── color helpers ───────────────

METRIC_DEFS = {
    "OTP %": ("otp_time_pass", "higher_better"),
    "Margin (route)": ("_margin", "higher_better"),
    "Claims-risk": ("_risk", "lower_better"),
    "Fill % cases": ("fill_pct_cases", "higher_better"),
    "Reefer excursion (loaded)": ("_excursion", "lower_better"),
    "Alarm events": ("alarm_events", "lower_better"),
    "Dwell minutes": ("dwell_minutes", "lower_better"),
}


def _color_for(value, direction: str, vmin: float = 0, vmax: float = 100) -> str:
    """Map a numeric value to a hex color (red → orange → green or vice versa)."""
    if value is None or pd.isna(value):
        return "#9ca3af"
    span = max(vmax - vmin, 1e-9)
    pct = max(0, min(1, (value - vmin) / span))
    if direction == "lower_better":
        pct = 1 - pct
    if pct >= 0.7:
        return "#22c55e"
    if pct >= 0.4:
        return "#f59e0b"
    return "#ef4444"


def _stop_popup(row: pd.Series) -> str:
    """Build an HTML popup for a stop CircleMarker."""
    bits = [
        f"<b>{row.get('customer', '?')}</b> · {row.get('city', '?')}, {row.get('state', '?')}",
        f"Route: {row.get('route_name', '?')} ({row.get('order_number', '?')})",
        f"Stop seq: {row.get('stop_seq', '?')} · {row.get('stop_direction', '?')}",
        f"Arrival: {row.get('arrival_date', '?')}",
        f"Status: <b>{row.get('stop_performance_status', '?')}</b>",
        f"Dwell: {row.get('dwell_minutes', '?')} min",
        f"Fill %: {row.get('fill_pct_cases', '?')}",
        f"Trailer: {row.get('trailer', '?')} · Driver: {row.get('drivers', '?')}",
    ]
    return "<br>".join(str(b) for b in bits)


def _trailer_popup(row: pd.Series) -> str:
    bits = [
        f"<b>Trailer {row.get('trailer', '?')}</b>",
        f"Last known: {row.get('last_known_city', '?')}, {row.get('last_known_state', '?')}",
        f"Utilization: {row.get('utilization_pct', '?')}%",
        f"Stops: {row.get('total_stops', '?')} · Routes: {row.get('distinct_routes', '?')}",
        f"Alarms: {row.get('alarm_event_total', '?')}",
        f"Min battery: {row.get('min_battery_seen', '?')} V",
    ]
    return "<br>".join(str(b) for b in bits)


# ─────────────── builders ───────────────

def _build_stops_layer(
    stops: pd.DataFrame, metric_col: str, direction: str, vmin: float, vmax: float
) -> folium.FeatureGroup:
    fg = folium.FeatureGroup(name="Stops", show=True)
    cluster = MarkerCluster().add_to(fg)
    for _, row in stops.iterrows():
        if pd.isna(row.get("lat")) or pd.isna(row.get("lon")):
            continue
        val = row.get(metric_col)
        color = _color_for(val, direction, vmin, vmax)
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=5,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            weight=1,
            popup=folium.Popup(_stop_popup(row), max_width=320),
            tooltip=f"{row.get('customer', '?')} · {row.get('stop_performance_status', '?')}",
        ).add_to(cluster)
    return fg


def _build_routes_layer(stops: pd.DataFrame) -> folium.FeatureGroup:
    fg = folium.FeatureGroup(name="Routes (polylines)", show=False)
    if "order_number" not in stops.columns:
        return fg
    for route_id, group in stops.dropna(subset=["lat", "lon"]).groupby("order_number"):
        group = group.sort_values("stop_seq")
        if len(group) < 2:
            continue
        coords = list(zip(group["lat"], group["lon"]))
        route_name = group["route_name"].dropna().iloc[0] if group["route_name"].notna().any() else "?"
        folium.PolyLine(
            locations=coords,
            color="#3b82f6",
            weight=2,
            opacity=0.6,
            tooltip=f"Route {route_name} ({route_id}) · {len(group)} stops",
        ).add_to(fg)
    return fg


def _build_trailers_layer(util_df: pd.DataFrame) -> folium.FeatureGroup:
    fg = folium.FeatureGroup(name="Trailers (last known)", show=False)
    if util_df.empty:
        return fg
    util = util_df.copy()
    coords = util.apply(
        lambda r: lookup_city(r.get("last_known_city"), r.get("last_known_state")) or (None, None), axis=1
    )
    util["lat"] = [c[0] for c in coords]
    util["lon"] = [c[1] for c in coords]
    util = util.dropna(subset=["lat", "lon"])
    for _, row in util.iterrows():
        util_pct = row.get("utilization_pct", 0) or 0
        color = _color_for(util_pct, "higher_better", 0, 100)
        radius = max(4, min(12, int((row.get("total_stops", 0) or 0) / 5)))
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=radius,
            color="#1f2937",
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            weight=1.5,
            popup=folium.Popup(_trailer_popup(row), max_width=320),
            tooltip=f"Trailer {row['trailer']} · {util_pct:.0f}% util",
        ).add_to(fg)
    return fg


def _build_customers_layer(stops: pd.DataFrame) -> folium.FeatureGroup:
    fg = folium.FeatureGroup(name="Customers (volume)", show=False)
    if stops.empty or "customer" not in stops.columns:
        return fg
    grouped = (
        stops.dropna(subset=["lat", "lon", "customer"])
        .groupby(["customer", "city", "state"])
        .agg(stops=("transaction_id", "count"), lat=("lat", "first"), lon=("lon", "first"))
        .reset_index()
    )
    if grouped.empty:
        return fg
    max_stops = grouped["stops"].max()
    for _, row in grouped.iterrows():
        radius = 4 + int(20 * (row["stops"] / max_stops)) if max_stops else 6
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=radius,
            color="#7c3aed",
            fill=True,
            fill_color="#a78bfa",
            fill_opacity=0.5,
            weight=1,
            popup=folium.Popup(
                f"<b>{row['customer']}</b><br>{row['city']}, {row['state']}<br>{row['stops']} stops",
                max_width=240,
            ),
            tooltip=f"{row['customer']} · {row['stops']} stops",
        ).add_to(fg)
    return fg


def _build_timestamped_geojson(stops: pd.DataFrame, metric_col: str, direction: str, vmin: float, vmax: float) -> TimestampedGeoJson:
    features = []
    for _, row in stops.iterrows():
        if pd.isna(row.get("lat")) or pd.isna(row.get("lon")):
            continue
        ts = row.get("arrival_date")
        if pd.isna(ts) or not ts:
            continue
        val = row.get(metric_col)
        color = _color_for(val, direction, vmin, vmax)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
            "properties": {
                "time": str(ts),
                "popup": _stop_popup(row),
                "icon": "circle",
                "iconstyle": {
                    "fillColor": color,
                    "fillOpacity": 0.7,
                    "stroke": True,
                    "color": color,
                    "weight": 1,
                    "radius": 6,
                },
            },
        })
    return TimestampedGeoJson(
        {"type": "FeatureCollection", "features": features},
        period="P1D",
        duration="P1D",
        add_last_point=True,
        auto_play=False,
        loop=False,
        max_speed=10,
        loop_button=True,
        date_options="YYYY-MM-DD",
        time_slider_drag_update=True,
    )


# ─────────────── main render ───────────────

def render():
    st.header("Live Map")
    st.caption(
        "Stops, routes, trailers, and customers on an interactive map. "
        "Toggle layers on/off, hover/click for detail, switch the metric color, "
        "or scrub through time on the **Time slider** tab."
    )

    stops_df = _load("stop_master")
    util_df = _load("trailer_utilization")
    rev_df = _load("route_revenue")
    risk_df = _load("claims_risk")

    if stops_df.empty:
        st.info("No stop data yet. Run the pipeline from **Load Data**.")
        return

    cfg = load_config()
    map_cfg = getattr(cfg, "map", None)
    cfg_height = getattr(map_cfg, "default_height_px", 900) if map_cfg else 900
    cfg_max_stops = getattr(map_cfg, "default_max_stops_render", 1500) if map_cfg else 1500

    # ── Sidebar controls ──
    with st.sidebar:
        st.subheader("Map filters")
        dates = pd.to_datetime(stops_df["arrival_date"], errors="coerce").dropna()
        if dates.empty:
            st.warning("No valid arrival dates.")
            return
        min_d, max_d = dates.min().date(), dates.max().date()
        start_end = st.date_input(
            "Date range (pick one day for snapshot)",
            value=(min_d, max_d),
            min_value=min_d, max_value=max_d,
            key="map_date_range_main",
        )
        if isinstance(start_end, tuple) and len(start_end) == 2:
            start, end = start_end
        else:
            start = end = start_end if isinstance(start_end, date) else min_d

        metric_label = st.selectbox(
            "Metric color (Stops layer)",
            list(METRIC_DEFS.keys()),
            index=0,
        )
        max_stops_render = st.slider(
            "Max stops to render", min_value=100, max_value=5000, value=cfg_max_stops, step=100,
            help="Pulled in newest-first to keep the map snappy.",
        )

    # ── Filter stops by date ──
    stops_df["arrival_dt"] = pd.to_datetime(stops_df["arrival_date"], errors="coerce")
    stops_df = stops_df[
        (stops_df["arrival_dt"].dt.date >= start)
        & (stops_df["arrival_dt"].dt.date <= end)
    ]

    if stops_df.empty:
        st.warning("No stops in selected date range.")
        return

    # Cap rendering for performance
    stops_df = stops_df.sort_values("arrival_dt", ascending=False).head(max_stops_render)

    # Bring metric data into stops_df
    if "_margin" in METRIC_DEFS[metric_label][0] or metric_label == "Margin (route)":
        if not rev_df.empty:
            margin_map = rev_df.set_index(rev_df["route_id"].astype(str))["margin"].to_dict()
            stops_df["_margin"] = stops_df["order_number"].astype(str).map(margin_map).fillna(0)
        else:
            stops_df["_margin"] = 0
    if "_risk" in METRIC_DEFS[metric_label][0] or metric_label == "Claims-risk":
        if not risk_df.empty:
            risk_map = risk_df.set_index(risk_df["route_id"].astype(str))["risk_score"].to_dict()
            stops_df["_risk"] = stops_df["order_number"].astype(str).map(risk_map).fillna(0)
        else:
            stops_df["_risk"] = 0
    if metric_label == "Reefer excursion (loaded)":
        loaded = stops_df["loaded_at_stop"].fillna(0).astype(int) == 1
        out_of_range = (
            (pd.to_numeric(stops_df.get("min_s1"), errors="coerce") < -30)
            | (pd.to_numeric(stops_df.get("max_s1"), errors="coerce") > -20)
        )
        stops_df["_excursion"] = (loaded & out_of_range).astype(int)

    # Geocode
    stops_df = _geocode_stops(stops_df)
    geocoded = stops_df.dropna(subset=["lat", "lon"])
    if geocoded.empty:
        st.warning("No stops in this period have geocodable city/state.")
        return

    # Determine vmin/vmax for the chosen metric
    metric_col, direction = METRIC_DEFS[metric_label]
    vals = pd.to_numeric(geocoded[metric_col], errors="coerce").dropna() if metric_col in geocoded.columns else pd.Series([0])
    vmin = float(vals.min()) if len(vals) else 0
    vmax = float(vals.max()) if len(vals) else 100
    if metric_col in ("otp_time_pass",):
        vmin, vmax = 0, 1  # 0/1 boolean

    # ── KPI strip ──
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Stops on map", f"{len(geocoded):,}")
    c2.metric("Date range", f"{start} → {end}")
    c3.metric("Routes", f"{geocoded['order_number'].nunique():,}")
    c4.metric("Customers", f"{geocoded['customer'].nunique():,}")

    # ── Tabs: Static interactive map vs Time slider ──
    tab1, tab2 = st.tabs(["Layered map", "Time slider"])

    center_lat = geocoded["lat"].mean()
    center_lon = geocoded["lon"].mean()

    with tab1:
        st.caption("Toggle layers via the layer-control widget (top-right of map).")
        m = folium.Map(
            location=[center_lat, center_lon], zoom_start=4,
            tiles="OpenStreetMap", control_scale=True,
        )
        _build_stops_layer(geocoded, metric_col, direction, vmin, vmax).add_to(m)
        _build_routes_layer(geocoded).add_to(m)
        _build_trailers_layer(util_df).add_to(m)
        _build_customers_layer(geocoded).add_to(m)
        folium.LayerControl(collapsed=False).add_to(m)
        st_folium(m, height=cfg_height, use_container_width=True, returned_objects=[])

    with tab2:
        st.caption(
            "Press play to scrub through stops chronologically. "
            "Drag the slider to jump. Speed controls in the bottom-left."
        )
        m2 = folium.Map(
            location=[center_lat, center_lon], zoom_start=4,
            tiles="OpenStreetMap", control_scale=True,
        )
        _build_timestamped_geojson(geocoded, metric_col, direction, vmin, vmax).add_to(m2)
        st_folium(m2, height=700, returned_objects=[])
