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

import os
from typing import Dict, List

import pandas as pd
import streamlit as st

# The uploaded file uses spaces (not underscores) before "from MorningStar".
EXCEL_FILENAME = "AI_Equities_Universe_Data from MorningStar.xlsx"
SHEET = "AI Equity Analysis"

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

    df = df.copy().reset_index(drop=True)  # de-fragment
    return df


def sector_options(df: pd.DataFrame) -> List[str]:
    return sorted(df["Sector"].dropna().unique().tolist())


def style_options(df: pd.DataFrame) -> List[str]:
    return sorted(df["Stock Style Box"].dropna().unique().tolist())
