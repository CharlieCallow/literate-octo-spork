# Historical failure markers — reference for section_audit

Reference list of the markers the section_audit gate was designed to catch,
with quoted phrases from the four shipped reports that motivated the ticket.
This file is reference material for future maintenance, NOT a runtime fixture
— the unit tests use the synthetic per-marker files in this directory.

Expected pass rate of the gate against the four reports: **0/4** (every one
of them shipped with at least one of these markers in body prose).

## Markers

- `agent halted` — agent loop hit max_iters mid-synthesis, partial draft
  shipped with bracketed note left in.
- `data pull failed` — analyst self-narrated an upstream tool failure inside
  the section instead of either retrying or omitting the claim.
- `notes failed to load` — research notes file missing or empty at draft
  time; analyst drafted from memory and admitted to it.
- `section pulled` — EIC handwave folded into REVISED SECTIONS instead of
  removing the section header from the TOC.
- `the equity view will arrive` — analyst deferred their own work to a
  later report rather than scoping down the current one.
- `we did not answer` — analyst admitted to skipping a brief question.
- `shipping that gap` — same failure mode, different phrasing.
- `retry next cycle` — analyst left a TODO in body prose.
- `we are not going to manufacture` — analyst refusal to fabricate, but
  surfaced in the report rather than flagged to the EIC.

## Why synthetic fixtures, not real-report fixtures

The audit gate's job is pattern matching against failure markers. Synthetic
fixtures isolate each marker in a controlled file and let the test assert
which marker triggered. Real reports drag in correlated noise — multiple
markers, formatting variations, surrounding context — that makes failures
harder to localise when the test breaks. If we ever need real-report
fixtures, they go in `tests/fixtures/historical/` as text extractions.
