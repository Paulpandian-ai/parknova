"""Unit tests for core/equity_research.py and data/web_search.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core import equity_research as er


# ---------------------------------------------------------------------------
# Prompt builders — structure and safety
# ---------------------------------------------------------------------------
class TestFilingResearchPrompt:
    def test_contains_all_section_headings(self):
        prompt = er.filing_research_prompt(
            "NVDA", "NVIDIA Corporation", "10-K", "2025-01-28",
            "Revenue was $35B this quarter.")
        for heading in (
            "## Overview",
            "## Financial Highlights",
            "## Business & Operational Findings",
            "## Risk Factors",
            "## Forward-Looking Statements",
            "## Red Flags",
            "## Net Assessment",
        ):
            assert heading in prompt, f"Missing section: {heading}"

    def test_contains_ticker_and_name(self):
        prompt = er.filing_research_prompt("TSLA", "Tesla Inc", "10-Q", "2025-04-01", "")
        assert "TSLA" in prompt
        assert "Tesla Inc" in prompt

    def test_contains_form_and_date(self):
        prompt = er.filing_research_prompt("AAPL", "Apple Inc", "10-K", "2025-11-01", "")
        assert "10-K" in prompt
        assert "2025-11-01" in prompt

    def test_filing_text_appended(self):
        text = "Net income was $10B. Revenue grew 20%."
        prompt = er.filing_research_prompt("AAPL", "Apple", "10-K", "2025-01-01", text)
        assert text in prompt

    def test_truncation_note_added_when_flag_true(self):
        prompt = er.filing_research_prompt(
            "AAPL", "Apple", "10-K", "2025-01-01", "text", truncated=True)
        assert "truncated" in prompt.lower()

    def test_no_truncation_note_by_default(self):
        prompt = er.filing_research_prompt(
            "AAPL", "Apple", "10-K", "2025-01-01", "text")
        assert "truncated" not in prompt.lower()

    def test_no_buysell_language_in_rules(self):
        prompt = er.filing_research_prompt("X", "Company X", "10-K", "2025-01-01", "")
        # The rules block should forbid buy/sell but not use the words affirmatively.
        assert "NOT give buy/sell advice" in prompt


class TestWebResearchPrompt:
    def test_contains_question(self):
        q = "What is the revenue growth trend?"
        prompt = er.web_research_prompt("NVDA", "NVIDIA", q, [])
        assert q in prompt

    def test_sources_enumerated(self):
        snippets = [
            {"title": "NVDA Earnings", "url": "https://ex.com/1", "snippet": "Revenue up 80%"},
            {"title": "NVDA Forecast", "url": "https://ex.com/2", "snippet": "Strong outlook"},
        ]
        prompt = er.web_research_prompt("NVDA", "NVIDIA", "earnings?", snippets)
        assert "[1]" in prompt
        assert "[2]" in prompt
        assert "Revenue up 80%" in prompt

    def test_no_snippets_noted(self):
        prompt = er.web_research_prompt("NVDA", "NVIDIA", "any question?", [])
        assert "No web results" in prompt

    def test_no_buysell_in_instructions(self):
        prompt = er.web_research_prompt("X", "Company", "revenue?", [])
        assert "NOT give buy/sell" in prompt


class TestBuildSearchQuery:
    def test_includes_ticker_and_name(self):
        q = er.build_search_query("TSLA", "Tesla Inc", "earnings results")
        assert "TSLA" in q
        assert "Tesla" in q
        assert "earnings results" in q

    def test_strips_long_legal_suffixes(self):
        q = er.build_search_query("AAPL", "Apple Inc, California", "revenue")
        assert "Apple" in q
        # The long suffix after the comma should be stripped
        assert "California" not in q


class TestQuickQuestions:
    def test_four_preset_questions(self):
        assert len(er.QUICK_QUESTIONS) == 4

    def test_no_buysell_in_presets(self):
        for q in er.QUICK_QUESTIONS:
            assert "buy" not in q.lower()
            assert "sell" not in q.lower()


# ---------------------------------------------------------------------------
# WebSearchClient — unit tests (mocked HTTP)
# ---------------------------------------------------------------------------
class TestWebSearchClient:
    def _make_client(self):
        from data.web_search import WebSearchClient
        return WebSearchClient()

    def test_empty_query_returns_empty(self):
        client = self._make_client()
        assert client.search("") == []
        assert client.search("   ") == []

    def test_network_error_returns_empty(self):
        import requests
        client = self._make_client()
        with patch.object(client._session, "post",
                          side_effect=requests.ConnectionError("offline")):
            result = client.search("NVDA earnings", max_results=3)
        assert result == []

    def test_http_error_returns_empty(self):
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 503
        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.search("NVDA earnings")
        assert result == []

    def test_parses_mock_html(self):
        client = self._make_client()
        # Minimal DDG-style HTML with one result card.
        html = """
        <html><body>
        <div class="result">
          <a class="result__a" href="https://example.com/nvda">NVDA Q2 Results</a>
          <div class="result__snippet">Revenue hit $30B, up 120% YoY.</div>
        </div>
        </body></html>"""
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.text = html
        with patch.object(client._session, "post", return_value=mock_resp):
            results = client.search("NVDA earnings")
        assert len(results) == 1
        assert results[0]["title"] == "NVDA Q2 Results"
        assert results[0]["url"] == "https://example.com/nvda"
        assert "30B" in results[0]["snippet"]

    def test_max_results_respected(self):
        client = self._make_client()
        cards = "".join(
            f'<div class="result">'
            f'<a class="result__a" href="https://ex.com/{i}">Title {i}</a>'
            f'<div class="result__snippet">Snippet {i}</div>'
            f'</div>'
            for i in range(10))
        html = f"<html><body>{cards}</body></html>"
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.text = html
        with patch.object(client._session, "post", return_value=mock_resp):
            results = client.search("query", max_results=3)
        assert len(results) == 3

    def test_429_retry_then_empty(self):
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.ok = False
        with patch.object(client._session, "post", return_value=mock_resp):
            with patch("data.web_search.time.sleep"):  # skip actual delay
                result = client.search("NVDA")
        assert result == []
