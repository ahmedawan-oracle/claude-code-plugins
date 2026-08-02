from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


TargetKind = Literal["skill", "plugin"]
Operation = Literal[
    "keep-as-is",
    "rename",
    "rewrite",
    "split-into-reference",
    "delete",
    "manual",
]

ALLOWED_OPERATIONS = {
    "keep-as-is",
    "rename",
    "rewrite",
    "split-into-reference",
    "delete",
    "manual",
}


@dataclass(slots=True)
class SourceFile:
    path: str
    absolute_path: Path
    kind: str
    size: int
    sha256: str
    binary: bool
    text: str | None
    source_matches: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["absolute_path"] = str(self.absolute_path)
        value.pop("text", None)
        return value


@dataclass(slots=True)
class Inventory:
    root: Path
    source_kind: str
    files: list[SourceFile]
    warnings: list[str] = field(default_factory=list)
    source_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "sourceKind": self.source_kind,
            "sourceName": self.source_name,
            "files": [item.to_dict() for item in self.files],
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class PlanItem:
    source_path: str
    kind: str
    operation: Operation
    target_path: str | None
    reason: str
    rewrites: list[str] = field(default_factory=list)
    status: str = "planned"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ArchitectureDecision:
    target: TargetKind
    reason: str
    forced: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MigrationPlan:
    source_root: Path
    source_kind: str
    target_name: str
    decision: ArchitectureDecision
    metadata: dict[str, Any]
    items: list[PlanItem]
    warnings: list[str] = field(default_factory=list)

    @property
    def manual_items(self) -> list[PlanItem]:
        return [item for item in self.items if item.operation == "manual"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceRoot": str(self.source_root),
            "sourceKind": self.source_kind,
            "targetName": self.target_name,
            "decision": self.decision.to_dict(),
            "metadata": self.metadata,
            "items": [item.to_dict() for item in self.items],
            "warnings": list(self.warnings),
            "summary": {
                operation: sum(1 for item in self.items if item.operation == operation)
                for operation in sorted(ALLOWED_OPERATIONS)
            },
        }


@dataclass(slots=True)
class ValidationResult:
    target: TargetKind
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cleanup_findings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.cleanup_findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "cleanupFindings": list(self.cleanup_findings),
        }


@dataclass(slots=True)
class MigrationResult:
    output_root: Path
    package_root: Path
    reports_root: Path
    plan: MigrationPlan
    validation: ValidationResult
    strict_failed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "outputRoot": str(self.output_root),
            "packageRoot": str(self.package_root),
            "reportsRoot": str(self.reports_root),
            "decision": self.plan.decision.to_dict(),
            "targetName": self.plan.target_name,
            "validation": self.validation.to_dict(),
            "manualItems": len(self.plan.manual_items),
            "strictFailed": self.strict_failed,
        }
