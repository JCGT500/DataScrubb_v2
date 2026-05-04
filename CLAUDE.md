# DataScrubb conventions for AI assistants

This file is loaded automatically by Claude Code at session start. **Both
sections below are binding when working in this repo.** They were assembled
from two distinct sources — see the headers for provenance.

- **Section 1** is project-specific lore for DataScrubb, captured over
  many sessions of building this codebase.
- **Section 2** is the observability conventions doc adapted from the
  VanguardV1 project, used here to govern instrumentation around
  `@observe` and `quality_check`.

---

## Section 1 — DataScrubb project conventions

> **Source:** captured iteratively while building the DataScrubb v2
> codebase. These rules reflect real bugs we've hit and design decisions
> we've made together.

### Git / publishing

- The git remote we publish to is **`v2`** (https://github.com/JCGT500/DataScrubb_v2.git), **not** `origin`. `origin` points at the legacy v1 repo (`JCGT500/datascrubb`) — leave it alone unless explicitly asked otherwise.
- Push with `git push v2 v2-sync:main` from the local `v2-sync` branch (the working branch that tracks `v2/main`).
- **Never** include `Co-Authored-By: Claude ...` trailers in commit messages. Sign commits as the user only.
- The git user is `justin <justin.cyphert@gmail.com>`.

### Code patterns we've been bitten by

- **NaN safety**: always guard `int(x)` and `float(x)` with `pd.notna(x)` first. NaN converts to int silently raises in pandas; we've crashed dashboard pages on this multiple times. See the `_safe_int` / `_safe_float_str` helpers in `dashboard/pages/reefer_diagnostics.py` for the pattern.
- **Schema migrations**: `Base.metadata.create_all(engine)` only CREATES tables, it doesn't ALTER existing ones. When you add a column to a SQLAlchemy model, existing prod databases will hit `OperationalError: no such column` until they're migrated by hand. There's no Alembic in this repo (yet) — for now, when adding columns, also write a one-off ALTER TABLE script and tell the user to run it before re-running the pipeline.
- **Console encoding**: any new CLI script that prints emoji needs `sys.stdout.reconfigure(encoding='utf-8')` at the top — Windows `cp1252` will crash without it.
- **Streamlit re-renders the whole script** on every interaction. Heavy work in render functions (msal calls, `pd.ExcelWriter`, file reads) MUST be cached with `@st.cache_data` or short-circuited via early-return when not needed. See `dashboard/pages/admin.py::_render_sharepoint_tab` for the short-circuit pattern when SharePoint is disabled.

### File / data conventions

- Source data files (CRST, SAP, telemetry, M3PL backups) live in the project root and are gitignored.
- The user **CANNOT** share customer data files into the dev environment outside the enterprise machine. When designing importers or parsers for new file types, build them to be flexible at runtime — interactive wizards in the dashboard, runtime auto-detection, downloadable templates. Don't plan "drop the file here so I can inspect it" steps. (Memory note: `feedback_company_data.md`.)
- `data/datascrubb.db` is the source of truth that the dashboard reads. Pipeline runs refresh slices of it; missing optional sources fall back to cached DB tables when `Pipeline.run(reuse_cached=True)` (the default). Don't break this.
- WAL mode is enabled on SQLite (`.db-wal`, `.db-shm` files exist alongside). When backing up the DB, checkpoint first via `PRAGMA wal_checkpoint(TRUNCATE)` so the `.db` file is a complete snapshot.

### UI conventions

- All web UIs go inside the existing Streamlit dashboard. When a feature arrives with reference Flask code, the Flask code is reference material — reimplement as a Streamlit page rather than running a second service. (Memory note: `feedback_streamlit_only.md`.)
- Dashboard pages register in `dashboard/app.py::NAV_GROUPS`.
- For new diagnostic / inspection UIs, use the `🔍 Diagnostics` nav group.
- For new operational settings, add a tab to `dashboard/pages/admin.py` and register the keys in `datascrubb/admin.py::TAB_BLOCKS` + `DEFAULTS` — don't invent a new admin pattern.

### Performance

- `compute_route_revenue` runs on every pipeline run; keep the flat-pricing branch fully vectorized. Per-row Python loops only for the small banded subset.
- The Excel export is the slowest single step (~3 minutes for 32 sheets). Don't add more sheets without asking. Use `xlsxwriter` instead of `openpyxl` if you need a speed-up.
- The pipeline takes ~4 minutes end-to-end on the user's typical monthly dataset. Anything that pushes it past 10 minutes needs explanation.

### Auto-accepted prompts (used by ExitPlanMode `allowedPrompts`)

When approving plans, these are the categories we routinely allow without per-call confirmation:
- "Run pytest" — full suite or specific files
- "Run the pipeline end-to-end"
- "Regenerate the standalone HTML docs"

---

## Section 2 — Observability conventions

> **Source:** dropped into the repo as `CLAUDE_observability.md` from the
> VanguardV1 project. Adapted here: paths point to DataScrubb locations
> (`datascrubb/observability/` instead of `vanguard/observability/`),
> example calc names use DataScrubb KPIs (`compute_route_revenue`,
> `compute_trailer_vci`, etc.), and the debug dashboard is now a Streamlit
> page (`dashboard/pages/observability.py`) rather than a Flask app. The
> principles are unchanged.

DataScrubb uses a lightweight in-house observability module
(`datascrubb/observability/__init__.py`) for data-quality and
calculation-correctness instrumentation. The goal is not infrastructure
monitoring — it's answering "did this calc produce the right number, and
why?"

Reference implementation: `datascrubb/observability/__init__.py`
Streamlit dashboard: `dashboard/pages/observability.py` (under 🔍 Diagnostics)
Tests: `tests/test_observability.py`

### When to instrument

**Instrument:**
- KPI calculations (revenue, VCI, claims risk, driver scorecard, OTP)
- Composite scoring / index calculations (anything weighted, banded, or with hard-override rules)
- Aggregations over time windows (rolling baselines, weekly rollups)
- Any function whose output ends up persisted to a SQLite table, displayed on the dashboard, or driving an alert

**Do NOT instrument:**
- I/O and adapters (CRST/SAP/telemetry/M3PL file reads, HTTP handlers, SharePoint client)
- Config loaders, constants, pure utility functions (formatters, parsers without logic)
- Logging or observability code itself
- Tight inner loops that run >1000x per pipeline run — wrap the parent function instead

When in doubt: if a function's output would be wrong because of bad input data or a math bug, instrument it. If it would be wrong because the network is down or a config file is missing, don't.

### The two primitives

#### `@observe("calc_name")`

Wraps a function. Captures inputs, output, duration, errors, and any quality check flags raised inside it. Generates a correlation ID (or inherits the parent's).

```python
from datascrubb.observability import observe, quality_check

@observe("compute_route_revenue")
def compute_route_revenue(stops_df, m3pl_df, rate_matrix=None):
    ...
```

#### `quality_check("check_name", condition, detail=..., raise_on_fail=False)`

Asserts an invariant. Records pass/fail, logs a warning on fail, flips the calc status to `flagged`. Soft by default — keeps running so you see all violations in one trace.

```python
quality_check("revenue_non_negative", bool((out["revenue"] >= 0).all()),
              detail=f"min={out['revenue'].min()}")
```

### Naming conventions

**Calc names** (`@observe("...")`):
- `snake_case`
- Describe what the function *produces*, not what it does internally
- Match the function name when reasonable (DataScrubb KPI functions are already named this way)
- Examples: `compute_route_revenue`, `compute_trailer_vci`, `lookup_banded_rate`, `calculate_otp`

**Quality check names** (`quality_check("...", ...)`):
- `snake_case`, format `<subject>_<assertion>`
- Reusable — the same check name should mean the same thing everywhere
- Examples: `weights_sum_to_one`, `score_in_bounds`, `stops_df_not_empty`, `most_routes_have_miles`, `no_nan_values`

When the same conceptual check appears in multiple calcs, use the same name. Pass-rate dashboards aggregate by check name.

### Soft vs. hard checks

Default is **soft** — log, record, and keep going. This is what you want 95% of the time during development because it surfaces every violation in a single run rather than crashing on the first one.

Use `raise_on_fail=True` only for:
- Preconditions where continuing would produce nonsense output (empty input lists, null required IDs)
- Type/shape violations the rest of the function can't survive
- Invariants that indicate a bug, not a data quality issue

```python
# Hard: function literally cannot proceed
quality_check("crst_not_empty", df is not None and not df.empty,
              detail=f"crst has {0 if df is None else len(df)} rows",
              raise_on_fail=True)

# Soft: bad data, but we want to see the whole picture
quality_check("most_routes_have_miles",
              n_zero_miles / n_routes < 0.5,
              detail=f"{n_zero_miles}/{n_routes} routes have miles=0 (M3PL not joined?)")
```

### Detail strings

Always include a `detail=` when the check could fail. The detail is what makes a failed check actionable.

**Good details** include the actual values that caused the failure:
```python
quality_check("vci_in_bounds", bool(out["vci"].between(0, 100).all()),
              detail=f"min={out['vci'].min()}, max={out['vci'].max()}")
```

**Bad details** restate the check name:
```python
# Don't do this
quality_check("vci_in_bounds", ..., detail="VCI must be in 0..100")
```

### Correlation IDs

A correlation ID groups everything that happened during one logical operation. `@observe` creates one automatically per top-level call; nested `@observe` calls inherit it.

For batch operations (like a full pipeline run), use the `correlation()` context manager to set one explicitly. **DataScrubb's pipeline already does this** — `Pipeline.run()` wraps its body in `with correlation(run_id):` so every instrumented calc in the run shares the run's `run_id`. To inspect a run's audit trail, paste the `run_id` from `pipeline.log` (or the Validation Report page) into the Trace explorer at 🔍 Diagnostics → Observability.

```python
from datascrubb.observability import correlation

with correlation(f"ingest-{batch_id}"):
    for trailer_data in batch:
        compute_trailer_vci(...)
```

### What gets persisted

Two SQLite tables (`data/observability.db`, separate from `data/datascrubb.db`):

- `calculations` — one row per `@observe` invocation: inputs, output, duration, status (`ok` / `flagged` / `error`), flag count
- `quality_checks` — one row per `quality_check` call: pass/fail, detail, correlation ID, calc name

Inputs and outputs are JSON-serialized with truncation at 4000 chars. **DataFrames are summarized** by default — captured as `{shape, columns, head_3_rows}` instead of fully dumped. Override per-decorator with `summarize_dataframes=False` if you need the full dump for a specific calc (rarely worth it).

### Performance notes

- Each `@observe` call writes one row to SQLite at the end. ~1ms overhead.
- Each `quality_check` writes one row immediately. ~0.5ms overhead.
- For DataScrubb's typical pipeline run (~7 instrumented calcs × dozens of quality checks), total observability overhead is ~50-100ms — negligible against the ~4 minute pipeline runtime.
- If a function runs >100x per pipeline run, instrument the *parent* that calls it in a loop, not the function itself. Example: `lookup_banded_rate` is called once per banded route, but only when banded customers exist; we instrument it but understand it'll multiply audit rows when banded pricing rolls out widely.
- Don't put `quality_check` inside tight inner loops. Check invariants on inputs and outputs, not on every iteration.

### Anti-patterns to avoid

- **Wrapping every function in the file.** Instrument calcs and transformations, not glue code.
- **Quality checks that always pass.** If a check has never failed in any reasonable scenario, it's noise.
- **Generic check names** like `valid_input` or `data_ok`. Be specific.
- **Side effects in instrumented functions.** `@observe` captures inputs and outputs; if the function also writes to a database or sends a message, that's invisible to the audit trail. Either split the calc from the side effect, or accept the limitation.
- **Capturing secrets in inputs.** If a function takes API keys or PII as args, pass `capture_args=False` to `@observe`.

### Workflow when adding observability to a new module

1. Identify the top-level calc function(s) in the module
2. Add `@observe("...")` to each, with a clear calc name
3. Identify the input invariants the function assumes — add `quality_check` calls for each near the top
4. Identify the output invariants — add `quality_check` calls before returning
5. For nested calcs that have their own meaningful logic, repeat 1-4
6. Run the pipeline once with `observability.enabled: true` in `default.yaml`, then check 🔍 Diagnostics → Observability to confirm calcs and checks appear correctly
7. List the calc names and check names added in your PR description so the team can spot duplicates / inconsistencies

### Quick reference

| Need | Tool |
|---|---|
| Wrap a calc | `@observe("calc_name")` |
| Assert an invariant | `quality_check("name", cond, detail=...)` |
| Hard-fail on violation | add `raise_on_fail=True` |
| Group multi-calc operations | `with correlation("batch-id"):` |
| See recent calcs | 🔍 Diagnostics → Observability page, or `recent_calcs(limit=N, status=...)` |
| Trace one operation | Paste `run_id` into the Trace explorer, or `trace(cid)` |
| Find systematic problems | 🔍 Diagnostics → Observability (quality summary table), or `quality_summary(hours=24)` |
