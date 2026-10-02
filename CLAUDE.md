# ParkNova: notes for coding agents

## PRISM tracing (do not remove)

This project sends traces to PRISM. Env vars: `PRISMTRACE_API_KEY`,
`PRISMTRACE_PROJECT_ID`, `PRISMTRACE_HOST`.

Tracing is currently wired at: `data/prism_trace.py` (HTTP ingest helper), `data/anthropic_client.py` (`_call_messages`, used by `generate_summary`, `analyze_filing`, `analyze_filing_activity`), `tests/test_prism_trace.py`, `.env.example`, `.streamlit/secrets.toml.example`

**Standing rule.** Whenever you add or change an agent, chain, graph, tool,
retriever, or any entry point that calls a model, wire it to PRISM before you
finish. Unwired code is invisible in the dashboard. If you are unsure whether
something is covered, assume it is not and wire it.
