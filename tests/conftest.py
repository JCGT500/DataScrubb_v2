"""Shared test fixtures for DataScrubb tests."""

from datetime import datetime, timedelta

import pandas as pd
import pytest


@pytest.fixture
def sample_stops() -> pd.DataFrame:
    """Synthetic stop_master-shaped DataFrame with 10 stops across 2 routes.

    Route A (1001 / CSL / AMES):
      - 5 PU/SO sequence
      - All loaded
      - On time
      - One stop short by 10 cases
    Route B (1002 / BIOLIFE / AUGUSTA):
      - 5 PU/SO sequence
      - 2 late stops (140 min late)
      - One stop with reefer excursion (min_s1 = -32, outside -25 ± 5)
      - One stop over by 5 cases
      - One detention candidate (220 min dwell)
      - Some door-open-while-moving + alarm events
    """
    base = datetime(2026, 1, 5, 8, 0)
    rows = []
    for i in range(5):
        rows.append({
            "transaction_id": f"1001_2026-01-0{5+i}_01",
            "order_#": "1001",
            "order_number": "1001",
            "route_name": "AMES",
            "customer": "CSL",
            "s_code": "S1234",
            "stop_type": "PLASMA_CENTER",
            "stop_direction": "PU" if i < 4 else "SO",
            "tender_cases": 100.0,
            "current_cases": 100.0 * (4 - i) if i < 4 else 0.0,
            "cases_variance": 0.0 if i != 2 else -10.0,
            "loaded_at_stop": 1,
            "city": "AMES",
            "state": "IA" if i < 4 else "KY",
            "arrival_date": (base + timedelta(days=i)).strftime("%Y-%m-%d"),
            "actual_arrival": base + timedelta(days=i, minutes=15),
            "actual_departure": base + timedelta(days=i, minutes=75),
            "dwell_minutes": 60.0,
            "current_appt": base + timedelta(days=i),
            "original_appt": base + timedelta(days=i),
            "resolved_appt": base + timedelta(days=i),
            "minutes_from_appt": 15.0,
            "otp_time_pass": 1.0,
            "otp_day_pass": 1.0,
            "otp_original_pass": 1.0,
            "otp_time_original_pass": 1.0,
            "stop_performance_status": "On Time",
            "trailer": "RX001",
            "truck": "T01",
            "drivers": "JDOE",
            "stop_seq": f"{i+1:02d}",
            "min_s1": -27.0,
            "max_s1": -23.0,
            "avg_amb_temp": -25.0,
            "min_amb_temp": -27.0,
            "max_amb_temp": -23.0,
            "telem_events": 10,
            "door_open_events": 2,
            "door_open_while_moving": 0,
            "alarm_events": 0,
            "min_battery": 12.5,
            "avg_battery": 12.7,
            "idle_minutes": 30.0,
            "max_speed": 65.0,
            "avg_speed": 50.0,
            "reefer_runtime_minutes": 60.0,
            "reefer_gallons": 0.0,
            "reefer_fuel_cost": 0.0,
            "max_engine_hours": 5000 + i,
            "max_total_hours": 6000 + i,
            "setpoint_changes": 0,
            "late_code": "",
            "error_flag": "N",
            "error_reason": "",
            "sum_of_weight": 1000.0,
        })
    for i in range(5):
        rows.append({
            "transaction_id": f"1002_2026-01-0{8+i}_01",
            "order_#": "1002",
            "order_number": "1002",
            "route_name": "AUGUSTA",
            "customer": "BIOLIFE",
            "s_code": "S5678",
            "stop_type": "PLASMA_CENTER",
            "stop_direction": "PU" if i < 4 else "SO",
            "tender_cases": 50.0,
            "current_cases": 50.0 * (4 - i) if i < 4 else 0.0,
            "cases_variance": 5.0 if i == 1 else 0.0,
            "loaded_at_stop": 1,
            "city": "AUGUSTA",
            "state": "GA" if i < 4 else "KY",
            "arrival_date": (base + timedelta(days=3 + i)).strftime("%Y-%m-%d"),
            "actual_arrival": base + timedelta(days=3 + i, minutes=140),
            "actual_departure": base + timedelta(days=3 + i, hours=4),
            "dwell_minutes": 220.0 if i == 0 else 100.0,
            "current_appt": base + timedelta(days=3 + i),
            "original_appt": base + timedelta(days=3 + i),
            "resolved_appt": base + timedelta(days=3 + i),
            "minutes_from_appt": 140.0 if i < 2 else 30.0,
            "otp_time_pass": 0.0 if i < 2 else 1.0,
            "otp_day_pass": 1.0,
            "otp_original_pass": 1.0,
            "otp_time_original_pass": 0.0 if i < 2 else 1.0,
            "stop_performance_status": "Late" if i < 2 else "On Time",
            "trailer": "RX002",
            "truck": "T02",
            "drivers": "ASMITH",
            "stop_seq": f"{i+1:02d}",
            "min_s1": -32.0 if i == 2 else -27.0,
            "max_s1": -22.0,
            "avg_amb_temp": -24.0,
            "min_amb_temp": -28.0,
            "max_amb_temp": -20.0,
            "telem_events": 10,
            "door_open_events": 1,
            "door_open_while_moving": 1 if i == 1 else 0,
            "alarm_events": 3 if i == 2 else 0,
            "min_battery": 11.5,
            "avg_battery": 12.0,
            "idle_minutes": 60.0,
            "max_speed": 70.0,
            "avg_speed": 55.0,
            "reefer_runtime_minutes": 90.0,
            "reefer_gallons": 0.0,
            "reefer_fuel_cost": 0.0,
            "max_engine_hours": 7000 + i,
            "max_total_hours": 8000 + i,
            "setpoint_changes": 1 if i == 0 else 0,
            "late_code": "WX" if i < 2 else "",
            "error_flag": "N",
            "error_reason": "",
            "sum_of_weight": 800.0,
        })
    return pd.DataFrame(rows)


@pytest.fixture
def sample_m3pl() -> pd.DataFrame:
    """Synthetic M3PL billing snapshot — one PRO per route in sample_stops."""
    return pd.DataFrame([
        {
            "pro_number": "1001", "legacy_route": "AMES", "lane": "Erlanger Legacy",
            "crst_miles": 1500.0, "stop_count": 5.0,
            "team_miles": 0.0, "solo_miles": 1500.0,
            "team_deficit_miles": 0.0, "solo_deficit_miles": 0.0, "tolls": 50.0,
            "stop_rate": 76.30, "team_rate": 1.75, "solo_rate": 1.52,
            "team_deficit_rate": 1.16, "solo_deficit_rate": 0.97,
            "billed_miles_amount": 2280.0, "billed_stops_amount": 381.5,
            "billed_deficit_amount": 0.0, "billed_amount": 2711.5,
            "tractor": "T01", "trailer": "RX001",
            "billing_week_end": pd.Timestamp("2026-01-10"),
            "source_file": "test.xlsx",
        },
        {
            "pro_number": "1002", "legacy_route": "AUGUSTA", "lane": "Whitestown",
            "crst_miles": 800.0, "stop_count": 5.0,
            "team_miles": 0.0, "solo_miles": 800.0,
            "team_deficit_miles": 0.0, "solo_deficit_miles": 0.0, "tolls": 25.0,
            "stop_rate": 79.80, "team_rate": 1.85, "solo_rate": 1.67,
            "team_deficit_rate": 1.16, "solo_deficit_rate": 0.97,
            "billed_miles_amount": 1336.0, "billed_stops_amount": 399.0,
            "billed_deficit_amount": 0.0, "billed_amount": 1760.0,
            "tractor": "T02", "trailer": "RX002",
            "billing_week_end": pd.Timestamp("2026-01-10"),
            "source_file": "test.xlsx",
        },
    ])


@pytest.fixture
def sample_crst_raw():
    """Minimal CRST DataFrame after column normalization."""
    return pd.DataFrame({
        "order_#": ["ORD001", "ORD001", "ORD002"],
        "location_date": ["S12345 Chicago", "Warehouse A", "S67890 Denver"],
        "original_appt": pd.to_datetime(["2026-04-10 08:00", "2026-04-10 12:00", "2026-04-11 09:00"]),
        "current_appt": pd.to_datetime(["2026-04-10 08:30", None, "2026-04-11 09:00"]),
        "actual_arrival": pd.to_datetime(["2026-04-10 08:15", "2026-04-10 12:30", None]),
    })


@pytest.fixture
def sample_sap_raw():
    """Minimal SAP DataFrame after column normalization."""
    return pd.DataFrame({
        "document_number": ["DOC100", "DOC101"],
        "segment_number": ["1", "1"],
        "shipper_search_term": ["S12345 Plasma Inc", "S99999 Unknown"],
        "pick_up_date": ["2026-04-10", "2026-04-11"],
        "arrive": ["08:00:00", "10:00:00"],
        "shipper_name": ["Plasma Center A", "Plasma Center B"],
        "consignee_name": ["Warehouse X", "Warehouse Y"],
    })


@pytest.fixture
def sample_telemetry_raw():
    """Minimal telemetry DataFrame after column normalization."""
    return pd.DataFrame({
        "date_&_time": pd.to_datetime([
            "2026-04-10 07:00", "2026-04-10 08:00", "2026-04-10 09:00",
        ]),
        "vehicle_name": ["TRAILER01", "TRAILER01", "TRAILER01"],
        "door_1": ["C", "O", "C"],
        "amb_temp": [35.0, 36.5, 34.0],
        "s1": [-20.0, -19.5, -21.0],
    })
