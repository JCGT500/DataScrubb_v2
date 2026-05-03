# =========================================================
# BUSINESS CONSTANTS
# =========================================================
# These are defaults; they can be overridden via config/default.yaml

# On-Time Performance
OTP_TOLERANCE_MINUTES = 120

# SAP matching
SAP_MATCH_MAX_HOURS = 36

# Telemetry
TELEMETRY_WINDOW_MINUTES = 120
TELEMETRY_MIN_PINGS_PER_STOP = 5
TELEMETRY_DEFAULT_HEADER_ROW = 6

# CRST header detection
CRST_HEADER_KEYWORDS = ["order", "location", "actual", "original", "current"]
CRST_HEADER_MAX_ROWS = 15

# CRST required columns (after normalization)
CRST_REQUIRED_COLUMNS = [
    "order_#",
    "location_date",
    "original_appt",
    "current_appt",
    "actual_arrival",
]

# SAP required columns (after normalization)
SAP_REQUIRED_COLUMNS = [
    "document_number",
    "segment_number",
    "shipper_search_term",
    "pick_up_date",
    "arrive",
]

# Telemetry temperature columns
TELEMETRY_TEMP_COLUMNS = [
    "amb_temp",
    "da1",
    "ra1",
    "s1",
    "s2",
    "s3",
    "s4",
    "s5",
    "s6",
    "tl1",
]

# Other numeric telemetry columns we coerce / aggregate
TELEMETRY_NUMERIC_COLUMNS = [
    "speed",
    "engine_rpm",
    "engine_hours",
    "total_hours",
    "battery_voltage",
    "sp1",  # set point
]

# Telemetry sampling interval (minutes between consecutive events from the
# reefer unit). Samples come in at ~15 min intervals from the AI Troubleshooting
# CSV. Used to convert event-counts to runtime minutes.
TELEMETRY_SAMPLE_INTERVAL_MINUTES = 15

# Stop type labels
STOP_TYPE_PLASMA = "PLASMA_CENTER"
STOP_TYPE_WAREHOUSE = "WAREHOUSE"
