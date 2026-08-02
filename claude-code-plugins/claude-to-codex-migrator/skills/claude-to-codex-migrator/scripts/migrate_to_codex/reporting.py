from __future__ import annotations

from collections import Counter
from pathlib import Path

from .common import write_json, write_text
from .models import Inventory, MigrationPlan, ValidationResult


def _decision_markdown(plan: MigrationPlan) -> str:
    return (
        f"# Architecture Decision\n\n"
        f"**Build as {plan.decision.target.capitalize()}.**\n\n"
        f"{plan.decision.reason}\n"
    )


def _migration_markdown(
    inventory: Inventory, plan: MigrationPlan, validation: ValidationResult
) -> str:
    counts = Counter(item.operation for item in plan.items)
    lines = [
        f"# Migration Report: {plan.target_name}",
        "",
        f"- Source type: `{inventory.source_kind}`",
        f"- Target: `{plan.decision.target}`",
        f"- Source files classified: {len(plan.items)}",
        f"- Manual items: {len(plan.manual_items)}",
        f"- Validation: {'passed' if validation.ok else 'failed'}",
        "",
        "## Operation summary",
        "",
    ]
    for operation in (
        "keep-as-is",
        "rename",
        "rewrite",
        "split-into-reference",
        "delete",
        "manual",
    ):
        lines.append(f"- `{operation}`: {counts.get(operation, 0)}")
    lines.extend(
        [
            "",
            "## Component plan",
            "",
            "| Source | Kind | Operation | Target | Status |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for item in plan.items:
        target = f"`{item.target_path}`" if item.target_path else "—"
        lines.append(
            f"| `{item.source_path}` | {item.kind} | {item.operation} | {target} | {item.status} |"
        )
    lines.extend(["", "## Warnings", ""])
    if plan.warnings:
        lines.extend(f"- {warning}" for warning in plan.warnings)
    else:
        lines.append("- None")
    lines.extend(["", "## Manual follow-ups", ""])
    if plan.manual_items:
        for item in plan.manual_items:
            lines.append(f"- `{item.source_path}`: {item.reason}")
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def _validation_markdown(validation: ValidationResult) -> str:
    lines = [
        "# Validation Report",
        "",
        f"**Result: {'PASS' if validation.ok else 'FAIL'}**",
        "",
        "## Errors",
        "",
    ]
    lines.extend(
        f"- {value}" for value in validation.errors
    ) if validation.errors else lines.append("- None")
    lines.extend(["", "## Warnings", ""])
    lines.extend(
        f"- {value}" for value in validation.warnings
    ) if validation.warnings else lines.append("- None")
    lines.extend(["", "## Cleanup findings", ""])
    if validation.cleanup_findings:
        for finding in validation.cleanup_findings:
            location = f"{finding['path']}:{finding.get('line', '')}".rstrip(":")
            lines.append(f"- `{location}`: {finding['pattern']} ({finding['kind']})")
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def write_reports(
    reports_root: Path,
    inventory: Inventory,
    plan: MigrationPlan,
    validation: ValidationResult,
) -> None:
    reports_root.mkdir(parents=True, exist_ok=True)
    write_text(reports_root / "decision.md", _decision_markdown(plan))
    write_json(reports_root / "source-inventory.json", inventory.to_dict())
    write_json(reports_root / "migration-plan.json", plan.to_dict())
    write_text(
        reports_root / "migration-report.md",
        _migration_markdown(inventory, plan, validation),
    )
    write_json(
        reports_root / "cleanup-report.json",
        {
            "passed": not validation.cleanup_findings,
            "findingCount": len(validation.cleanup_findings),
            "findings": validation.cleanup_findings,
        },
    )
    write_json(reports_root / "validation-report.json", validation.to_dict())
    write_text(reports_root / "validation-report.md", _validation_markdown(validation))
