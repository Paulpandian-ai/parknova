"""Unit tests for the PRISM tracing wired into data/anthropic_client.py.

Network is fully mocked: no Anthropic or PRISM requests are made.
"""

from __future__ import annotations

from unittest import mock

import pytest

from data import anthropic_client as anth
from data import prism_trace


class _Resp:
    def __init__(self, ok=True, status=200, body=None):
        self.ok = ok
        self.status_code = status
        self._body = body or {}
        self.text = "err"

    def json(self):
        return self._body


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("PRISMTRACE_API_KEY", "pt-sk-test")
    monkeypatch.setenv("PRISMTRACE_PROJECT_ID", "proj-1")
    monkeypatch.setenv("PRISMTRACE_HOST", "https://prism.example")


def _run(response, monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        return response if "anthropic" in url else _Resp()

    monkeypatch.setattr(anth.requests, "post", fake_post)
    monkeypatch.setattr(prism_trace.requests, "post", fake_post)
    # Run the trace POST inline instead of on a background thread.
    with mock.patch.object(prism_trace.threading, "Thread") as T:
        T.side_effect = lambda target, args, daemon: mock.Mock(
            start=lambda: target(*args))
        out = anth.analyze_filing("8-K", "2026-09-01", "nvda", "body")
    traces = [c for c in calls if c[0].endswith("/api/traces")]
    return out, traces


def test_success_emits_trace(env, monkeypatch):
    body = {"content": [{"type": "text", "text": "summary"}],
            "usage": {"input_tokens": 3, "output_tokens": 1}}
    out, traces = _run(_Resp(body=body), monkeypatch)
    assert out["text"] == "summary"
    assert len(traces) == 1
    url, kw = traces[0]
    assert url == "https://prism.example/api/traces"
    assert kw["headers"]["X-PRISMtrace-Key"] == "pt-sk-test"
    p = kw["json"]
    assert p["project_id"] == "proj-1"
    assert p["output_message"] == "summary"
    assert p["model"] == anth.DEFAULT_MODEL
    assert p["session_id"].startswith("parknova-filings-NVDA-")


def test_api_error_emits_error_trace(env, monkeypatch):
    out, traces = _run(_Resp(ok=False, status=529), monkeypatch)
    assert out["text"] is None
    assert traces[0][1]["json"]["output_message"].startswith("[error] HTTP 529")


def test_no_prism_key_is_noop(env, monkeypatch):
    monkeypatch.delenv("PRISMTRACE_API_KEY")
    body = {"content": [{"type": "text", "text": "x"}]}
    out, traces = _run(_Resp(body=body), monkeypatch)
    assert out["text"] == "x"
    assert traces == []


def test_trace_failure_never_raises(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(prism_trace.requests, "post", boom)
    prism_trace.emit("p", "o", model="m", latency_ms=1, blocking=True)
