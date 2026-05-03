# DataScrubb

Transportation data integration & analytics for plasma / refrigerated freight operations. Ingests CRST stop-level data, SAP shipment segments, reefer telemetry, and weekly M3PL invoice files; computes route, customer, driver, trailer, and lane KPIs (OTP, dwell, claims, reefer compliance, **cost vs revenue margin** from a per-customer rate matrix, equipment utilization, alarms, churn signal, demand forecast); presents everything in a Streamlit dashboard.

> **Status:** internal tool, private repo. Customer data files are excluded from version control by `.gitignore`.

> **For HOW each metric is calculated**, see [LOGIC.md](LOGIC.md).

---

## Table of contents

1. [What's in here](#whats-in-here)
2. [Quick start](#quick-start)
3. [Required data files](#required-data-files)
4. [Running the pipeline](#running-the-pipeline)
5. [Running the dashboard](#running-the-dashboard)
6. [Dashboard pages](#dashboard-pages)
7. [Customer rate matrix](#customer-rate-matrix)
8. [Data model](#data-model)
9. [Outputs](#outputs)
10. [Validation rules](#validation-rules)
11. [Troubleshooting](#troubleshooting)
12. [Architecture](#architecture)
13. [For developers](#for-developers)

---

## What's in here

| Capability | Source data | Where it lives |
|---|---|---|
| Stop-level OTP (4 metrics) | CRST | `stop_master`, OTP Analysis page |
| Customer extraction & filter | CRST `location_date` | `customer` column |
| Loaded vs empty trailer detection | CRST `stop_type` + `current_cases` | `loaded_at_stop` flag |
| Route KPIs (OTP, dwell, stops) | CRST stops rolled up | `route_kpi`, Route KPIs page |
| Loaded vs deadhead miles per route | CRST + M3PL | `loaded_miles`, Route KPIs page |
| Cross-source SAP join (S-Code + ±36 h) | CRST + SAP | `sap_segment`, SAP Matching page |
| Reefer telemetry stop aggregation | Telemetry CSV | `telemetry_stop` (25+ cols) |
| Load-aware reefer excursions | telemetry + load flag | `temp_compliance`, Telemetry page |
| Reefer fuel cost per route | telemetry power-on + fuel rate | `route_reefer_cost` |
| Equipment alarm log | telemetry `Unit Alarm` | `alarm_log`, Telemetry page |
| Idle time + speed per stop | telemetry `Speed` + `Engine RPM` | `telemetry_stop`, Driver Scorecard |
| Door-open-while-moving safety flag | telemetry `Door 1` + `Speed` | `telemetry_stop`, Telemetry page |
| Battery & engine-hours health | telemetry `Battery Voltage` / `Engine Hours` | `trailer_utilization`, Telemetry page |
| M3PL **cost** ingest (weekly) | M3PL invoice xlsx | `billing_snapshot` |
| Customer **revenue** + margin | rate matrix YAML + CRST + M3PL | `route_revenue`, Cost vs Revenue page |
| Lane profitability (origin × destination) | route_revenue + stops | `lane_profitability`, Cost vs Revenue page |
| Case shortage / overage tracking | CRST `cases_variance` | Case Variance page |
| Claims-risk index per route | cases + temp + door events | `claims_risk`, Case Variance page |
| Driver scorecard (composite 0-100) | OTP + dwell + cases + speed + idle | `driver_scorecard`, Driver Scorecard page |
| Trailer utilization & idle assets | CRST + telemetry health | `trailer_utilization`, Trailer Utilization page |
| Customer service scorecard | CRST + revenue | `customer_scorecard`, Customer Insights page |
| Customer churn signal (week-over-week) | CRST stops by week | `customer_churn`, Customer Insights page |
| Customer revenue concentration (Pareto) | route_revenue | `customer_concentration`, Customer Insights page |
| Cycle-time consistency per named route | CRST arrivals/departures | `cycle_time`, Operations Insights page |
| Late code root-cause ranking | CRST `late_code` | `late_code_analysis`, Operations Insights page |
| Detention audit (billable hours per customer) | CRST dwell > 2hr | `detention_audit`, Operations Insights page |
| 4-week demand forecast per customer | CRST weekly stops | `demand_forecast`, Operations Insights page |
| **Vanguard Cooling Index (VCI)** per trailer | telemetry DA1/RA1/S1-S6/op_1 | `trailer_vci`, Reefer Diagnostics page |
| Per-trailer rolling baselines (evap delta, compliance, defrost) | telemetry, last 30 days | `vanguard_baselines` |
| Reefer readiness check ("can I load frozen plasma?") | VCI + hot-load detection | `trailer_vci.can_load_frozen` |
| Vanguard alerts (positive delta, hot-load, high defrost, bulkhead) | VCI overrides + thresholds | `vanguard_alerts`, Reefer Diagnostics page |
| Late-stops geographic map | CRST city/state | OTP Analysis page (state choropleth + city pins) |
| Heat maps (route×stop temp, day×hour, customer×day) | CRST + telemetry | Telemetry page |
| Multi-sheet Excel export | All of the above | `output/Trans_KPI_Validation_<runid>.xlsx` (32 sheets) |

---

## Quick start

### Windows (PowerShell or bash)

```bash
git clone https://github.com/JCGT500/DataScrubb_v2.git
cd datascrubb

python -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pip install -e .

# Drop your data files into the project root (see "Required data files")
# Then run the dashboard:
.venv/Scripts/python.exe -m streamlit run dashboard/app.py
```

The dashboard opens at <http://localhost:8501>. Use the **Load Data** page to upload files and run the pipeline; every other page reads from the resulting SQLite DB.

### Mac / Linux

```bash
git clone https://github.com/JCGT500/DataScrubb_v2.git
cd datascrubb
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
streamlit run dashboard/app.py
```

### VS Code (one-click)

`.vscode/launch.json` provides two configurations:

- **Streamlit: DataScrubb dashboard** — F5 launches Streamlit with the right Python interpreter.
- **Pipeline: CRST + SAP + Telemetry + M3PL (current files)** — F5 runs `run_pipeline.py` against whatever data files are in the project root. Useful for breakpoints / headless reruns.

> **Important:** when launching from VS Code, pick the right config from the **Run and Debug** dropdown (Ctrl+Shift+D). The default "Run Python File" button just runs the .py directly and won't render a Streamlit UI.

---

## Required data files

Drop these into the project root. **Do not commit them** — `.gitignore` excludes them by name pattern.

| File pattern | What it is | Required? |
|---|---|---|
| `CRST data*.xlsx` | TMS export — one row per stop with appointments, arrivals, cases, BOL, driver, equipment | **Yes** |
| `SAP*.xlsx` | SAP shipment-segment export with shipper/consignee, BOL, cases, weight | Optional |
| `AI Troubleshooting*.csv` (or any reefer telemetry CSV) | Per-event telemetry: timestamp, vehicle, GPS, ambient/reefer temps, door, engine, speed, fuel, alarms | Optional |
| `Backup *M3PL*.xlsx` (one per billing week) | Weekly carrier invoice with INVOICE + SUMMARY sheets | Optional |

The `m3pl` source accepts a list of files — `run_pipeline.py` auto-discovers all matching `Backup *M3PL*.xlsx` files in the project root.

---

## Running the pipeline

### From the dashboard (recommended for analysts)

1. Start Streamlit: `.venv/Scripts/python.exe -m streamlit run dashboard/app.py`
2. Go to **Load Data**.
3. Upload the CRST file (required) and any of SAP / Telemetry / M3PL (multiple M3PL files OK).
4. Click **Run Pipeline**. ~90 seconds for a typical month's data.
5. Result metrics appear at the top; Excel output is written to `output/`. Every other page now reads from the populated SQLite DB.

### From the command line

```bash
.venv/Scripts/python.exe run_pipeline.py
```

`run_pipeline.py` discovers any matching data files in the project root and runs everything end-to-end, then prints a summary.

### From Python

```python
from datascrubb.pipeline import Pipeline

result = Pipeline().run(source_files={
    "crst": "CRST data - 2026-Jan.xlsx",
    "sap":  "SAP_Jan thru_feb.xlsx",
    "telemetry": "AI Troubleshooting_050126_193038_14_60663.csv",
    "m3pl": [
        "Backup 36673 of M3PL 01032026.xlsx",
        "Backup 36962 M3PL INVOICE 01.17.26.xlsx",
        "Backup 37323 M3PL INVOICE WE 1.31.2026.xlsx",
    ],
}, export_excel=True)

print(result)
```

---

## Running the dashboard

```bash
.venv/Scripts/python.exe -m streamlit run dashboard/app.py
```

Open <http://localhost:8501>. The sidebar has:

- **Navigation** — pick a page (top of sidebar).
- **Filters** — shared across every data page. Selections persist as you switch pages until you click **Reset filters** at the bottom.

### Shared filters

| Filter | Applies to |
|---|---|
| Date range (Start / End) | Every stop / billing row by `arrival_date` / `billing_week_end` |
| Customer | Stops whose extracted customer matches |
| Route Name | Stops whose CRST `route_name` matches (e.g. AMES, AUGUSTA) |
| S-Code | Stops with the selected plasma center code |
| Lane | M3PL billing rows in the selected lane |
| Stop Type | All / PLASMA_CENTER / WAREHOUSE |
| Performance | On Time / Late / Early / Missing Arrival / Missing Appointment |
| Search Order# | Substring match on order number / PRO# |

---

## Dashboard pages

Listed in nav order.

### Overview
Headline KPI cards (stops, OTP %, errors, distinct routes/customers, avg dwell, total billed, SAP match rate). Performance distribution pie + stops-per-day bar + OTP trend line + **OTP by customer** bar.

### Load Data
File uploaders for CRST (required) plus SAP / Telemetry / M3PL (M3PL accepts multiple weekly files). Click **Run Pipeline** to process everything end-to-end. Result row shows stops, billing rows, SAP match rate, telemetry coverage, M3PL match rate, error counts.

### Route KPIs
Per-route rollup: OTP rate, avg dwell minutes, stop count, equipment used. Sortable by OTP / stops / dwell / late minutes. Top-25 horizontal bar of OTP, plus a stops × dwell scatter colored by OTP. **Loaded vs Deadhead Miles** section with KPI cards (total/loaded/deadhead/loaded%) and top-20 deadhead bar. Three tabs at bottom for **Drivers / Tractors / Trailers** utilization.

### Cost vs Revenue *(margin analysis)*
- **Cost** = M3PL `billed_amount` per PRO#.
- **Revenue** = miles × $/mile + stops × $/stop + cwt × $/cwt, floored at minimum charge (from rate matrix).
- **Margin** = revenue − cost.

Four tabs: *Margin by Route*, *Cost (M3PL) detail*, *Customer rollup*, *Lane Profitability* (origin-state × destination-state heatmap).

### Rate Matrix
View, edit, **upload**, download per-customer rate table.

- **Upload** CSV/Excel with columns `customer`, `rate_per_mile`, `rate_per_stop`, `rate_per_cwt`, `minimum_charge`. Tolerant of header variants.
- Save modes: **Replace** or **Merge**.
- Templates (CSV + Excel) and current-matrix export.
- Inline `st.data_editor` for row-by-row edits.

### Case Variance
Stops where `tender_cases ≠ current_cases`. Net variance, total short, total over. Breakouts by **Customer**, **Driver**, **Route**. Per-stop detail. **Claims-Risk Index** section: 0-100 composite blending case shortages (40%), loaded reefer excursions (40%), excess door events (20%), banded HIGH/MEDIUM/LOW.

### Driver Scorecard
Composite 0-100 score: 40% OTP + 20% low-late-rate + 20% low-dwell + 20% low-cases-variance, all min-max scaled. Top/bottom 15 bars, score histogram with median line, sortable roster (also by max speed / idle minutes / door-open-while-moving).

### Trailer Utilization
Per-trailer asset utilization. KPI cards (trailers, period days, avg utilization, total stops/miles), quartile bands. Distribution histogram, bottom-20 by utilization, **Pareto curve** of cumulative miles share, **activity heatmap** (top 50 trailers × date). Sortable detail with health columns: alarms, battery min/avg, engine hours, reefer fuel cost.

### Reefer Diagnostics (Vanguard)
Per-trailer **Vanguard Cooling Index (VCI)** for frozen plasma operations. Implements the Vanguard V1 SOP — 0–100 risk score combining four subscores: Refrigeration Health (40%), Defrost & Recovery (20%), Temperature Stability (20%), Airflow / Bulkhead / Heat-Flow (20%) — plus 5 hard overrides (positive evap delta, hot-load incident, low compliance, drift > 3 °C, repeat issue). Bands: GREEN ≤ 24 · YELLOW ≤ 49 · ORANGE ≤ 74 · RED ≤ 99 · CRITICAL = 100. Layout: fleet VCI summary (5 cards by band + cleared-vs-blocked), active alerts table, sortable VCI-by-trailer with color-coded bands, per-trailer drill-down (subscore bar chart, baseline vs current, readiness check). All thresholds tunable in **Admin → Reefer (Vanguard SOP)**. Methodology in [LOGIC.md → Vanguard](LOGIC.md#vanguard-reefer-diagnostics).

### Customer Insights
Four tabs:
- **Scorecard** — per-customer stops / routes / OTP / dwell / claims / excursions / revenue / margin. Stops × OTP scatter colored by margin.
- **Churn signal** — week-over-week PRO# change, banded CHURN_RISK / DECLINING / STABLE / GROWING / NEW.
- **Concentration risk** — top-N revenue share metrics + Pareto curve with 80% line + top-20 revenue bar.
- **Weekly trend** — top-10 customers' weekly stops line + top-30 weekly heatmap.

### Operations Insights
Four tabs:
- **Cycle Time Consistency** — std dev of cycle time across PRO# instances of a named route.
- **Late Code Root Cause** — top late codes by occurrence with avg minutes late.
- **Detention Audit** — customers with dwell > 2 hr; total billable detention hours.
- **Demand Forecast** — 4-week ES projection per customer; history (solid) + forecast (dashed) line chart.

### Stop Explorer
Every column on `stop_master` filtered by the shared sidebar, sortable by customer / arrival / dwell / lateness / route. Includes load info, telemetry summary (min_s1, max_s1, door events, etc.).

### OTP Analysis
KPI metric selector (4 OTP definitions). KPI cards (rate, evaluable, total, avg minutes late). Performance pie + OTP trend over time. **OTP by S-Code** + **OTP by Customer** bars. Minutes-from-appointment histogram with ±120-min reference lines. **Where are the late stops?** — state choropleth + city pin map (offline geocoding via `geonamescache`).

### SAP Matching
Total segments, matched / unmatched counts, match rate. Tabs for Matched (with time-difference histogram) and Unmatched (with download).

### Telemetry & Reefer Compliance
Coverage cards. Distribution histograms for ambient and **min S1 reefer temp** (with -25 °C setpoint reference line). Three **heat map** tabs: Route × Stop # (min S1), Day × Hour (door events), Customer × Day of Week (excursions). Stop-level telemetry table. **Route-level reefer compliance** (load-aware — only loaded stops count). **Equipment Health & Safety** section: alarm leaderboard, battery health bar, door-open-while-moving incident table.

### Validation Report
Run history, error summary cards (Hard / Soft / Warning), error-by-reason and by-source bars, drill-down per run, error reference table.

---

## Customer rate matrix

Lives at `config/customer_rates.yaml`. Format:

```yaml
default:
  rate_per_mile: 2.25
  rate_per_stop: 95.00
  rate_per_cwt: 0.00
  minimum_charge: 250.00

customers:
  CSL:
    rate_per_mile: 2.50
    rate_per_stop: 100.00
    minimum_charge: 300.00
  BIOLIFE:
    rate_per_mile: 2.65
    ...
```

**Customer names are uppercased** automatically. Matched against the `customer` column on each stop (extracted from CRST `location_date`).

### Three ways to edit

1. **Upload** CSV/Excel via the Rate Matrix page (easiest for bulk edits).
2. **Inline edit** the per-customer table on the Rate Matrix page (`st.data_editor`).
3. **Edit YAML directly** with any text editor.

After editing, **re-run the pipeline** (Load Data → Run) to recompute revenue and margin.

For the **revenue formula** see [LOGIC.md → Customer Revenue](LOGIC.md#customer-revenue).

---

## Data model

SQLite database at `data/datascrubb.db`. WAL mode, foreign keys ON.

| Table | Grain | Source | Key columns |
|---|---|---|---|
| `stop_master` | one row per physical stop | CRST normalized | transaction_id (PK), order_number, customer, s_code, arrival_date, actual_arrival/departure, dwell_minutes, loaded_at_stop, cases_variance, otp_*, telemetry summary |
| `sap_segment` | one row per matched SAP segment | SAP + matcher | transaction_id, document_number, segment_number, time_diff_hours, sap_match_flag |
| `telemetry_stop` | one row per stop with telemetry | telemetry + matcher | transaction_id (PK), 30+ aggregations: temps, doors, speed, idle, fuel, alarms, battery, engine hours, setpoint changes, **avg/min/max evap delta, setpoint compliance %, defrost event count, max cargo temp, bulkhead seal index** |
| `billing_snapshot` | one row per (PRO#, billing week) | M3PL adapter | pro_number + billing_week_end (PK), lane, crst_miles, billed_amount, all rates |
| `route_kpi` | one row per route | KPI rollup | route_id, stop_count, otp_time_pass_rate, dwell_minutes_avg, route_name |
| `loaded_miles` | one row per route | KPI rollup | route_id, total_segments, loaded_segments, loaded_pct, total_miles, estimated_loaded_miles, estimated_deadhead_miles |
| `route_revenue` | one row per route | KPI revenue calc | route_id, customer, cost, revenue, margin, margin_pct, miles, stop_count, weight_lbs, all rate inputs |
| `route_reefer_cost` | one row per route | KPI rollup | route_id, reefer_runtime_hours, reefer_gallons, reefer_fuel_cost |
| `miles_variance` | one row per (route × billing week) | KPI rollup | route_id, m3pl_billed_miles, crst_stop_count, miles_per_crst_stop |
| `billing_recon` | one row per PRO# | KPI rollup | pro_number, billed_amount, expected_amount, delta, delta_pct |
| `claims_risk` | one row per route | KPI rollup | route_id, risk_score, risk_band, short_cases, excursion_stops, excess_door_events |
| `lane_profitability` | one row per (origin × dest) | KPI rollup | origin_state, dest_state, routes, cost, revenue, margin, margin_pct |
| `equip_util_tractor` / `_trailer` / `_driver` | one row per equipment / driver | KPI rollup | id, total_stops, distinct_routes, otp_rate, total_miles, active_days |
| `driver_scorecard` | one row per driver | KPI rollup | rank, driver, score, OTP, late_rate, dwell, cases_variance, max_speed, idle_minutes |
| `trailer_utilization` | one row per trailer | KPI rollup | trailer, utilization_pct, active_days, idle_days, max_consecutive_idle_days, alarms, battery, engine hours |
| `temp_compliance` | one row per route | KPI rollup | route_id, excursion_count (loaded only), excursion_minutes, empty_stops_skipped, compliance_flag |
| `alarm_log` | one row per trailer | KPI rollup | trailer, alarm_event_total, stops_with_alarms, first_alarm_date, last_alarm_date |
| `customer_scorecard` | one row per customer | KPI rollup | customer, stops, OTP, dwell, claims, excursions, revenue/cost/margin |
| `customer_churn` | one row per customer (latest week) | KPI rollup | customer, week, pros, prev_pros, delta_pct, churn_band |
| `customer_concentration` | one row per customer (ranked) | KPI rollup | rank, customer, revenue, share_pct, cumulative_share_pct |
| `cycle_time` | one row per named route | KPI rollup | route_name, instances, avg_cycle_min, std_cycle_min, consistency_pct |
| `late_code_analysis` | one row per late code | KPI rollup | late_code, occurrences, distinct customers/routes/drivers, avg_minutes_late |
| `detention_audit` | one row per customer | KPI rollup | customer, detention_stops, billable_hours, total_dwell_min |
| `demand_forecast` | one row per (customer × forecast week) | KPI rollup | customer, forecast_week, horizon, forecast_stops |
| `vanguard_baselines` | one row per trailer | Vanguard engine | trailer, baseline_evap_delta, baseline_compliance_pct, baseline_defrost_per_day, baseline_window_days, baseline_source |
| `trailer_vci` | one row per trailer (current period) | Vanguard engine | trailer, vci, band, can_load_frozen, block_reason, rh_score, dr_score, ts_score, abhf_score, current_evap_delta, baseline_evap_delta, hard_override_applied |
| `vanguard_alerts` | one row per active alert per trailer | Vanguard engine | trailer, alert_code, severity, evidence, vci_at_trigger |
| `pipeline_run` | one row per pipeline execution | pipeline | run_id, status, source_files (JSON), match_rates (JSON) |
| `validation_error` | one row per error finding | validation engine | transaction_id, source, error_type, error_reason, run_id |

---

## Outputs

Every pipeline run produces:

- **`data/datascrubb.db`** — SQLite source-of-truth for the dashboard.
- **`output/Trans_KPI_Validation_<runid>.xlsx`** — 32-sheet Excel: raw inputs + every KPI table above (incl. `VANGUARD_BASELINES`, `TRAILER_VCI`, `VANGUARD_ALERTS`) + INFO + ERROR_REFERENCE.
- **`logs/pipeline.log`** — full pipeline log.

---

## Validation rules

Three severities:

| Severity | Meaning | Examples |
|---|---|---|
| `HARD` | Pipeline-breaking — investigate immediately | Duplicate `transaction_id` |
| `SOFT` | Data gap — informational | Missing actual arrival, missing appointment, missing S-Code on plasma stop, reefer temp excursion on a loaded stop |
| `WARNING` | Quality concern — monitor | SAP match rate < 50%, telemetry coverage < 30%, M3PL PRO# not in CRST, PRO# in multiple billing weeks, CRST vs M3PL stop count diverges > 10% |

The Validation Report page lets you filter by run, browse by source, and download.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'dashboard'` | Running `python dashboard/app.py` directly instead of via Streamlit | `python -m streamlit run dashboard/app.py`, OR pick the **Streamlit: DataScrubb dashboard** launch config in VS Code |
| Dashboard pages blank, no error | Stale Streamlit session | Stop Streamlit (Ctrl+C) and restart, then hard-refresh the browser (Ctrl+Shift+R) |
| `sqlite3.IntegrityError: FOREIGN KEY constraint failed` during pipeline | Old code where `persist_errors` ran before `persist_run` | Already fixed; pull latest |
| `Cannot remove 'data/datascrubb.db'` | Streamlit holding DB open | Stop Streamlit before deleting the DB |
| Pipeline takes 9+ minutes | Old per-row ORM `session.merge()` | Already fixed — bulk inserts ~1 sec for 3k rows |
| All M3PL match rate is 0% | CRST `order_#` not matching M3PL `pro_number` | Check PRO numbers are numeric in M3PL SUMMARY and align with CRST Order # |
| Telemetry coverage 0% | Telemetry timestamps don't overlap CRST date range, or trailer IDs differ | Verify file's date range; check `Vehicle Name` matches CRST `trailer` |
| Map page blank | All stops in current filter have null state, or no stops marked late | Reset filters; verify `state` column is populated |
| Reefer fuel cost = $0 | Telemetry CSV has `'-'` in Avg Fuel Rate column for all rows | Source data limitation; infrastructure ready when fuel data appears |

---

## Architecture

```
┌──────────────────────┐
│   Source files       │  CRST.xlsx, SAP.xlsx, Telemetry.csv, Backup*M3PL*.xlsx
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Adapters            │  CrstAdapter, SapAdapter, TelemetryAdapter, M3plAdapter
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  OTP calculator      │  4 metrics per stop
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Matching engine     │  SAP→CRST (S-Code, ±36 h), Telemetry→CRST (trailer, ±120 min),
│                      │  M3PL→CRST (PRO# = order_#)
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  KPI engine          │  ~20 KPI functions producing one table each
│  (kpi/route_kpi.py + │  Route, customer, driver, trailer, lane, billing,
│   kpi/revenue.py)    │  telemetry health, ops insights — see LOGIC.md
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Validation engine   │  9+ rules → SOFT / HARD / WARNING
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Persistence         │  SQLite (bulk upserts) + 29-sheet Excel export
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Streamlit dashboard │  13 pages — shared filters, heat maps, geo maps
└──────────────────────┘
```

### Module map

```
datascrubb/
  pipeline.py            # Orchestrator
  config.py              # YAML loader
  constants.py           # Tolerances, thresholds, column lists
  models.py              # SQLAlchemy ORM
  db.py                  # Engine, bulk upserts
  adapters/
    base.py              # BaseAdapter
    crst.py              # Customer extraction, load detection, OTP setup
    sap.py
    telemetry.py         # 27 columns: temps, speed, fuel, alarms, battery, etc.
    m3pl.py              # Multi-section invoice parser
  matching/
    engine.py            # MatchResults + orchestration
    sap_matcher.py       # S-Code + time-window match
    telemetry_matcher.py # Trailer + window aggregation (25+ metrics)
  otp/
    calculator.py        # 4-bucket OTP
  kpi/
    route_kpi.py         # ~20 KPI functions (route, driver, trailer, customer, ops)
    revenue.py           # Customer rate matrix → revenue, margin
  validation/
    engine.py
    rules.py             # 9 rule functions
    report.py
  export/
    excel.py             # 29-sheet writer
    csv.py
  utils/
    geo.py               # Offline US city → lat/lon (geonamescache)
    columns.py
    s_code.py

dashboard/
  app.py                 # Streamlit entry point + page registry (sys.path bootstrap)
  components/
    sidebar.py           # Shared filter sidebar (session_state)
    charts.py            # Plotly chart builders (heat maps, geo maps, OTP-by-x)
    export_button.py
  pages/
    overview.py
    data_load.py
    route_kpi.py
    billing_recon.py     # = "Cost vs Revenue (Margin Analysis)" in nav
    rate_matrix.py
    case_variance.py
    driver_scorecard.py
    trailer_utilization.py
    customer_insights.py
    operations.py        # = "Operations Insights" in nav
    stop_explorer.py
    otp_analysis.py
    sap_matching.py
    telemetry_view.py
    validation_report.py

config/
  default.yaml           # Tolerances, thresholds, fuel price, sample interval
  sources.yaml           # Per-source loader config
  customer_rates.yaml    # Revenue rate matrix
```

---

## For developers

### How to add a new KPI

1. Add a function to `datascrubb/kpi/route_kpi.py` (or a new module under `kpi/`).
2. Re-export from `datascrubb/kpi/__init__.py`.
3. Add a field to `MatchResults` in `datascrubb/matching/engine.py`.
4. Call it in `pipeline.py` after the matching step; attach to `results.<your_kpi>`.
5. Add to the `_persist_kpi` list so it lands in SQLite.
6. Add to `export_to_excel` parameters + the sheet list so it appears in the Excel output.
7. Document the formula in [LOGIC.md](LOGIC.md).

### How to add a new dashboard page

1. Create `dashboard/pages/<page>.py` with a `render()` function.
2. Prepend project root to `sys.path` and call `render_global_filters(df)` if it should respect shared filters.
3. Register in `dashboard/app.py` `PAGES` dict and add an `elif page == "..."` branch.

### Conventions

- Use `_options_from(df, col)` and the `apply_to_*` helpers in `dashboard/components/sidebar.py` rather than rolling your own filters.
- Don't pass `value=` AND `key=` to the same Streamlit widget — use `st.session_state.setdefault()` to seed defaults, then refer to widgets by `key=` only.
- Use `bulk_insert_mappings` (or `pd.to_sql(..., if_exists="replace")`) for large writes — never per-row `session.merge()`.
- Persist KPI rollups via `pd.to_sql` **outside** the SQLAlchemy session block to avoid SQLite write-lock contention.
- Customer / driver names are normalized to **UPPERCASE + stripped** at extraction.

### Tests

`tests/` is wired with pytest packages but doesn't yet have meaningful coverage. To run:

```bash
.venv/Scripts/python.exe -m pytest tests/ -v
```

---

## License & contact

Internal tool. Repo: <https://github.com/JCGT500/DataScrubb_v2>.

For metric calculation details, see **[LOGIC.md](LOGIC.md)**.
