"""Equity research: prompt builders and preset research question templates.

All prompts enforce the no-buy/sell-advice rule and use factual, analyst-style
language. The filing prompt produces a structured 7-section report; the web
research prompt synthesizes search snippets into a cited narrative answer.
Streamlit-free; the Streamlit-cached service wrappers live in data/service.py.
"""

from __future__ import annotations

from typing import List

# Preset research questions shown as quick-pick buttons in the UI.
QUICK_QUESTIONS: List[str] = [
    "What were the key financial results in the most recent quarter or fiscal year?",
    "What is the company's competitive positioning and main strategic risks?",
    "What recent strategic announcements or management changes have occurred?",
    "What are the key capital allocation priorities and balance sheet trends?",
]

_FILING_INSTRUCTIONS = (
    "Structure your analysis using exactly these section headings:\n\n"
    "## Overview\n"
    "2-3 sentences on what this filing covers and the reporting period.\n\n"
    "## Financial Highlights\n"
    "Key financial metrics — revenue, operating income, net income, EPS, free cash "
    "flow, cash/debt balance — with specific numbers and year-over-year or "
    "quarter-over-quarter comparisons. If not a periodic financial report (e.g. 8-K), "
    "describe the specific financial figures or guidance disclosed.\n\n"
    "## Business & Operational Findings\n"
    "Material business developments: segment performance, management commentary, "
    "strategy updates, M&A activity, product launches, headcount changes, and "
    "other non-financial disclosures of note.\n\n"
    "## Risk Factors\n"
    "Top 3-5 risks disclosed or updated in this filing. Flag any that appear new, "
    "materially changed, or elevated compared to a routine periodic report.\n\n"
    "## Forward-Looking Statements\n"
    "Financial guidance ranges, capital allocation plans, management commentary "
    "on macro conditions, or other notable forward-looking language.\n\n"
    "## Red Flags\n"
    "Potentially concerning items: accounting policy changes, restatements, unusual "
    "related-party transactions, going-concern disclosures, covenant issues, auditor "
    "changes, or executive departures. Write 'None identified' if nothing stands out.\n\n"
    "## Net Assessment\n"
    "One factual sentence summarizing the net informational read from this filing.\n\n"
    "Rules:\n"
    "- Be factual; cite specific figures and dates from the filing text.\n"
    "- Do NOT give buy/sell advice or price targets.\n"
    "- Do NOT use 'should', 'recommend', or similar prescriptive language.\n"
    "- Use only information present in the filing text provided."
)


def filing_research_prompt(
    ticker: str,
    name: str,
    form: str,
    filing_date: str,
    text: str,
    truncated: bool = False,
) -> str:
    """Comprehensive structured prompt for equity-research-grade filing analysis."""
    trunc_note = (
        "\n\nNOTE: The filing text was truncated or section-extracted for cost "
        "control. Acknowledge this briefly in the Overview section."
        if truncated
        else ""
    )
    return (
        f"You are an equity analyst performing a thorough analysis of a {form} "
        f"filed {filing_date} by {ticker} ({name}).\n\n"
        + _FILING_INSTRUCTIONS
        + trunc_note
        + "\n\n--- FILING TEXT ---\n"
        + (text or "")
    )


def web_research_prompt(
    ticker: str,
    name: str,
    question: str,
    snippets: List[dict],
) -> str:
    """Synthesis prompt from web search results for a research question."""
    if not snippets:
        sources = "(No web results were returned for this query.)"
    else:
        sources = "\n\n".join(
            f"[{i + 1}] {s.get('title', '').strip()}\n"
            f"URL: {s.get('url', '')}\n"
            f"{s.get('snippet', '').strip()}"
            for i, s in enumerate(snippets)
        )
    return (
        f"You are a financial researcher answering the following question "
        f"about {ticker} ({name}):\n\n"
        f"Question: {question}\n\n"
        "The following web search results were retrieved:\n\n"
        f"{sources}\n\n"
        "Synthesize the above into a factual, well-structured answer of 4-8 "
        "sentences. Cite sources by number [1], [2], etc. wherever relevant. "
        "Do NOT give buy/sell advice or price targets. "
        "If the results do not adequately address the question, state what "
        "information was found and what was not. "
        "Be objective and factual throughout."
    )


def build_search_query(ticker: str, name: str, question: str) -> str:
    """Construct a targeted web search query from a research question."""
    # Prefix with ticker and company name to anchor results to this company.
    short_name = name.split(",")[0].split("(")[0].strip()
    return f"{ticker} {short_name} {question}"
