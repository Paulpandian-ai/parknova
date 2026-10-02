"""Optional PRISM tracing for the Anthropic calls in ``anthropic_client``.

Strictly opt-in: a no-op unless ``PRISMTRACE_API_KEY`` is set. Uses PRISM's HTTP
ingest via ``requests`` (no SDK dependency, matching ``anthropic_client``). The
POST runs on a daemon thread with a short timeout so tracing can never slow down
or break the UI; every failure is swallowed.
"""

from __future__ import annotations

import threading
from datetime import date
from typing import Dict, Optional

import requests

from core.config import get_secret

DEFAULT_HOST = "https://prism-api-prod.up.railway.app"
TIMEOUT = 5
# Filing prompts can be ~60k chars; cap what we ship to the trace store.
MAX_INPUT_CHARS = 20000


def is_enabled() -> bool:
    return bool(get_secret("PRISMTRACE_API_KEY")
                and get_secret("PRISMTRACE_PROJECT_ID"))


def session_id_for(kind: str, ticker: str) -> str:
    """One trajectory per (feature, ticker, day): groups a ticker's per-filing
    analyses with its activity synthesis."""
    return f"parknova-{kind}-{(ticker or 'na').upper()}-{date.today():%Y%m%d}"


def _post(payload: Dict, host: str, key: str) -> None:
    try:
        requests.post(f"{host.rstrip('/')}/api/traces", json=payload,
                      headers={"X-PRISMtrace-Key": key}, timeout=TIMEOUT)
    except Exception:
        pass


def emit(prompt: str, output: Optional[str], *, model: str, latency_ms: int,
         session_id: Optional[str] = None, error: Optional[str] = None,
         blocking: bool = False) -> None:
    """Send one LLM-call trace to PRISM. Never raises."""
    try:
        key = get_secret("PRISMTRACE_API_KEY")
        project_id = get_secret("PRISMTRACE_PROJECT_ID")
        if not key or not project_id:
            return
        host = get_secret("PRISMTRACE_HOST", DEFAULT_HOST) or DEFAULT_HOST
        content = prompt or ""
        if len(content) > MAX_INPUT_CHARS:
            content = content[:MAX_INPUT_CHARS] + "\n…[truncated for trace]"
        out = output or ""
        if error:
            out = f"[error] {error}" + (f"\n{out}" if out else "")
        payload = {
            "project_id": project_id,
            "model": model,
            "input_messages": [{"role": "user", "content": content}],
            "output_message": out,
            "latency_ms": int(latency_ms),
            "session_id": session_id,
        }
        if blocking:
            _post(payload, host, key)
        else:
            threading.Thread(target=_post, args=(payload, host, key),
                             daemon=True).start()
    except Exception:
        pass
