from __future__ import annotations

import bz2
import gzip
import io
import json
import lzma
import os
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .common import (
    FORBIDDEN_PATTERNS,
    NAME_RE,
    SEMVER_RE,
    read_text_if_safe,
    split_frontmatter,
)
from .models import MigrationPlan, TargetKind, ValidationResult


MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
PLUGIN_ROOT_PATH_RE = re.compile(
    r"\$(?:\{PLUGIN_ROOT\}|PLUGIN_ROOT)/([A-Za-z0-9_./-]+)"
)


def _binary_term_patterns() -> tuple[re.Pattern[bytes], ...]:
    """Forbidden terms as raw bytes: ASCII plus UTF-16 LE/BE encodings, so
    binaries cannot smuggle source-platform strings past the cleanup scan."""
    patterns: list[re.Pattern[bytes]] = []
    for term in ("claude", "anthropic"):
        raw = term.encode("ascii")
        encodings = (
            raw,
            b"".join(bytes([byte]) + b"\x00" for byte in raw),
            b"".join(b"\x00" + bytes([byte]) for byte in raw),
        )
        patterns.extend(
            re.compile(re.escape(encoded), re.IGNORECASE) for encoded in encodings
        )
    return tuple(patterns)


BINARY_TERM_PATTERNS = _binary_term_patterns()
MAX_BINARY_SCAN_BYTES = 64 * 1024 * 1024
MAX_CONTAINER_DEPTH = 3
MAX_CONTAINER_MEMBERS = 512


def _stream_scan(handle: Any) -> tuple[bool, bool]:
    """Scan a byte stream. Returns (term_found, fully_scanned) — a stream that
    exceeds the scan budget is not fully verified."""
    overlap = 64
    previous = b""
    scanned = 0
    while True:
        chunk = handle.read(1024 * 1024)
        if not chunk:
            return False, True
        window = previous + chunk
        if any(pattern.search(window) for pattern in BINARY_TERM_PATTERNS):
            return True, True
        scanned += len(chunk)
        if scanned > MAX_BINARY_SCAN_BYTES:
            return False, False
        previous = window[-overlap:]


def _read_limited(handle: Any, budget: list[int]) -> tuple[bytes, bool]:
    """Read a stream against the shared decompression budget. Returns
    (payload, fully_read)."""
    chunks: list[bytes] = []
    while True:
        if budget[0] <= 0:
            return b"".join(chunks), False
        chunk = handle.read(1024 * 1024)
        if not chunk:
            return b"".join(chunks), True
        budget[0] -= len(chunk)
        chunks.append(chunk)


def _scan_bytes(data: bytes, depth: int, budget: list[int]) -> tuple[bool, bool]:
    """Recursively scan bytes, decompressing recognized containers
    (gzip/bzip2/xz/zip) with shared limits for depth, member count, and total
    decompressed size. Returns (term_found, fully_verified)."""
    if any(pattern.search(data) for pattern in BINARY_TERM_PATTERNS):
        return True, True
    is_gzip = data.startswith(b"\x1f\x8b")
    is_bz2 = data.startswith(b"BZh")
    is_xz = data.startswith(b"\xfd7zXZ\x00")
    # ZIP structure is detected independently of the leading bytes:
    # self-extracting archives carry an executable prefix (e.g. MZ) while
    # remaining fully readable by zipfile.
    is_zip = False
    if not (is_gzip or is_bz2 or is_xz):
        is_zip = zipfile.is_zipfile(io.BytesIO(data))
    if not (is_gzip or is_bz2 or is_xz or is_zip):
        return False, True
    if depth >= MAX_CONTAINER_DEPTH:
        return False, False
    try:
        if is_zip:
            complete = True
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = [info for info in archive.infolist() if not info.is_dir()]
                if len(members) > MAX_CONTAINER_MEMBERS:
                    return False, False
                for info in members:
                    with archive.open(info) as member:
                        payload, fully_read = _read_limited(member, budget)
                    found, sub_complete = _scan_bytes(payload, depth + 1, budget)
                    if found:
                        return True, True
                    complete = complete and fully_read and sub_complete
            return False, complete
        if is_gzip:
            stream: Any = gzip.GzipFile(fileobj=io.BytesIO(data))
        elif is_bz2:
            stream = bz2.BZ2File(io.BytesIO(data))
        else:
            stream = lzma.LZMAFile(io.BytesIO(data))
        with stream:
            payload, fully_read = _read_limited(stream, budget)
        found, sub_complete = _scan_bytes(payload, depth + 1, budget)
        if found:
            return True, True
        return False, fully_read and sub_complete
    except (OSError, EOFError, ValueError, RuntimeError, zipfile.BadZipFile):
        return False, False


def _scan_binary(path: Path) -> str | None:
    """Return a finding label for a binary file, or None when it is clean.
    Compressed containers are decompressed recursively so nesting cannot
    smuggle terms past cleanup; anything that cannot be fully verified is
    reported rather than silently trusted."""
    if path.stat().st_size > MAX_BINARY_SCAN_BYTES:
        with path.open("rb") as handle:
            found, _ = _stream_scan(handle)
        return "source-term-in-binary" if found else "unverifiable-binary-content"
    found, complete = _scan_bytes(path.read_bytes(), 0, [MAX_BINARY_SCAN_BYTES])
    if found:
        return "source-term-in-binary"
    if not complete:
        return "unverifiable-binary-content"
    return None


def scan_leftovers(package_root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in sorted(
        candidate for candidate in package_root.rglob("*") if candidate.is_file()
    ):
        relative = path.relative_to(package_root).as_posix()
        for label, pattern in FORBIDDEN_PATTERNS:
            if pattern.search(relative):
                findings.append(
                    {"path": relative, "kind": "filename", "pattern": label}
                )
        binary, text = read_text_if_safe(path)
        if binary or text is None:
            # Binary or oversized files still get a raw-byte scan (including
            # decompression of supported containers); they must not become a
            # channel that bypasses cleanup entirely.
            label = _scan_binary(path)
            if label:
                findings.append(
                    {"path": relative, "kind": "binary-content", "pattern": label}
                )
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in FORBIDDEN_PATTERNS:
                match = pattern.search(line)
                if match:
                    findings.append(
                        {
                            "path": relative,
                            "line": line_number,
                            "kind": "content",
                            "pattern": label,
                            "match": match.group(0),
                        }
                    )
    return findings


def _validate_markdown_links(
    skill_root: Path, boundary: Path, errors: list[str]
) -> None:
    root = boundary.resolve()
    for path in sorted(skill_root.rglob("*.md")):
        binary, text = read_text_if_safe(path)
        if binary or text is None:
            # Binary or oversized Markdown-named resources carry no parseable
            # links; the cleanup scanner still raw-scans their bytes.
            continue
        for raw in MARKDOWN_LINK_RE.findall(text):
            target = raw.strip().strip("<>").split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (path.parent / target).resolve()
            if resolved != root and root not in resolved.parents:
                errors.append(
                    f"Markdown link escapes the package root in {path.relative_to(skill_root)}: {raw}"
                )
            elif not resolved.exists():
                errors.append(
                    f"Broken Markdown link in {path.relative_to(skill_root)}: {raw}"
                )


def _validate_skill(
    skill_root: Path,
    errors: list[str],
    warnings: list[str],
    package_root: Path | None = None,
) -> None:
    skill_file = skill_root / "SKILL.md"
    if not skill_file.is_file():
        errors.append(f"Missing SKILL.md in {skill_root}")
        return
    metadata, body = split_frontmatter(skill_file.read_text(encoding="utf-8"))
    unknown = sorted(set(metadata) - {"name", "description"})
    if unknown:
        errors.append(
            f"SKILL.md contains unsupported frontmatter fields in {skill_root.name}: {', '.join(unknown)}"
        )
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        errors.append(f"Skill name must be lowercase hyphen-case in {skill_file}")
    elif name != skill_root.name:
        errors.append(
            f"Skill folder {skill_root.name} must match frontmatter name {name}"
        )
    if not isinstance(description, str) or not description.strip():
        errors.append(f"Skill description must be non-empty in {skill_file}")
    elif re.search(r"[<>]", description):
        # Mirror the official validator: angle brackets are rejected there.
        errors.append(
            f"Skill description must not contain angle brackets in {skill_file}"
        )
    if not body.strip():
        errors.append(f"Skill body must be non-empty in {skill_file}")
    openai_yaml = skill_root / "agents" / "openai.yaml"
    if not openai_yaml.is_file():
        warnings.append(f"Recommended agents/openai.yaml is missing in {skill_root}")
    elif isinstance(name, str) and f"${name}" not in openai_yaml.read_text(
        encoding="utf-8"
    ):
        errors.append(
            f"agents/openai.yaml default_prompt must mention ${name} in {skill_root}"
        )
    # A plugin-bundled Skill may reference shared plugin resources (scripts,
    # assets) that live outside its own folder but inside the plugin package.
    _validate_markdown_links(skill_root, package_root or skill_root, errors)


def _validate_mcp(plugin_root: Path, errors: list[str]) -> None:
    mcp_path = plugin_root / ".mcp.json"
    if not mcp_path.exists():
        return
    try:
        payload = json.loads(mcp_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        errors.append(f"Invalid .mcp.json: {error}")
        return
    if not isinstance(payload, dict) or not isinstance(payload.get("mcpServers"), dict):
        errors.append(".mcp.json must contain an mcpServers object")
        return
    known_suffixes = {".js", ".mjs", ".cjs", ".py", ".sh", ".ts", ".rb", ".php", ".jar"}
    for server_name, config in payload["mcpServers"].items():
        if not isinstance(config, dict):
            errors.append(f"MCP server {server_name} must be an object")
            continue
        command = config.get("command")
        if isinstance(command, str) and command.startswith(("./", "../")):
            candidate = (plugin_root / command).resolve()
            if plugin_root.resolve() not in candidate.parents or not candidate.exists():
                errors.append(
                    f"MCP server {server_name} command does not resolve inside the package: {command}"
                )
        args = config.get("args", [])
        if isinstance(args, list):
            for argument in args:
                if (
                    not isinstance(argument, str)
                    or argument.startswith("-")
                    or "://" in argument
                ):
                    continue
                candidate_path = PurePosixPath(argument)
                if candidate_path.suffix.lower() in known_suffixes or "/" in argument:
                    candidate = (plugin_root / Path(*candidate_path.parts)).resolve()
                    if (
                        plugin_root.resolve() not in candidate.parents
                        or not candidate.exists()
                    ):
                        errors.append(
                            f"MCP server {server_name} references a missing local dependency: {argument}"
                        )


def _validate_app(plugin_root: Path, errors: list[str]) -> None:
    app_path = plugin_root / ".app.json"
    if not app_path.exists():
        return
    try:
        payload = json.loads(app_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        errors.append(f"Invalid .app.json: {error}")
        return
    # An empty apps mapping is accepted by the official validator; only a
    # missing or non-object apps field is an error.
    if not isinstance(payload, dict) or not isinstance(payload.get("apps"), dict):
        errors.append(".app.json must contain an apps object")
        return
    unsupported_root = sorted(set(payload) - {"apps"})
    if unsupported_root:
        errors.append(
            ".app.json contains unsupported root fields: "
            f"{', '.join(unsupported_root)}"
        )
    allowed_fields = {"id", "category"}
    for app_key, config in payload["apps"].items():
        if not isinstance(app_key, str) or not app_key.strip():
            errors.append(".app.json app keys must be non-empty strings")
        if not isinstance(config, dict):
            errors.append(f".app.json app {app_key!r} must map to an object")
            continue
        connector_id = config.get("id")
        if not isinstance(connector_id, str) or not connector_id.strip():
            errors.append(
                f".app.json app {app_key!r} must declare a non-empty id"
            )
        category = config.get("category")
        if category is not None and (
            not isinstance(category, str) or not category.strip()
        ):
            errors.append(
                f".app.json app {app_key!r} category must be a non-empty string"
            )
        unsupported = sorted(set(config) - allowed_fields)
        if unsupported:
            errors.append(
                f".app.json app {app_key!r} contains unsupported fields: "
                f"{', '.join(unsupported)}"
            )


def _validate_plugin(plugin_root: Path, errors: list[str], warnings: list[str]) -> None:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    if not manifest_path.is_file():
        errors.append("Missing .codex-plugin/plugin.json")
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        errors.append(f"Invalid plugin.json: {error}")
        return
    if not isinstance(manifest, dict):
        errors.append("plugin.json must contain an object")
        return
    for field in ("name", "version", "description"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            errors.append(f"plugin.json field {field} must be a non-empty string")
    name = manifest.get("name")
    if isinstance(name, str):
        if not NAME_RE.fullmatch(name):
            errors.append("plugin.json name must be lowercase hyphen-case")
        if name != plugin_root.name:
            errors.append(
                f"Plugin folder {plugin_root.name} must match manifest name {name}"
            )
    version = manifest.get("version")
    if isinstance(version, str) and not SEMVER_RE.fullmatch(version):
        errors.append("plugin.json version must be strict semver")
    author = manifest.get("author")
    if (
        not isinstance(author, dict)
        or not isinstance(author.get("name"), str)
        or not author["name"].strip()
    ):
        errors.append("plugin.json author.name must be a non-empty string")
    interface = manifest.get("interface")
    required_interface = {
        "displayName": str,
        "shortDescription": str,
        "longDescription": str,
        "developerName": str,
        "category": str,
        "capabilities": list,
        "defaultPrompt": list,
    }
    if not isinstance(interface, dict):
        errors.append("plugin.json interface must be an object")
    else:
        for field, expected in required_interface.items():
            value = interface.get(field)
            if not isinstance(value, expected) or (
                hasattr(value, "__len__") and len(value) == 0
            ):
                errors.append(
                    f"plugin.json interface.{field} has an invalid or empty value"
                )
    for field in ("skills", "mcpServers", "apps"):
        value = manifest.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.startswith("./"):
            errors.append(f"plugin.json field {field} must be a ./-prefixed path")
            continue
        if not (plugin_root / value[2:]).exists():
            errors.append(
                f"plugin.json field {field} points to a missing path: {value}"
            )
    skills_root = plugin_root / "skills"
    if skills_root.exists():
        for child in sorted(path for path in skills_root.iterdir() if path.is_dir()):
            _validate_skill(child, errors, warnings, package_root=plugin_root)
    hooks = plugin_root / "hooks" / "hooks.json"
    if hooks.exists():
        try:
            payload = json.loads(hooks.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(
                payload.get("hooks"), dict
            ):
                errors.append("hooks/hooks.json must contain a hooks object")
            else:

                def inspect_hook_value(value: Any) -> None:
                    if isinstance(value, dict):
                        for key, nested in value.items():
                            if key == "command" and isinstance(nested, str):
                                for match in PLUGIN_ROOT_PATH_RE.finditer(nested):
                                    dependency = (
                                        plugin_root / match.group(1)
                                    ).resolve()
                                    if (
                                        plugin_root.resolve() not in dependency.parents
                                        or not dependency.exists()
                                    ):
                                        errors.append(
                                            f"Hook command references a missing package dependency: {match.group(1)}"
                                        )
                                    elif not os.access(dependency, os.X_OK):
                                        errors.append(
                                            f"Hook command dependency is not executable: {match.group(1)}"
                                        )
                            inspect_hook_value(nested)
                    elif isinstance(value, list):
                        for nested in value:
                            inspect_hook_value(nested)

                inspect_hook_value(payload)
        except json.JSONDecodeError as error:
            errors.append(f"Invalid hooks/hooks.json: {error}")
    _validate_mcp(plugin_root, errors)
    _validate_app(plugin_root, errors)


def validate_package(
    package_root: Path, target: TargetKind, plan: MigrationPlan | None = None
) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    if target == "skill":
        _validate_skill(package_root, errors, warnings)
    else:
        _validate_plugin(package_root, errors, warnings)
    if plan is not None:
        for item in plan.items:
            if item.operation in {
                "keep-as-is",
                "rename",
                "rewrite",
                "split-into-reference",
            }:
                if not item.target_path:
                    errors.append(f"Plan item lacks a target path: {item.source_path}")
                elif not (
                    package_root / Path(*PurePosixPath(item.target_path).parts)
                ).exists():
                    errors.append(
                        f"Planned target was not generated: {item.target_path} from {item.source_path}"
                    )
    cleanup = scan_leftovers(package_root)
    return ValidationResult(
        target=target,
        errors=sorted(set(errors)),
        warnings=sorted(set(warnings)),
        cleanup_findings=cleanup,
    )
