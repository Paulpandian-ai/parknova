---
name: thesis-drafter
description: >
  Draft or refine a ParkNova investment thesis from an exported evidence brief.
  Use when the user pastes a ParkNova "thesis brief" JSON (evidence_snapshot +
  optional existing_thesis) and wants a structured thesis back to import into the
  app's Thesis view. Zero marginal cost on a Claude Max plan — no paid API call.
---

# Thesis drafter

ParkNova's **Thesis** view exports a JSON brief (an `evidence_snapshot` —
factor percentiles, valuation/value-trap, crest/side, returns, filing net-reads —
plus any `existing_thesis`). Turn that evidence into a disciplined, falsifiable
thesis the user can import straight back.

## Input

A JSON object the user pastes, shaped like:

```json
{
  "task": "...",
  "ticker": "MU",
  "evidence_snapshot": { "factors": {...}, "valuation": {...},
                         "position": {"side": "...", "crest": "..."},
                         "returns": {...}, "filings": {...} },
  "existing_thesis": { ... } | null,
  "schema_hint": { ...the `current` schema... }
}
```

## Output — return ONLY this JSON object (the `current` schema)

```json
{
  "stance": "Watch | Tactical long | Core long | Trim/Exit | Avoid",
  "conviction": 1-10,
  "time_horizon": "e.g. 18-24 months",
  "position_size_pct": 0.0,
  "bull_case": "...",
  "bear_case": "...",
  "valuation_view": "your read vs Morningstar fair value, citing upside_fv / fwd P/E / tier",
  "catalysts": ["..."],
  "risks": ["..."],
  "crest_note": "where it sits in the capex wave (Early/Mid/Late) and the timing call",
  "defined_exit": "the concrete condition that would prove the thesis wrong"
}
```

## Rules

- Ground every claim in the `evidence_snapshot`; cite specific numbers
  (upside-to-FV, forward P/E, valuation tier, factor percentiles, 1Y/3Y returns).
- If `valuation.value_trap` is true, address it head-on in `bear_case` and make
  `defined_exit` concrete (a cheap multiple on an extended early-crest cyclical
  is a trap, not a bargain — say so).
- Tie `crest_note` to the capex-cycle sequence: Early (chips/memory/optics) →
  Mid (equipment/software/networking) → Late (power/cooling/grid). State whether
  the call is tactical (timing the cycle) or structural (durable earnings).
- Calibrate `conviction` to the weight of evidence; do not anchor on the
  existing value unless the evidence supports it.
- Set `position_size_pct` consistent with conviction and the existing thesis;
  keep it modest unless conviction is high.
- Do NOT give buy/sell advice or price targets beyond the fair-value read.
- Output **only** the JSON object — no prose around it — so it imports cleanly.
