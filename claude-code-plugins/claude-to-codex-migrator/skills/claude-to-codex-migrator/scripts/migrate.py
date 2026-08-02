#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from migrate_to_codex import MigrationError, MigrationOptions, migrate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate a source assistant package into a clean Codex Skill or plugin."
    )
    parser.add_argument("source", help="Source folder, ZIP, file, or - for stdin")
    parser.add_argument(
        "--out", required=True, help="Output root containing package/ and reports/"
    )
    parser.add_argument("--target", choices=("auto", "skill", "plugin"), default="auto")
    parser.add_argument("--name", help="Override the normalized target package name")
    parser.add_argument(
        "--stdin-name", default="SOURCE.md", help="Filename for non-JSON stdin content"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail when manual items or validation errors remain",
    )
    parser.add_argument(
        "--force", action="store_true", help="Replace an existing output root"
    )
    parser.add_argument(
        "--trust-runtime",
        action="store_true",
        help=(
            "Place hooks, MCP, and app configuration at their active discovery "
            "paths. Use only after reviewing every command and executable; "
            "without this flag they are quarantined as manual items."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stdin_text = sys.stdin.read() if args.source == "-" else None
    options = MigrationOptions(
        target=args.target,
        name=args.name,
        strict=args.strict,
        force=args.force,
        trust_runtime=args.trust_runtime,
        stdin_text=stdin_text,
        stdin_name=args.stdin_name,
    )
    try:
        result = migrate(args.source, Path(args.out), options)
    except MigrationError as error:
        if error.result is not None:
            print(json.dumps(error.result.to_dict(), indent=2))
        print(str(error), file=sys.stderr)
        return 2
    except (FileExistsError, FileNotFoundError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
