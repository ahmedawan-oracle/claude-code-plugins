from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from .common import (
    ENV_VAR_MAP,
    SOURCE_ENV_VAR_RE,
    clean_path_part,
    normalize_name,
    rewrite_source_terms,
    split_frontmatter,
)
from .models import ArchitectureDecision, Inventory, MigrationPlan, PlanItem


RUNTIME_KINDS = {"mcp", "app", "hook", "runtime-config", "runtime-source"}


def _read_metadata(inventory: Inventory) -> dict[str, Any]:
    manifest = next(
        (item for item in inventory.files if item.kind == "source-manifest"), None
    )
    if manifest and manifest.text:
        try:
            value = json.loads(manifest.text)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    root_skill = next(
        (item for item in inventory.files if item.path == "SKILL.md"), None
    )
    skill_meta: dict[str, Any] = {}
    if root_skill and root_skill.text:
        skill_meta, _ = split_frontmatter(root_skill.text)
    return {
        "name": str(
            skill_meta.get("name") or inventory.source_name or inventory.root.name
        ),
        "description": str(
            skill_meta.get("description")
            or "Reusable workflow migrated into Codex format."
        ),
        "version": "0.1.0",
        "sourceSkill": root_skill.path if root_skill else None,
    }


def _unmapped_env_vars(inventory: Inventory) -> list[str]:
    found: set[str] = set()
    for item in inventory.files:
        if item.text:
            found.update(SOURCE_ENV_VAR_RE.findall(item.text))
    return sorted(found - set(ENV_VAR_MAP))


def _unique_name(base: str, used: set[str]) -> str:
    candidate = normalize_name(base)
    index = 2
    while candidate in used:
        candidate = normalize_name(f"{base}-{index}")
        index += 1
    used.add(candidate)
    return candidate


def _clean_relative(relative: str) -> str:
    return "/".join(clean_path_part(part) for part in PurePosixPath(relative).parts)


def _architecture(inventory: Inventory, requested: str) -> ArchitectureDecision:
    runtime = sorted(
        {item.kind for item in inventory.files if item.kind in RUNTIME_KINDS}
    )
    if requested in {"skill", "plugin"}:
        target = requested
        detail = "Target architecture was explicitly requested."
        if target == "skill" and runtime:
            detail += (
                f" Runtime components will be reported as manual: {', '.join(runtime)}."
            )
        return ArchitectureDecision(target=target, reason=detail, forced=True)  # type: ignore[arg-type]
    if runtime:
        return ArchitectureDecision(
            target="plugin",
            reason=f"Build as plugin because runtime integration components were detected: {', '.join(runtime)}.",
        )
    return ArchitectureDecision(
        target="skill",
        reason="Build as Skill because the source is primarily instructions, prompts, or reusable workflow content.",
    )


def build_plan(
    inventory: Inventory,
    *,
    requested_target: str = "auto",
    requested_name: str | None = None,
    trust_runtime: bool = False,
) -> MigrationPlan:
    metadata = _read_metadata(inventory)
    source_name = str(metadata.get("name") or inventory.root.name)
    target_name = normalize_name(requested_name or source_name)
    decision = _architecture(inventory, requested_target)
    warnings = list(inventory.warnings)
    unmapped_env_vars = _unmapped_env_vars(inventory)
    if unmapped_env_vars:
        warnings.append(
            "Unmapped source environment variables have no automatic Codex "
            f"equivalent and require manual replacement: {', '.join(unmapped_env_vars)}."
        )
    if decision.target == "skill" and any(
        item.kind in RUNTIME_KINDS for item in inventory.files
    ):
        warnings.append(
            "A Skill target cannot package detected runtime integration components; they require manual follow-up."
        )

    source_skill_names = sorted(
        {
            PurePosixPath(item.path).parts[1]
            for item in inventory.files
            if item.kind == "skill-entrypoint"
            and item.path != "SKILL.md"
            and len(PurePosixPath(item.path).parts) >= 3
        }
    )
    has_root_skill = any(item.path == "SKILL.md" for item in inventory.files)
    workflow_count = len(source_skill_names) + sum(
        item.kind in {"command", "agent"} for item in inventory.files
    )
    direct_skill = decision.target == "skill" and (
        (has_root_skill and not source_skill_names and workflow_count == 0)
        or (not has_root_skill and len(source_skill_names) == 1 and workflow_count == 1)
    )

    used_skill_names: set[str] = set()
    skill_name_map = {
        name: _unique_name(name, used_skill_names) for name in source_skill_names
    }
    if has_root_skill:
        skill_name_map["<root>"] = target_name
        used_skill_names.add(target_name)
    command_name_map: dict[str, str] = {}
    agent_name_map: dict[str, str] = {}
    for item in inventory.files:
        stem = PurePosixPath(item.path).stem
        if item.kind == "command":
            command_name_map[item.path] = _unique_name(stem, used_skill_names)
        elif item.kind == "agent":
            agent_name_map[item.path] = _unique_name(stem, used_skill_names)
    primary_skill_name = (
        next(iter(skill_name_map.values()), None)
        or next(iter(command_name_map.values()), None)
        or next(iter(agent_name_map.values()), None)
        or target_name
    )

    items: list[PlanItem] = []
    for source in inventory.files:
        path = PurePosixPath(source.path)
        parts = path.parts
        kind = source.kind
        operation: str
        target_path: str | None
        reason: str
        rewrites: list[str] = []

        if kind == "source-manifest":
            if decision.target == "plugin":
                operation, target_path = "rewrite", ".codex-plugin/plugin.json"
                reason = (
                    "Convert source metadata into a validated Codex plugin manifest."
                )
            else:
                operation, target_path = "delete", None
                reason = "Consume source metadata while generating the target Skill; do not copy the source manifest."
        elif kind == "source-marketplace":
            operation, target_path = "manual", None
            reason = "A marketplace catalog can describe multiple packages and requires package-by-package migration."
        elif kind == "skill-entrypoint":
            if source.path == "SKILL.md":
                mapped = target_name
                remainder = "SKILL.md"
            else:
                mapped = skill_name_map[parts[1]]
                remainder = "/".join(parts[2:])
            if decision.target == "plugin":
                operation, target_path = (
                    "rewrite",
                    f"skills/{mapped}/{_clean_relative(remainder)}",
                )
                reason = "Rewrite the source Skill as a Codex plugin-bundled Skill."
            elif direct_skill:
                operation, target_path = "rewrite", "SKILL.md"
                reason = (
                    "Rewrite the source workflow as the target Codex Skill entrypoint."
                )
            else:
                operation, target_path = (
                    "split-into-reference",
                    f"references/{mapped}.md",
                )
                reason = "Preserve this workflow as a focused reference selected by the consolidated Skill."
            rewrites = [
                "Normalize Skill metadata",
                "Rewrite source-platform references",
                "Preserve instruction intent",
            ]
        elif kind == "skill-resource":
            mapped = skill_name_map.get(parts[1], normalize_name(parts[1]))
            remainder_parts = list(parts[2:])
            if decision.target == "plugin":
                operation = "rewrite" if not source.binary else "rename"
                target_path = (
                    f"skills/{mapped}/{_clean_relative('/'.join(remainder_parts))}"
                )
                reason = (
                    "Preserve the Skill resource under its normalized plugin Skill."
                )
            elif direct_skill:
                operation = "rewrite" if not source.binary else "rename"
                target_path = _clean_relative("/".join(remainder_parts))
                reason = "Preserve the resource in the standalone target Skill."
            else:
                top = remainder_parts[0].lower() if remainder_parts else "references"
                tail = (
                    "/".join(remainder_parts[1:])
                    if len(remainder_parts) > 1
                    else path.name
                )
                if top == "scripts":
                    target_path = f"scripts/{mapped}/{_clean_relative(tail)}"
                elif top == "assets":
                    target_path = f"assets/{mapped}/{_clean_relative(tail)}"
                else:
                    target_path = f"references/{mapped}-{normalize_name(path.stem)}{path.suffix.lower()}"
                operation = "rewrite" if not source.binary else "rename"
                reason = "Move this supporting resource into the consolidated Skill's resource layout."
            rewrites = (
                ["Rewrite source-platform references"] if not source.binary else []
            )
        elif kind == "command":
            mapped = command_name_map[source.path]
            if decision.target == "plugin":
                operation, target_path = "rewrite", f"skills/{mapped}/SKILL.md"
                reason = "Convert the command into a reusable plugin-bundled Skill."
            else:
                operation, target_path = (
                    "split-into-reference",
                    f"references/{mapped}.md",
                )
                reason = "Convert command intent into a workflow reference selected by the target Skill."
            rewrites = [
                "Remove slash-command assumptions",
                "Rewrite source-platform invocations",
                "Preserve command intent",
            ]
        elif kind == "agent":
            mapped = agent_name_map[source.path]
            if decision.target == "plugin":
                operation, target_path = "rewrite", f"skills/{mapped}/SKILL.md"
                reason = (
                    "Convert reusable agent behavior into a discoverable Codex Skill."
                )
            else:
                operation, target_path = (
                    "split-into-reference",
                    f"references/{mapped}.md",
                )
                reason = "Preserve specialized behavior as a workflow reference instead of undocumented bundled agent config."
            rewrites = [
                "Preserve the full instruction body",
                "Map model and tool assumptions to manual notes",
                "Rewrite source terms",
            ]
        elif kind in {"agent-resource", "command-resource"}:
            operation, target_path = "manual", None
            reason = "Non-Markdown command or agent resources require semantic review before placement."
        elif kind == "mcp":
            if decision.target == "plugin" and trust_runtime:
                operation, target_path = "rewrite", ".mcp.json"
                reason = (
                    "Adapt MCP configuration and validate local runtime dependencies."
                )
                rewrites = [
                    "Rewrite local paths and source environment variables",
                    "Validate referenced executables",
                ]
            elif decision.target == "plugin":
                operation, target_path = "manual", None
                reason = (
                    "MCP configuration launches local executables once the plugin is "
                    "installed; it is quarantined until reviewed. Re-run with "
                    "--trust-runtime after verifying every command and dependency."
                )
            else:
                operation, target_path = "manual", None
                reason = "MCP runtime integration requires a plugin target."
        elif kind == "app":
            if decision.target == "plugin" and trust_runtime:
                operation, target_path = "rewrite", ".app.json"
                reason = "Preserve the app mapping in the plugin package."
            elif decision.target == "plugin":
                operation, target_path = "manual", None
                reason = (
                    "App integration activates on install; it is quarantined until "
                    "reviewed. Re-run with --trust-runtime after review."
                )
            else:
                operation, target_path = "manual", None
                reason = "App integration requires a plugin target."
        elif kind == "hook":
            if decision.target == "plugin" and trust_runtime:
                operation, target_path = "rewrite", _clean_relative(source.path)
                reason = (
                    "Adapt the hook and use default hooks/hooks.json plugin discovery."
                )
                rewrites = [
                    "Rewrite plugin environment variables",
                    "Preserve hook event and matcher semantics",
                ]
            elif decision.target == "plugin":
                operation, target_path = "manual", None
                reason = (
                    "Hooks execute automatically once the plugin is installed; they "
                    "are quarantined until reviewed. Re-run with --trust-runtime "
                    "after verifying every hook command."
                )
            else:
                operation, target_path = "manual", None
                reason = "Distributable runtime hooks require a plugin target."
        elif kind in {"lsp", "settings"}:
            operation, target_path = "manual", None
            reason = "This source configuration has no safe package-level mapping without project-specific review."
        elif kind in {"script", "asset"}:
            operation = "rewrite" if not source.binary else "rename"
            target_path = _clean_relative(source.path)
            reason = "Preserve a supporting runtime or workflow resource in the target package."
            rewrites = (
                ["Rewrite source-platform references"] if not source.binary else []
            )
        elif kind == "reference":
            operation = "rewrite" if not source.binary else "rename"
            relative_reference = "/".join(parts[1:]) if len(parts) > 1 else path.name
            if decision.target == "plugin":
                target_path = f"skills/{primary_skill_name}/references/{_clean_relative(relative_reference)}"
                reason = (
                    "Attach detailed reference material to the primary plugin Skill."
                )
            else:
                target_path = f"references/{_clean_relative(relative_reference)}"
                reason = (
                    "Preserve detailed material in the standalone Skill references."
                )
            rewrites = (
                ["Rewrite source-platform references"] if not source.binary else []
            )
        elif kind in {"runtime-config", "runtime-source"}:
            if decision.target == "plugin":
                operation = "rewrite" if not source.binary else "rename"
                target_path = _clean_relative(source.path)
                reason = (
                    "Preserve plugin runtime implementation required by an integration."
                )
                rewrites = (
                    ["Rewrite source-platform references"] if not source.binary else []
                )
            else:
                operation, target_path = "manual", None
                reason = "Runtime implementation requires review before inclusion in a standalone Skill."
        elif kind == "documentation":
            if source.text is not None:
                if decision.target == "plugin":
                    # A plugin ships as a repository: documentation keeps its
                    # original layout instead of being funneled into one
                    # skill's references folder.
                    operation = "rewrite"
                    target_path = _clean_relative(source.path)
                    reason = (
                        "Preserve documentation at its original repository path."
                    )
                    rewrites = ["Rewrite source-platform references"]
                else:
                    operation = "split-into-reference"
                    target_path = f"references/{normalize_name(path.stem)}.md"
                    reason = "Preserve relevant documentation as progressively disclosed Skill reference material."
                    rewrites = [
                        "Remove source-only setup",
                        "Rewrite source-platform references",
                    ]
            else:
                operation, target_path = "manual", None
                reason = "Binary documentation requires manual review."
        elif kind == "test":
            operation = "rewrite" if not source.binary else "rename"
            target_path = _clean_relative(source.path)
            reason = "Preserve the source test suite alongside the migrated runtime code."
            rewrites = (
                ["Rewrite source-platform references"] if not source.binary else []
            )
        elif kind == "ci-workflow":
            if trust_runtime:
                operation = "rewrite" if not source.binary else "rename"
                target_path = _clean_relative(source.path)
                reason = "Preserve the reviewed CI workflow in the migrated repository."
                rewrites = (
                    ["Rewrite source-platform references"] if not source.binary else []
                )
            else:
                operation, target_path = "manual", None
                reason = (
                    "CI workflows and composite actions execute automatically once "
                    "the repository is pushed; they are quarantined until reviewed. "
                    "Re-run with --trust-runtime after verifying every step."
                )
        elif kind == "repository-metadata":
            operation = "rewrite" if not source.binary else "rename"
            target_path = _clean_relative(source.path)
            reason = "Preserve repository metadata so the migrated package remains a complete repository."
            rewrites = (
                ["Rewrite source-platform references"] if not source.binary else []
            )
        else:
            if (
                decision.target == "skill"
                and source.text is not None
                and path.suffix.lower() in {".md", ".txt"}
            ):
                operation, target_path = (
                    "split-into-reference",
                    f"references/{normalize_name(path.stem)}{path.suffix.lower()}",
                )
                reason = "Preserve unclassified textual guidance as a reference and flag its origin in the plan."
                warnings.append(
                    f"Unclassified text was retained as a reference: {source.path}"
                )
            else:
                operation, target_path = "manual", None
                reason = "Unknown components require explicit manual placement; they are never silently dropped."

        items.append(
            PlanItem(
                source_path=source.path,
                kind=kind,
                operation=operation,  # type: ignore[arg-type]
                target_path=target_path,
                reason=reason,
                rewrites=rewrites,
            )
        )

    if len(items) != len(inventory.files):
        raise AssertionError(
            "Every source file must receive exactly one migration operation"
        )
    return MigrationPlan(
        source_root=inventory.root,
        source_kind=inventory.source_kind,
        target_name=target_name,
        decision=decision,
        metadata={
            key: rewrite_source_terms(str(value)) if isinstance(value, str) else value
            for key, value in metadata.items()
        },
        items=items,
        warnings=sorted(set(warnings)),
    )
