from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "claude-to-codex-migrator"
SCRIPTS = SKILL_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from migrate_to_codex import (  # noqa: E402
    MigrationError,
    MigrationOptions,
    discover_installed,
    migrate,
    resolve_installed,
    scan_leftovers,
    validate_package,
)
from migrate_to_codex import source as source_module  # noqa: E402
from migrate_to_codex.common import (  # noqa: E402
    normalize_name,
    render_openai_yaml,
    rewrite_source_terms,
)


FIXTURES = Path(__file__).parent / "fixtures"


class MigratorTests(unittest.TestCase):
    def test_repository_is_a_skills_only_plugin(self) -> None:
        # This copy is packaged for a Claude Code marketplace; the canonical
        # repository at craxelfn/claude-to-codex-migrator carries the Codex
        # packaging (.codex-plugin + .agents/plugins/marketplace.json).
        manifest = json.loads(
            (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["name"], ROOT.name)
        self.assertEqual(manifest["author"]["name"], "Oussama Lakrafi")
        self.assertEqual(
            manifest["author"]["email"], "oussama.lakrafi@oracle.com"
        )
        self.assertNotIn("apps", manifest)
        self.assertNotIn("mcpServers", manifest)
        self.assertNotIn("hooks", manifest)
        self.assertTrue((SKILL_ROOT / "SKILL.md").is_file())
        self.assertTrue((SKILL_ROOT / "agents" / "openai.yaml").is_file())

    def test_instruction_source_builds_clean_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            result = migrate(
                FIXTURES / "instruction-source",
                output,
                MigrationOptions(strict=True),
            )

            self.assertEqual(result.plan.decision.target, "skill")
            self.assertTrue((result.package_root / "SKILL.md").is_file())
            self.assertTrue((result.package_root / "agents" / "openai.yaml").is_file())
            self.assertTrue(
                (result.package_root / "references" / "release.md").is_file()
            )
            self.assertTrue(
                (result.package_root / "references" / "release-checklist.md").is_file()
            )
            self.assertTrue(
                (result.package_root / "references" / "risk-reviewer.md").is_file()
            )
            self.assertTrue(result.validation.ok)
            self.assertEqual(scan_leftovers(result.package_root), [])
            risk = (result.package_root / "references" / "risk-reviewer.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("release risk reviewer", risk)
            self.assertIn("missing tests", risk)
            self.assertNotIn("Claude", risk)
            release = (result.package_root / "references" / "release.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("](release-checklist.md)", release)
            plan = json.loads(
                (result.reports_root / "migration-plan.json").read_text(
                    encoding="utf-8"
                )
            )
            source_file_count = sum(
                1
                for path in (FIXTURES / "instruction-source").rglob("*")
                if path.is_file()
            )
            self.assertEqual(len(plan["items"]), source_file_count)
            self.assertTrue(all(item["operation"] for item in plan["items"]))

    def test_runtime_source_builds_valid_clean_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            result = migrate(
                FIXTURES / "integration-source",
                output,
                MigrationOptions(strict=True, trust_runtime=True),
            )

            self.assertEqual(result.plan.decision.target, "plugin")
            self.assertTrue(
                (result.package_root / ".codex-plugin" / "plugin.json").is_file()
            )
            self.assertTrue(
                (result.package_root / "skills" / "sync" / "SKILL.md").is_file()
            )
            self.assertTrue((result.package_root / "scripts" / "server.js").is_file())
            self.assertTrue((result.package_root / "hooks" / "hooks.json").is_file())
            plugin_reference = (
                result.package_root / "skills" / "sync" / "references" / "operations.md"
            )
            self.assertTrue(plugin_reference.is_file())
            sync_skill = (
                result.package_root / "skills" / "sync" / "SKILL.md"
            ).read_text(encoding="utf-8")
            self.assertIn("](references/operations.md)", sync_skill)
            hook_text = (result.package_root / "hooks" / "hooks.json").read_text(
                encoding="utf-8"
            )
            self.assertIn("PLUGIN_ROOT", hook_text)
            self.assertNotIn("CLAUDE_PLUGIN_ROOT", hook_text)
            # Documentation keeps its repository layout instead of being
            # funneled into the primary skill's references folder.
            self.assertTrue((result.package_root / "README.md").is_file())
            self.assertTrue(
                (result.package_root / "docs" / "architecture.md").is_file()
            )
            self.assertFalse(
                (
                    result.package_root
                    / "skills"
                    / "sync"
                    / "references"
                    / "architecture.md"
                ).exists()
            )
            self.assertTrue(result.validation.ok)
            self.assertEqual(scan_leftovers(result.package_root), [])
            validated = validate_package(result.package_root, "plugin")
            self.assertTrue(validated.ok, validated.errors)

    def test_tests_and_repository_metadata_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            result = migrate(
                FIXTURES / "integration-source",
                output,
                MigrationOptions(strict=True, trust_runtime=True),
            )

            migrated_test = result.package_root / "tests" / "test_server.py"
            self.assertTrue(migrated_test.is_file())
            test_text = migrated_test.read_text(encoding="utf-8")
            self.assertIn("PLUGIN_ROOT", test_text)
            self.assertNotIn("CLAUDE_PLUGIN_ROOT", test_text)
            self.assertTrue((result.package_root / ".gitignore").is_file())
            self.assertTrue((result.package_root / ".editorconfig").is_file())
            self.assertTrue(
                (
                    result.package_root / ".github" / "ISSUE_TEMPLATE" / "bug.md"
                ).is_file()
            )
            # trust_runtime=True lets reviewed CI workflows and actions ship.
            self.assertTrue(
                (result.package_root / ".github" / "workflows" / "ci.yml").is_file()
            )
            self.assertTrue(
                (
                    result.package_root
                    / ".github"
                    / "actions"
                    / "setup"
                    / "action.yml"
                ).is_file()
            )
            plan = json.loads(
                (result.reports_root / "migration-plan.json").read_text(
                    encoding="utf-8"
                )
            )
            dropped = [
                item
                for item in plan["items"]
                if item["kind"] in {"test", "repository-metadata"}
                and item["operation"] == "delete"
            ]
            self.assertEqual(dropped, [])

    def test_zip_input_is_safely_staged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            archive_path = temporary_path / "source.zip"
            source = FIXTURES / "instruction-source"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for path in sorted(
                    candidate for candidate in source.rglob("*") if candidate.is_file()
                ):
                    archive.write(path, Path("wrapped") / path.relative_to(source))
            result = migrate(
                archive_path, temporary_path / "result", MigrationOptions(strict=True)
            )
            self.assertEqual(result.plan.source_kind, "zip")
            self.assertTrue(result.validation.ok)

    def test_zip_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "unsafe.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escape.md", "unsafe")
            with self.assertRaises(ValueError):
                migrate(archive_path, Path(temporary) / "result")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX special files")
    def test_fifo_and_socket_sources_are_rejected(self) -> None:
        skill_text = (
            "---\n"
            "name: special-files\n"
            "description: Guide work. Use when guiding.\n"
            "---\n\nBody.\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            # A FIFO nested in a source directory must fail fast, not hang.
            source = Path(temporary) / "fifo-source"
            source.mkdir()
            (source / "SKILL.md").write_text(skill_text, encoding="utf-8")
            os.mkfifo(source / "pipe")
            with self.assertRaises(ValueError):
                migrate(source, Path(temporary) / "result-fifo")

            # A FIFO passed as the top-level source must fail before
            # is_zipfile() opens it.
            top_level = Path(temporary) / "pipe-input"
            os.mkfifo(top_level)
            with self.assertRaises(ValueError):
                migrate(top_level, Path(temporary) / "result-top-fifo")

            # A socket nested in a source directory is rejected the same way.
            sock_source = Path(temporary) / "socket-source"
            sock_source.mkdir()
            (sock_source / "SKILL.md").write_text(skill_text, encoding="utf-8")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                try:
                    server.bind(str(sock_source / "app.sock"))
                except OSError:
                    self.skipTest("cannot bind AF_UNIX socket in tempdir")
                with self.assertRaises(ValueError):
                    migrate(sock_source, Path(temporary) / "result-socket")
            finally:
                server.close()

    def test_binary_markdown_resource_does_not_crash_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "binary-md"
            (source / "skills" / "demo").mkdir(parents=True)
            (source / "skills" / "demo" / "SKILL.md").write_text(
                "---\n"
                "name: demo\n"
                "description: Demo workflow. Use when demoing.\n"
                "---\n\nBody.\n",
                encoding="utf-8",
            )
            (source / "skills" / "demo" / "blob.md").write_bytes(b"\xff\xfe\x00\x01")

            result = migrate(source, Path(temporary) / "result")

            self.assertTrue(
                (result.reports_root / "migration-plan.json").is_file()
            )
            preserved = sorted(result.package_root.rglob("blob.md"))
            self.assertTrue(preserved, "binary Markdown resource was not preserved")
            self.assertEqual(preserved[0].read_bytes(), b"\xff\xfe\x00\x01")
            self.assertFalse(
                any("UnicodeDecodeError" in error for error in result.validation.errors)
            )

    def test_stdin_json_bundle_builds_skill(self) -> None:
        bundle = json.dumps(
            {
                "files": {
                    "SKILL.md": (
                        "---\n"
                        "name: claude-helper\n"
                        "description: Help with source workflows. Use when Claude is requested.\n"
                        "---\n\n"
                        "Ask Claude Code to inspect the current repository.\n"
                    ),
                    "references/details.md": "# Details\n\nPreserve the Claude workflow intent.\n",
                }
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = migrate(
                "-",
                Path(temporary) / "result",
                MigrationOptions(name="bundle-helper", strict=True, stdin_text=bundle),
            )
            self.assertEqual(result.plan.source_kind, "stdin")
            self.assertEqual(result.package_root.name, "bundle-helper")
            self.assertTrue(result.validation.ok)
            self.assertEqual(scan_leftovers(result.package_root), [])

    def test_strict_mode_preserves_reports_when_manual_work_remains(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            (source / ".claude-plugin").mkdir(parents=True)
            (source / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "manual-case", "version": "1.0.0"}),
                encoding="utf-8",
            )
            (source / "settings.json").write_text(
                '{"agent":"reviewer"}', encoding="utf-8"
            )
            output = Path(temporary) / "result"
            with self.assertRaises(MigrationError) as captured:
                migrate(source, output, MigrationOptions(strict=True))
            self.assertIsNotNone(captured.exception.result)
            self.assertTrue(
                (output / "reports" / "unresolved" / "settings.json").is_file()
            )
            self.assertTrue((output / "reports" / "migration-report.md").is_file())

    def test_missing_mcp_dependency_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            shutil.copytree(FIXTURES / "integration-source", source)
            (source / ".mcp.json").write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "broken": {
                                "command": "node",
                                "args": ["scripts/missing.js"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(MigrationError) as captured:
                migrate(
                    source,
                    Path(temporary) / "result",
                    MigrationOptions(strict=True, trust_runtime=True),
                )
            self.assertIsNotNone(captured.exception.result)
            errors = captured.exception.result.validation.errors  # type: ignore[union-attr]
            self.assertTrue(
                any("missing local dependency" in error for error in errors)
            )

    def test_existing_output_requires_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            output.mkdir()
            with self.assertRaises(FileExistsError):
                migrate(FIXTURES / "instruction-source", output)
            result = migrate(
                FIXTURES / "instruction-source",
                output,
                MigrationOptions(force=True, strict=True),
            )
            self.assertTrue(result.validation.ok)

    def test_manifestless_source_keeps_its_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            named_by_frontmatter = Path(temporary) / "my-release-skill"
            named_by_frontmatter.mkdir()
            (named_by_frontmatter / "SKILL.md").write_text(
                "---\n"
                "name: release-helper\n"
                "description: Prepare a release. Use when releasing.\n"
                "---\n\n# Release Helper\n\nFollow the checklist.\n",
                encoding="utf-8",
            )
            result = migrate(
                named_by_frontmatter,
                Path(temporary) / "out-frontmatter",
                MigrationOptions(strict=True),
            )
            self.assertEqual(result.package_root.name, "release-helper")

            named_by_folder = Path(temporary) / "docs-helper"
            named_by_folder.mkdir()
            (named_by_folder / "README.md").write_text(
                "# Docs Helper\n\nExplain the workflow.\n", encoding="utf-8"
            )
            result = migrate(
                named_by_folder,
                Path(temporary) / "out-folder",
                MigrationOptions(strict=True),
            )
            self.assertEqual(result.package_root.name, "docs-helper")

    def test_plugin_skill_can_link_to_shared_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bridge"
            (source / ".claude-plugin").mkdir(parents=True)
            (source / ".claude-plugin" / "plugin.json").write_text(
                json.dumps(
                    {
                        "name": "bridge",
                        "version": "1.0.0",
                        "description": "Bridge workflows for local automation.",
                    }
                ),
                encoding="utf-8",
            )
            (source / "skills" / "sync").mkdir(parents=True)
            (source / "skills" / "sync" / "SKILL.md").write_text(
                "---\n"
                "name: sync\n"
                "description: Sync things. Use when syncing.\n"
                "---\n\n# Sync\n\nRun the [check script](../../scripts/check.sh) first.\n",
                encoding="utf-8",
            )
            (source / "scripts").mkdir()
            check = source / "scripts" / "check.sh"
            check.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
            check.chmod(0o755)
            (source / "hooks").mkdir()
            (source / "hooks" / "hooks.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PostToolUse": [
                                {
                                    "matcher": "Write",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "${CLAUDE_PLUGIN_ROOT}/scripts/check.sh",
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = migrate(
                source,
                Path(temporary) / "result",
                MigrationOptions(strict=True, trust_runtime=True),
            )
            self.assertTrue(result.validation.ok, result.validation.errors)
            sync_skill = (
                result.package_root / "skills" / "sync" / "SKILL.md"
            ).read_text(encoding="utf-8")
            self.assertIn("](../../scripts/check.sh)", sync_skill)

    def test_force_refuses_directory_that_is_not_migration_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "precious"
            output.mkdir()
            keepsake = output / "data.txt"
            keepsake.write_text("irreplaceable", encoding="utf-8")
            with self.assertRaises(ValueError):
                migrate(
                    FIXTURES / "instruction-source",
                    output,
                    MigrationOptions(force=True),
                )
            self.assertEqual(keepsake.read_text(encoding="utf-8"), "irreplaceable")

            # A name-shaped decoy is not migration output: a user FILE named
            # 'reports' (or a package/ dir of unrelated data) must be refused.
            decoy = Path(temporary) / "decoy"
            decoy.mkdir()
            (decoy / "reports").write_text("user data", encoding="utf-8")
            with self.assertRaises(ValueError):
                migrate(
                    FIXTURES / "instruction-source",
                    decoy,
                    MigrationOptions(force=True),
                )
            self.assertEqual(
                (decoy / "reports").read_text(encoding="utf-8"), "user data"
            )

            # The marker alone is not proof of ownership: unrelated files
            # living next to previous output must survive a force run.
            mixed = Path(temporary) / "mixed"
            (mixed / "reports").mkdir(parents=True)
            (mixed / "reports" / "migration-report.md").write_text(
                "old report", encoding="utf-8"
            )
            treasured = mixed / "irreplaceable.txt"
            treasured.write_text("precious", encoding="utf-8")
            with self.assertRaises(ValueError):
                migrate(
                    FIXTURES / "instruction-source",
                    mixed,
                    MigrationOptions(force=True),
                )
            self.assertEqual(treasured.read_text(encoding="utf-8"), "precious")

    def test_runtime_code_is_never_prose_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "sdk-bridge"
            (source / ".claude-plugin").mkdir(parents=True)
            (source / ".claude-plugin" / "plugin.json").write_text(
                json.dumps(
                    {
                        "name": "sdk-bridge",
                        "version": "1.0.0",
                        "description": "SDK bridge workflows.",
                    }
                ),
                encoding="utf-8",
            )
            (source / "src").mkdir()
            (source / "requirements.txt").write_text(
                "anthropic>=0.40\nrequests\n", encoding="utf-8"
            )
            (source / "src" / "client.py").write_text(
                "from anthropic import Anthropic\nclient = Anthropic()\n",
                encoding="utf-8",
            )
            result = migrate(source, Path(temporary) / "result")
            requirements = (result.package_root / "requirements.txt").read_text(
                encoding="utf-8"
            )
            client = (result.package_root / "src" / "client.py").read_text(
                encoding="utf-8"
            )
            self.assertIn("anthropic>=0.40", requirements)
            self.assertIn("from anthropic import Anthropic", client)
            self.assertNotIn("provider", requirements + client)
            # The preserved terms must surface as explicit manual work.
            self.assertFalse(result.validation.ok)
            self.assertTrue(result.validation.cleanup_findings)

    def test_markdown_code_spans_are_not_prose_rewritten(self) -> None:
        # Mechanical rules still apply inside code; prose rules never do.
        self.assertEqual(
            rewrite_source_terms("Run `${CLAUDE_PLUGIN_ROOT}/check.sh` for Claude."),
            "Run `${PLUGIN_ROOT}/check.sh` for Codex.",
        )
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "sdk-guide"
            source.mkdir()
            (source / "SKILL.md").write_text(
                "---\n"
                "name: sdk-guide\n"
                "description: SDK usage guide. Use when integrating.\n"
                "---\n\n"
                "Install the Claude SDK, then:\n\n"
                "```python\n"
                "from anthropic import Anthropic\n"
                "client = Anthropic()\n"
                "```\n\n"
                "Run `pip install anthropic` first.\n",
                encoding="utf-8",
            )
            result = migrate(source, Path(temporary) / "result")
            skill = (result.package_root / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("from anthropic import Anthropic", skill)
            self.assertIn("`pip install anthropic`", skill)
            self.assertNotIn("provider", skill)
            self.assertIn("Codex SDK", skill)
            # Preserved SDK identifiers must surface as manual work.
            self.assertFalse(result.validation.ok)
            self.assertTrue(result.validation.cleanup_findings)

    def test_indented_and_unclosed_code_blocks_are_protected(self) -> None:
        indented = (
            "Install, then run:\n\n"
            "    from anthropic import Anthropic\n"
            "    client = Anthropic()\n\n"
            "Done with Claude."
        )
        rewritten = rewrite_source_terms(indented)
        self.assertIn("    from anthropic import Anthropic", rewritten)
        self.assertNotIn("provider", rewritten)
        self.assertIn("Done with Codex.", rewritten)

        unclosed = "Setup:\n\n```python\nfrom anthropic import Anthropic\n"
        self.assertIn(
            "from anthropic import Anthropic", rewrite_source_terms(unclosed)
        )

        # A longer fence is closed only by a fence of at least the same
        # length, so shorter fences nested inside stay protected.
        nested = (
            "Example:\n\n"
            "````markdown\n"
            "```python\n"
            "from anthropic import Anthropic\n"
            "```\n"
            "````\n\n"
            "Ask Claude.\n"
        )
        rewritten = rewrite_source_terms(nested)
        self.assertIn("from anthropic import Anthropic", rewritten)
        self.assertNotIn("provider", rewritten)
        self.assertIn("Ask Codex.", rewritten)

        # Inline spans are delimiter-aware: a triple-backtick inline span may
        # contain double-backtick runs without terminating early.
        inline = "Run ```literal `` from anthropic import Anthropic``` now."
        self.assertEqual(rewrite_source_terms(inline), inline)

        # CommonMark code spans may cross line endings (single, double, and
        # triple backtick variants), but never a blank line.
        for delimiter in ("`", "``", "```"):
            multiline = (
                f"Run {delimiter}prefix\n"
                f"from anthropic import Anthropic{delimiter} now with Claude."
            )
            rewritten = rewrite_source_terms(multiline)
            self.assertIn("from anthropic import Anthropic", rewritten, delimiter)
            self.assertNotIn("provider", rewritten, delimiter)
            self.assertIn("now with Codex.", rewritten, delimiter)
        across_paragraphs = "Do not pair ` here.\n\nAsk Claude about ` that."
        self.assertIn("Ask Codex about", rewrite_source_terms(across_paragraphs))

        # Spans close only on a run of EXACTLY the opening length: longer and
        # shorter runs inside are content, for every delimiter length.
        exact_length_cases = (
            "Run `prefix `` from anthropic import Anthropic` now.",
            "Run ``prefix ``` from anthropic import Anthropic`` now.",
            "Run ```prefix `` and ```` from anthropic import Anthropic``` now.",
            "Run `multi\nline `` from anthropic import Anthropic` now.",
        )
        for case in exact_length_cases:
            self.assertEqual(rewrite_source_terms(case), case, case)

    def test_compressed_binaries_are_scanned_for_source_terms(self) -> None:
        import gzip as gzip_module

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "gz-pack"
            (source / ".claude-plugin").mkdir(parents=True)
            (source / ".claude-plugin" / "plugin.json").write_text(
                json.dumps(
                    {
                        "name": "gz-pack",
                        "version": "1.0.0",
                        "description": "Compressed asset pack.",
                    }
                ),
                encoding="utf-8",
            )
            (source / "assets").mkdir()
            (source / "src").mkdir()
            (source / "src" / "run.js").write_text("console.log(1)\n", encoding="utf-8")
            with gzip_module.open(source / "assets" / "data.gz", "wb") as handle:
                handle.write(b"Claude and Anthropic branding inside")
            with self.assertRaises(MigrationError) as captured:
                migrate(
                    source, Path(temporary) / "result", MigrationOptions(strict=True)
                )
            result = captured.exception.result
            assert result is not None
            self.assertTrue(
                any(
                    finding["kind"] == "binary-content"
                    and finding["pattern"] == "source-term-in-binary"
                    for finding in result.validation.cleanup_findings
                ),
                result.validation.cleanup_findings,
            )

            with gzip_module.open(source / "assets" / "data.gz", "wb") as handle:
                handle.write(b"perfectly ordinary payload")
            result = migrate(
                source,
                Path(temporary) / "result-clean",
                MigrationOptions(strict=True),
            )
            self.assertTrue(result.validation.ok)

            # Nested containers: a zip holding a gzip holding the term.
            import io as io_module
            import zipfile as zipfile_module

            inner = io_module.BytesIO()
            with gzip_module.GzipFile(fileobj=inner, mode="wb") as handle:
                handle.write(b"Anthropic Claude hidden payload")
            with zipfile_module.ZipFile(
                source / "assets" / "data.gz", "w"
            ) as archive:
                archive.writestr("payload.gz", inner.getvalue())
            with self.assertRaises(MigrationError) as captured:
                migrate(
                    source,
                    Path(temporary) / "result-nested",
                    MigrationOptions(strict=True),
                )
            result = captured.exception.result
            assert result is not None
            self.assertTrue(
                any(
                    finding["pattern"] == "source-term-in-binary"
                    for finding in result.validation.cleanup_findings
                ),
                result.validation.cleanup_findings,
            )

            # A self-extracting (executable-prefixed) ZIP is still a ZIP.
            zip_buffer = io_module.BytesIO()
            with zipfile_module.ZipFile(zip_buffer, "w") as archive:
                archive.writestr("payload.gz", inner.getvalue())
            (source / "assets" / "data.gz").write_bytes(
                b"MZ\x90\x00sfx-stub" + zip_buffer.getvalue()
            )
            with self.assertRaises(MigrationError) as captured:
                migrate(
                    source,
                    Path(temporary) / "result-sfx",
                    MigrationOptions(strict=True),
                )
            result = captured.exception.result
            assert result is not None
            self.assertTrue(
                any(
                    finding["pattern"] == "source-term-in-binary"
                    for finding in result.validation.cleanup_findings
                ),
                result.validation.cleanup_findings,
            )

    def test_binary_files_are_scanned_for_source_terms(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bin-pack"
            (source / ".claude-plugin").mkdir(parents=True)
            (source / ".claude-plugin" / "plugin.json").write_text(
                json.dumps(
                    {
                        "name": "bin-pack",
                        "version": "1.0.0",
                        "description": "Binary asset pack.",
                    }
                ),
                encoding="utf-8",
            )
            (source / "assets").mkdir()
            (source / "assets" / "blob.bin").write_bytes(
                b"\x00\x01BIN" + b"Claude data" + "Anthropic".encode("utf-16-le")
            )
            (source / "src").mkdir()
            (source / "src" / "run.js").write_text("console.log(1)\n", encoding="utf-8")
            with self.assertRaises(MigrationError) as captured:
                migrate(
                    source, Path(temporary) / "result", MigrationOptions(strict=True)
                )
            result = captured.exception.result
            assert result is not None
            self.assertTrue(
                any(
                    finding["kind"] == "binary-content"
                    for finding in result.validation.cleanup_findings
                ),
                result.validation.cleanup_findings,
            )

            # A clean binary must not be flagged.
            (source / "assets" / "blob.bin").write_bytes(b"\x00\x01clean bytes")
            result = migrate(
                source,
                Path(temporary) / "result-clean",
                MigrationOptions(strict=True),
            )
            self.assertTrue(result.validation.ok)

    def test_app_configuration_structure_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "app-pack"
            (source / ".claude-plugin").mkdir(parents=True)
            (source / ".claude-plugin" / "plugin.json").write_text(
                json.dumps(
                    {
                        "name": "app-pack",
                        "version": "1.0.0",
                        "description": "App integration pack.",
                    }
                ),
                encoding="utf-8",
            )
            (source / ".app.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(MigrationError) as captured:
                migrate(
                    source,
                    Path(temporary) / "result",
                    MigrationOptions(strict=True, trust_runtime=True),
                )
            result = captured.exception.result
            assert result is not None
            self.assertTrue(
                any(".app.json" in error for error in result.validation.errors),
                result.validation.errors,
            )

            # An entry without a connector id fails the official schema.
            (source / ".app.json").write_text(
                json.dumps({"apps": {"main": {}}}), encoding="utf-8"
            )
            with self.assertRaises(MigrationError) as captured:
                migrate(
                    source,
                    Path(temporary) / "result-no-id",
                    MigrationOptions(strict=True, trust_runtime=True),
                )
            result = captured.exception.result
            assert result is not None
            self.assertTrue(
                any("non-empty id" in error for error in result.validation.errors),
                result.validation.errors,
            )

            # Unsupported fields are rejected.
            (source / ".app.json").write_text(
                json.dumps({"apps": {"main": {"id": "main-connector", "cmd": "x"}}}),
                encoding="utf-8",
            )
            with self.assertRaises(MigrationError) as captured:
                migrate(
                    source,
                    Path(temporary) / "result-extra",
                    MigrationOptions(strict=True, trust_runtime=True),
                )
            result = captured.exception.result
            assert result is not None
            self.assertTrue(
                any("unsupported fields" in error for error in result.validation.errors)
            )

            # Unsupported ROOT fields are rejected too (official-schema parity).
            (source / ".app.json").write_text(
                json.dumps(
                    {"apps": {"main": {"id": "main-connector"}}, "metadata": {"x": 1}}
                ),
                encoding="utf-8",
            )
            with self.assertRaises(MigrationError) as captured:
                migrate(
                    source,
                    Path(temporary) / "result-root",
                    MigrationOptions(strict=True, trust_runtime=True),
                )
            result = captured.exception.result
            assert result is not None
            self.assertTrue(
                any(
                    "unsupported root fields" in error
                    for error in result.validation.errors
                ),
                result.validation.errors,
            )

            # An empty apps mapping is officially valid.
            (source / ".app.json").write_text(
                json.dumps({"apps": {}}), encoding="utf-8"
            )
            result = migrate(
                source,
                Path(temporary) / "result-empty-apps",
                MigrationOptions(strict=True, trust_runtime=True),
            )
            self.assertTrue(result.validation.ok, result.validation.errors)

            (source / ".app.json").write_text(
                json.dumps(
                    {"apps": {"main": {"id": "main-connector", "category": "tools"}}}
                ),
                encoding="utf-8",
            )
            result = migrate(
                source,
                Path(temporary) / "result-valid",
                MigrationOptions(strict=True, trust_runtime=True),
            )
            self.assertTrue(result.validation.ok, result.validation.errors)

    def test_hooks_and_mcp_are_quarantined_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            with self.assertRaises(MigrationError) as captured:
                migrate(
                    FIXTURES / "integration-source",
                    output,
                    MigrationOptions(strict=True),
                )
            result = captured.exception.result
            self.assertIsNotNone(result)
            assert result is not None
            self.assertFalse((result.package_root / "hooks").exists())
            self.assertFalse((result.package_root / ".mcp.json").exists())
            self.assertTrue(
                (result.reports_root / "unresolved" / "hooks" / "hooks.json").is_file()
            )
            self.assertTrue(
                (result.reports_root / "unresolved" / ".mcp.json").is_file()
            )
            self.assertFalse(
                (result.package_root / ".github" / "workflows").exists()
            )
            self.assertFalse((result.package_root / ".github" / "actions").exists())
            self.assertTrue(
                (
                    result.reports_root
                    / "unresolved"
                    / ".github"
                    / "workflows"
                    / "ci.yml"
                ).is_file()
            )
            quarantined = {
                item.source_path
                for item in result.plan.manual_items
            }
            self.assertIn("hooks/hooks.json", quarantined)
            self.assertIn(".mcp.json", quarantined)
            self.assertIn(".github/workflows/ci.yml", quarantined)
            self.assertIn(".github/actions/setup/action.yml", quarantined)
            # Inert repository metadata still ships without the trust flag.
            self.assertTrue(
                (
                    result.package_root / ".github" / "ISSUE_TEMPLATE" / "bug.md"
                ).is_file()
            )
            self.assertTrue((result.package_root / ".editorconfig").is_file())

    def test_output_overlapping_a_file_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            document = Path(temporary) / "self.md"
            document.write_text("# Guide\n\nUse the workflow.\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                migrate(document, document, MigrationOptions(force=True))
            self.assertTrue(document.is_file())
            self.assertIn("# Guide", document.read_text(encoding="utf-8"))
            # An output directory above the source file would delete it too.
            with self.assertRaises(ValueError):
                migrate(document, Path(temporary), MigrationOptions(force=True))
            self.assertTrue(document.is_file())

    def test_force_refuses_output_with_unowned_nested_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            result = migrate(
                FIXTURES / "instruction-source", output, MigrationOptions(strict=True)
            )
            self.assertTrue(result.validation.ok)
            stray = output / "package" / "irreplaceable.txt"
            stray.write_text("precious", encoding="utf-8")
            with self.assertRaises(ValueError):
                migrate(
                    FIXTURES / "instruction-source",
                    output,
                    MigrationOptions(force=True),
                )
            self.assertEqual(stray.read_text(encoding="utf-8"), "precious")

    def test_output_inside_the_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "src"
            source.mkdir()
            (source / "SKILL.md").write_text(
                "---\nname: nested\ndescription: Nested. Use when nested.\n---\n\nBody.\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                migrate(source, source / "output")
            with self.assertRaises(ValueError):
                migrate(source, source)
            # Nothing may be written into the source tree by the attempt.
            self.assertEqual(
                [path.name for path in source.iterdir()], ["SKILL.md"]
            )

    def test_force_replaces_previous_output_and_plain_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            result = migrate(
                FIXTURES / "instruction-source", output, MigrationOptions(strict=True)
            )
            self.assertTrue(result.validation.ok)
            # Real previous output with OS junk added must still be replaceable.
            (output / ".DS_Store").write_bytes(b"\x00junk")
            result = migrate(
                FIXTURES / "instruction-source",
                output,
                MigrationOptions(force=True, strict=True),
            )
            self.assertTrue(result.validation.ok)

            stale_file = Path(temporary) / "stale.out"
            stale_file.write_text("old artifact", encoding="utf-8")
            result = migrate(
                FIXTURES / "instruction-source",
                stale_file,
                MigrationOptions(force=True, strict=True),
            )
            self.assertTrue(stale_file.is_dir())
            self.assertTrue(result.validation.ok)

    def test_generated_reference_keeps_a_single_h1(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            document = Path(temporary) / "guide.md"
            document.write_text(
                "# My Doc\n\nUse Claude to review changes.\n", encoding="utf-8"
            )
            result = migrate(
                document, Path(temporary) / "result", MigrationOptions(strict=True)
            )
            self.assertEqual(result.package_root.name, "guide")
            reference = (
                result.package_root / "references" / "guide.md"
            ).read_text(encoding="utf-8")
            self.assertTrue(reference.startswith("# My Doc"))
            self.assertNotIn("\n# ", reference)

    def test_reused_reference_heading_is_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            document = Path(temporary) / "review-guide.md"
            document.write_text(
                "# Claude Code Review Guide\n\nAsk Claude Code for a review.\n",
                encoding="utf-8",
            )
            result = migrate(
                document, Path(temporary) / "result", MigrationOptions(strict=True)
            )
            self.assertTrue(result.validation.ok)
            reference = (
                result.package_root / "references" / "review-guide.md"
            ).read_text(encoding="utf-8")
            self.assertTrue(reference.startswith("# Codex Review Guide"))
            self.assertNotIn("Claude", reference)

    def test_plain_text_comment_is_not_promoted_to_title(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "env-notes"
            source.mkdir()
            (source / "SKILL.md").write_text(
                "---\n"
                "name: env-notes\n"
                "description: Keep notes. Use when noting.\n"
                "---\n\nRead the notes.\n",
                encoding="utf-8",
            )
            (source / "notes.txt").write_text(
                "# export EXAMPLE_API_KEY=your-key\nexport EDITOR=vim\n",
                encoding="utf-8",
            )
            result = migrate(source, Path(temporary) / "result")
            reference = (
                result.package_root / "references" / "notes.txt"
            ).read_text(encoding="utf-8")
            self.assertTrue(reference.startswith("# Notes"))
            self.assertIn("# export EXAMPLE_API_KEY=your-key", reference)

    def test_wrapped_zip_keeps_the_source_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "my-skill.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "dist/SKILL.md",
                    "---\ndescription: Do the thing. Use when asked.\n---\n\nDo it.\n",
                )
            result = migrate(
                archive_path, Path(temporary) / "result", MigrationOptions(strict=True)
            )
            self.assertEqual(result.package_root.name, "my-skill")

    def test_model_identifiers_survive_rewriting(self) -> None:
        self.assertEqual(
            rewrite_source_terms(
                "Ask Claude to call claude-sonnet-4-5 or "
                "us.anthropic.claude-3-5-sonnet-20241022-v2:0."
            ),
            "Ask Codex to call claude-sonnet-4-5 or "
            "us.anthropic.claude-3-5-sonnet-20241022-v2:0.",
        )
        # Version-bearing ids from ANY family are protected — no allowlist.
        self.assertEqual(
            rewrite_source_terms("Use claude-fable-5 today."),
            "Use claude-fable-5 today.",
        )
        # Non-identifier compounds are still rewritten in prose.
        self.assertEqual(
            rewrite_source_terms("A claude-style helper."), "A codex-style helper."
        )
        # Names and paths are never protected: generated layouts stay clean.
        self.assertEqual(normalize_name("claude-3-review"), "codex-3-review")
        self.assertEqual(normalize_name("claude-sonnet-helper"), "codex-sonnet-helper")
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "model-caller"
            source.mkdir()
            (source / "SKILL.md").write_text(
                "---\n"
                "name: model-caller\n"
                "description: Call hosted models. Use when calling models.\n"
                "---\n\nRequest `claude-sonnet-4-5` from Claude Code.\n",
                encoding="utf-8",
            )
            result = migrate(source, Path(temporary) / "result")
            skill = (result.package_root / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("claude-sonnet-4-5", skill)
            self.assertNotIn("Claude Code", skill)
            self.assertFalse(result.validation.ok)
            self.assertTrue(result.validation.cleanup_findings)

    def test_unmapped_environment_variables_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "env-helper"
            source.mkdir()
            (source / "SKILL.md").write_text(
                "---\n"
                "name: env-helper\n"
                "description: Use env vars. Use when configuring.\n"
                "---\n\nSet $CLAUDE_PROJECT_DIR before running.\n",
                encoding="utf-8",
            )
            result = migrate(source, Path(temporary) / "result")
            self.assertTrue(
                any("CLAUDE_PROJECT_DIR" in warning for warning in result.plan.warnings)
            )

            # Test files ship with the package, so their unmapped env vars warn.
            noisy = Path(temporary) / "noisy-helper"
            (noisy / "tests").mkdir(parents=True)
            (noisy / "SKILL.md").write_text(
                "---\n"
                "name: noisy-helper\n"
                "description: Noisy workflows. Use when noisy.\n"
                "---\n\nRun the workflow.\n",
                encoding="utf-8",
            )
            (noisy / "tests" / "fixture.py").write_text(
                'URL = "$CLAUDE_SANDBOX_URL"\n', encoding="utf-8"
            )
            result = migrate(noisy, Path(temporary) / "noisy-result")
            self.assertTrue(
                any("CLAUDE_SANDBOX_URL" in warning for warning in result.plan.warnings)
            )

            # Substring matches must not warn.
            quiet = Path(temporary) / "quiet-helper"
            quiet.mkdir()
            (quiet / "SKILL.md").write_text(
                "---\n"
                "name: quiet-helper\n"
                "description: Quiet workflows. Use when quiet.\n"
                "---\n\nExport MY_CLAUDE_TOKEN before running.\n",
                encoding="utf-8",
            )
            result = migrate(quiet, Path(temporary) / "quiet-result")
            self.assertFalse(
                any(
                    "environment variables" in warning
                    for warning in result.plan.warnings
                ),
                result.plan.warnings,
            )

    def test_zip_limits_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "many.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for index in range(6):
                    archive.writestr(f"file-{index}.md", "content")
            original_entries = source_module.MAX_ZIP_ENTRIES
            source_module.MAX_ZIP_ENTRIES = 5
            try:
                with self.assertRaises(ValueError):
                    migrate(archive_path, Path(temporary) / "result-entries")
            finally:
                source_module.MAX_ZIP_ENTRIES = original_entries

            original_bytes = source_module.MAX_ZIP_TOTAL_BYTES
            source_module.MAX_ZIP_TOTAL_BYTES = 8
            try:
                with self.assertRaises(ValueError):
                    migrate(archive_path, Path(temporary) / "result-bytes")
            finally:
                source_module.MAX_ZIP_TOTAL_BYTES = original_bytes

    def test_zip_entry_limit_counts_directory_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "dirs-only.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for index in range(6):
                    archive.writestr(f"dir-{index}/", "")
            original_entries = source_module.MAX_ZIP_ENTRIES
            source_module.MAX_ZIP_ENTRIES = 5
            try:
                with self.assertRaises(ValueError):
                    migrate(archive_path, Path(temporary) / "result")
            finally:
                source_module.MAX_ZIP_ENTRIES = original_entries
            # The offending entry must be rejected before it is materialized.
            staged = list((Path(temporary) / "result").rglob("dir-*"))
            self.assertLessEqual(len(staged), 5, staged)

    def test_zip_limits_ignore_excluded_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "vendored.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "SKILL.md",
                    "---\n"
                    "name: vendored\n"
                    "description: Vendored workflows. Use when vendoring.\n"
                    "---\n\nDo the work.\n",
                )
                for index in range(6):
                    archive.writestr(f"node_modules/dep/file-{index}.js", "x" * 100)
            original_entries = source_module.MAX_ZIP_ENTRIES
            original_bytes = source_module.MAX_ZIP_TOTAL_BYTES
            source_module.MAX_ZIP_ENTRIES = 5
            source_module.MAX_ZIP_TOTAL_BYTES = 200
            try:
                result = migrate(
                    archive_path,
                    Path(temporary) / "result",
                    MigrationOptions(strict=True),
                )
                self.assertTrue(result.validation.ok)
            finally:
                source_module.MAX_ZIP_ENTRIES = original_entries
                source_module.MAX_ZIP_TOTAL_BYTES = original_bytes

    def test_frontmatter_block_scalars_and_quoting(self) -> None:
        from migrate_to_codex.common import render_skill, split_frontmatter

        for indicator in (">-", ">+", "|-", "|+", ">", "|"):
            metadata, _ = split_frontmatter(
                f"---\nname: x\ndescription: {indicator}\n  folded text here\n---\n\nBody."
            )
            self.assertEqual(metadata["description"], "folded text here", indicator)

        skill = render_skill("x", "Sync: fast and safe. Use when syncing.", "Body.")
        description_line = next(
            line for line in skill.splitlines() if line.startswith("description:")
        )
        self.assertTrue(description_line.startswith('description: "'))
        metadata, _ = split_frontmatter(skill)
        self.assertIn("Sync: fast and safe", metadata["description"])

    def test_external_urls_are_never_rewritten(self) -> None:
        value = (
            "See https://github.com/acme/claude-code-plugins and "
            "https://docs.anthropic.com/en/api for Claude Code details."
        )
        self.assertEqual(
            rewrite_source_terms(value),
            "See https://github.com/acme/claude-code-plugins and "
            "https://docs.anthropic.com/en/api for Codex details.",
        )

    def test_descriptions_never_contain_angle_brackets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bracket-skill"
            source.mkdir()
            (source / "SKILL.md").write_text(
                "---\n"
                "name: bracket-skill\n"
                "description: Process <input> safely. Use when processing.\n"
                "---\n\nProcess it.\n",
                encoding="utf-8",
            )
            result = migrate(
                source, Path(temporary) / "result", MigrationOptions(strict=True)
            )
            self.assertTrue(result.validation.ok, result.validation.errors)
            skill = (result.package_root / "SKILL.md").read_text(encoding="utf-8")
            description_line = next(
                line for line in skill.splitlines() if line.startswith("description:")
            )
            self.assertNotIn("<", description_line)
            self.assertNotIn(">", description_line)
            self.assertIn("Process input safely", description_line)

            # Parity: the internal validator rejects what the official one does.
            bad = Path(temporary) / "bad-skill"
            bad.mkdir()
            (bad / "SKILL.md").write_text(
                "---\n"
                "name: bad-skill\n"
                "description: Process <input> safely.\n"
                "---\n\nBody.\n",
                encoding="utf-8",
            )
            validated = validate_package(bad, "skill")
            self.assertTrue(
                any("angle brackets" in error for error in validated.errors),
                validated.errors,
            )

    def test_comparison_operators_become_words_not_deletions(self) -> None:
        from migrate_to_codex.common import clean_description

        self.assertEqual(
            clean_description("Use when count < 5 or count > 10.", "x"),
            "Use when count less than 5 or count greater than 10.",
        )
        self.assertEqual(
            clean_description("Retry when size <= 4 and depth >= 2.", "x"),
            "Retry when size at most 4 and depth at least 2.",
        )
        self.assertEqual(
            clean_description("Process <input> safely.", "x"),
            "Process input safely.",
        )
        # Spaced and path-like placeholders unwrap instead of becoming
        # malformed comparison prose.
        self.assertEqual(
            clean_description("Use when processing <input file>.", "x"),
            "Use when processing input file.",
        )
        self.assertEqual(
            clean_description("Read <path/to/file> fully.", "x"),
            "Read path/to/file fully.",
        )
        # Recognized HTML tags are stripped, and comparisons inside survive.
        self.assertEqual(
            clean_description("Wrap in <code>tags</code> now.", "x"),
            "Wrap in tags now.",
        )
        self.assertEqual(
            clean_description("Check <code>x < y</code> cases.", "x"),
            "Check x less than y cases.",
        )
        # Bracketed text that reads as a comparison is NOT unwrapped.
        self.assertEqual(
            clean_description("Alert when errors < 5 or retries > 3.", "x"),
            "Alert when errors less than 5 or retries greater than 3.",
        )

    def test_short_description_keeps_real_content_over_fallback(self) -> None:
        rendered = render_openai_yaml("x-skill", "Runs " + "y" * 80)
        short = next(
            line for line in rendered.splitlines() if "short_description" in line
        )
        self.assertIn("Runs y", short)
        self.assertNotIn("migration workflow", short)

    def test_installed_source_discovery_and_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "cache" / "demo" / "1.0.0"
            (install / ".claude-plugin").mkdir(parents=True)
            (install / ".claude-plugin" / "plugin.json").write_text(
                "{}", encoding="utf-8"
            )
            (root / "installed_plugins.json").write_text(
                json.dumps(
                    {
                        "plugins": {
                            "demo@local": [
                                {
                                    "installPath": str(install),
                                    "version": "1.0.0",
                                    "scope": "user",
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            discovered = discover_installed(root)
            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0]["kind"], "plugin")
            self.assertEqual(resolve_installed("demo@local", root), install.resolve())


if __name__ == "__main__":
    unittest.main()
