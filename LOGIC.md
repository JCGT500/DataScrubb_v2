# DataScrubb Metric Logic Reference

How every metric in the dashboard and Excel export is calculated. Each entry lists:

- **What it answers** — the business question.
- **Formula** — the math.
- **Inputs** — which columns feed it.
- **Edge cases** — null handling, empty data, etc.
- **Where to override** — config keys / files where you can change defaults.
- **Where to find it** — code path + dashboard page + Excel sheet + SQLite table.

> All code paths are relative to repo root. All dashboard pages refer to the sidebar nav label.

---

## Table of contents

1. [Stop-level fields](#stop-level-fields)
   - Customer extraction · Loaded-at-stop · Dwell minutes · S-Code
2. [On-Time Performance](#on-time-performance-otp)
3. [Cross-source matching](#cross-source-matching)
   - SAP → CRST · Telemetry → CRST · M3PL → CRST
4. [Telemetry stop aggregations](#telemetry-stop-aggregations)
   - Idle · Speed · Reefer fuel · Door-while-moving · Alarms · Battery · Engine hrs · Setpoint changes
5. [Route-level KPIs](#route-level-kpis)
   - Route OTP · Loaded vs deadhead miles · Reefer fuel cost · Temp compliance · Claims risk
6. [Customer revenue & margin](#customer-revenue--margin)
7. [Customer analytics](#customer-analytics)
   - Service scorecard · Churn signal · Concentration risk
8. [Driver scorecard](#driver-scorecard)
9. [Trailer utilization](#trailer-utilization)
10. [Lane profitability](#lane-profitability)
11. [Operations](#operations)
   - Cycle time · Late code analysis · Detention audit · Demand forecast
12. [Equipment utilization](#equipment-utilization)
13. [Validation rules](#validation-rules)
14. [Defaults & thresholds](#defaults--thresholds)

---

## Stop-level fields

### Customer extraction

**Answers:** "Which customer does this stop belong to?"

**Formula:** From CRST `location_date` (e.g. `BIOLIFE - LENEXA S1304 01/02 07:00`):
1. Strip trailing `MM/DD HH:MM` (or `MM/DD/YYYY HH:MM`) timestamp via regex.
2. If a `' - '` (space-dash-space) is present, take the part before the **first** ` - `.
3. Otherwise use the whole stripped string.
4. Uppercase + strip.

**Examples:**
| Input | Output |
|---|---|
| `RX CROSSROADS - DISTRIBUTION 01/01 08:00` | `RX CROSSROADS` |
| `BIOLIFE - LENEXA S1304 01/02 07:00` | `BIOLIFE` |
| `CSL - TUCSON AZ 624 - S9682 01/06 15:00` | `CSL` |
| `CRST International 01/05 07:00` | `CRST INTERNATIONAL` |

**Edge cases:** None / NaN → `None`. Empty after stripping → returned as empty.

**Override:** Edit the regex `_LOC_DATE_TRAIL_RE` in `datascrubb/adapters/crst.py`.

**Code:** `datascrubb/adapters/crst.py::extract_customer` · **Column:** `stop_master.customer`

---

### Loaded-at-stop flag

**Answers:** "Did the trailer have product on board during this stop?"

**Formula:**
```
loaded_at_stop = 1 if (current_cases > 0)
                       OR (stop_direction == "SO" AND tender_cases > 0)
                else 0
```

**Reasoning:**
- `current_cases > 0` — cases on board after this stop, so the trailer is loaded going to the next stop.
- `stop_direction == "SO" AND tender_cases > 0` — this is a delivery stop where cases were dropped, so the trailer was loaded coming in (even if `current_cases` is now 0).

**Edge cases:** Null cases → treated as 0. No `stop_direction` column → `loaded_at_stop = 0` for all rows.

**Override:** Modify the formula in `datascrubb/adapters/crst.py` in the CRST `normalize()` method (look for `# 1e. Loaded-at-stop flag`).

**Code:** `datascrubb/adapters/crst.py` · **Column:** `stop_master.loaded_at_stop`

---

### Dwell minutes

**Formula:** `(actual_departure − actual_arrival).dt.total_seconds() / 60`, rounded to 1 decimal.

**Edge cases:** If `actual_departure` is missing on a row, dwell is null. If column doesn't exist (older data), dwell is `pd.NA`.

**Code:** `datascrubb/adapters/crst.py` · **Column:** `stop_master.dwell_minutes`

---

### S-Code extraction

**Formula:** Regex `S\d{3,5}` against `location_date`. First match wins.

**Used to:** Identify plasma-center stops (`stop_type = 'PLASMA_CENTER'` if S-code present, else `WAREHOUSE`).

**Code:** `datascrubb/utils/s_code.py::extract_s_code` · **Column:** `stop_master.s_code`

---

## On-Time Performance (OTP)

Four metrics, all stop-level.

| Metric | Formula |
|---|---|
| `otp_day_pass` | `1` if `actual_arrival.date() == resolved_appt.date()`, else `0`. NaN if either is missing. |
| `otp_time_pass` | `1` if `abs(actual_arrival − resolved_appt) ≤ tolerance_minutes`, else `0`. NaN if either is missing. |
| `otp_original_pass` | Like `otp_day_pass` but compared to `original_appt` (not resolved). |
| `otp_time_original_pass` | Like `otp_time_pass` but vs `original_appt`. |

`resolved_appt` = `current_appt` if present, else `original_appt`.

`minutes_from_appt` = `(actual_arrival − resolved_appt).total_seconds() / 60`, rounded.

**Stop performance status** (human-readable):
- `Missing Arrival` — no `actual_arrival`
- `Missing Appointment` — no `resolved_appt`
- `On Time` — `|minutes_from_appt| ≤ tolerance`
- `Early` — `minutes_from_appt < -tolerance`
- `Late` — `minutes_from_appt > tolerance`

**Default tolerance:** **120 minutes**. Override at `config/default.yaml::pipeline.otp_tolerance_minutes`.

**Code:** `datascrubb/otp/calculator.py::calculate_otp` · **Columns:** `otp_day_pass`, `otp_time_pass`, `otp_original_pass`, `otp_time_original_pass`, `minutes_from_appt`, `stop_performance_status` on `stop_master`.

---

## Cross-source matching

### SAP → CRST

**Answers:** "Which SAP shipment segment corresponds to which CRST stop?"

**Formula:**
1. Inner-join SAP segments to CRST stops on `s_code`.
2. Filter to CRST stops where `stop_type == 'PLASMA_CENTER'` AND `actual_arrival` is not null.
3. For each `(document_number, segment_number)` SAP row, pick the CRST stop with **smallest** `|sap.arrive − crst.actual_arrival|` (in hours).
4. Drop matches where the time difference exceeds `max_hours` (default **36**).

**Match rate:** `matched_segments / total_sap_segments`.

**Override:** `config/default.yaml::pipeline.sap_match_max_hours`.

**Code:** `datascrubb/matching/sap_matcher.py::match_sap_to_crst` · **Table:** `sap_segment` · **Page:** SAP Matching

---

### Telemetry → CRST

**Answers:** "Which telemetry events happened during which stop?"

**Formula:**
1. Build a stop-time window for each CRST stop:
   - `stop_start_ts = actual_arrival − window_minutes` (default **120 min**).
   - `stop_end_ts = actual_departure + window_minutes` if departure exists; else `actual_arrival + 6 hours`.
2. Inner-join telemetry events to CRST stops on `trailer_id == trailer`.
3. Filter to events where `event_ts ∈ [stop_start_ts, stop_end_ts]`.
4. Aggregate per `transaction_id` (see [Telemetry stop aggregations](#telemetry-stop-aggregations)).

**Coverage:** `unique_transaction_ids_with_telemetry / unique_transaction_ids_in_crst`.

**Override:** `config/default.yaml::pipeline.telemetry_window_minutes`.

**Code:** `datascrubb/matching/telemetry_matcher.py::match_telemetry_to_crst` · **Table:** `telemetry_stop`

---

### M3PL → CRST

**Answers:** "Which M3PL billing PRO# corresponds to which CRST route?"

**Formula:** Direct join on `m3pl.pro_number == crst.order_#` (after string normalisation). No time window — billing is route-level.

**Match rate:** `m3pl_pros_found_in_crst / total_m3pl_pros`.

**Code:** `datascrubb/matching/engine.py` (M3PL section) · **Table:** `billing_snapshot`

---

## Telemetry stop aggregations

Per `transaction_id`, aggregated from telemetry events that fall in the stop window. Default sample interval = **15 minutes**.

| Column | Formula |
|---|---|
| `telem_events` | Count of events in window |
| `min_amb_temp` / `max_amb_temp` / `avg_amb_temp` | min / max / mean of `Amb Temp` |
| `min_s1` / `max_s1` | min / max of `S1` (reefer probe) |
| `avg_tl1` / `min_tl1` / `max_tl1` | True product probe (separate from S1) |
| `door_open_events` | Sum of events where `Door 1 == "O"` |
| `door_open_while_moving` | Sum of events where door open AND `Speed > 5 mph` |
| `max_speed` / `avg_speed` | mph |
| `idle_minutes` | `(events with speed == 0 AND engine_rpm > 0) × sample_interval_minutes` |
| `reefer_runtime_minutes` | `(events with unit_power == "On") × sample_interval_minutes` |
| `reefer_gallons` | `mean(avg_fuel_rate when On) × runtime_hours` |
| `reefer_fuel_cost` | `reefer_gallons × fuel_price_per_gallon` (default $4.50) |
| `alarm_events` | Sum of events where `Unit Alarm == "Yes"` |
| `min_battery` / `avg_battery` | Battery voltage min / mean |
| `max_engine_hours` / `max_total_hours` | Highest reading of each odometer-style counter seen during the window |
| `setpoint_changes` | `max(distinct(SP1) − 1, 0)` — the trailer's set point was changed mid-window N times |
| `avg_da_ra_delta` | `mean(RA1 − DA1)` — large positive value indicates active load (heat being absorbed) |

**Overrides:**
- `config/default.yaml::pipeline.telemetry_sample_interval_minutes` (default 15)
- `config/default.yaml::pipeline.fuel_price_per_gallon` (default 4.50)

**Code:** `datascrubb/matching/telemetry_matcher.py` · **Table:** `telemetry_stop`

---

## Route-level KPIs

### Route OTP

**Per route_id (= order_#):**

| Metric | Formula |
|---|---|
| `stop_count` | Count of stops on the route |
| `otp_time_pass_rate` | `mean(otp_time_pass) × 100`, rounded |
| `otp_day_pass_rate` | `mean(otp_day_pass) × 100`, rounded |
| `avg_minutes_late` | `mean(minutes_from_appt)` |
| `dwell_minutes_total` / `_avg` | sum / mean of stop dwell |
| `first_arrival` | min(actual_arrival) |
| `last_departure` | max(actual_departure) (falls back to actual_arrival) |
| `route_name` | mode of `route_name` over the route's stops |

**Code:** `datascrubb/kpi/route_kpi.py::compute_route_otp` · **Table:** `route_kpi` · **Page:** Route KPIs

---

### Loaded vs deadhead miles

**Answers:** "What share of route miles were driven loaded vs empty?"

**Formula:** Per route_id:
1. Sort the route's stops by `stop_seq`.
2. For each transition `stop_n → stop_n+1`, the segment is **loaded** if `current_cases at stop_n > 0`.
3. `loaded_pct = loaded_segments / total_segments × 100`.
4. `estimated_loaded_miles = m3pl_total_miles × loaded_pct / 100`.
5. `estimated_deadhead_miles = m3pl_total_miles − estimated_loaded_miles`.

**Caveats:** This is an **approximation** — CRST has no per-segment mileage. Each segment is assumed to be the same length. A real per-leg mileage source (PC*Miler, GPS distance) would replace this.

**Code:** `datascrubb/kpi/route_kpi.py::compute_loaded_miles` · **Table:** `loaded_miles` · **Page:** Route KPIs

---

### Reefer fuel cost per route

**Per route_id:** sum of `telemetry_stop.reefer_runtime_minutes` / `reefer_gallons` / `reefer_fuel_cost` across the route's stops.

**Code:** `datascrubb/kpi/route_kpi.py::compute_route_reefer_cost` · **Table:** `route_reefer_cost`

---

### Temperature compliance (load-aware)

**Answers:** "Are reefer temperatures in spec when product is on board?"

**Formula:** Per route_id, sum over stops of:
```
excursion = (loaded_at_stop == 1)
            AND has_telemetry
            AND ((min_s1 < setpoint_c − tolerance_c)
                 OR (max_s1 > setpoint_c + tolerance_c))
```
- `excursion_count` = sum of `excursion`.
- `excursion_minutes` = `excursion_count × min_excursion_minutes` (default 15) — conservative lower bound.
- `empty_stops_skipped` = stops where the trailer was empty AND temp was out of range (these don't count toward excursion).
- `compliance_flag` = `EXCURSION` if `excursion_count > 0`, else `OK`.

**Defaults:**
- `setpoint_c = -25` (plasma cold-chain)
- `tolerance_c = 5` (so ±5°C around setpoint)
- `min_excursion_minutes = 15`

**Override:** Function args in `datascrubb/kpi/route_kpi.py::compute_temp_compliance`. Constants at the top of `route_kpi.py`: `PLASMA_TEMP_SETPOINT_C`, `PLASMA_TEMP_TOLERANCE_C`, `TEMP_EXCURSION_MIN_MINUTES`.

**Code:** `datascrubb/kpi/route_kpi.py::compute_temp_compliance` · **Table:** `temp_compliance` · **Page:** Telemetry

---

### Claims-risk index

**Answers:** "Which routes are most likely to generate a claim?"

**Formula:** Per route_id, three components combined:

| Component | Per stop |
|---|---|
| `short_cases` | `abs(cases_variance)` when `cases_variance < 0` |
| `excursion_stops` | 1 if loaded AND min_s1 < setpoint − tolerance OR max_s1 > setpoint + tolerance |
| `excess_door_events` | `door_open_events` when > 5 |

Sum each per route → min-max scale across all routes (each scaled to 0..1) → weighted blend:

```
risk_score = (0.40 × short_norm + 0.40 × excursion_norm + 0.20 × door_norm) × 100
```

**Bands:**
- HIGH ≥ 70
- MEDIUM ≥ 40
- LOW > 0
- NONE = 0

**Code:** `datascrubb/kpi/route_kpi.py::compute_claims_risk` · **Table:** `claims_risk` · **Page:** Case Variance

---

## Customer revenue & margin

**The formula on the Cost vs Revenue page:**

```
revenue_miles  = miles            × rate_per_mile
revenue_stops  = stop_count       × rate_per_stop
revenue_weight = (weight_lbs/100) × rate_per_cwt
revenue_calc   = revenue_miles + revenue_stops + revenue_weight
revenue        = max(revenue_calc, minimum_charge)
margin         = revenue − cost
margin_pct     = margin / revenue × 100
```

**Inputs per route:**
- `miles` = sum of M3PL `crst_miles` for matching `pro_number`. 0 if no M3PL row.
- `stop_count` = count of CRST stops on the route.
- `weight_lbs` = sum of CRST `sum_of_weight`.
- `customer` = mode of `customer` across the route's stops.
- `cost` = sum of M3PL `billed_amount` for matching `pro_number`. 0 if no M3PL row.

**Rate matrix lookup:**
1. Read `config/customer_rates.yaml`.
2. Look up customer by uppercase exact match.
3. If found, merge customer entry over default. If not, return default (with `_source = "default"`).
4. Apply rates to inputs.

**Override:** Edit `config/customer_rates.yaml` directly, or upload via the Rate Matrix page in the dashboard.

**Code:** `datascrubb/kpi/revenue.py::compute_route_revenue`, `rate_for`, `load_rate_matrix` · **Table:** `route_revenue` · **Page:** Cost vs Revenue

---

## Customer analytics

### Customer service scorecard

**One row per customer:**

| Column | Formula |
|---|---|
| `stops` | count of stops |
| `distinct_routes` | nunique of `order_#` |
| `active_days` | nunique of `arrival_date` |
| `otp_rate` | `mean(otp_time_pass) × 100` |
| `late_stops` | count where `stop_performance_status == 'Late'` |
| `late_rate_pct` | `late_stops / stops × 100` |
| `avg_dwell_min` | mean of `dwell_minutes` |
| `short_cases_total` | sum of `abs(cases_variance)` where `< 0` |
| `claims_per_stop` | `short_cases_total / stops` |
| `excursion_stops` | count where loaded AND min_s1 < setpoint − tolerance |
| `revenue` / `cost` / `margin` / `margin_pct` | sums from `route_revenue` joined on customer |

**Code:** `datascrubb/kpi/route_kpi.py::compute_customer_scorecard` · **Table:** `customer_scorecard` · **Page:** Customer Insights

---

### Customer churn signal

**Answers:** "Which customers are trending down?"

**Formula:**
1. For each customer, count distinct PRO# per ISO week.
2. Sort by week; compute `delta_pros = pros − prev_pros`, `delta_pct = delta_pros / prev_pros × 100`.
3. Take the **latest week** per customer.
4. Band by `delta_pct`:
   - **CHURN_RISK** ≤ -50%
   - **DECLINING** -20 to -50%
   - **STABLE** -20 to +50%
   - **GROWING** ≥ +50%
   - **NEW** = no prior week

**Caveats:** Needs ≥ 2 weeks of data per customer. If your data window is < 2 weeks, this returns empty.

**Code:** `datascrubb/kpi/route_kpi.py::compute_customer_churn_signal` · **Table:** `customer_churn`

---

### Customer concentration (Pareto)

**Per customer, ranked by revenue desc:**

| Column | Formula |
|---|---|
| `share_pct` | `revenue / total_revenue × 100` |
| `cumulative_share_pct` | running sum of `share_pct` |
| `rank` | 1 = highest revenue |

**Use:** "How many customers do I need to lose 80% of revenue?" Find the row where `cumulative_share_pct ≥ 80`.

**Code:** `datascrubb/kpi/route_kpi.py::compute_customer_concentration` · **Table:** `customer_concentration`

---

## Driver scorecard

**Answers:** "Who are my best and worst drivers?"

**Formula:** Per driver (uppercased + stripped):
1. Aggregate base metrics: `total_stops`, `otp_rate (×100)`, `late_count`, `late_rate (% of stops)`, `avg_dwell`, `cases_variance_total (sum of |cases_variance|)`, `cases_variance_per_stop`, `active_days`, `max_speed_mph`, `avg_speed_mph`, `idle_minutes_total`, `door_moving_events`.
2. Min-max scale four inputs across the driver pool to 0..1:
   - `n_otp = scale_higher_better(otp_rate)`
   - `n_late = scale_lower_better(late_rate)`
   - `n_dwell = scale_lower_better(avg_dwell)`
   - `n_var = scale_lower_better(cases_variance_per_stop)`
3. Composite: `score = (0.40 × n_otp + 0.20 × n_late + 0.20 × n_dwell + 0.20 × n_var) × 100`
4. Rank by score desc.

**Caveats:**
- Min-max scaling is **relative to the current driver pool** in the dataset. A driver who scores 100 in January isn't "perfect" in absolute terms — they just topped that month's pool.
- Speed / idle metrics are surfaced for context but **don't currently affect the composite score**. To weight them in, edit the formula in `datascrubb/kpi/route_kpi.py::compute_driver_scorecard`.

**Code:** `datascrubb/kpi/route_kpi.py::compute_driver_scorecard` · **Table:** `driver_scorecard` · **Page:** Driver Scorecard

---

## Trailer utilization

**Answers:** "Is each trailer earning its keep?"

**Formula:** Per trailer (uppercased + stripped):

| Column | Formula |
|---|---|
| `first_seen` / `last_seen` | min / max of `arrival_date` for this trailer |
| `total_stops` | count of stops |
| `loaded_stops` | sum of `loaded_at_stop` |
| `loaded_pct` | `loaded_stops / total_stops × 100` |
| `distinct_routes` | nunique of `order_#` |
| `distinct_drivers` | nunique of `drivers` |
| `distinct_customers` | nunique of `customer` |
| `active_days` | nunique of `arrival_date.dt.normalize()` for this trailer |
| `period_days` | `(max date − min date in dataset) + 1` (whole dataset, not per-trailer) |
| `idle_days` | `period_days − active_days` |
| `utilization_pct` | `active_days / period_days × 100` |
| `max_consecutive_idle_days` | longest gap between consecutive active days for this trailer |
| `total_miles` | sum of `crst_miles` for distinct (trailer, route_id) pairs from M3PL |
| `miles_per_active_day` | `total_miles / active_days` |
| `last_known_state` / `last_known_city` | state / city of the trailer's most recent stop |
| `alarm_event_total` | sum of `telemetry_stop.alarm_events` for this trailer's stops |
| `min_battery_seen` / `avg_battery` | min / mean of telemetry battery voltage |
| `max_engine_hours` / `max_total_hours` | highest counter readings observed |
| `reefer_fuel_cost_total` / `reefer_gallons_total` | sum of stop-level reefer cost / gallons |
| `idle_minutes_total` | sum of `idle_minutes` |
| `door_open_while_moving_total` / `setpoint_changes_total` | sums |

**Caveats:**
- `period_days` is **dataset-wide** — a trailer that joined mid-month gets penalized in `utilization_pct`. Future fix: per-trailer in-service window.
- A "DUMMY" trailer prefix in the data (e.g. DUMMY1, DUMMY4) appears to be a placeholder for missing trailer info; treat their utilization as "no truck assigned" rather than "idle asset".

**Code:** `datascrubb/kpi/route_kpi.py::compute_trailer_utilization` · **Table:** `trailer_utilization` · **Page:** Trailer Utilization

---

## Lane profitability

**Answers:** "Which origin → destination lanes make money?"

**Formula:**
1. For each route, find:
   - `origin_state` = `state` of the **first PU** stop (sorted by stop_seq + actual_arrival).
   - `dest_state` = `state` of the **last SO** stop.
2. Aggregate `route_revenue` (cost, revenue, margin) by `(origin_state, dest_state)`.
3. `margin_pct = margin / revenue × 100`.

**Caveats:** Routes where origin or destination state is null are excluded.

**Code:** `datascrubb/kpi/route_kpi.py::compute_lane_profitability` · **Table:** `lane_profitability` · **Page:** Cost vs Revenue (Lane Profitability tab)

---

## Operations

### Cycle time consistency

**Answers:** "How predictable is each named route?"

**Formula:** Per route_name (must have ≥ 2 PRO# instances):
1. Per PRO# instance: `cycle_minutes = (last_departure − first_arrival).total_seconds() / 60`. Drop if ≤ 0.
2. Aggregate across instances:
   - `instances` = nunique of `order_#`
   - `avg_cycle_min` / `median_cycle_min` / `std_cycle_min` / `min` / `max`
   - `consistency_pct = clip(100 − std_cycle_min / avg_cycle_min × 100, 0, 100)`

**Reading the metric:** higher consistency_pct = more predictable. 100 = identical cycle every time. 0 = std dev exceeds the mean (very erratic).

**Code:** `datascrubb/kpi/route_kpi.py::compute_cycle_time_consistency` · **Table:** `cycle_time` · **Page:** Operations Insights

---

### Late code analysis

**Per CRST `late_code` value (uppercased + stripped):**

| Column | Formula |
|---|---|
| `occurrences` | count of stops with this code |
| `distinct_customers` / `distinct_routes` / `distinct_drivers` | nunique counts |
| `avg_minutes_late` | mean of `minutes_from_appt` |

**Use:** rank by occurrences to see what's actually causing the lateness.

**Code:** `datascrubb/kpi/route_kpi.py::compute_late_code_analysis` · **Table:** `late_code_analysis`

---

### Detention audit

**Answers:** "How many billable hours of detention are we eating?"

**Formula:**
1. Filter `stop_master` to stops where `dwell_minutes > threshold_minutes` (default **120 min** = 2 hours).
2. Group by customer:
   - `detention_stops` = count of long-dwell stops
   - `total_dwell_min` = sum of all long dwells (the *full* dwell, not just the over-threshold portion)
   - `avg_dwell_min` / `max_dwell_min` = stats
   - `billable_hours` = `total_dwell_min / 60`

**Caveats:** Reports the **full dwell**, not just the time beyond the threshold. To bill only the over-threshold portion, change `total_dwell_min` to `sum(dwell_minutes − threshold)`.

**Override:** `compute_detention_audit(stops_df, threshold_minutes=120)` — change the kwarg if you bill at a different free-time threshold.

**Code:** `datascrubb/kpi/route_kpi.py::compute_detention_audit` · **Table:** `detention_audit`

---

### Demand forecast

**Answers:** "What's our 4-week stop projection per customer?"

**Formula:** Per customer (≥ 3 weeks of data required):
1. Bucket arrivals into ISO weeks; count stops per week.
2. Simple exponential smoothing with `α = 0.5`:
   ```
   s_0 = stops[week_0]
   s_t = α × stops[week_t] + (1 − α) × s_{t-1}
   ```
3. The final smoothed value `s_n` is the forecast for **every** future week in the horizon (no trend component).
4. Output rows for `horizon = 1..4`.

**Caveats:**
- Flat forecast (no trend) — this is intentional for an MVP. Customers with strong trends will be over- or under-projected.
- α=0.5 = equal weight on current vs prior smoothed value. Increase for more responsive, decrease for smoother.
- Won't capture seasonality (weekly cyclic patterns).

**Override:** `compute_demand_forecast(stops_df, horizon_weeks=4)` and the `alpha=0.5` constant in the function body.

**Code:** `datascrubb/kpi/route_kpi.py::compute_demand_forecast` · **Table:** `demand_forecast`

---

## Equipment utilization

Per **tractor**, **trailer**, **driver** (three separate tables, all from `compute_equipment_util`):

| Column | Formula |
|---|---|
| `total_stops` | count of stops |
| `distinct_routes` | nunique of `order_#` |
| `otp_rate` | `mean(otp_time_pass) × 100` |
| `total_miles` | sum of `crst_miles` for distinct (equipment, route) pairs from M3PL |
| `active_days` | nunique of `arrival_date` |

**Code:** `datascrubb/kpi/route_kpi.py::compute_equipment_util` · **Tables:** `equip_util_tractor`, `equip_util_trailer`, `equip_util_driver` · **Page:** Route KPIs (Equipment & Driver Utilization tabs)

---

## Validation rules

The validation engine emits findings into `validation_error` with three severities:

| Rule | Severity | Trigger |
|---|---|---|
| `Missing Actual Arrival` | SOFT | `stop_master.actual_arrival is null` |
| `Missing Appointment` | SOFT | `stop_master.resolved_appt is null` |
| `Missing S_Code for plasma stop` | SOFT | `stop_type == 'PLASMA_CENTER' AND s_code is null` |
| `Duplicate TransactionID` | HARD | `transaction_id` appears twice — pipeline is leaking duplicates |
| `SAP match rate <50%` | WARNING | `(sap_match_flag == 'MATCHED').mean() < 0.50` |
| `Telemetry coverage <30%` | WARNING | `telemetry_stops / crst_stops < 0.30` |
| `BILLING_DUP_PRO` | WARNING | Same PRO# in more than one billing week |
| `M3PL_PRO_NOT_IN_CRST` | WARNING | M3PL PRO# has no matching CRST `order_#` |
| `TEMP_EXCURSION` | SOFT | One per route flagged in `temp_compliance` (load-aware) |
| `MILES_VARIANCE > 10%` | WARNING | `abs(crst_stop_count − m3pl_stop_count) / m3pl_stop_count > 10%` |

**Code:** `datascrubb/validation/rules.py` · **Table:** `validation_error` · **Page:** Validation Report

---

## Defaults & thresholds

All defaults live in `config/default.yaml`:

```yaml
pipeline:
  otp_tolerance_minutes: 120          # ± minutes around appointment for "on time"
  sap_match_max_hours: 36             # SAP arrive vs CRST arrival must be within this
  telemetry_window_minutes: 120       # ± window around stop for telemetry events
  telemetry_min_pings_per_stop: 5     # (currently unused; reserved for future filter)
  telemetry_sample_interval_minutes: 15  # used to convert event counts → runtime minutes
  fuel_price_per_gallon: 4.50         # used for reefer fuel cost calc
```

Plus per-rule constants in code:

| Constant | Default | File | Used by |
|---|---|---|---|
| `PLASMA_TEMP_SETPOINT_C` | -25 | `kpi/route_kpi.py` | temp compliance, claims risk |
| `PLASMA_TEMP_TOLERANCE_C` | 5 | `kpi/route_kpi.py` | temp compliance, claims risk |
| `TEMP_EXCURSION_MIN_MINUTES` | 15 | `kpi/route_kpi.py` | temp compliance |
| Detention threshold | 120 min | `compute_detention_audit` kwarg | detention audit |
| Forecast horizon | 4 weeks | `compute_demand_forecast` kwarg | demand forecast |
| Forecast α | 0.5 | inside `compute_demand_forecast` | demand forecast |
| Driver score weights | 40/20/20/20 | inside `compute_driver_scorecard` | driver scorecard |
| Claims-risk weights | 40/40/20 | inside `compute_claims_risk` | claims risk |
| Door-open "excess" threshold | > 5 events/stop | inside `compute_claims_risk` | claims risk |
| Speed threshold for "moving" | > 5 mph | `telemetry_matcher.py` | door-open-while-moving |
| Min routes for cycle time | ≥ 2 instances | inside `compute_cycle_time_consistency` | cycle time |
| Min weeks for forecast | ≥ 3 | inside `compute_demand_forecast` | demand forecast |
| Customer churn bands | -50 / -20 / +50 | inside `compute_customer_churn_signal` | churn signal |
| Concentration warning | 80% (visual line only) | hardcoded in chart | concentration page |

Anything in code (not YAML) requires a code change to override. We can promote any of these to `default.yaml` if you want them tunable without redeployment — just say which.
