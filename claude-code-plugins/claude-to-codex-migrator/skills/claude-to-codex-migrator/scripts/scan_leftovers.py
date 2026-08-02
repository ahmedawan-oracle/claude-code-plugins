#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from migrate_to_codex import scan_leftovers


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan a generated package for source-platform leftovers."
    )
    parser.add_argument("package", help="Generated Skill or plugin package root")
    args = parser.parse_args()
    findings = scan_leftovers(Path(args.package).expanduser().resolve())
    print(
        json.dumps(
            {
                "passed": not findings,
                "findingCount": len(findings),
                "findings": findings,
            },
            indent=2,
        )
    )
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
