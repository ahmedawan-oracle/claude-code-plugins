#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from migrate_to_codex import (
    default_source_plugins_root,
    discover_installed,
    resolve_installed,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover locally installed source packages."
    )
    parser.add_argument("--root", help="Override the source plugins root")
    parser.add_argument(
        "--resolve",
        metavar="PLUGIN_ID",
        help="Resolve one installed package to its source path",
    )
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve() if args.root else None
    if args.resolve:
        print(resolve_installed(args.resolve, root))
        return 0
    selected_root = root or default_source_plugins_root()
    print(
        json.dumps(
            {
                "root": str(selected_root),
                "discovered": discover_installed(root),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
