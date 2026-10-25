#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-FileCopyrightText: 2026 Srihari <sriharilegend23@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""
Pre-commit hook: ensures the committer's SPDX-FileCopyrightText line is present
in every staged file that already has an SPDX header.

- Reads committer identity from git config (user.name + user.email)
- Skips files with no existing SPDX header (new files without headers are
  caught by reuse-lint in CI)
- Skips binary files and files in LICENSES/ or .reuse/
- Uses the current year
- Idempotent: does nothing if the email is already in the header
"""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

SKIP_DIRS = {"LICENSES", ".reuse", "node_modules", ".git", ".venv", "__pycache__", "vendor"}
SKIP_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map", ".lock"}
# JSON has no comments, so a header would break the file; REUSE.toml covers these instead.
SKIP_EXTS |= {".json", ".jsonl"}


def git_identity() -> tuple[str, str]:
    def run(args):
        return subprocess.run(args, capture_output=True, text=True).stdout.strip()

    name = run(["git", "config", "user.name"])
    email = run(["git", "config", "user.email"])
    if not name or not email:
        print("::error:: git user.name or user.email not configured", file=sys.stderr)
        sys.exit(1)
    return name, email


def comment_prefix(path: Path) -> tuple[str, str] | None:
    """Return (prefix, suffix) for the file's comment style, or None to skip."""
    name = path.name
    ext = path.suffix.lower()

    if ext in SKIP_EXTS:
        return None
    if ext in {
        ".py",
        ".sh",
        ".yml",
        ".yaml",
        ".toml",
        ".tf",
        ".tfvars",
        ".conf",
        ".cfg",
        ".env",
        ".example",
        ".gitignore",
    }:
        return ("# ", "")
    if ext in {".ts", ".tsx", ".mjs", ".js"}:
        return ("// ", "")
    if ext in {".md", ".xml", ".svg", ".html"}:
        return ("<!-- ", " -->")
    if ext == ".css":
        return ("/* ", " */")
    if name in {
        "Makefile",
        "Dockerfile",
        "Dockerfile.api",
        "Dockerfile.web",
        ".dockerignore",
        ".editorconfig",
        ".gitattributes",
    }:
        return ("# ", "")
    # default
    return ("# ", "")


def already_has_email(raw: bytes, email: str) -> bool:
    try:
        head = raw[:1024].decode("utf-8", errors="ignore")
    except Exception:
        return True  # skip on error
    return email.lower() in head.lower()


def has_spdx_header(raw: bytes) -> bool:
    try:
        return b"SPDX-FileCopyrightText" in raw[:512]
    except Exception:
        return False


def inject_copyright(path: Path, name: str, email: str, year: int):
    style = comment_prefix(path)
    if style is None:
        return

    prefix = style[0]

    raw = path.read_bytes()
    eol = b"\r\n" if b"\r\n" in raw[:1024] else b"\n"
    nl = "\r\n" if eol == b"\r\n" else "\n"

    text = raw.decode("utf-8", errors="replace")

    # Insert after the last SPDX-FileCopyrightText line of the leading comment
    # block only. Files that generate SPDX headers (release tooling, manifest
    # writers) mention the pattern inside strings further down; injecting there
    # corrupts the source.
    lines = text.splitlines(keepends=True)
    header_end = _header_block_end(lines, prefix)
    header_has_copyright = any("SPDX-FileCopyrightText" in line for line in lines[:header_end])
    if not header_has_copyright and _starts_with_comment(lines):
        # Templates, Helm partials, SQL files and license sidecars use comment
        # styles that do not match the extension-derived prefix. Fall back to
        # the first run of copyright lines instead of skipping the injection,
        # and stop there so SPDX examples further down stay untouched. Files
        # that do not start with a comment (e.g. generators whose strings merely
        # mention SPDX) keep the header-only restriction.
        header_end = next((i for i, line in enumerate(lines) if "SPDX-FileCopyrightText" in line), len(lines))
        while header_end < len(lines) and "SPDX-FileCopyrightText" in lines[header_end]:
            header_end += 1
    last_copyright_idx = -1
    for i, line in enumerate(lines[:header_end]):
        if "SPDX-FileCopyrightText" in line:
            last_copyright_idx = i

    if last_copyright_idx == -1:
        return  # no existing copyright lines, skip

    actual_prefix, actual_suffix = _line_comment_style(lines[last_copyright_idx])
    new_line = f"{actual_prefix}SPDX-FileCopyrightText: {year} {name} <{email}>{actual_suffix}{nl}"
    lines.insert(last_copyright_idx + 1, new_line)
    path.write_bytes("".join(lines).encode("utf-8", errors="replace"))


def _line_comment_style(line: str) -> tuple[str, str]:
    """Prefix and suffix for a copyright line inserted right after ``line``.

    Derived from the matched line rather than the extension so any comment style
    stays valid, including block comments that open or close on this line.
    """
    body = line.rstrip()
    prefix = body[: body.index("SPDX-FileCopyrightText")]
    indent = prefix[: len(prefix) - len(prefix.lstrip())]
    opener = prefix.strip()
    closer = next((c for c in BLOCK_COMMENTS.values() if body.endswith(c)), None)
    suffix = body[len(body[: -len(closer)].rstrip()) :] if closer else ""
    if opener in BLOCK_COMMENTS and not closer:
        # Opens a multi-line comment: repeating the opener would nest it, which
        # is invalid XML. Stay inside the block with the indentation only.
        return indent, ""
    if closer and opener not in BLOCK_COMMENTS:
        # Closes a multi-line comment: the new line lands after the block, so it
        # needs a complete comment of its own.
        op = next(o for o, c in BLOCK_COMMENTS.items() if c == closer)
        return f"{indent}{op} ", suffix
    return prefix, suffix


def _header_block_end(lines: list[str], prefix: str) -> int:
    """Index one past the leading run of blank and comment lines.

    A leading XML declaration (``<?xml ...?>``) belongs to the header.
    """
    marker = prefix.strip()
    end = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(marker) or (index == 0 and stripped.startswith("<?xml")):
            end = index + 1
            continue
        break
    return end


BLOCK_COMMENTS = {"<!--": "-->", "/*": "*/", "{{/*": "*/}}"}
COMMENT_STARTS = ("#", "//", "--", "/*", "{{/*", "<!--", ";", "*", "SPDX-")


def _starts_with_comment(lines: list[str]) -> bool:
    """True when the first non-blank line looks like a comment in any style."""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.startswith(COMMENT_STARTS)
    return False


def is_docs_page(path: Path) -> bool:
    """Pages under docs/ are MDX on the docs site, where an HTML comment is a syntax error."""
    return path.suffix.lower() == ".md" and "docs" in path.parts


def docs_frontmatter_header(text: str, name: str, email: str, year: int, nl: str) -> str:
    """Put the SPDX header in YAML frontmatter as ``#`` comments.

    An existing frontmatter block keeps its keys. Without one, a leading H1
    becomes the page title, matching how the other docs pages are written.
    """
    header = f"# SPDX-FileCopyrightText: {year} {name} <{email}>{nl}"
    # REUSE-IgnoreStart
    header += f"# SPDX-License-Identifier: Apache-2.0{nl}"
    # REUSE-IgnoreEnd
    first_line, eol, rest = text.partition("\n")
    if eol and first_line in ("---", "---\r") and any(line in ("---", "---\r") for line in rest.split("\n")):
        return first_line + eol + header + rest

    body = text.lstrip("\r\n")
    title = ""
    first, _, rest = body.partition("\n")
    if first.startswith("# "):
        title = f"title: {json.dumps(first[2:].strip().replace('`', ''))}{nl}"
        body = rest.lstrip("\r\n")
    return f"---{nl}{header}{title}---{nl}{nl}{body}"


def add_fresh_header(path: Path, name: str, email: str, year: int):
    """Add a complete SPDX header to a file that doesn't have one yet."""
    style = comment_prefix(path)
    if style is None:
        return

    prefix, suffix = style
    copyright_line = f"{prefix}SPDX-FileCopyrightText: {year} {name} <{email}>{suffix}"
    # REUSE-IgnoreStart
    license_line = f"{prefix}SPDX-License-Identifier: Apache-2.0{suffix}"
    # REUSE-IgnoreEnd

    raw = path.read_bytes()
    eol = b"\r\n" if b"\r\n" in raw[:1024] else b"\n"
    nl = "\r\n" if eol == b"\r\n" else "\n"

    text = raw.decode("utf-8", errors="replace")

    if is_docs_page(path):
        path.write_bytes(docs_frontmatter_header(text, name, email, year, nl).encode("utf-8", errors="replace"))
        return

    header = copyright_line + nl + license_line + nl + nl

    # Preserve shebang on first line
    if text.startswith("#!"):
        newline_pos = text.index("\n") + 1
        new_content = text[:newline_pos] + header + text[newline_pos:]
    else:
        new_content = header + text

    path.write_bytes(new_content.encode("utf-8", errors="replace"))


def staged_files() -> list[Path]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True,
        text=True,
    )
    paths = []
    for line in result.stdout.splitlines():
        p = Path(line.strip())
        if not p.exists():
            continue
        parts = set(p.parts)
        if parts & SKIP_DIRS or p.parts[:2] == ("fuzz", "corpus"):
            continue
        paths.append(p)
    return paths


def main():
    name, email = git_identity()
    year = date.today().year
    files = staged_files()
    modified = []
    created = []

    for path in files:
        if comment_prefix(path) is None:
            continue  # nothing to add, so nothing to report
        try:
            raw = path.read_bytes()
        except Exception:
            continue

        if not has_spdx_header(raw):
            # New file without header — add a fresh one
            add_fresh_header(path, name, email, year)
            subprocess.run(["git", "add", str(path)])
            created.append(str(path))
            continue
        if already_has_email(raw, email):
            continue

        inject_copyright(path, name, email, year)
        # re-stage the file
        subprocess.run(["git", "add", str(path)])
        modified.append(str(path))

    if created:
        print(f"[spdx-update] Added SPDX header to {len(created)} new file(s):")
        for f in created:
            print(f"  {f}")
    if modified:
        print(f"[spdx-update] Added copyright line to {len(modified)} file(s):")
        for f in modified:
            print(f"  {f}")


if __name__ == "__main__":
    main()
