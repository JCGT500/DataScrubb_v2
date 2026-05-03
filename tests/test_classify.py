"""Tests for stop classification — PLASMA wins over name regex; rules are config-driven."""

import pandas as pd

from datascrubb.classify import (
    DISTRIBUTION_CENTER,
    INTERNAL_BASE,
    OTHER,
    PLASMA_CENTER,
    ClassifyConfig,
    classify_stop,
    classify_stops_df,
)


def test_s_code_overrides_name():
    cfg = ClassifyConfig()
    # Customer name suggests warehouse, but S-code makes it plasma
    assert classify_stop("RX CROSSROADS", "S1234", cfg) == PLASMA_CENTER


def test_default_rules_distribution():
    cfg = ClassifyConfig()
    assert classify_stop("RX CROSSROADS", None, cfg) == DISTRIBUTION_CENTER
    assert classify_stop("CSL PLASMA WAREHOUSE", None, cfg) == DISTRIBUTION_CENTER
    assert classify_stop("SOME WAREHOUSE", None, cfg) == DISTRIBUTION_CENTER


def test_default_rules_internal_base():
    cfg = ClassifyConfig()
    assert classify_stop("CRST INTERNATIONAL", None, cfg) == INTERNAL_BASE
    assert classify_stop("THERMOKING INDY", None, cfg) == INTERNAL_BASE
    assert classify_stop("CRST LOUISVILLE", None, cfg) == INTERNAL_BASE


def test_unmatched_falls_to_default():
    cfg = ClassifyConfig()
    assert classify_stop("Some Random Customer", None, cfg) == OTHER
    assert classify_stop(None, None, cfg) == OTHER
    assert classify_stop("", None, cfg) == OTHER


def test_custom_rules():
    cfg = ClassifyConfig(rules=[{"class": "CUSTOM", "pattern": "ACME"}], default_class="UNKNOWN")
    assert classify_stop("ACME CORP", None, cfg) == "CUSTOM"
    assert classify_stop("Other", None, cfg) == "UNKNOWN"


def test_dataframe_classification():
    df = pd.DataFrame({
        "customer": ["RX CROSSROADS", "BIOLIFE", "CRST INTERNATIONAL", "ACME"],
        "s_code": [None, "S1234", None, None],
    })
    out = classify_stops_df(df, ClassifyConfig())
    assert list(out) == [DISTRIBUTION_CENTER, PLASMA_CENTER, INTERNAL_BASE, OTHER]


def test_empty_dataframe():
    out = classify_stops_df(pd.DataFrame(), ClassifyConfig())
    assert out.empty
