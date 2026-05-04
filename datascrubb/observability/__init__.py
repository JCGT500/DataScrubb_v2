"""Lightweight observability for DataScrubb KPI calculations.

Three primitives:
  1. ``@observe("calc_name")``  — wrap a function; record inputs / outputs /
     duration / errors / quality-check flag count to SQLite.
  2. ``quality_check(name, condition, detail=...)`` — assert an invariant.
     Soft by default (logs + records, doesn't raise) so a single run surfaces
     every violation. Set ``raise_on_fail=True`` for preconditions.
  3. ``correlation(cid)`` — context manager that groups all calcs run inside
     it under one correlation_id. The pipeline wraps each run in
     ``with correlation(run_id):`` so every instrumented calc shares the
     run's id.

Disabled-mode: when ``enabled=False`` (the default), ``@observe`` is a
zero-overhead pass-through and ``quality_check`` returns immediately. Opt
in via ``observability.enabled: true`` in ``config/default.yaml``.

Audit trail: SQLite at ``data/observability.db``. Two tables: ``calculations``
(one row per @observe invocation) and ``quality_checks`` (one row per
quality_check call, pass or fail). Query helpers: ``recent_calcs``,
``trace``, ``quality_summary``.

Originally adapted from the VanguardV1 observability module — see
``CLAUDE.md`` Section 2 for the conventions.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import traceback
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterable

logger = logging.getLogger("datascrubb.observability")


# ─── Module-level state ─────────────────────────────────────────────

# Configurable at runtime via ``configure()``. Defaults are conservative:
# disabled, so importing the module costs nothing.
_DB_PATH: Path = Path("data/observability.db")
_ENABLED: bool = False
_SUMMARIZE_DF: bool = True

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_calc_name: ContextVar[str | None] = ContextVar("calc_name", default=None)


def configure(
    enabled: bool = False,
    db_path: str | Path = "data/observability.db",
    summarize_dataframes: bool = True,
) -> None:
    """Set runtime options. Call once at startup (e.g. from Pipeline.run)."""
    global _DB_PATH, _ENABLED, _SUMMARIZE_DF
    _ENABLED = bool(enabled)
    _DB_PATH = Path(db_path)
    _SUMMARIZE_DF = bool(summarize_dataframes)
    if _ENABLED:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        init_db(_DB_PATH)


def is_enabled() -> bool:
    return _ENABLED


def current_correlation_id() -> str | None:
    return _correlation_id.get()


# ─── SQLite schema ──────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS calculations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id  TEXT NOT NULL,
    calc_name       TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    duration_ms     REAL,
    inputs_json     TEXT,
    output_json     TEXT,
    status          TEXT,
    error           TEXT,
    flag_count      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_calc_corr ON calculations(correlation_id);
CREATE INDEX IF NOT EXISTS idx_calc_name ON calculations(calc_name, started_at);
CREATE INDEX IF NOT EXISTS idx_calc_status ON calculations(status, started_at);

CREATE TABLE IF NOT EXISTS quality_checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id  TEXT NOT NULL,
    calc_name       TEXT,
    check_name      TEXT NOT NULL,
    passed          INTEGER NOT NULL,
    detail          TEXT,
    ts              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qc_corr ON quality_checks(correlation_id);
CREATE INDEX IF NOT EXISTS idx_qc_failed ON quality_checks(passed, ts);
"""


def init_db(path: Path | str | None = None) -> None:
    p = Path(path) if path else _DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as conn:
        conn.executescript(SCHEMA)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def apply_retention(retention_days: int) -> tuple[int, int]:
    """Delete rows older than ``retention_days`` from both tables.
    Returns ``(calcs_deleted, checks_deleted)``. No-op if disabled or <= 0.
    """
    if not _ENABLED or retention_days <= 0:
        return (0, 0)
    cutoff = f"-{int(retention_days)} days"
    try:
        with _connect() as conn:
            c1 = conn.execute(
                "DELETE FROM calculations WHERE started_at < datetime('now', ?)",
                (cutoff,),
            ).rowcount
            c2 = conn.execute(
                "DELETE FROM quality_checks WHERE ts < datetime('now', ?)",
                (cutoff,),
            ).rowcount
        return (c1 or 0, c2 or 0)
    except sqlite3.Error as e:
        logger.warning("observability retention failed: %s", e)
        return (0, 0)


# ─── Quality checks ─────────────────────────────────────────────────

@dataclass
class QualityCheckResult:
    name: str
    passed: bool
    detail: str | None = None


_flag_buffer: ContextVar[list[QualityCheckResult]] = ContextVar(
    "flag_buffer", default=[]
)


def quality_check(
    name: str,
    condition: bool,
    detail: str | None = None,
    *,
    raise_on_fail: bool = False,
) -> bool:
    """Assert an invariant. Soft by default — logs + persists, keeps running."""
    if not _ENABLED:
        if not condition and raise_on_fail:
            raise AssertionError(f"Quality check failed: {name} ({detail})")
        return bool(condition)

    cid = _correlation_id.get() or "no-correlation"
    calc = _calc_name.get()
    ts = datetime.now(timezone.utc).isoformat()
    result = QualityCheckResult(name=name, passed=bool(condition), detail=detail)

    if not result.passed:
        buf = _flag_buffer.get()
        buf.append(result)
        _flag_buffer.set(buf)

    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO quality_checks "
                "(correlation_id, calc_name, check_name, passed, detail, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (cid, calc, name, int(result.passed), detail, ts),
            )
    except sqlite3.Error as e:
        logger.error("quality_check_persist_failed: %s", e)

    if not result.passed:
        logger.warning("quality_check_failed: %s — %s", name, detail or "")
        if raise_on_fail:
            raise AssertionError(f"Quality check failed: {name} ({detail})")
    return result.passed


# ─── Argument summarization ─────────────────────────────────────────

def _summarize_dataframe(df) -> dict:
    """Compact summary of a pandas DataFrame for the audit trail.

    Captures shape + columns + first 3 rows. ~1KB instead of potentially MB.
    """
    try:
        import pandas as pd  # local import — module shouldn't hard-depend on pandas
        if not isinstance(df, pd.DataFrame):
            return {"_repr": repr(df)[:500]}
        head = df.head(3).to_dict(orient="records") if len(df) else []
        return {
            "_dataframe": True,
            "shape": list(df.shape),
            "columns": [str(c) for c in df.columns][:50],
            "head_3_rows": head,
        }
    except Exception:
        return {"_repr": repr(df)[:500]}


def _summarize_arg(value: Any) -> Any:
    """Summarize a single arg — DataFrames get shape/head, others pass through."""
    if not _SUMMARIZE_DF:
        return value
    try:
        import pandas as pd
        if isinstance(value, pd.DataFrame):
            return _summarize_dataframe(value)
    except ImportError:
        pass
    return value


def _safe_json(obj: Any, max_len: int = 4000) -> str:
    """Best-effort JSON serialization with truncation. Never raises."""
    try:
        s = json.dumps(obj, default=str)
    except (TypeError, ValueError):
        s = repr(obj)
    if len(s) > max_len:
        s = s[:max_len] + f"...<truncated {len(s) - max_len} chars>"
    return s


def _serialize_inputs(args: tuple, kwargs: dict) -> str:
    summarized_args = [_summarize_arg(a) for a in args]
    summarized_kwargs = {k: _summarize_arg(v) for k, v in kwargs.items()}
    return _safe_json({"args": summarized_args, "kwargs": summarized_kwargs})


def _serialize_output(value: Any) -> str:
    return _safe_json(_summarize_arg(value))


# ─── @observe decorator ─────────────────────────────────────────────

def observe(
    calc_name: str,
    *,
    capture_args: bool = True,
    capture_result: bool = True,
):
    """Wrap a calculation. Records inputs/output/duration/status to the audit DB.

    No-op when observability is disabled (zero overhead).
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not _ENABLED:
                return fn(*args, **kwargs)

            existing_cid = _correlation_id.get()
            cid = existing_cid or uuid.uuid4().hex[:12]
            tok_cid = _correlation_id.set(cid)
            tok_name = _calc_name.set(calc_name)
            tok_buf = _flag_buffer.set([])

            started = datetime.now(timezone.utc)
            t0 = time.perf_counter()
            inputs = _serialize_inputs(args, kwargs) if capture_args else None

            status = "ok"
            error_text: str | None = None
            output: Any = None

            try:
                output = fn(*args, **kwargs)
                return output
            except Exception:
                status = "error"
                error_text = traceback.format_exc()
                logger.error("observe: calc_error %s", calc_name)
                raise
            finally:
                duration_ms = (time.perf_counter() - t0) * 1000
                flags = _flag_buffer.get()
                if status == "ok" and flags:
                    status = "flagged"
                output_json = (
                    _serialize_output(output)
                    if capture_result and status != "error"
                    else None
                )
                try:
                    with _connect() as conn:
                        conn.execute(
                            "INSERT INTO calculations "
                            "(correlation_id, calc_name, started_at, finished_at, "
                            " duration_ms, inputs_json, output_json, status, "
                            " error, flag_count) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (cid, calc_name, started.isoformat(),
                             datetime.now(timezone.utc).isoformat(),
                             duration_ms, inputs, output_json, status,
                             error_text, len(flags)),
                        )
                except sqlite3.Error as e:
                    logger.error("calc_persist_failed: %s", e)

                _correlation_id.reset(tok_cid)
                _calc_name.reset(tok_name)
                _flag_buffer.reset(tok_buf)
        return wrapper
    return decorator


@contextmanager
def correlation(cid: str | None = None):
    """Manually scope a correlation_id (e.g. one per pipeline run)."""
    if not _ENABLED:
        yield cid
        return
    cid = cid or uuid.uuid4().hex[:12]
    tok = _correlation_id.set(cid)
    try:
        yield cid
    finally:
        _correlation_id.reset(tok)


# ─── Query helpers (powering the Diagnostics dashboard) ─────────────

def recent_calcs(limit: int = 50, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM calculations"
    params: tuple = ()
    if status:
        sql += " WHERE status = ?"
        params = (status,)
    sql += " ORDER BY id DESC LIMIT ?"
    params = (*params, limit)
    try:
        with _connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.Error as e:
        logger.warning("recent_calcs failed: %s", e)
        return []


def trace(correlation_id: str) -> dict:
    """Return everything that happened under one correlation_id."""
    try:
        with _connect() as conn:
            calcs = [dict(r) for r in conn.execute(
                "SELECT * FROM calculations WHERE correlation_id = ? ORDER BY id",
                (correlation_id,),
            ).fetchall()]
            checks = [dict(r) for r in conn.execute(
                "SELECT * FROM quality_checks WHERE correlation_id = ? ORDER BY id",
                (correlation_id,),
            ).fetchall()]
        return {"correlation_id": correlation_id, "calcs": calcs, "checks": checks}
    except sqlite3.Error as e:
        logger.warning("trace failed: %s", e)
        return {"correlation_id": correlation_id, "calcs": [], "checks": []}


def quality_summary(hours: int = 24) -> list[dict]:
    """Pass rate per check name in the last N hours. Sorted ascending pass rate."""
    sql = """
    SELECT check_name,
           COUNT(*)                              AS total,
           SUM(passed)                           AS passed,
           SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END) AS failed,
           ROUND(100.0 * SUM(passed) / COUNT(*), 2) AS pass_rate_pct
    FROM quality_checks
    WHERE ts >= datetime('now', ?)
    GROUP BY check_name
    ORDER BY pass_rate_pct ASC, total DESC
    """
    try:
        with _connect() as conn:
            return [dict(r) for r in conn.execute(sql, (f"-{int(hours)} hours",)).fetchall()]
    except sqlite3.Error as e:
        logger.warning("quality_summary failed: %s", e)
        return []


def total_row_counts() -> dict[str, int]:
    """Row counts for the Admin page status display."""
    try:
        with _connect() as conn:
            calcs = conn.execute("SELECT COUNT(*) AS n FROM calculations").fetchone()["n"]
            checks = conn.execute("SELECT COUNT(*) AS n FROM quality_checks").fetchone()["n"]
        return {"calculations": int(calcs or 0), "quality_checks": int(checks or 0)}
    except sqlite3.Error:
        return {"calculations": 0, "quality_checks": 0}


__all__ = [
    "configure", "is_enabled", "current_correlation_id",
    "init_db", "apply_retention",
    "observe", "quality_check", "correlation",
    "recent_calcs", "trace", "quality_summary", "total_row_counts",
    "QualityCheckResult",
]
