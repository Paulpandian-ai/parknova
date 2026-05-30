"""On-disk cache for LLM filing analyses.

A filing's content never changes, so once analyzed it is cached forever. We store
one JSON file per ``(accession_number, model)`` under ``.cache/filings/`` so the
cache survives app restarts and a filing is analyzed at most once per model.

This is deliberately a plain-filesystem cache (not ``st.cache_data``) so the UI
can cheaply check *whether* an analysis exists — without triggering an API call —
in order to show cached results instantly and hide the spinner/button.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, Optional

CACHE_DIR = os.path.join(os.getcwd(), ".cache", "filings")


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s))


def _key_path(accession_number: str, model: str) -> str:
    # Short model hash keeps filenames tidy while staying model-specific.
    mh = hashlib.sha1(model.encode("utf-8")).hexdigest()[:8]
    return os.path.join(CACHE_DIR, f"{_safe(accession_number)}__{mh}.json")


def load(accession_number: str, model: str) -> Optional[Dict[str, Any]]:
    """Return a cached analysis dict, or None if not present / unreadable."""
    path = _key_path(accession_number, model)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def exists(accession_number: str, model: str) -> bool:
    return os.path.exists(_key_path(accession_number, model))


def save(accession_number: str, model: str, payload: Dict[str, Any]) -> None:
    """Persist an analysis dict. Best-effort; failures are swallowed."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = _key_path(accession_number, model)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except OSError:
        pass
