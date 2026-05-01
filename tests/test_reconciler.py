"""Reconciler trailer parser. The reconciler emits the cleaned draft
followed by `# RECONCILER POSITION TABLE` (JSON) and
`# RECONCILER CHANGE LOG` (markdown bullets); the stage handler peels
both off before the rest of the pipeline touches the prose."""

from __future__ import annotations

from api.agents.reconciler import split_output


def test_split_keeps_draft_and_extracts_json_table_and_log() -> None:
    text = """# OPENING
Some opening prose.

# REVISED SECTIONS
## Sec
**author:** A
**role:** Analyst

Body.

# CLOSING
Closing.

# RECONCILER POSITION TABLE
```json
[
  {"asset": "TSM", "direction": "long", "conviction": 4, "target_level": 240,
   "contributor_slug": "equity-analyst", "claim_text": "TSM long on substrate cycle"}
]
```

# RECONCILER CHANGE LOG
- TSM masthead: flipped from short to long; the body argues it as the trade.
- Stripped Stylist working note from page 4.
"""
    draft, table, log = split_output(text)
    assert "# OPENING" in draft and "# CLOSING" in draft
    assert "RECONCILER POSITION TABLE" not in draft
    assert "RECONCILER CHANGE LOG" not in draft
    assert len(table) == 1
    assert table[0]["asset"] == "TSM"
    assert table[0]["direction"] == "long"
    assert table[0]["conviction"] == 4
    assert table[0]["target_level"] == 240.0
    assert table[0]["contributor_slug"] == "equity-analyst"
    assert "TSM masthead" in log
    assert "Stripped Stylist" in log


def test_split_with_no_trailers_returns_full_draft() -> None:
    text = "# OPENING\nbody\n# CLOSING\nend\n"
    draft, table, log = split_output(text)
    assert draft.strip() == text.strip()
    assert table == []
    assert log == ""


def test_split_with_only_change_log_returns_empty_table() -> None:
    text = """# OPENING
x
# CLOSING
y

# RECONCILER CHANGE LOG
- one fix
"""
    draft, table, log = split_output(text)
    assert "# OPENING" in draft
    assert "RECONCILER CHANGE LOG" not in draft
    assert table == []
    assert "one fix" in log


def test_split_drops_invalid_table_rows() -> None:
    text = """# OPENING
x
# CLOSING
y

# RECONCILER POSITION TABLE
```json
[
  {"asset": "TSM", "direction": "long", "contributor_slug": "equity-analyst"},
  {"asset": "BAD", "direction": "hold", "contributor_slug": "x"},
  {"asset": "", "direction": "long", "contributor_slug": "y"},
  {"direction": "short", "contributor_slug": "y"},
  {"asset": "OK", "direction": "short"}
]
```

# RECONCILER CHANGE LOG
(none)
"""
    _, table, _ = split_output(text)
    assert len(table) == 1
    assert table[0]["asset"] == "TSM"


def test_split_handles_bare_json_without_fence() -> None:
    text = """# OPENING
x
# CLOSING
y

# RECONCILER POSITION TABLE

[{"asset": "AAPL", "direction": "long", "contributor_slug": "tech",
  "conviction": 3, "target_level": null, "claim_text": "long aapl"}]

# RECONCILER CHANGE LOG
- a fix
"""
    _, table, log = split_output(text)
    assert len(table) == 1
    assert table[0]["asset"] == "AAPL"
    assert table[0]["target_level"] is None
    assert "a fix" in log


def test_split_garbled_table_returns_empty_but_keeps_log() -> None:
    text = """# OPENING
x
# CLOSING
y

# RECONCILER POSITION TABLE
not json at all

# RECONCILER CHANGE LOG
- still logged
"""
    draft, table, log = split_output(text)
    assert "# OPENING" in draft
    assert table == []
    assert "still logged" in log
