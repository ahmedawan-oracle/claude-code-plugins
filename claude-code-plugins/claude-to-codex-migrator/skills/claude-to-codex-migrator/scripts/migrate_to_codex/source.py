from __future__ import annotations

import json
import os
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from .common import (
    read_text_if_safe,
    scan_text_forbidden,
    sha256_file,
    validate_relative_path,
)
from .models import Inventory, SourceFile


EXCLUDED_DIRECTORIES = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
}
MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_TOTAL_BYTES = 512 * 1024 * 1024


def _safe_destination(root: Path, relative: PurePosixPath) -> Path:
    destination = (root / Path(*relative.parts)).resolve()
    root_resolved = root.resolve()
    if destination != root_resolved and root_resolved not in destination.parents:
        raise ValueError(f"Path escapes staging root: {relative}")
    return destination


def _copy_source_tree(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for current, directories, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(
            name for name in directories if name not in EXCLUDED_DIRECTORIES
        )
        for directory in directories:
            candidate = current_path / directory
            if candidate.is_symlink():
                raise ValueError(f"Symlinked directories are not accepted: {candidate}")
        for filename in sorted(files):
            candidate = current_path / filename
            if candidate.is_symlink():
                raise ValueError(f"Symlinked files are not accepted: {candidate}")
            if not stat.S_ISREG(candidate.lstat().st_mode):
                # FIFOs, sockets, and device nodes block or misbehave on open.
                raise ValueError(
                    f"Only regular files are accepted: {candidate}"
                )
            relative = candidate.relative_to(source)
            target = _safe_destination(destination, PurePosixPath(relative.as_posix()))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)


def _extract_zip(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        remaining = MAX_ZIP_TOTAL_BYTES
        extracted = 0
        for info in archive.infolist():
            if info.flag_bits & 0x1:
                raise ValueError(
                    f"Encrypted ZIP entries are not accepted: {info.filename}"
                )
            relative = (
                validate_relative_path(info.filename.rstrip("/"))
                if info.filename.rstrip("/")
                else None
            )
            if relative is None:
                continue
            # Excluded directories never reach the inventory, so they must not
            # count against the caps either — a zipped source with a vendored
            # node_modules behaves the same as the unzipped folder.
            if any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ValueError(
                    f"Symlink ZIP entries are not accepted: {info.filename}"
                )
            # Directory entries count against the cap too: each one creates a
            # path on disk, so a directory-only archive can exhaust inodes just
            # as well as a file-only one.
            extracted += 1
            if extracted > MAX_ZIP_ENTRIES:
                raise ValueError(
                    f"ZIP contains more than {MAX_ZIP_ENTRIES} entries: {source}"
                )
            target = _safe_destination(destination, relative)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with (
                archive.open(info) as source_handle,
                target.open("wb") as target_handle,
            ):
                # Count actual decompressed bytes: declared sizes can lie.
                while True:
                    chunk = source_handle.read(1024 * 1024)
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ValueError(
                            "ZIP contents exceed the maximum accepted total size "
                            f"of {MAX_ZIP_TOTAL_BYTES} bytes: {source}"
                        )
                    target_handle.write(chunk)


def _stage_stdin_bundle(value: str, destination: Path, stdin_name: str) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("files"), dict):
        for raw_path, content in payload["files"].items():
            if not isinstance(raw_path, str) or not isinstance(content, str):
                raise ValueError(
                    "stdin bundle files must map string paths to string contents"
                )
            relative = validate_relative_path(raw_path)
            target = _safe_destination(destination, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return
    relative = validate_relative_path(stdin_name)
    target = _safe_destination(destination, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8")


def _resolve_wrapped_root(root: Path) -> Path:
    markers = [root / ".claude-plugin" / "plugin.json", root / "SKILL.md"]
    if any(marker.exists() for marker in markers):
        return root
    children = sorted(path for path in root.iterdir() if path.is_dir())
    files = [path for path in root.iterdir() if path.is_file()]
    if len(children) == 1 and not files:
        child = children[0]
        child_markers = [child / ".claude-plugin" / "plugin.json", child / "SKILL.md"]
        if any(marker.exists() for marker in child_markers):
            return child
    return root


def stage_source(
    source: str | Path,
    staging_parent: Path,
    *,
    stdin_text: str | None = None,
    stdin_name: str = "SOURCE.md",
) -> tuple[Path, str, str | None]:
    """Stage the source and return (root, kind, source_name).

    source_name carries the original folder/zip/file identity as explicit data
    — it survives _resolve_wrapped_root descending into a wrapper directory,
    which would otherwise swap the identity for the wrapper's name.
    """
    if str(source) == "-":
        if stdin_text is None:
            raise ValueError("stdin input requires stdin_text")
        destination = staging_parent / "source"
        _stage_stdin_bundle(stdin_text, destination, stdin_name)
        return _resolve_wrapped_root(destination), "stdin", None

    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Source does not exist: {source_path}")
    destination = staging_parent / "source"
    if source_path.is_dir():
        _copy_source_tree(source_path, destination)
        return _resolve_wrapped_root(destination), "folder", source_path.name or None
    if not stat.S_ISREG(source_path.stat().st_mode):
        # Must precede is_zipfile(), which opens and reads the path — a FIFO
        # would block there before any copy.
        raise ValueError(f"Only regular files are accepted: {source_path}")
    if zipfile.is_zipfile(source_path):
        _extract_zip(source_path, destination)
        return _resolve_wrapped_root(destination), "zip", source_path.stem or None
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination / source_path.name)
    return destination, "file", source_path.stem or None


def classify_source_path(relative_path: str) -> str:
    path = PurePosixPath(relative_path)
    parts = path.parts
    lower = relative_path.lower()
    name = path.name.lower()
    if lower == ".claude-plugin/plugin.json":
        return "source-manifest"
    if lower == ".claude-plugin/marketplace.json":
        return "source-marketplace"
    if lower == "skill.md":
        return "skill-entrypoint"
    if len(parts) >= 3 and parts[0].lower() == "skills" and name == "skill.md":
        return "skill-entrypoint"
    if parts and parts[0].lower() == "skills":
        return "skill-resource"
    if parts and parts[0].lower() == "commands":
        return "command" if name.endswith(".md") else "command-resource"
    if parts and parts[0].lower() == "agents":
        return "agent" if name.endswith(".md") else "agent-resource"
    if parts and parts[0].lower() == "hooks":
        return "hook"
    if lower == ".mcp.json":
        return "mcp"
    if lower == ".app.json":
        return "app"
    if lower == ".lsp.json":
        return "lsp"
    if lower == "settings.json":
        return "settings"
    if parts and parts[0].lower() == "scripts":
        return "script"
    if parts and parts[0].lower() == "assets":
        return "asset"
    if parts and parts[0].lower() in {"references", "reference"}:
        return "reference"
    if parts and parts[0].lower() in {"docs", "documentation"}:
        return "documentation"
    if parts and parts[0].lower() in {"test", "tests", "fixtures"}:
        return "test"
    if name in {"readme.md", "readme.txt"}:
        return "documentation"
    if name in {
        "package.json",
        "package-lock.json",
        "pyproject.toml",
        "requirements.txt",
        "tsconfig.json",
    }:
        return "runtime-config"
    if parts and parts[0].lower() in {"src", "lib", "bin"}:
        return "runtime-source"
    if parts and parts[0].lower() == ".github":
        # Workflows and composite actions execute automatically on push/PR;
        # they are runtime, not inert metadata.
        if len(parts) >= 2 and parts[1].lower() in {"workflows", "actions"}:
            return "ci-workflow"
        return "repository-metadata"
    if name in {
        ".gitignore",
        ".gitattributes",
        ".gitmodules",
        ".editorconfig",
        ".mailmap",
        "codeowners",
    }:
        return "repository-metadata"
    return "unknown"


def inventory_source(
    root: Path, source_kind: str = "folder", source_name: str | None = None
) -> Inventory:
    files: list[SourceFile] = []
    warnings: list[str] = []
    for path in sorted(
        candidate for candidate in root.rglob("*") if candidate.is_file()
    ):
        if any(part in EXCLUDED_DIRECTORIES for part in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Symlinked files are not accepted: {path}")
        relative = path.relative_to(root).as_posix()
        binary, text = read_text_if_safe(path)
        matches = scan_text_forbidden(relative)
        if text is not None:
            matches.extend(scan_text_forbidden(text))
        if binary and path.stat().st_size > 4 * 1024 * 1024:
            warnings.append(
                f"Large or binary file requires path-only cleanup validation: {relative}"
            )
        files.append(
            SourceFile(
                path=relative,
                absolute_path=path,
                kind=classify_source_path(relative),
                size=path.stat().st_size,
                sha256=sha256_file(path),
                binary=binary,
                text=text,
                source_matches=sorted(set(matches)),
            )
        )
    if not files:
        warnings.append("The staged source contains no files.")
    return Inventory(
        root=root,
        source_kind=source_kind,
        files=files,
        warnings=warnings,
        source_name=source_name,
    )
