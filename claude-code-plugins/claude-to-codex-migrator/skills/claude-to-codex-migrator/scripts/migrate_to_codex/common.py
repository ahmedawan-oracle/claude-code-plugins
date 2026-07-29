from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


MAX_TEXT_BYTES = 4 * 1024 * 1024
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

# Single source of truth for source env vars: the automatic rewrites, the
# forbidden-pattern scan, and the planner's unmapped-variable warning all
# derive from these two definitions.
ENV_VAR_MAP: dict[str, str] = {
    "CLAUDE_PLUGIN_ROOT": "PLUGIN_ROOT",
    "CLAUDE_PLUGIN_DATA": "PLUGIN_DATA",
}
SOURCE_ENV_VAR_RE = re.compile(r"(?<![A-Z0-9_])CLAUDE_[A-Z0-9_]+")

FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("source-product-name", re.compile(r"claude", re.IGNORECASE)),
    ("source-vendor-name", re.compile(r"anthropic", re.IGNORECASE)),
    ("source-plugin-directory", re.compile(r"\.claude(?:-plugin)?", re.IGNORECASE)),
    ("source-environment-variable", SOURCE_ENV_VAR_RE),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_binary_bytes(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def read_text_if_safe(path: Path) -> tuple[bool, str | None]:
    if path.stat().st_size > MAX_TEXT_BYTES:
        return True, None
    data = path.read_bytes()
    if is_binary_bytes(data):
        return True, None
    return False, data.decode("utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def validate_relative_path(value: str) -> PurePosixPath:
    candidate = PurePosixPath(value.replace("\\", "/"))
    if candidate.is_absolute() or not candidate.parts:
        raise ValueError(f"Path must be relative: {value}")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"Path contains unsafe traversal: {value}")
    return candidate


def _replacement_for_case(match: re.Match[str], replacement: str) -> str:
    value = match.group(0)
    if value.isupper():
        return replacement.upper()
    if value.islower():
        return replacement.lower()
    return replacement


# Model identifiers (API model names, Bedrock-style ids) must survive content
# rewriting untouched: blindly renaming them produces identifiers that do not
# exist. Version-bearing tokens (claude-<...digit...>) are the signal — no
# family-name allowlist to maintain — and the cleanup scan still flags them as
# manual work. Names and paths are never protected: generated package layouts
# must stay free of source terms, so name normalization always rewrites.
PROTECTED_IDENTIFIER_RE = re.compile(
    r"(?:[a-z]{2,8}\.)?anthropic\.[a-z0-9._:-]*claude[a-z0-9._:-]*"
    r"|(?<![\w.-])claude-[a-z0-9.:-]*\d[a-z0-9.:-]*",
    re.IGNORECASE,
)

# External URLs are addresses, not branding: rewriting them silently produces
# broken links. They stay intact and the cleanup scan flags any that still
# mention the source platform, turning them into explicit manual work.
PROTECTED_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

# Markdown code — fenced blocks (even unclosed ones), inline spans, and
# CommonMark indented (4-space/tab) blocks — is executable content, not prose:
# it receives mechanical rewrites only, and the cleanup scan flags any
# remaining SDK identifiers for manual review. Fences are matched by length
# and character via backreference: a four-backtick fence is closed only by a
# line with at least four backticks, so shorter fences inside stay protected.
BLOCK_CODE_RE = re.compile(
    r"^[ ]{0,3}(?P<bt>`{3,}).*?(?:^[ ]{0,3}(?P=bt)`*[ \t]*$|\Z)"
    r"|^[ ]{0,3}(?P<tl>~{3,}).*?(?:^[ ]{0,3}(?P=tl)~*[ \t]*$|\Z)"
    r"|(?:^(?:[ ]{4}|\t)[^\n]*(?:\n|\Z))+",
    re.DOTALL | re.MULTILINE,
)

_BACKTICK_RUN_RE = re.compile(r"`+")
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")


def _inline_code_spans(text: str) -> list[tuple[int, int]]:
    """CommonMark inline code spans as (start, end) offsets. A span opened by
    a run of N backticks closes only on a later run of EXACTLY N — shorter and
    longer runs inside are content — and never crosses a blank line. A regex
    cannot express the exact-length rule, so runs are measured explicitly."""
    spans: list[tuple[int, int]] = []
    open_start: int | None = None
    open_len = 0
    open_end = 0
    for match in _BACKTICK_RUN_RE.finditer(text):
        length = match.end() - match.start()
        if open_start is not None and _BLANK_LINE_RE.search(
            text, open_end, match.start()
        ):
            open_start = None
        if open_start is None:
            open_start, open_len, open_end = match.start(), length, match.end()
        elif length == open_len:
            spans.append((open_start, match.end()))
            open_start = None
        # Runs of a different length are span content; keep scanning.
    return spans

# Mechanical rules map environment variables and well-known paths. They are
# the only rules safe for executable code and configuration.
MECHANICAL_REWRITE_RULES: tuple[tuple[str, str], ...] = tuple(
    (source_var, target_var) for source_var, target_var in ENV_VAR_MAP.items()
) + (
    (r"\.claude-plugin", ".codex-plugin"),
    (r"~[/\\]\.claude", "~/.codex"),
)

_PROSE_REWRITE_RULES: tuple[tuple[str, str], ...] = (
    (r"/reload-plugins\b", "restart Codex"),
    (r"\breload-plugins\b", "restart-codex"),
    (r"\bplugin-dir\b", "plugin-root"),
    (r"\bClaude Code\b", "Codex"),
    (r"\bAnthropic\b", "provider"),
    (r"\bClaude\b", "Codex"),
)


def _apply_rewrite_rules(value: str, rules: tuple[tuple[str, str], ...]) -> str:
    result = value
    for pattern, replacement in rules:
        result = re.sub(
            pattern,
            lambda match, word=replacement: _replacement_for_case(match, word),
            result,
            flags=re.IGNORECASE,
        )
    return result


def rewrite_source_terms(value: str, *, protect_identifiers: bool = True) -> str:
    protected: list[str] = []

    def _stash_text(text: str) -> str:
        protected.append(text)
        return f"\x00{len(protected) - 1}\x00"

    def _stash(match: re.Match[str]) -> str:
        return _stash_text(match.group(0))

    result = value
    if protect_identifiers:
        # Block-level code is stashed first, so its backtick runs never reach
        # the inline scanner.
        result = BLOCK_CODE_RE.sub(
            lambda match: _stash_text(rewrite_runtime_terms(match.group(0))), result
        )
        pieces: list[str] = []
        cursor = 0
        for start, end in _inline_code_spans(result):
            pieces.append(result[cursor:start])
            pieces.append(_stash_text(rewrite_runtime_terms(result[start:end])))
            cursor = end
        pieces.append(result[cursor:])
        result = "".join(pieces)
        result = PROTECTED_URL_RE.sub(_stash, result)
        result = PROTECTED_IDENTIFIER_RE.sub(_stash, result)
    result = _apply_rewrite_rules(
        result, MECHANICAL_REWRITE_RULES + _PROSE_REWRITE_RULES
    )
    if protected:
        result = re.sub(
            r"\x00(\d+)\x00", lambda match: protected[int(match.group(1))], result
        )
    return result


def rewrite_runtime_terms(value: str) -> str:
    """Rewrite executable code and configuration: environment variables and
    well-known paths only. Prose substitutions silently corrupt code (imports,
    dependency pins, string constants) — remaining source terms are left for
    the cleanup scan to surface as manual work instead."""
    return _apply_rewrite_rules(value, MECHANICAL_REWRITE_RULES)


def clean_path_part(value: str) -> str:
    cleaned = rewrite_source_terms(value, protect_identifiers=False)
    cleaned = cleaned.replace(" ", "-")
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned


def normalize_name(value: str, fallback: str = "migrated-workflow") -> str:
    cleaned = rewrite_source_terms(value, protect_identifiers=False).strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    if not cleaned:
        cleaned = fallback
    return cleaned[:64].rstrip("-") or fallback


def humanize(value: str) -> str:
    return " ".join(part.capitalize() for part in normalize_name(value).split("-"))


HTML_TAG_RE = re.compile(
    r"</?(?:a|b|br|code|div|em|hr|i|kbd|li|ol|p|pre|samp|span|strong|tt|ul|var)"
    r"(?:\s[^<>]*)?/?>",
    re.IGNORECASE,
)
PLACEHOLDER_RE = re.compile(
    r"<((?:[A-Za-z][A-Za-z0-9._/-]*)(?: [A-Za-z][A-Za-z0-9._/-]*)*)>"
)


def clean_description(value: str | None, name: str, *, trigger: bool = False) -> str:
    cleaned = rewrite_source_terms(value or "")
    # The official Skill validator rejects angle brackets in descriptions.
    # Resolve them in order of confidence: strip recognized HTML tags, unwrap
    # <placeholder> tokens (words, paths, and spaced phrases whose every word
    # starts with a letter — never comparisons like "< 5 or count >"), then
    # translate the remaining comparison operators into words.
    cleaned = HTML_TAG_RE.sub("", cleaned)
    cleaned = PLACEHOLDER_RE.sub(r"\1", cleaned)
    cleaned = re.sub(r"\s*<=\s*", " at most ", cleaned)
    cleaned = re.sub(r"\s*>=\s*", " at least ", cleaned)
    cleaned = re.sub(r"\s*<\s*", " less than ", cleaned)
    cleaned = re.sub(r"\s*>\s*", " greater than ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip("'\"")
    if not cleaned:
        cleaned = f"Run the {humanize(name)} workflow."
    if trigger and "use when" not in cleaned.lower():
        cleaned = (
            cleaned.rstrip(".") + ". Use when this workflow matches the user's request."
        )
    return cleaned[:500]


def scan_text_forbidden(value: str) -> list[str]:
    labels: list[str] = []
    for label, pattern in FORBIDDEN_PATTERNS:
        if pattern.search(value):
            labels.append(label)
    return sorted(set(labels))


def split_frontmatter(value: str) -> tuple[dict[str, Any], str]:
    normalized = value.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized.strip()
    end = normalized.find("\n---\n", 4)
    if end < 0:
        return {}, normalized.strip()
    header = normalized[4:end]
    body = normalized[end + 5 :].strip()
    result: dict[str, Any] = {}
    lines = header.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not match:
            index += 1
            continue
        key, raw = match.groups()
        raw = raw.strip()
        if re.fullmatch(r"[|>][+-]?[0-9]?", raw):
            index += 1
            buffer: list[str] = []
            while index < len(lines) and (
                lines[index].startswith("  ") or not lines[index].strip()
            ):
                buffer.append(lines[index][2:] if lines[index].startswith("  ") else "")
                index += 1
            result[key] = "\n".join(buffer).strip()
            continue
        if (
            not raw
            and index + 1 < len(lines)
            and re.match(r"^\s+-\s+", lines[index + 1])
        ):
            index += 1
            values: list[str] = []
            while index < len(lines):
                item = re.match(r"^\s+-\s+(.*)$", lines[index])
                if not item:
                    break
                values.append(item.group(1).strip().strip("'\""))
                index += 1
            result[key] = values
            continue
        if raw.startswith("[") and raw.endswith("]"):
            result[key] = [
                item.strip().strip("'\"")
                for item in raw[1:-1].split(",")
                if item.strip()
            ]
        elif raw.lower() in {"true", "false"}:
            result[key] = raw.lower() == "true"
        else:
            result[key] = raw.strip("'\"")
        index += 1
    return result, body


def render_skill(name: str, description: str, body: str) -> str:
    safe_description = clean_description(description, name, trigger=True).replace(
        "\n", " "
    )
    # Quote the description: an unquoted value containing ": " is invalid YAML
    # for compliant parsers even though it looks fine to a naive reader.
    return (
        "---\n"
        f"name: {normalize_name(name)}\n"
        f"description: {yaml_quote(safe_description)}\n"
        "---\n\n"
        f"{rewrite_source_terms(body).strip()}\n"
    )


def yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_openai_yaml(name: str, description: str) -> str:
    display = humanize(name)
    short = re.sub(r"\s+", " ", rewrite_source_terms(description)).strip()
    if len(short) > 64:
        truncated = short[:64]
        if " " in truncated:
            # Prefer a word boundary, but never trade real content below the
            # minimum length for the generic fallback: hard-cut instead.
            candidate = truncated.rsplit(" ", 1)[0].rstrip(" ,;:.")
            if len(candidate) >= 25:
                truncated = candidate
        short = truncated.rstrip()
    if len(short) < 25:
        short = f"Run the {display} migration workflow"[:64].rstrip()
    prompt = f"Use ${normalize_name(name)} to run this workflow."
    return (
        "interface:\n"
        f"  display_name: {yaml_quote(display)}\n"
        f"  short_description: {yaml_quote(short)}\n"
        f"  default_prompt: {yaml_quote(prompt)}\n"
    )
