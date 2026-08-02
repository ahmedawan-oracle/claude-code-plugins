#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from migrate_to_codex import validate_package


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a generated Codex Skill or plugin package."
    )
    parser.add_argument("package", help="Generated package root")
    parser.add_argument("--target", choices=("auto", "skill", "plugin"), default="auto")
    args = parser.parse_args()
    package = Path(args.package).expanduser().resolve()
    target = args.target
    if target == "auto":
        target = (
            "plugin"
            if (package / ".codex-plugin" / "plugin.json").exists()
            else "skill"
        )
    result = validate_package(package, target)  # type: ignore[arg-type]
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
