"""Offline US city → (latitude, longitude) lookup.

Uses ``geonamescache`` (no network calls). Lookups are cached per-process so
repeated calls during a Streamlit rerun are cheap.

Also exposes USPS state-name → 2-letter code helpers for choropleth maps.
"""

from __future__ import annotations

from functools import lru_cache

import geonamescache

# min_city_population=1000 ships ~17k US cities — wide enough to resolve
# small towns like Hutchins TX, Whitestown IN, Social Circle GA that appear
# in the CRST data. Default (15k) misses these.
_GC = geonamescache.GeonamesCache(min_city_population=1000)


# 2-letter USPS code → full name and back
_STATE_NAME_TO_CODE = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE",
    "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM",
    "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH",
    "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX",
    "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "DISTRICT OF COLUMBIA": "DC",
}


def normalize_state(state: str | None) -> str | None:
    """Return the 2-letter USPS code for a state name or code; None if unknown."""
    if not state:
        return None
    s = str(state).strip().upper()
    if len(s) == 2:
        return s if s in _STATE_NAME_TO_CODE.values() else None
    return _STATE_NAME_TO_CODE.get(s)


@lru_cache(maxsize=8192)
def lookup_city(city: str | None, state: str | None) -> tuple[float, float] | None:
    """Look up (latitude, longitude) for a US city name + state.

    Returns None if the city can't be resolved.
    """
    if not city:
        return None
    state_code = normalize_state(state)
    matches = _GC.get_cities_by_name(city.strip().title())
    if not matches:
        # Try uppercase variant
        matches = _GC.get_cities_by_name(city.strip().upper())
    if not matches:
        return None

    # geonamescache returns a list of {geonameid: city_dict} dicts
    candidates: list[dict] = []
    for m in matches:
        for _gid, info in m.items():
            if info.get("countrycode") != "US":
                continue
            if state_code and info.get("admin1code") != state_code:
                continue
            candidates.append(info)

    if not candidates and state_code:
        # No exact state match — fall back to any US city with that name
        for m in matches:
            for _gid, info in m.items():
                if info.get("countrycode") == "US":
                    candidates.append(info)

    if not candidates:
        return None

    # Pick the largest (most populous) match
    best = max(candidates, key=lambda c: c.get("population", 0))
    return float(best["latitude"]), float(best["longitude"])


def geocode_city_state(city: str | None, state: str | None) -> tuple[float | None, float | None]:
    """Convenience: returns (lat, lon) tuple with Nones on failure."""
    result = lookup_city(city, state)
    if result is None:
        return None, None
    return result
