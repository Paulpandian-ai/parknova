"""On-disk cache for filing analyses (imported + paid-API).

Two stores live under ``.cache/filings/``:

* ``imported/`` — JSON objects produced by the ``sec-filing-analyzer`` skill
  (analyzed inside Claude on the user's Max plan, zero marginal cost). One file
  per filing, named ``{accession_nodashes}.json``. This is the **primary** path.
* the flat ``.cache/filings/`` dir — results of the optional paid Anthropic API
  path, one file per ``(accession_number, model)``.

A filing's content never changes, so anything cached here is valid forever and
survives restarts. This is a plain-filesystem cache (not ``st.cache_data``) so the
UI can cheaply check *whether* an analysis exists without triggering any API call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("parknova.filing_cache")

CACHE_DIR = os.path.join(os.getcwd(), ".cache", "filings")
IMPORTED_DIR = os.path.join(CACHE_DIR, "imported")


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s))


def normalize_accession(accn: object) -> str:
    """Canonical accession key: dashes (and stray chars) removed."""
    return re.sub(r"[^A-Za-z0-9]", "", str(accn or ""))


# ---------------------------------------------------------------------------
# Paid-API cache (keyed by accession + model)
# ---------------------------------------------------------------------------
def _key_path(accession_number: str, model: str) -> str:
    # Short model hash keeps filenames tidy while staying model-specific.
    mh = hashlib.sha1(model.encode("utf-8")).hexdigest()[:8]
    return os.path.join(CACHE_DIR, f"{_safe(accession_number)}__{mh}.json")


def load(accession_number: str, model: str) -> Optional[Dict[str, Any]]:
    """Return a cached (paid-API) analysis dict, or None if absent/unreadable."""
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
    """Persist a (paid-API) analysis dict. Best-effort; failures swallowed."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = _key_path(accession_number, model)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Imported analyses (primary path — produced by the sec-filing-analyzer skill)
# ---------------------------------------------------------------------------
def validate_imported(obj: Any) -> Tuple[bool, str]:
    """Validate a skill-produced analysis object against the expected schema.

    Returns ``(ok, message)``. Lenient on optional fields but requires an
    ``accession`` and an ``analysis`` object with the core narrative keys.
    """
    if not isinstance(obj, dict):
        return False, "Top-level JSON is not an object."
    if not obj.get("accession"):
        return False, "Missing 'accession'."
    analysis = obj.get("analysis")
    if not isinstance(analysis, dict):
        return False, "Missing or invalid 'analysis' object."
    if not analysis.get("what_and_why") and not analysis.get("net_read"):
        return False, "Analysis has neither 'what_and_why' nor 'net_read'."
    return True, "ok"


def save_imported(obj: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate + write an imported analysis under its normalized accession.

    Returns ``(ok, message)``; never raises.
    """
    ok, msg = validate_imported(obj)
    if not ok:
        return False, msg
    key = normalize_accession(obj.get("accession"))
    if not key:
        return False, "Empty accession after normalization."
    try:
        os.makedirs(IMPORTED_DIR, exist_ok=True)
        with open(os.path.join(IMPORTED_DIR, f"{key}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(obj, fh)
        return True, key
    except OSError as exc:
        return False, f"Could not write file: {exc}"


def load_imported_index() -> Dict[str, Dict[str, Any]]:
    """Scan the imported folder and return ``{accession_nodashes: obj}``.

    Malformed / schema-invalid files are skipped and logged, never fatal.
    """
    index: Dict[str, Dict[str, Any]] = {}
    if not os.path.isdir(IMPORTED_DIR):
        return index
    for name in os.listdir(IMPORTED_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(IMPORTED_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable imported file %s: %s", name, exc)
            continue
        ok, msg = validate_imported(obj)
        if not ok:
            logger.warning("Skipping invalid imported file %s: %s", name, msg)
            continue
        index[normalize_accession(obj.get("accession"))] = obj
    return index

