#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from migrate_to_codex import inventory_source, stage_source


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage and recursively inventory a migration source."
    )
    parser.add_argument("source", help="Source folder, ZIP, file, or - for stdin")
    parser.add_argument("--stdin-name", default="SOURCE.md")
    args = parser.parse_args()
    stdin_text = sys.stdin.read() if args.source == "-" else None
    with tempfile.TemporaryDirectory(
        prefix="claude-to-codex-migrator-inventory-"
    ) as temporary:
        root, source_kind, source_name = stage_source(
            args.source,
            Path(temporary),
            stdin_text=stdin_text,
            stdin_name=args.stdin_name,
        )
        result = inventory_source(root, source_kind, source_name)
        print(json.dumps(result.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
