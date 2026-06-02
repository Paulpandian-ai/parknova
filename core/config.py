"""Single source of truth for secrets / config values.

Each value is read with precedence: ``st.secrets`` first (Streamlit Community
Cloud's encrypted store), then ``os.environ`` (a local ``.env`` loaded by
python-dotenv during development). This keeps one code path that works both on
the hosted app and locally, with no hardcoded keys.

Secret *values* are never logged or printed here.
"""

from __future__ import annotations

import os
from typing import Optional


def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    """Return secret ``name`` from st.secrets, then os.environ, then ``default``.

    st.secrets is preferred so the hosted app uses its encrypted store; the
    os.environ fallback keeps local `.env` development working. Accessing
    st.secrets when no secrets file exists raises, so the lookup is guarded and
    degrades quietly to the environment.
    """
    try:
        import streamlit as st

        # Membership check avoids a KeyError on missing keys; the whole block is
        # guarded because st.secrets raises when no secrets file is present.
        if name in st.secrets:
            val = st.secrets[name]
            if val is not None and str(val) != "":
                return str(val)
    except Exception:
        pass

    val = os.environ.get(name)
    if val is not None and val != "":
        return val
    return default
