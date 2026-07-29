from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .common import read_json, write_json
from .models import MigrationResult
from .planner import build_plan
from .reporting import write_reports
from .source import inventory_source, stage_source
from .transform import build_package
from .validation import validate_package


@dataclass(slots=True)
class MigrationOptions:
    target: str = "auto"
    name: str | None = None
    strict: bool = False
    force: bool = False
    trust_runtime: bool = False
    stdin_text: str | None = None
    stdin_name: str = "SOURCE.md"


class MigrationError(RuntimeError):
    def __init__(self, message: str, result: MigrationResult | None = None):
        super().__init__(message)
        self.result = result


def _validate_output_path(output: Path) -> None:
    resolved = output.resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError("Refusing to use a filesystem root as migration output")


# Every migration run writes an ownership manifest listing all generated
# files; force-replacement recursively verifies that nothing unowned exists.
OUTPUT_MANIFEST = Path("reports") / "output-manifest.json"
JUNK_ENTRIES = {".DS_Store"}


def _is_replaceable_output(path: Path) -> bool:
    """Only previous migration output, an effectively empty directory, or a
    single file/symlink (no tree to destroy) may be force-replaced. Every file
    in the tree must be listed in the ownership manifest — unrelated files at
    any depth must survive."""
    if path.is_symlink() or path.is_file():
        return True
    entries = {child.name for child in path.iterdir()} - JUNK_ENTRIES
    if not entries:
        return True
    manifest_path = path / OUTPUT_MANIFEST
    if not manifest_path.is_file():
        return False
    try:
        owned = set(read_json(manifest_path).get("files", []))
    except ValueError:
        return False
    for candidate in path.rglob("*"):
        if candidate.name in JUNK_ENTRIES:
            continue
        if candidate.is_dir() and not candidate.is_symlink():
            continue
        if candidate.relative_to(path).as_posix() not in owned:
            return False
    return True


def _remove_output(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def migrate(
    source: str | Path, output: str | Path, options: MigrationOptions | None = None
) -> MigrationResult:
    selected = options or MigrationOptions()
    if selected.target not in {"auto", "skill", "plugin"}:
        raise ValueError("target must be auto, skill, or plugin")
    output_root = Path(output).expanduser().resolve()
    _validate_output_path(output_root)
    if str(source) != "-":
        source_path = Path(source).expanduser().resolve()
        # Any overlap loses data or contaminates staging: the same path would
        # replace the source with its own output, a nested output gets copied
        # back into its own inventory mid-flight, and an output above the
        # source would delete the source when replaced.
        if source_path.exists() and (
            output_root == source_path
            or source_path in output_root.parents
            or output_root in source_path.parents
        ):
            raise ValueError(
                f"Output overlaps the source path: {output_root}. "
                "Choose an output location outside the source tree."
            )
    output_root.parent.mkdir(parents=True, exist_ok=True)
    if output_root.exists():
        if not selected.force:
            raise FileExistsError(
                f"Output already exists: {output_root}. Use force=True to replace it."
            )
        if not _is_replaceable_output(output_root):
            raise ValueError(
                f"Refusing to replace {output_root}: it is not empty and does not "
                "contain previous migration output (reports/output-manifest.json). "
                "Remove it manually if replacement is intended."
            )

    workspace = Path(
        tempfile.mkdtemp(
            prefix="claude-to-codex-migrator-", dir=output_root.parent
        )
    )
    try:
        staged_root, source_kind, source_name = stage_source(
            source,
            workspace / "staging",
            stdin_text=selected.stdin_text,
            stdin_name=selected.stdin_name,
        )
        inventory = inventory_source(staged_root, source_kind, source_name)
        plan = build_plan(
            inventory,
            requested_target=selected.target,
            requested_name=selected.name,
            trust_runtime=selected.trust_runtime,
        )
        result_root = workspace / "result"
        package_root = result_root / "package" / plan.target_name
        reports_root = result_root / "reports"
        build_package(inventory, plan, package_root, reports_root)
        validation = validate_package(package_root, plan.decision.target, plan)
        strict_failed = selected.strict and (
            bool(plan.manual_items) or not validation.ok
        )
        write_reports(reports_root, inventory, plan, validation)
        owned_files = sorted(
            {
                candidate.relative_to(result_root).as_posix()
                for candidate in result_root.rglob("*")
                if candidate.is_file()
            }
            | {OUTPUT_MANIFEST.as_posix()}
        )
        write_json(result_root / OUTPUT_MANIFEST, {"files": owned_files})

        _remove_output(output_root)
        os.replace(result_root, output_root)
        final_package = output_root / "package" / plan.target_name
        final_reports = output_root / "reports"
        result = MigrationResult(
            output_root=output_root,
            package_root=final_package,
            reports_root=final_reports,
            plan=plan,
            validation=validation,
            strict_failed=strict_failed,
        )
        if strict_failed:
            raise MigrationError(
                "Strict migration failed because manual items or validation failures remain. Reports were preserved.",
                result,
            )
        return result
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
