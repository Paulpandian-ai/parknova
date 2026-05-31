"""Load and clean the Morningstar fundamentals export.

This file is the **primary, static** data source for ParkNova: all fundamentals,
valuation, profitability, growth, financial health, Morningstar verdicts and
trailing returns (YTD/1Y/3Y/5Y) come from here. Nothing in this module touches
an API.

Design notes / data-quality reality handled here:

* 226 rows, single sheet "AI Equity Analysis", header in row 1 (``header=0``).
* Percent columns are already in percent units (e.g. 332.55 == +332.55%); we do
  NOT multiply by 100. Internally we keep returns/margins as *fractions* only
  where the factor/format layer expects fractions — but to keep one consistent
  convention, this loader leaves Morningstar percent columns in **percent units**
  and the format helpers know that. (See PERCENT_COLS.)
* Coverage is partial for many columns (P/E ~151/226, moat ~118/226 ...). Blanks
  are kept as NaN and rendered "—" downstream; never coerced to 0.
* Large absolutes (Revenue, Net Income, ...) are raw dollars.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

logger = logging.getLogger("parknova.morningstar")

# The uploaded file uses spaces (not underscores) before "from MorningStar".
EXCEL_FILENAME = "AI_Equities_Universe_Data from MorningStar.xlsx"
SHEET = "AI Equity Analysis"

# Curated Primary Bucket taxonomy (Feature A). Joined from bucket_mapping.csv.
BUCKET_MAPPING_FILENAME = "bucket_mapping.csv"
UNCLASSIFIED = "Unclassified"
# Canonical bucket order (code -> label), used for ordering/options everywhere.
BUCKET_ORDER: List[str] = [
    "1 Compute Semi", "2 Memory", "3 Foundry/Semicap", "4 Networking",
    "5 Power/Cooling", "6 AI Software", "7 Hyperscaler", "R Robotics/Autonomy",
    "X Edge AI/Vision", "Q Quantum", UNCLASSIFIED,
]
_VALID_BUCKETS = set(BUCKET_ORDER) - {UNCLASSIFIED}

# Curated Side / Crest timing taxonomy (Capex Cycle). Joined from
# crest_timing_mapping.csv. Side = who they are in the buildout; Crest = where
# the layer sits in the capex wave (Early chips/memory/optics -> Mid equipment/
# software/networking -> Late power/cooling/grid).
CREST_MAPPING_FILENAME = "crest_timing_mapping.csv"
SIDE_UNKNOWN = "Unknown"
CREST_DEFAULT = "Mid"
SIDE_ORDER: List[str] = ["Supplier", "Demand", "Hyperscaler", SIDE_UNKNOWN]
CREST_ORDER: List[str] = ["Early", "Mid", "Late"]
_VALID_SIDES = {"Supplier", "Demand", "Hyperscaler"}
_VALID_CRESTS = set(CREST_ORDER)

# --- Identity / price -------------------------------------------------------
ID_COLS = ["Ticker", "Name", "Sector", "Stock Style Box", "Last Price",
           "Day Change", "Day Change (%)"]

# --- Morningstar verdicts ---------------------------------------------------
VERDICT_COLS = ["Economic Moat", "Fair Value", "Fair Value Uncertainty",
                "Growth Grade", "Profitability Grade",
                "Morningstar Rating for Stocks"]

# --- Trailing returns (PERCENT units) ---------------------------------------
TRAILING_RETURN_COLS = ["Total Return (YTD)", "Total Return (1Y)",
                        "Total Return (3Y)", "Total Return (5Y)"]

# --- Column groups for the togglable fundamentals table ---------------------
# Each entry: friendly label -> list of source column names.
VALUATION_COLS: List[str] = [
    "Price/Earnings", "Price/Earnings (Normalized)", "Price/Earnings (Forward)",
    "Price/Earnings to Growth", "Price/Earnings to Growth (Forward)",
    "Price/Book Value", "Price/Sales", "Price/Cash Flow",
    "Price/Free Cash Flow", "Price/EBITDA", "Price/Cash", "PEG Payback",
    "Earnings Yield", "Sales Yield", "Book Yield", "Cash Flow Yield",
    "Total Yield", "Buyback Yield",
]

PROFITABILITY_COLS: List[str] = [
    "Gross Margin", "Operating Margin", "Net Margin",
    "Return on Equity", "Return on Equity (Normalized)",
    "Return on Assets", "Return on Assets (Normalized)",
    "Return on Invested Capital", "Return on Invested Capital (Normalized)",
    "Asset Turnover Ratio", "Inventory Turnover (1Y Avg)",
]

GROWTH_COLS: List[str] = [
    "Revenue Growth (1Y)", "Revenue Growth (5Y)",
    "EPS Growth (TTM)", "EPS Growth (1Y)", "EPS Growth (3Y)", "EPS Growth (5Y)",
    "Net Income Growth (1Y)", "Net Income Growth (3Y)", "Net Income Growth (5Y)",
    "Operating Profit Growth (1Y)", "Operating Profit Growth (3Y)",
    "Book Value per Share Growth (1Y)", "Book Value per Share Growth (5Y)",
]

HEALTH_COLS: List[str] = [
    "Current Ratio", "Quick Ratio", "Total Debt/Equity",
    "Long Term Debt/Equity", "Total Debt/Capital", "Financial Leverage",
    "Interest Coverage", "Working Capital",
]

ABSOLUTE_COLS: List[str] = [
    "Revenue", "Net Income", "EBITDA", "Operating Profit", "Free Cash Flow",
    "Cash Flow from Operations", "Capital Expenditures", "Total Assets",
    "Total Liabilities", "Total Equity", "Cash (Balance Sheet)",
    "Long-Term Debt", "Short-Term Debt",
]

# Column-group registry for the UI toggles (Functionality 2a).
COLUMN_GROUPS: Dict[str, List[str]] = {
    "Valuation": VALUATION_COLS,
    "Profitability": PROFITABILITY_COLS,
    "Growth": GROWTH_COLS,
    "Financial Health": HEALTH_COLS,
    "Morningstar Verdicts": VERDICT_COLS,
    "Statement Absolutes": ABSOLUTE_COLS,
}

# --- Formatting classification ---------------------------------------------
# Multiples render as 24.5x.
MULTIPLE_COLS = {
    "Price/Earnings", "Price/Earnings (Normalized)", "Price/Earnings (Forward)",
    "Price/Earnings to Growth", "Price/Earnings to Growth (Forward)",
    "Price/Book Value", "Price/Book Value (3Y Avg)", "Price/Book Value (5Y Avg)",
    "Price/Sales", "Price/Sales (3Y Avg)", "Price/Sales (5Y Avg)",
    "Price/Cash Flow", "Price/Cash Flow (3Y Avg)", "Price/Cash Flow (5Y Avg)",
    "Price/Free Cash Flow", "Price/Free Cash Flow (3Y Avg)",
    "Price/Free Cash Flow (5Y Avg)", "Price/EBITDA", "Price/Cash",
}

# Plain ratios render as 1.75.
RATIO_COLS = {
    "Current Ratio", "Quick Ratio", "Total Debt/Equity", "Long Term Debt/Equity",
    "Total Debt/Capital", "Long Term Debt/Capital", "Financial Leverage",
    "Interest Coverage", "Asset Turnover Ratio", "Fixed Asset Turnover (1Y Avg)",
    "Fixed Asset Turnover (3Y Avg)", "Fixed Asset Turnover (5Y Avg)",
    "Inventory Turnover (1Y Avg)", "Inventory Turnover (3Y Avg)",
    "Inventory Turnover (5Y Avg)", "PEG Payback",
    "r-Squared (1Y Monthly)", "r-Squared (3Y Monthly)", "r-Squared (5Y Monthly)",
}

# Percent-unit columns (already percent; do NOT multiply by 100).
PERCENT_COLS = set(TRAILING_RETURN_COLS) | {
    "Day Change (%)",
    "Gross Margin", "Gross Margin (1Y Avg)", "Gross Margin (3Y Avg)",
    "Operating Margin", "Operating Margin (1Y Avg)", "Operating Margin (3Y Avg)",
    "Net Margin", "Net Margin (1Y Avg)", "Net Margin (3Y Avg)",
    "Return on Equity", "Return on Equity (Normalized)", "Return on Equity (Forward)",
    "Return on Equity (3Y Avg)", "Return on Equity (5Y Avg)",
    "Return on Assets", "Return on Assets (Normalized)", "Return on Assets (Forward)",
    "Return on Assets (1Y Avg)", "Return on Assets (3Y Avg)", "Return on Assets (5Y Avg)",
    "Return on Invested Capital", "Return on Invested Capital (Normalized)",
    "Revenue Growth (1Y)", "Revenue Growth (5Y)",
    "EPS Growth (TTM)", "EPS Growth (1Y)", "EPS Growth (3Y)", "EPS Growth (5Y)",
    "Net Income Growth (1Y)", "Net Income Growth (3Y)", "Net Income Growth (5Y)",
    "Operating Profit Growth (1Y)", "Operating Profit Growth (3Y)",
    "Operating Profit Growth (5Y)", "Operating Expenses Growth (1Y)",
    "Book Value per Share Growth (1Y)", "Book Value per Share Growth (5Y)",
    "Earnings Yield", "Sales Yield", "Book Yield", "Cash Flow Yield",
    "Total Yield", "Total Yield (5Y Avg)", "Buyback Yield", "Buyback Yield (5Y Avg)",
    "Free Cash Flow/Revenue (1Y)", "Free Cash Flow/Revenue (3Y)",
    "Free Cash Flow/Revenue (5Y)",
}

# Money-magnitude columns ($B/$M). Includes Last Price/Fair Value as plain $.
MONEY_COLS = set(ABSOLUTE_COLS) | {
    "Current Assets", "Current Liabilities", "Inventories", "Receivables",
    "Payables", "Goodwill and Other Intangibles", "Fixed Assets",
}


def _excel_path(path: str | None = None) -> str:
    if path:
        return path
    return os.path.join(os.getcwd(), EXCEL_FILENAME)


@st.cache_data(show_spinner=False)
def load_morningstar(path: str | None = None) -> pd.DataFrame:
    """Load + clean the Morningstar sheet into a tidy frame.

    Cached for the session (no ttl) — the file is static input.
    Returns the full 226-row frame with original column names preserved, plus a
    couple of derived helper columns (``upside_pct``).
    """
    p = _excel_path(path)
    if not os.path.exists(p):
        raise FileNotFoundError(
            f"Morningstar file '{EXCEL_FILENAME}' not found in project root."
        )

    df = pd.read_excel(p, sheet_name=SHEET, header=0)

    # Normalise text identity columns (strip whitespace, upper ticker).
    df["Ticker"] = df["Ticker"].astype(str).str.strip().str.upper()

    def _clean_str(v) -> str | None:
        if v is None or pd.isna(v):
            return None
        s = str(v).strip()
        return s if s and s.lower() != "nan" else None

    for c in ["Name", "Sector", "Stock Style Box", "Economic Moat",
              "Fair Value Uncertainty", "Growth Grade", "Profitability Grade"]:
        if c in df.columns:
            df[c] = df[c].map(_clean_str).astype("object")

    # Drop the always-empty "Price Chart" placeholder if present.
    if "Price Chart" in df.columns:
        df = df.drop(columns=["Price Chart"])

    # Coerce every numeric column to float (non-numeric -> NaN). Identity/verdict
    # string columns are left as-is.
    string_cols = {"Ticker", "Name", "Sector", "Stock Style Box", "Economic Moat",
                   "Fair Value Uncertainty", "Growth Grade", "Profitability Grade"}
    for c in df.columns:
        if c not in string_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Derived: implied upside to Morningstar fair value (fraction).
    fv, lp = df.get("Fair Value"), df.get("Last Price")
    if fv is not None and lp is not None:
        upside = (fv - lp) / lp.where(lp != 0)
        df = pd.concat([df, upside.rename("upside_pct")], axis=1)

    # Join the curated Primary Bucket taxonomy (Feature A).
    df = _join_buckets(df, os.path.dirname(p))

    # Join the curated Side / Crest timing taxonomy (Capex Cycle).
    df = _join_crest(df, os.path.dirname(p))

    df = df.copy().reset_index(drop=True)  # de-fragment
    return df


# ---------------------------------------------------------------------------
# Primary Bucket taxonomy (Feature A)
# ---------------------------------------------------------------------------
def parse_secondary_weights(raw: object) -> Dict[str, float]:
    """Parse ``"1:0.70/4:0.20/6:0.10"`` -> ``{"1": 0.70, "4": 0.20, "6": 0.10}``.

    Returns an empty dict for blanks/unparseable input. Keys are bucket codes
    (the leading token of a bucket label, e.g. "1", "R", "Q").
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return {}
    text = str(raw).strip()
    if not text or text.lower() == "nan":
        return {}
    out: Dict[str, float] = {}
    for part in re.split(r"[/,]", text):
        part = part.strip()
        if not part or ":" not in part:
            continue
        code, _, w = part.partition(":")
        code = code.strip()
        try:
            out[code] = float(w.strip())
        except ValueError:
            continue
    return out


def _load_bucket_mapping(root: str) -> Optional[pd.DataFrame]:
    path = os.path.join(root or os.getcwd(), BUCKET_MAPPING_FILENAME)
    if not os.path.exists(path):
        logger.warning("bucket_mapping.csv not found at %s; all tickers will be "
                       "Unclassified.", path)
        return None
    try:
        m = pd.read_csv(path, dtype=str)
    except Exception as exc:  # malformed file -> degrade, do not crash
        logger.warning("Failed reading bucket_mapping.csv: %s", exc)
        return None
    m["Ticker"] = m["Ticker"].astype(str).str.strip().str.upper()
    return m


def _join_buckets(df: pd.DataFrame, root: str) -> pd.DataFrame:
    """Left-join Primary Bucket / code / secondary weights onto ``df`` by Ticker.

    Every ticker gets a bucket; unmapped or invalid -> ``Unclassified`` (logged).
    """
    mapping = _load_bucket_mapping(root)
    if mapping is not None:
        keep = ["Ticker", "Primary Bucket Code", "Primary Bucket",
                "Secondary Bucket Weights"]
        present = [c for c in keep if c in mapping.columns]
        df = df.merge(mapping[present], on="Ticker", how="left")
    # Ensure columns exist even when the mapping was missing entirely.
    for col in ["Primary Bucket Code", "Primary Bucket", "Secondary Bucket Weights"]:
        if col not in df.columns:
            df[col] = None

    # Validate / normalise the primary bucket; fall back to Unclassified.
    def _norm_bucket(v: object) -> str:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return UNCLASSIFIED
        s = str(v).strip()
        return s if s in _VALID_BUCKETS else UNCLASSIFIED

    df["Primary Bucket"] = df["Primary Bucket"].map(_norm_bucket)
    df["Primary Bucket Code"] = df["Primary Bucket"].map(
        lambda b: b.split(" ", 1)[0] if b != UNCLASSIFIED else UNCLASSIFIED)

    # Parsed secondary weights (dict per row) for future exposure-splitting.
    df["secondary_weights"] = df["Secondary Bucket Weights"].map(parse_secondary_weights)

    missing = df.loc[df["Primary Bucket"] == UNCLASSIFIED, "Ticker"].tolist()
    if missing:
        logger.warning("%d ticker(s) Unclassified (no valid bucket): %s",
                       len(missing), ", ".join(missing))
    return df


def _join_crest(df: pd.DataFrame, root: str) -> pd.DataFrame:
    """Left-join Side / Crest / Crest Rationale onto ``df`` by Ticker.

    Missing or invalid -> Side=Unknown, Crest=Mid (logged), so every ticker has
    a timing position and nothing downstream crashes.
    """
    path = os.path.join(root or os.getcwd(), CREST_MAPPING_FILENAME)
    mapping = None
    if os.path.exists(path):
        try:
            mapping = pd.read_csv(path, dtype=str)
            mapping["Ticker"] = mapping["Ticker"].astype(str).str.strip().str.upper()
        except Exception as exc:  # malformed -> degrade, do not crash
            logger.warning("Failed reading crest_timing_mapping.csv: %s", exc)
            mapping = None
    else:
        logger.warning("crest_timing_mapping.csv not found at %s; all tickers "
                       "will be Side=Unknown / Crest=Mid.", path)

    if mapping is not None:
        keep = [c for c in ["Ticker", "Side", "Crest", "Crest Rationale"]
                if c in mapping.columns]
        df = df.merge(mapping[keep], on="Ticker", how="left")
    for col in ["Side", "Crest", "Crest Rationale"]:
        if col not in df.columns:
            df[col] = None

    def _norm_side(v: object) -> str:
        s = str(v).strip() if v is not None and not (isinstance(v, float)
                                                     and pd.isna(v)) else ""
        return s if s in _VALID_SIDES else SIDE_UNKNOWN

    def _norm_crest(v: object) -> str:
        s = str(v).strip() if v is not None and not (isinstance(v, float)
                                                     and pd.isna(v)) else ""
        return s if s in _VALID_CRESTS else CREST_DEFAULT

    df["Side"] = df["Side"].map(_norm_side)
    df["Crest"] = df["Crest"].map(_norm_crest)
    df["Crest Rationale"] = df["Crest Rationale"].where(
        df["Crest Rationale"].notna(), None)

    missing = df.loc[df["Side"] == SIDE_UNKNOWN, "Ticker"].tolist()
    if missing:
        logger.warning("%d ticker(s) with no Side/Crest mapping (-> Unknown/Mid):"
                       " %s", len(missing), ", ".join(missing))
    return df


def sector_options(df: pd.DataFrame) -> List[str]:
    return sorted(df["Sector"].dropna().unique().tolist())


def side_options(df: pd.DataFrame) -> List[str]:
    present = set(df["Side"].dropna().unique())
    return [s for s in SIDE_ORDER if s in present]


def crest_options(df: pd.DataFrame) -> List[str]:
    present = set(df["Crest"].dropna().unique())
    return [c for c in CREST_ORDER if c in present]


def style_options(df: pd.DataFrame) -> List[str]:
    return sorted(df["Stock Style Box"].dropna().unique().tolist())


def bucket_options(df: pd.DataFrame) -> List[str]:
    """Buckets present in ``df``, returned in canonical taxonomy order."""
    present = set(df["Primary Bucket"].dropna().unique())
    return [b for b in BUCKET_ORDER if b in present]
