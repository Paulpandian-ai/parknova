"""Load and parse the AI equities universe from the Excel workbook.

Only the *static metadata* columns are used here (Ticker, Company, buckets,
GICS, AI relevance, notes). All price/cap/multiple values in the spreadsheet are
treated as stale placeholders — live numbers come from FMP at runtime.
"""

from __future__ import annotations

import os
import re
from typing import Optional

import pandas as pd
import streamlit as st

EXCEL_FILENAME = "AI_Equities_Universe.xlsx"

MASTER_SHEET = "AI Universe (Master)"
ETF_SHEET = "ETFs"
OTC_SHEET = "OTC Sleeve (Lower Liquidity)"

GROUP_SINGLE = "Single Name"
GROUP_ETF = "ETF"
GROUP_OTC = "OTC / Lower Liquidity"

# Bucket prefixes use either a digit or a letter (R/X/Q). Map to readable labels.
_BUCKET_RE = re.compile(r"^\s*([0-9A-Za-z])\s+(.*)$")


def _excel_path() -> str:
    return os.path.join(os.getcwd(), EXCEL_FILENAME)


def parse_bucket(raw: object) -> tuple[Optional[str], str]:
    """Split a raw bucket like ``'7 Hyperscaler'`` into (code, label).

    Returns (None, 'Unclassified') for blanks. The label is what we show; the
    code preserves ordering ('1'..'7' then R/X/Q).
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None, "Unclassified"
    text = str(raw).strip()
    if not text:
        return None, "Unclassified"
    m = _BUCKET_RE.match(text)
    if m:
        return m.group(1), m.group(2).strip()
    return None, text


def _clean_ticker(raw: object) -> Optional[str]:
    """Normalise a ticker cell. Multi-symbol cells (e.g. 'HXSCL / HXSCF') keep
    the first symbol, which is the more liquid / primary one in this workbook."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    text = str(raw).strip()
    if not text or text.lower() == "nan":
        return None
    # Take first symbol if several are slash-separated.
    first = re.split(r"[\/,]", text)[0].strip()
    return first.upper() or None


def _str_or_none(val: object) -> Optional[str]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    text = str(val).strip()
    return text or None


@st.cache_data(show_spinner=False)
def load_universe(path: Optional[str] = None) -> pd.DataFrame:
    """Load all three sheets into one tidy metadata frame.

    Columns: ticker, company, group, bucket_code, bucket, secondary_weights,
    gics, ai_relevance, notes, low_liquidity, exchange.
    Cached for the session (no ttl) since the file does not change at runtime.
    """
    path = path or _excel_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Universe file '{EXCEL_FILENAME}' not found in project root."
        )

    frames = []

    # --- Master single names (header row 4 -> header=3) ---
    master = pd.read_excel(path, sheet_name=MASTER_SHEET, header=3)
    frames.append(_normalise_master(master))

    # --- ETFs (header row 3 -> header=2) ---
    try:
        etfs = pd.read_excel(path, sheet_name=ETF_SHEET, header=2)
        frames.append(_normalise_etfs(etfs))
    except Exception:
        pass

    # --- OTC sleeve (header row 3 -> header=2) ---
    try:
        otc = pd.read_excel(path, sheet_name=OTC_SHEET, header=2)
        frames.append(_normalise_otc(otc))
    except Exception:
        pass

    out = pd.concat(frames, ignore_index=True)
    out = out[out["ticker"].notna()].copy()
    # Drop duplicate tickers, keeping the first (master/ETF before OTC).
    out = out.drop_duplicates(subset="ticker", keep="first").reset_index(drop=True)
    return out


def _normalise_master(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        ticker = _clean_ticker(r.get("Ticker"))
        if not ticker:
            continue
        code, label = parse_bucket(r.get("Primary Bucket"))
        rows.append(
            {
                "ticker": ticker,
                "company": _str_or_none(r.get("Company")) or ticker,
                "group": GROUP_SINGLE,
                "bucket_code": code,
                "bucket": label,
                "secondary_weights": _str_or_none(r.get("Secondary Bucket Weights")),
                "gics": _str_or_none(r.get("GICS Sector / Industry")),
                "ai_relevance": _str_or_none(r.get("AI Relevance")),
                "notes": _str_or_none(r.get("Notes / Liquidity Flag")),
                "exchange": _str_or_none(r.get("Exchange")),
                "low_liquidity": False,
            }
        )
    return pd.DataFrame(rows)


def _normalise_etfs(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        ticker = _clean_ticker(r.get("Ticker"))
        if not ticker:
            continue
        rows.append(
            {
                "ticker": ticker,
                "company": _str_or_none(r.get("ETF Name")) or ticker,
                "group": GROUP_ETF,
                "bucket_code": "ETF",
                "bucket": "ETF",
                "secondary_weights": None,
                "gics": _str_or_none(r.get("Focus")),
                "ai_relevance": _str_or_none(r.get("Focus")),
                "notes": _str_or_none(r.get("Notes")),
                "exchange": _str_or_none(r.get("Exchange")),
                "low_liquidity": False,
            }
        )
    return pd.DataFrame(rows)


def _normalise_otc(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        ticker = _clean_ticker(r.get("Ticker"))
        if not ticker:
            continue
        code, label = parse_bucket(r.get("Primary Bucket"))
        rows.append(
            {
                "ticker": ticker,
                "company": _str_or_none(r.get("Company")) or ticker,
                "group": GROUP_OTC,
                "bucket_code": code,
                "bucket": label,
                "secondary_weights": None,
                "gics": None,
                "ai_relevance": None,
                "notes": _str_or_none(r.get("Notes"))
                or _str_or_none(r.get("Liquidity Flag")),
                "exchange": _str_or_none(r.get("Venue")),
                "low_liquidity": True,
            }
        )
    return pd.DataFrame(rows)


def bucket_options(df: pd.DataFrame) -> list[str]:
    """Sorted unique bucket labels for the filter multiselect."""
    buckets = (
        df.loc[df["bucket"].notna(), ["bucket_code", "bucket"]]
        .drop_duplicates()
        .sort_values(by="bucket_code", key=lambda s: s.fillna("zz").astype(str))
    )
    return buckets["bucket"].tolist()
