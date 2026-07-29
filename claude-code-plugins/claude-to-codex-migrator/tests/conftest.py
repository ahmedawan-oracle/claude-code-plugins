"""Pytest collection config for the migrator test suite.

`tests/fixtures/` holds sample *source projects* that the migrator ingests as
INPUT data — including files named `test_*.py` (e.g.
`fixtures/integration-source/tests/test_server.py`). Those are fixture payloads,
not tests of the migrator, and they assert on their own runtime environment
(e.g. a `CLAUDE_PLUGIN_ROOT` env var) that is absent here — so a plain
`pytest tests/` would collect and fail them. Exclude the fixtures tree from
collection; the migrator's real suite is `tests/test_migrator.py`.
"""

collect_ignore = ["fixtures"]
