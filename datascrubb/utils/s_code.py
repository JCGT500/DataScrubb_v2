"""S-Code extraction utility."""

import re

import pandas as pd


def extract_s_code(text) -> str | None:
    """Extract an S-Code (e.g. S12345) from a text string.

    Matches the pattern S followed by 3-5 digits.
    Returns None if text is NaN/None or no match is found.
    """
    if pd.isna(text):
        return None
    match = re.search(r"S\d{3,5}", str(text))
    return match.group(0) if match else None
