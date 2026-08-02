from __future__ import annotations

import json
import posixpath
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from .common import (
    SEMVER_RE,
    clean_description,
    humanize,
    normalize_name,
    render_openai_yaml,
    render_skill,
    rewrite_runtime_terms,
    rewrite_source_terms,
    split_frontmatter,
    write_json,
    write_text,
)
from .models import Inventory, MigrationPlan, PlanItem, SourceFile


# Only documentation gets prose rewriting; executable and runtime content
# (by classified kind, e.g. requirements.txt is runtime-config despite its
# suffix) receives mechanical rewrites exclusively.
MECHANICAL_KINDS = {
    "script",
    "hook",
    "mcp",
    "app",
    "runtime-config",
    "runtime-source",
    "test",
    "repository-metadata",
    "ci-workflow",
}
PROSE_SUFFIXES = {".md", ".markdown", ".txt"}


def _source_map(inventory: Inventory) -> dict[str, SourceFile]:
    return {item.path: item for item in inventory.files}


MARKDOWN_LINK_RE = re.compile(r"(!?\[[^\]]*\]\()([^)]+)(\))")


def _rewrite_internal_links(
    value: str,
    source_path: str,
    target_path: str,
    link_map: dict[str, str],
) -> str:
    def replace(match: re.Match[str]) -> str:
        raw = match.group(2).strip().strip("<>")
        if not raw or raw.startswith(("#", "http://", "https://", "mailto:")):
            return match.group(0)
        path_value, separator, anchor = raw.partition("#")
        resolved_source = posixpath.normpath(
            posixpath.join(posixpath.dirname(source_path), path_value)
        )
        mapped = link_map.get(resolved_source)
        if not mapped:
            return match.group(0)
        target_parent = posixpath.dirname(target_path) or "."
        rewritten = posixpath.relpath(mapped, target_parent)
        if separator:
            rewritten += f"#{anchor}"
        return f"{match.group(1)}{rewritten}{match.group(3)}"

    return MARKDOWN_LINK_RE.sub(replace, value)


def _split_lead_heading(
    body: str, fallback_title: str, *, markdown: bool = True
) -> tuple[str, str]:
    """Return (heading_line, remainder), reusing the body's own H1 when present
    so generated titles never stack on it. Non-markdown bodies always get the
    fallback: a leading '# ' there is content (a shell comment), not a title."""
    stripped = body.strip()
    if markdown and stripped.startswith("# "):
        first, _, rest = stripped.partition("\n")
        return first.strip(), rest.strip()
    return f"# {fallback_title}", stripped


def _reference_content(
    source: SourceFile, item: PlanItem, link_map: dict[str, str]
) -> str:
    metadata, body = split_frontmatter(source.text or "")
    # Rewrite links and source terms BEFORE splitting the heading so a reused
    # source H1 gets the same cleanup as the rest of the body.
    body = _rewrite_internal_links(
        body, source.path, item.target_path or source.path, link_map
    )
    body = rewrite_source_terms(body)
    target = PurePosixPath(item.target_path or source.path)
    heading, body = _split_lead_heading(
        body, humanize(target.stem), markdown=target.suffix.lower() == ".md"
    )
    description = metadata.get("description")
    lines = [heading, ""]
    if description:
        lines.extend([rewrite_source_terms(str(description)).strip(), ""])
    if source.kind == "agent":
        tools = metadata.get("tools")
        model = metadata.get("model")
        if tools:
            values = tools if isinstance(tools, list) else [str(tools)]
            lines.extend(
                [
                    "## Capability assumptions",
                    "",
                    "Confirm that the active Codex session exposes the required capabilities before following this workflow:",
                    "",
                    *[f"- {rewrite_source_terms(str(value))}" for value in values],
                    "",
                ]
            )
        if model and str(model).lower() != "inherit":
            lines.extend(
                [
                    "Use the active Codex model unless the user explicitly selects another valid target model.",
                    "",
                ]
            )
    lines.extend([body, ""])
    return "\n".join(lines)


def _skill_content(
    source: SourceFile,
    item: PlanItem,
    link_map: dict[str, str],
    target_name: str | None = None,
) -> tuple[str, str, str]:
    metadata, body = split_frontmatter(source.text or "")
    target = PurePosixPath(item.target_path or "SKILL.md")
    if str(target) == "SKILL.md" and target_name:
        name = target_name
    elif len(target.parts) >= 3 and target.parts[-1] == "SKILL.md":
        name = target.parts[-2]
    else:
        name = normalize_name(
            str(metadata.get("name") or PurePosixPath(source.path).stem)
        )
    description = clean_description(
        str(metadata.get("description") or "") or None,
        name,
        trigger=True,
    )
    body = _rewrite_internal_links(
        body, source.path, item.target_path or source.path, link_map
    )
    if source.kind == "command":
        heading, remainder = _split_lead_heading(
            body, humanize(PurePosixPath(source.path).stem)
        )
        body = (
            f"{heading}\n\n"
            "Follow the workflow below and preserve its intended outcome.\n\n"
            f"{remainder}"
        )
    elif source.kind == "agent":
        heading, remainder = _split_lead_heading(
            body, humanize(PurePosixPath(source.path).stem)
        )
        tools = metadata.get("tools")
        assumptions = ""
        if tools:
            values = tools if isinstance(tools, list) else [str(tools)]
            assumptions = (
                "\n\n## Capability assumptions\n\n"
                "Confirm the active session exposes these capabilities before proceeding:\n\n"
                + "\n".join(f"- {rewrite_source_terms(str(value))}" for value in values)
            )
        if metadata.get("model") and str(metadata.get("model")).lower() != "inherit":
            assumptions += "\n\nUse the active Codex model unless the user explicitly selects another valid target model."
        body = (
            f"{heading}\n\n"
            "Adopt the specialized role and preserve every constraint in the instructions below.\n\n"
            f"{remainder}{assumptions}"
        )
    return name, description, render_skill(name, description, body)


def _copy_or_rewrite(
    source: SourceFile, destination: Path, item: PlanItem, link_map: dict[str, str]
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.binary:
        shutil.copy2(source.absolute_path, destination)
        return
    linked = _rewrite_internal_links(
        source.text or "", source.path, item.target_path or source.path, link_map
    )
    prose = (
        source.kind not in MECHANICAL_KINDS
        and destination.suffix.lower() in PROSE_SUFFIXES
    )
    rewritten = rewrite_source_terms(linked) if prose else rewrite_runtime_terms(linked)
    if destination.suffix.lower() == ".json":
        try:
            parsed = json.loads(rewritten)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid JSON after rewriting {source.path}: {error}"
            ) from error
        write_json(destination, parsed)
    else:
        write_text(destination, rewritten)
    shutil.copymode(source.absolute_path, destination)


def _ensure_unique_targets(plan: MigrationPlan) -> None:
    seen: dict[str, str] = {}
    for item in plan.items:
        if not item.target_path or item.operation in {"delete", "manual"}:
            continue
        previous = seen.get(item.target_path)
        if previous is None:
            seen[item.target_path] = item.source_path
            continue
        if item.target_path == ".codex-plugin/plugin.json":
            continue
        target = PurePosixPath(item.target_path)
        index = 2
        while True:
            candidate_name = f"{target.stem}-{index}{target.suffix}"
            candidate = str(target.with_name(candidate_name))
            if candidate not in seen:
                item.target_path = candidate
                seen[candidate] = item.source_path
                plan.warnings.append(
                    f"Resolved target collision between {previous} and {item.source_path} as {candidate}."
                )
                break
            index += 1


def _write_generated_main_skill(package_root: Path, plan: MigrationPlan) -> None:
    skill_path = package_root / "SKILL.md"
    if skill_path.exists():
        metadata, _ = split_frontmatter(skill_path.read_text(encoding="utf-8"))
        description = clean_description(
            str(metadata.get("description") or ""), plan.target_name, trigger=True
        )
        write_text(
            package_root / "agents" / "openai.yaml",
            render_openai_yaml(plan.target_name, description),
        )
        return
    references = sorted(
        {
            item.target_path
            for item in plan.items
            if item.target_path
            and item.target_path.startswith("references/")
            and item.target_path.lower().endswith((".md", ".txt"))
        }
    )
    display = humanize(plan.target_name)
    lines = [
        f"# {display}",
        "",
        "Select the relevant workflow reference, read it completely, and preserve its intended outcome.",
    ]
    if references:
        lines.extend(["", "## Workflow references", ""])
        for reference in references:
            lines.append(
                f"- For {humanize(PurePosixPath(reference).stem)}, read [{PurePosixPath(reference).name}]({reference})."
            )
    lines.extend(
        [
            "",
            "## Process",
            "",
            "1. Inspect the user's request and select only the relevant workflow material.",
            "2. Follow the selected instructions as the source of truth.",
            "3. Preserve behavioral constraints and report anything that cannot be completed safely.",
        ]
    )
    description = clean_description(
        str(plan.metadata.get("description") or ""), plan.target_name, trigger=True
    )
    write_text(
        skill_path, render_skill(plan.target_name, description, "\n".join(lines))
    )
    write_text(
        package_root / "agents" / "openai.yaml",
        render_openai_yaml(plan.target_name, description),
    )


def _ensure_plugin_skills(package_root: Path, plan: MigrationPlan) -> None:
    skills_root = package_root / "skills"
    skill_files = sorted(skills_root.glob("*/SKILL.md")) if skills_root.exists() else []
    if not skill_files:
        name = plan.target_name
        description = clean_description(
            str(plan.metadata.get("description") or ""), name, trigger=True
        )
        body = (
            f"# {humanize(name)}\n\n"
            "Use the plugin's configured integrations and supporting resources to complete the user's request.\n\n"
            "1. Inspect the available plugin tools and workflow resources.\n"
            "2. Select the smallest capability set that completes the task.\n"
            "3. Respect approvals and report unavailable or unsafe actions."
        )
        skill_root = skills_root / name
        write_text(skill_root / "SKILL.md", render_skill(name, description, body))
        skill_files = [skill_root / "SKILL.md"]
    for skill_file in skill_files:
        references = (
            sorted(
                path
                for path in (skill_file.parent / "references").rglob("*")
                if path.is_file()
            )
            if (skill_file.parent / "references").exists()
            else []
        )
        if references:
            content = skill_file.read_text(encoding="utf-8")
            additions = []
            for reference in references:
                relative = reference.relative_to(skill_file.parent).as_posix()
                if f"]({relative})" not in content:
                    additions.append(
                        f"- Read [{reference.stem}]({relative}) when its details apply."
                    )
            if additions:
                content = (
                    content.rstrip()
                    + "\n\n## Additional references\n\n"
                    + "\n".join(additions)
                    + "\n"
                )
                write_text(skill_file, content)
        metadata, _ = split_frontmatter(skill_file.read_text(encoding="utf-8"))
        name = normalize_name(str(metadata.get("name") or skill_file.parent.name))
        description = clean_description(
            str(metadata.get("description") or ""), name, trigger=True
        )
        write_text(
            skill_file.parent / "agents" / "openai.yaml",
            render_openai_yaml(name, description),
        )


def _clean_author(value: Any) -> dict[str, str]:
    if isinstance(value, str) and value.strip():
        return {"name": rewrite_source_terms(value).strip()}
    if isinstance(value, dict):
        result: dict[str, str] = {}
        for key in ("name", "email", "url"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                result[key] = rewrite_source_terms(raw).strip()
        if result.get("name"):
            return result
    return {"name": "Package Maintainer"}


def _plugin_manifest(package_root: Path, plan: MigrationPlan) -> dict[str, Any]:
    metadata = plan.metadata
    version = str(metadata.get("version") or "0.1.0")
    if not SEMVER_RE.fullmatch(version):
        version = "0.1.0"
        plan.warnings.append(
            "Source version was not strict semver; generated plugin uses 0.1.0."
        )
    display = humanize(plan.target_name)
    description = clean_description(
        str(metadata.get("description") or ""), plan.target_name
    )
    short = description[:96].rstrip()
    long_description = (
        description
        if len(description) >= 25
        else f"Provide reusable workflows and integrations for {display}."
    )
    has_runtime = any(
        (package_root / value).exists() for value in (".mcp.json", ".app.json", "hooks")
    )
    manifest: dict[str, Any] = {
        "name": plan.target_name,
        "version": version,
        "description": description,
        "author": _clean_author(metadata.get("author")),
        "skills": "./skills/",
        "interface": {
            "displayName": display,
            "shortDescription": short,
            "longDescription": long_description,
            "developerName": _clean_author(metadata.get("author"))["name"],
            "category": "Productivity",
            "capabilities": ["Read", "Write"] if has_runtime else ["Read"],
            "defaultPrompt": [f"Use {display} for its primary workflow."],
        },
    }
    if (package_root / ".mcp.json").exists():
        manifest["mcpServers"] = "./.mcp.json"
    if (package_root / ".app.json").exists():
        manifest["apps"] = "./.app.json"
    for key in ("homepage", "repository", "license"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            cleaned = rewrite_source_terms(value).strip()
            if not re.search(r"claude|anthropic", cleaned, re.IGNORECASE):
                manifest[key] = cleaned
    keywords = metadata.get("keywords")
    if isinstance(keywords, list):
        cleaned_keywords = [normalize_name(str(value)) for value in keywords]
        manifest["keywords"] = sorted({value for value in cleaned_keywords if value})
    return manifest


def build_package(
    inventory: Inventory, plan: MigrationPlan, package_root: Path, reports_root: Path
) -> None:
    _ensure_unique_targets(plan)
    source_by_path = _source_map(inventory)
    link_map = {
        item.source_path: item.target_path
        for item in plan.items
        if item.target_path and item.operation not in {"delete", "manual"}
    }
    package_root.mkdir(parents=True, exist_ok=True)
    unresolved_root = reports_root / "unresolved"

    for item in plan.items:
        source = source_by_path[item.source_path]
        if item.operation == "manual":
            destination = unresolved_root / Path(*PurePosixPath(item.source_path).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source.absolute_path, destination)
            item.status = "manual"
            continue
        if item.operation == "delete":
            item.status = "deleted"
            continue
        if item.target_path == ".codex-plugin/plugin.json":
            item.status = "generated-later"
            continue
        if not item.target_path:
            raise ValueError(f"Missing target path for {item.source_path}")
        destination = package_root / Path(*PurePosixPath(item.target_path).parts)
        if (
            source.kind in {"skill-entrypoint", "command", "agent"}
            and destination.name == "SKILL.md"
        ):
            _, _, content = _skill_content(source, item, link_map, plan.target_name)
            write_text(destination, content)
            shutil.copymode(source.absolute_path, destination)
        elif item.operation == "split-into-reference":
            write_text(destination, _reference_content(source, item, link_map))
            shutil.copymode(source.absolute_path, destination)
        else:
            _copy_or_rewrite(source, destination, item, link_map)
        item.status = "written"

    if plan.decision.target == "skill":
        _write_generated_main_skill(package_root, plan)
    else:
        _ensure_plugin_skills(package_root, plan)
        manifest = _plugin_manifest(package_root, plan)
        write_json(package_root / ".codex-plugin" / "plugin.json", manifest)
        for item in plan.items:
            if item.target_path == ".codex-plugin/plugin.json":
                item.status = "written"
