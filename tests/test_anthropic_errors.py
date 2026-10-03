"""The Anthropic client must report WHY a call failed, never a bare None, and
the Equity Research view must surface that reason instead of 'Unknown error'."""

from __future__ import annotations

from unittest.mock import MagicMock

import requests
from streamlit.testing.v1 import AppTest

from data import anthropic_client as anth


def _resp(status, body=None, text=""):
    r = MagicMock()
    r.ok = 200 <= status < 300
    r.status_code = status
    r.text = text
    if body is None:
        r.json.side_effect = ValueError("no json")
    else:
        r.json.return_value = body
    return r


def _with_key(monkeypatch):
    monkeypatch.setattr(anth, "get_secret", lambda name, default=None: "sk-test")


def test_missing_key_reports_reason(monkeypatch):
    monkeypatch.setattr(anth, "get_secret", lambda name, default=None: None)
    text, usage, err = anth._call_messages("hi", anth.MODEL_SONNET, 10)
    assert text is None and "ANTHROPIC_API_KEY" in err


def test_http_error_includes_api_message(monkeypatch):
    _with_key(monkeypatch)
    body = {"type": "error", "error": {"type": "authentication_error",
                                       "message": "invalid x-api-key"}}
    monkeypatch.setattr(anth.requests, "post", lambda *a, **k: _resp(401, body))
    text, _, err = anth._call_messages("hi", anth.MODEL_SONNET, 10)
    assert text is None
    assert "401" in err and "invalid x-api-key" in err


def test_timeout_reports_reason(monkeypatch):
    _with_key(monkeypatch)

    def boom(*a, **k):
        raise requests.Timeout("slow")
    monkeypatch.setattr(anth.requests, "post", boom)
    _, _, err = anth._call_messages("hi", anth.MODEL_SONNET, 10)
    assert "timed out" in err


def test_empty_content_reports_stop_reason(monkeypatch):
    _with_key(monkeypatch)
    body = {"content": [], "stop_reason": "refusal", "usage": {}}
    monkeypatch.setattr(anth.requests, "post", lambda *a, **k: _resp(200, body))
    text, _, err = anth._call_messages("hi", anth.MODEL_SONNET, 10)
    assert text is None and "refusal" in err


def test_success_has_no_error(monkeypatch):
    _with_key(monkeypatch)
    body = {"content": [{"type": "text", "text": "## Overview\nok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 3}}
    monkeypatch.setattr(anth.requests, "post", lambda *a, **k: _resp(200, body))
    res = anth.equity_research_filing("ADBE", "Adobe", "10-Q", "2026-09-22", "x")
    assert res["text"].startswith("## Overview")
    assert res["error"] is None
    assert res["usage"]["output_tokens"] == 3


def test_long_read_timeout():
    # A full 10-K analysis on Sonnet routinely exceeds 60s.
    assert anth.TIMEOUT[1] >= 180


# ---------------------------------------------------------------------------
# View surfaces the real error (the screenshot showed "Unknown error").
# ---------------------------------------------------------------------------
def _failing_view_script():
    import sys
    sys.path.insert(0, "/home/user/parknova")
    import pandas as pd
    import streamlit as st
    import app
    from core import filing_cache

    filing = {"form": "10-Q", "filingDate": "2026-09-22",
              "accessionNumber": "0000796343-26-000099",
              "primaryDocument": "adbe-20260829.htm",
              "primaryDocDescription": "10-Q", "cik": 796343,
              "url": "https://www.sec.gov/x"}
    app.service.get_sec_filings = lambda t, limit=20: [filing]
    app.anth.has_anthropic_key = lambda: True
    filing_cache.load = lambda k, m: None
    app.service.equity_research_filing = lambda **kw: {
        "text": None, "error": "Anthropic API returned HTTP 401 "
                               "(authentication_error: invalid x-api-key)"}
    st.session_state["er_ticker"] = "ADBE"
    full = pd.DataFrame([{"Ticker": "ADBE", "Name": "Adobe Inc",
                          "Primary Bucket": "6 AI Software", "Crest": "Mid",
                          "Morningstar Rating for Stocks": 5,
                          "upside_pct": 0.699}])
    app.view_equity_research(full)


def test_view_shows_real_error_message():
    at = AppTest.from_function(_failing_view_script, default_timeout=60)
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    at.button(key="er_btn_0000796343-26-000099_" + anth.MODEL_HAIKU).click().run()
    errors = " ".join(e.value for e in at.error)
    assert "invalid x-api-key" in errors
    assert "Unknown error" not in errors


def test_secret_whitespace_and_quotes_stripped(monkeypatch):
    from core import config
    monkeypatch.setenv("PN_TEST_KEY", '  "sk-ant-abc123"\n')
    assert config.get_secret("PN_TEST_KEY") == "sk-ant-abc123"
    monkeypatch.setenv("PN_TEST_KEY", "   \n")
    assert config.get_secret("PN_TEST_KEY", "dflt") == "dflt"
