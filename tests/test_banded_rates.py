"""Unit tests for the banded rate-table lookup.

The lookup is a pure function, so all behavior — band-boundary handling,
clamping above the largest band, NaN inputs, shape validation — can be
covered without touching the pipeline or any I/O.
"""

from __future__ import annotations

import math

import pytest

from datascrubb.kpi.revenue import lookup_banded_rate, rate_for


# ─── canonical 5×5 matrix used across tests ──────────────────────
#
#         | ≤500 | ≤1000 | ≤2000 | ≤5000 | >5000
# ≤50mi   |  150 |   175 |   225 |   325 |   425
# ≤150    |  225 |   275 |   375 |   525 |   700
# ≤300    |  375 |   475 |   625 |   875 |  1175
# ≤500    |  575 |   725 |   950 |  1325 |  1750
# >500    |  850 |  1075 |  1400 |  1950 |  2600

MILE_BANDS = [50, 150, 300, 500]
WEIGHT_BANDS = [500, 1000, 2000, 5000]
MATRIX = [
    [150, 175, 225, 325, 425],
    [225, 275, 375, 525, 700],
    [375, 475, 625, 875, 1175],
    [575, 725, 950, 1325, 1750],
    [850, 1075, 1400, 1950, 2600],
]


# ─── cell selection ────────────────────────────────────────────────

@pytest.mark.parametrize("miles,weight,expected", [
    (10, 100, 150),       # smallest cell
    (10, 600, 175),       # row 0 col 1
    (200, 1500, 625),     # row 2 col 2
    (450, 4900, 1325),    # row 3 col 3 (just under both upper bounds)
    (700, 6000, 2600),    # largest cell (above-max on both axes)
])
def test_cell_selection(miles, weight, expected):
    assert lookup_banded_rate(miles, weight, MILE_BANDS, WEIGHT_BANDS, MATRIX) == expected


# ─── band boundaries ───────────────────────────────────────────────

def test_upper_bound_inclusive_for_miles():
    # Route at exactly 50 mi → first row, NOT second
    assert lookup_banded_rate(50, 100, MILE_BANDS, WEIGHT_BANDS, MATRIX) == 150
    # Just above 50 → second row
    assert lookup_banded_rate(50.001, 100, MILE_BANDS, WEIGHT_BANDS, MATRIX) == 225


def test_upper_bound_inclusive_for_weight():
    assert lookup_banded_rate(10, 500, MILE_BANDS, WEIGHT_BANDS, MATRIX) == 150
    assert lookup_banded_rate(10, 500.001, MILE_BANDS, WEIGHT_BANDS, MATRIX) == 175


# ─── above-max clamping ────────────────────────────────────────────

def test_miles_above_largest_band_clamps_to_last_row():
    assert lookup_banded_rate(10_000, 100, MILE_BANDS, WEIGHT_BANDS, MATRIX) == 850


def test_weight_above_largest_band_clamps_to_last_col():
    assert lookup_banded_rate(10, 99_999, MILE_BANDS, WEIGHT_BANDS, MATRIX) == 425


def test_both_above_clamp_to_far_corner():
    assert lookup_banded_rate(99_999, 99_999, MILE_BANDS, WEIGHT_BANDS, MATRIX) == 2600


# ─── NaN / missing handling ────────────────────────────────────────

def test_nan_miles_returns_nan():
    assert math.isnan(lookup_banded_rate(float("nan"), 100, MILE_BANDS, WEIGHT_BANDS, MATRIX))


def test_nan_weight_falls_into_lowest_weight_band():
    # Empty / unmeasured-weight routes still need a price — use first weight column
    assert lookup_banded_rate(200, float("nan"), MILE_BANDS, WEIGHT_BANDS, MATRIX) == 375


def test_zero_weight_falls_into_lowest_weight_band():
    assert lookup_banded_rate(200, 0, MILE_BANDS, WEIGHT_BANDS, MATRIX) == 375


# ─── shape validation ─────────────────────────────────────────────

def test_too_few_rows_raises():
    bad = MATRIX[:-1]  # 4 rows instead of 5
    with pytest.raises(ValueError, match="shape mismatch"):
        lookup_banded_rate(10, 100, MILE_BANDS, WEIGHT_BANDS, bad)


def test_too_few_cols_raises():
    bad = [row[:-1] for row in MATRIX]  # 4 cols instead of 5
    with pytest.raises(ValueError, match="shape mismatch"):
        lookup_banded_rate(10, 100, MILE_BANDS, WEIGHT_BANDS, bad)


# ─── rate_for() integration ────────────────────────────────────────

def test_rate_for_defaults_pricing_model_to_flat():
    matrix = {"default": {"rate_per_mile": 2.25}, "customers": {"CSL": {"rate_per_mile": 2.50}}}
    assert rate_for("CSL", matrix)["pricing_model"] == "flat"
    assert rate_for("UNKNOWN", matrix)["pricing_model"] == "flat"


def test_rate_for_surfaces_banded_pricing_model():
    matrix = {
        "default": {"rate_per_mile": 2.25},
        "customers": {
            "ACME": {
                "pricing_model": "banded",
                "mile_bands": MILE_BANDS,
                "weight_bands": WEIGHT_BANDS,
                "rate_matrix": MATRIX,
            }
        },
    }
    r = rate_for("ACME", matrix)
    assert r["pricing_model"] == "banded"
    assert r["mile_bands"] == MILE_BANDS
    assert r["weight_bands"] == WEIGHT_BANDS
    assert r["rate_matrix"] == MATRIX


def test_rate_for_handles_empty_matrix():
    # No default, no customer — pricing_model still defaults
    assert rate_for(None, {})["pricing_model"] == "flat"
    assert rate_for("ANY", {"customers": {}})["pricing_model"] == "flat"
