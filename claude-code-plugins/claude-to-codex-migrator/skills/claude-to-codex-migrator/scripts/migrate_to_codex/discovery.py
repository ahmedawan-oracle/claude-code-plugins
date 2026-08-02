from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def default_source_plugins_root() -> Path:
    return Path.home() / ".claude" / "plugins"


def discover_installed(source_plugins_root: Path | None = None) -> list[dict[str, Any]]:
    root = (source_plugins_root or default_source_plugins_root()).expanduser().resolve()
    registry = root / "installed_plugins.json"
    if not registry.is_file():
        return []
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid installed plugin registry: {registry}: {error}"
        ) from error
    plugins = payload.get("plugins", {}) if isinstance(payload, dict) else {}
    if not isinstance(plugins, dict):
        raise ValueError(
            f"Installed plugin registry must contain a plugins object: {registry}"
        )
    discovered: list[dict[str, Any]] = []
    for plugin_id, installs in plugins.items():
        if not isinstance(plugin_id, str) or not isinstance(installs, list):
            continue
        plugin_name, separator, marketplace = plugin_id.partition("@")
        for install in installs:
            if not isinstance(install, dict):
                continue
            raw_path = install.get("installPath")
            install_path = (
                Path(raw_path).expanduser().resolve()
                if isinstance(raw_path, str) and raw_path
                else None
            )
            manifest = (
                install_path / ".claude-plugin" / "plugin.json"
                if install_path
                else None
            )
            catalog = (
                install_path / ".claude-plugin" / "marketplace.json"
                if install_path
                else None
            )
            discovered.append(
                {
                    "pluginId": plugin_id,
                    "pluginName": plugin_name,
                    "marketplace": marketplace if separator else "unknown",
                    "version": str(install.get("version") or "unknown"),
                    "scope": str(install.get("scope") or "unknown"),
                    "installPath": str(install_path) if install_path else "",
                    "exists": bool(install_path and install_path.exists()),
                    "kind": "plugin"
                    if manifest and manifest.is_file()
                    else "marketplace-catalog"
                    if catalog and catalog.is_file()
                    else "unknown",
                    "installedAt": install.get("installedAt"),
                    "lastUpdated": install.get("lastUpdated"),
                }
            )
    return sorted(
        discovered,
        key=lambda item: (
            item["pluginId"],
            item.get("lastUpdated") or "",
            item.get("version") or "",
        ),
    )


def resolve_installed(plugin_id: str, source_plugins_root: Path | None = None) -> Path:
    candidates = [
        item
        for item in discover_installed(source_plugins_root)
        if item["pluginId"] == plugin_id and item["kind"] == "plugin" and item["exists"]
    ]
    if not candidates:
        raise FileNotFoundError(f"No installed source package found for {plugin_id}")
    selected = candidates[-1]
    return Path(selected["installPath"])
