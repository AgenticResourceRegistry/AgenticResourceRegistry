# SPDX-FileCopyrightText: 2026 Shree Harini <shree@observal.dev>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Verified skill folder bundle validation and installation.

This module implements secure installation of complete skill folders from the
registry. It validates all bundle metadata, verifies file integrity, stages
files atomically, and handles collision detection.

Security requirements for complete-folder delivery:
- Validate complete response before ANY destination write
- Require exactly one SKILL.md per bundle
- All file version IDs must equal selected version UUID
- Verify base64, SHA-256, size, mode for each file
- Reject path traversal, symlinks, case/Unicode collisions
- Stage to private sibling tree before modifying active path
- Never downgrade a v2 pin to v1

Contract reference: tests/fixtures/skill_folder_install_contract.json
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from loguru import logger as optic

if TYPE_CHECKING:
    from typing import Any

# ── Constants ───────────────────────────────────────────────────────────────

SUPPORTED_FEATURE = "skill_extra_files_v1"

# Bundle limits (from server contract)
MAX_EXTRA_FILES = 128
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MiB per newly authored file
MAX_TREE_SIZE = 4 * 1024 * 1024  # 4 MiB total decoded; grandfathered files may exceed 2 MiB
MAX_BUNDLE_FILES = MAX_EXTRA_FILES + 2  # SKILL.md, 128 extras, and an optional legacy script
MAX_PATH_BYTES = 240  # UTF-8 bytes total
MAX_PATH_UTF16 = 240  # UTF-16 units total
MAX_SEGMENT_LEN = 100  # per path segment
MAX_DEPTH = 12

VALID_MODES = frozenset(("0644", "0755"))
SKILL_MD_NAME = "SKILL.md"

# Case-insensitive reserved names (Windows + macOS)
_RESERVED_NAMES = frozenset(
    name.upper()
    for name in (
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
        ".DS_Store",
        "Thumbs.db",
        "desktop.ini",
    )
)

# Forbidden path segments
_FORBIDDEN_SEGMENTS = frozenset((".", "..", ".git"))


# ── Data structures ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BundleFile:
    """Validated bundle file ready for installation."""

    path: str  # Relative POSIX path within skill folder
    content: bytes  # Decoded content
    sha256: str  # Expected SHA-256 hex
    size: int  # Expected size
    mode: int  # File mode (0o644 or 0o755)
    version_id: str  # Version UUID this file belongs to


@dataclass(frozen=True, slots=True)
class ValidatedBundle:
    """Completely validated skill folder bundle."""

    listing_id: str
    version_id: str
    digest: str
    skill_file_path: str  # Harness-relative path to SKILL.md
    folder_name: str  # Declared folder name (from SKILL.md name)
    files: tuple[BundleFile, ...]
    total_size: int


class BundleValidationError(Exception):
    """Bundle validation failed with a specific reason."""

    def __init__(self, message: str, *, recoverable: bool = False):
        super().__init__(message)
        self.recoverable = recoverable


class BundleInstallError(Exception):
    """Bundle installation failed."""

    pass


# ── Path validation ─────────────────────────────────────────────────────────


def _normalize_path(path: str) -> str:
    """NFC-normalize and validate a relative POSIX path."""
    # Normalize to NFC (canonical decomposition then composition)
    normalized = unicodedata.normalize("NFC", path)

    # Must not be empty
    if not normalized:
        raise BundleValidationError("Empty path in bundle")

    # Parse as POSIX path
    posix = PurePosixPath(normalized)

    # Must be relative (no leading slash, no drive)
    if posix.is_absolute():
        raise BundleValidationError(f"Absolute path not allowed: {path}")

    # Check each segment
    parts = posix.parts
    if not parts:
        raise BundleValidationError(f"Path resolves to empty after normalization: {path}")
    if len(parts) > MAX_DEPTH:
        raise BundleValidationError(f"Path too deep ({len(parts)} > {MAX_DEPTH}): {path}")

    for segment in parts:
        # Forbidden segments
        if segment in _FORBIDDEN_SEGMENTS:
            raise BundleValidationError(f"Forbidden path segment '{segment}' in: {path}")

        # Reserved names (case-insensitive, with or without extension)
        name_upper = segment.upper()
        base_name = name_upper.split(".")[0] if "." in name_upper else name_upper
        if base_name in _RESERVED_NAMES or name_upper in _RESERVED_NAMES:
            raise BundleValidationError(f"Reserved filename '{segment}' in: {path}")

        # Segment length
        if len(segment) > MAX_SEGMENT_LEN:
            raise BundleValidationError(f"Path segment too long ({len(segment)} > {MAX_SEGMENT_LEN}): {segment}")

        # No trailing/leading spaces or dots
        if segment != segment.strip() or segment.endswith("."):
            raise BundleValidationError(f"Invalid path segment (trailing space/dot): {segment}")

    # Reconstruct canonical path from parts (removes ./)
    canonical = str(posix)

    # Total path length checks (on canonical form)
    path_bytes = canonical.encode("utf-8")
    if len(path_bytes) > MAX_PATH_BYTES:
        raise BundleValidationError(f"Path too long ({len(path_bytes)} UTF-8 bytes > {MAX_PATH_BYTES}): {path}")

    path_utf16_units = len(canonical.encode("utf-16-le")) // 2
    if path_utf16_units > MAX_PATH_UTF16:
        raise BundleValidationError(f"Path too long ({path_utf16_units} UTF-16 units > {MAX_PATH_UTF16}): {path}")

    return canonical


def _case_fold_key(path: str) -> str:
    """Return a case-folded key for collision detection."""
    return unicodedata.normalize("NFC", path.casefold())


# ── Bundle validation ───────────────────────────────────────────────────────


def validate_bundle(
    bundle: dict[str, Any],
    *,
    expected_version_id: str | None = None,
    expected_digest: str | None = None,
) -> ValidatedBundle:
    """Validate a complete skill folder bundle from server response.

    Args:
        bundle: Raw bundle dict from server (standalone or from skill_bundles)
        expected_version_id: If set, all files must have this version_id
        expected_digest: If set, bundle digest must match (for pinned installs)

    Returns:
        ValidatedBundle with verified files ready for installation

    Raises:
        BundleValidationError: If validation fails
    """
    optic.debug("Validating skill folder bundle")

    # Required top-level fields
    listing_id = bundle.get("listing_id")
    version_id = bundle.get("version_id")
    digest = bundle.get("digest")
    skill_file_path = bundle.get("skill_file_path")
    files_raw = bundle.get("files")

    if not listing_id:
        raise BundleValidationError("Bundle missing listing_id")
    if not version_id:
        raise BundleValidationError("Bundle missing version_id")
    if not digest:
        raise BundleValidationError("Bundle missing digest")
    if not skill_file_path:
        raise BundleValidationError("Bundle missing skill_file_path")
    if not isinstance(files_raw, list):
        raise BundleValidationError("Bundle missing or invalid files array")

    # Verify expected values if provided
    if expected_version_id and str(version_id) != str(expected_version_id):
        raise BundleValidationError(f"Bundle version_id mismatch: expected {expected_version_id}, got {version_id}")
    if expected_digest and digest != expected_digest:
        raise BundleValidationError(
            f"Bundle digest mismatch: expected {expected_digest}, got {digest}",
            recoverable=False,
        )

    # File count check
    if len(files_raw) == 0:
        raise BundleValidationError("Bundle has no files")
    if len(files_raw) > MAX_BUNDLE_FILES:
        raise BundleValidationError(f"Too many files ({len(files_raw)} > {MAX_BUNDLE_FILES})")

    # Validate each file and check for collisions
    validated_files: list[BundleFile] = []
    seen_paths: dict[str, str] = {}  # case-folded -> original
    has_skill_md = False
    total_size = 0

    for i, file_raw in enumerate(files_raw):
        try:
            vfile = _validate_file(file_raw, version_id)
        except BundleValidationError as e:
            raise BundleValidationError(f"File {i}: {e}") from e

        # Check for SKILL.md
        if vfile.path == SKILL_MD_NAME:
            has_skill_md = True
            # SKILL.md must not be executable
            if vfile.mode == 0o755:
                raise BundleValidationError("SKILL.md must not be executable")

        # Case-fold collision check
        fold_key = _case_fold_key(vfile.path)
        if fold_key in seen_paths:
            raise BundleValidationError(
                f"Path collision (case-insensitive): '{vfile.path}' vs '{seen_paths[fold_key]}'"
            )
        seen_paths[fold_key] = vfile.path

        # Track total size
        total_size += vfile.size
        if total_size > MAX_TREE_SIZE:
            raise BundleValidationError(f"Total bundle size exceeds {MAX_TREE_SIZE} bytes")

        validated_files.append(vfile)

    if not has_skill_md:
        raise BundleValidationError("Bundle must contain exactly one SKILL.md")

    # Extract folder name from skill_file_path
    # e.g., ".pi/skills/example/SKILL.md" -> "example"
    skill_path = PurePosixPath(skill_file_path)
    if skill_path.name != SKILL_MD_NAME:
        raise BundleValidationError(f"skill_file_path must end with SKILL.md: {skill_file_path}")
    folder_name = skill_path.parent.name
    if not folder_name:
        raise BundleValidationError(f"Cannot extract folder name from skill_file_path: {skill_file_path}")

    optic.debug(
        "Bundle validated: listing_id={}, version_id={}, files={}, size={}",
        listing_id,
        version_id,
        len(validated_files),
        total_size,
    )

    return ValidatedBundle(
        listing_id=str(listing_id),
        version_id=str(version_id),
        digest=str(digest),
        skill_file_path=str(skill_file_path),
        folder_name=folder_name,
        files=tuple(validated_files),
        total_size=total_size,
    )


def _validate_file(file_raw: dict[str, Any], expected_version_id: str) -> BundleFile:
    """Validate a single file entry from the bundle."""
    path = file_raw.get("path")
    content_b64 = file_raw.get("content")
    sha256_expected = file_raw.get("sha256")
    size_expected = file_raw.get("size")
    mode_str = file_raw.get("mode")
    file_version_id = file_raw.get("version_id")
    encoding = file_raw.get("encoding", "base64")

    # Required fields
    if not path:
        raise BundleValidationError("Missing path")
    if content_b64 is None:
        raise BundleValidationError(f"Missing content for {path}")
    if not sha256_expected:
        raise BundleValidationError(f"Missing sha256 for {path}")
    if size_expected is None:
        raise BundleValidationError(f"Missing size for {path}")
    if not mode_str:
        raise BundleValidationError(f"Missing mode for {path}")
    if not file_version_id:
        raise BundleValidationError(f"Missing version_id for {path}")

    # Version ID must match bundle
    if str(file_version_id) != str(expected_version_id):
        raise BundleValidationError(
            f"File version_id mismatch for {path}: expected {expected_version_id}, got {file_version_id}"
        )

    # Validate path
    normalized_path = _normalize_path(path)

    # Validate mode
    if mode_str not in VALID_MODES:
        raise BundleValidationError(f"Invalid mode '{mode_str}' for {path}")
    mode = 0o755 if mode_str == "0755" else 0o644

    # Validate encoding
    if encoding != "base64":
        raise BundleValidationError(f"Unsupported encoding '{encoding}' for {path}")

    # Decode content
    try:
        content = base64.b64decode(content_b64, validate=True)
    except Exception as e:
        raise BundleValidationError(f"Invalid base64 content for {path}: {e}") from e

    # Verify size
    if len(content) != size_expected:
        raise BundleValidationError(f"Size mismatch for {path}: expected {size_expected}, got {len(content)}")

    # Stored historical direct files may exceed the new 2 MiB authoring limit.
    # The server bounds each delivered file and the entire decoded folder to 4 MiB.
    if len(content) > MAX_TREE_SIZE:
        raise BundleValidationError(f"File too large ({len(content)} > {MAX_TREE_SIZE}): {path}")

    # Verify SHA-256
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != sha256_expected:
        raise BundleValidationError(f"SHA-256 mismatch for {path}: expected {sha256_expected}, got {actual_sha256}")

    return BundleFile(
        path=normalized_path,
        content=content,
        sha256=sha256_expected,
        size=len(content),
        mode=mode,
        version_id=str(file_version_id),
    )


# ── Collision detection ─────────────────────────────────────────────────────


def detect_destination_collisions(
    target_dir: Path,
    bundle: ValidatedBundle,
    *,
    existing_skills: list[Path] | None = None,
    bundled_skills: list[str] | None = None,
) -> list[str]:
    """Check for collisions with existing installations.

    Args:
        target_dir: Where the skill folder would be installed
        bundle: Validated bundle
        existing_skills: Paths to other installed skill folders
        bundled_skills: Names of bundled Observal skills (reserved)

    Returns:
        List of collision warning messages (empty if none)
    """
    collisions = []

    # Check if target already exists
    if target_dir.exists():
        if target_dir.is_symlink():
            collisions.append(f"Target is a symlink: {target_dir}")
        elif target_dir.is_dir():
            # Check if it's a managed skill folder
            skill_md = target_dir / SKILL_MD_NAME
            if skill_md.exists():
                collisions.append(f"Skill already exists at: {target_dir}")
            else:
                collisions.append(f"Non-skill directory exists at: {target_dir}")
        else:
            collisions.append(f"File exists at skill destination: {target_dir}")

    # Reserve auto-synced bundled skill names even when they are not yet on
    # disk; the next CLI startup could otherwise overwrite a registry folder.
    if bundled_skills is None:
        from observal_cli.skill_installer import _SKILL_DIRS

        bundled_skills = list(_SKILL_DIRS)
    if bundle.folder_name.casefold() in {name.casefold() for name in bundled_skills}:
        collisions.append(f"Folder name '{bundle.folder_name}' conflicts with bundled Observal skill")

    # Check other installed skills for case collisions
    if existing_skills:
        fold_key = _case_fold_key(bundle.folder_name)
        for skill_path in existing_skills:
            if skill_path == target_dir:
                continue
            existing_fold = _case_fold_key(skill_path.name)
            if existing_fold == fold_key and skill_path.name != bundle.folder_name:
                collisions.append(f"Case-insensitive collision: '{bundle.folder_name}' vs existing '{skill_path.name}'")

    return collisions


# ── Atomic installation ─────────────────────────────────────────────────────


def install_folder_bundle(
    bundle: ValidatedBundle,
    target_dir: Path,
    *,
    backup_dir: Path | None = None,
    force: bool = False,
) -> Path:
    """Install a validated bundle atomically with rollback support.

    Args:
        bundle: Validated bundle to install
        target_dir: Destination directory (e.g., ~/.claude-code/skills/example)
        backup_dir: Where to back up existing content (if target exists)
        force: Legacy opt-in; still requires an explicit unused backup_dir

    Returns:
        Path to installed SKILL.md

    Raises:
        BundleInstallError: If installation fails
    """
    optic.info("Installing skill folder bundle to {}", target_dir)

    # Never follow a pre-existing symlink in the destination hierarchy, even
    # when installing a folder for the first time.
    for parent in (target_dir, *target_dir.parents):
        if parent.is_symlink():
            raise BundleInstallError(f"Symlink in skill destination: {parent}")
    if target_dir.exists() and backup_dir is None:
        raise BundleInstallError(f"Target exists and no confirmed backup specified: {target_dir}")
    if force and backup_dir is None:
        raise BundleInstallError("Force replacement requires an explicit confirmed backup location")
    if backup_dir is not None and (backup_dir.exists() or backup_dir.is_symlink()):
        raise BundleInstallError(f"Backup destination already exists: {backup_dir}")
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    # A unique stage cannot overwrite a previous interrupted install's files.
    stage_dir = Path(tempfile.mkdtemp(prefix=f".{target_dir.name}.stage.", dir=target_dir.parent))

    try:
        # Write all files to staging
        for file in bundle.files:
            file_path = stage_dir / file.path
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Check for symlink attacks in stage
            if file_path.parent.resolve() != (stage_dir / PurePosixPath(file.path).parent).resolve():
                raise BundleInstallError(f"Path traversal detected during staging: {file.path}")

            file_path.write_bytes(file.content)
            os.chmod(file_path, file.mode)

        # Verify staged files
        for file in bundle.files:
            staged_file = stage_dir / file.path
            if not staged_file.is_file():
                raise BundleInstallError(f"Staged file missing: {file.path}")
            if staged_file.stat().st_size != file.size:
                raise BundleInstallError(f"Staged file size mismatch: {file.path}")
            if hashlib.sha256(staged_file.read_bytes()).hexdigest() != file.sha256:
                raise BundleInstallError(f"Staged file content mismatch: {file.path}")
            actual_mode = stat.S_IMODE(staged_file.stat().st_mode)
            if actual_mode != file.mode:
                raise BundleInstallError(f"Staged file mode mismatch: {file.path}")

        # Handle existing target
        backup_path = None
        if target_dir.exists():
            if backup_dir is None:
                raise BundleInstallError(f"Target exists and no confirmed backup specified: {target_dir}")
            backup_path = backup_dir
            target_dir.rename(backup_path)
            optic.debug("Backed up existing skill to {}", backup_path)

        # Atomic swap: rename stage to target
        try:
            stage_dir.rename(target_dir)
        except OSError as e:
            # Restore backup on failure
            if backup_path and backup_path.exists():
                optic.error("Swap failed, restoring backup")
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                backup_path.rename(target_dir)
            raise BundleInstallError(f"Failed to install skill folder: {e}") from e

        # Retain confirmed backups for explicit recovery, including after success.
        optic.info("Skill folder installed successfully: {}", target_dir)
        return target_dir / SKILL_MD_NAME

    except BundleInstallError:
        raise
    except Exception as e:
        raise BundleInstallError(f"Unexpected error during installation: {e}") from e
    finally:
        # Clean up staging directory if it still exists
        if stage_dir.exists():
            try:
                shutil.rmtree(stage_dir)
            except OSError:
                pass


# ── Directory capture for authoring ─────────────────────────────────────────

# Paths to exclude from directory capture
_EXCLUDE_PATTERNS = frozenset(
    (
        ".git",
        ".venv",
        "__pycache__",
        "node_modules",
        ".DS_Store",
        "Thumbs.db",
        ".env",
        ".env.local",
    )
)

# Files that likely contain secrets
_SENSITIVE_PATTERNS = (
    ".env",
    "secrets",
    "credentials",
    "private",
    ".pem",
    ".key",
    "password",
    "token",
)


@dataclass(frozen=True, slots=True)
class CapturedFile:
    """A file captured from a local directory."""

    path: str  # Relative POSIX path
    content: bytes  # Raw bytes
    executable: bool  # Has execute permission
    is_binary: bool  # Non-UTF-8 content


@dataclass(frozen=True, slots=True)
class DirectorySnapshot:
    """Immutable snapshot of a skill directory for upload."""

    skill_md_content: str
    extra_files: tuple[CapturedFile, ...]
    total_size: int
    excluded_paths: tuple[str, ...]
    warnings: tuple[str, ...]


class DirectoryCaptureError(Exception):
    """Directory capture failed."""

    pass


def _is_binary(content: bytes) -> bool:
    """Check if content appears to be binary (non-UTF-8)."""
    try:
        content.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def _is_likely_sensitive(path: str) -> bool:
    """Check if a file path suggests sensitive content."""
    lower = path.lower()
    return any(pattern in lower for pattern in _SENSITIVE_PATTERNS)


def _should_exclude(segment: str) -> bool:
    """Check if a path segment should be excluded."""
    return segment in _EXCLUDE_PATTERNS or segment.startswith(".")


def capture_directory(
    source_dir: Path,
    *,
    exclude: list[str] | None = None,
    include_hidden: bool = False,
) -> DirectorySnapshot:
    """Capture an immutable snapshot of a skill directory.

    Reads all regular files, validates paths, and prepares content for upload.
    Symlinks and special files are refused; excluded patterns are reported.

    Args:
        source_dir: Directory to capture (must contain SKILL.md)
        exclude: Additional paths to exclude (relative to source_dir)
        include_hidden: If True, include hidden files (except .git)

    Returns:
        DirectorySnapshot with validated content

    Raises:
        DirectoryCaptureError: If capture fails
    """
    optic.debug("Capturing directory: {}", source_dir)

    if not source_dir.is_dir():
        raise DirectoryCaptureError(f"Not a directory: {source_dir}")

    if source_dir.is_symlink():
        raise DirectoryCaptureError(f"Directory is a symlink: {source_dir}")

    # Check for SKILL.md
    skill_md_path = source_dir / SKILL_MD_NAME
    if not skill_md_path.is_file():
        raise DirectoryCaptureError(f"Directory must contain {SKILL_MD_NAME}: {source_dir}")

    if skill_md_path.is_symlink():
        raise DirectoryCaptureError(f"{SKILL_MD_NAME} must not be a symlink")

    # Read SKILL.md content
    try:
        skill_md_content = skill_md_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise DirectoryCaptureError(f"{SKILL_MD_NAME} must be valid UTF-8: {e}") from e

    # Build exclusion set
    exclusions = set(_EXCLUDE_PATTERNS)
    exclude_prefixes: set[str] = set()
    for value in exclude or []:
        # Exclusions may name files that cannot be uploaded (reserved filenames,
        # overlong paths) and may use Windows separators. Do not validate them
        # as bundle resources, but never silently ignore an unsafe pattern.
        relative = unicodedata.normalize("NFC", value.replace("\\", "/"))
        path = PurePosixPath(relative)
        if (
            not path.parts
            or path.is_absolute()
            or ".." in path.parts
            or (len(path.parts[0]) == 2 and path.parts[0][0].isalpha() and path.parts[0][1] == ":")
            or "\x00" in relative
        ):
            raise DirectoryCaptureError(f"Invalid relative exclusion: {value!r}")
        if SKILL_MD_NAME in path.parts:
            raise DirectoryCaptureError("SKILL.md cannot be excluded from a folder upload")
        normalized = path.as_posix()
        if "/" in normalized:
            exclude_prefixes.add(normalized)
        else:
            exclusions.add(normalized)

    # Capture all files
    extra_files: list[CapturedFile] = []
    excluded_paths: list[str] = []
    warnings: list[str] = []
    total_size = len(skill_md_content.encode("utf-8"))
    seen_paths: dict[str, str] = {}  # case-folded -> original

    for file_path in sorted(source_dir.rglob("*")):
        rel_path = file_path.relative_to(source_dir).as_posix()
        normalized_rel_path = unicodedata.normalize("NFC", rel_path)
        parts = PurePosixPath(normalized_rel_path).parts
        # Git metadata is never an authored skill resource. Report its root,
        # not every object in a local checkout.
        if ".git" in parts:
            if rel_path == ".git":
                excluded_paths.append(".git (Git metadata)")
            continue
        if any(segment in exclusions for segment in parts) or any(
            normalized_rel_path == prefix or normalized_rel_path.startswith(prefix + "/") for prefix in exclude_prefixes
        ):
            excluded_paths.append(rel_path)
            continue
        if file_path.is_symlink():
            raise DirectoryCaptureError(f"Symlink cannot be captured: {rel_path}")
        if file_path.is_dir():
            continue
        if not file_path.is_file():
            raise DirectoryCaptureError(f"Special file cannot be captured: {rel_path}")
        if rel_path == SKILL_MD_NAME:
            continue

        # Skip hidden files unless requested
        if not include_hidden and any(p.startswith(".") and p != "." for p in parts):
            if not any(p == ".git" for p in parts):  # .git is always excluded
                excluded_paths.append(f"{rel_path} (hidden)")
            continue

        # Validate path
        try:
            normalized_path = _normalize_path(rel_path)
        except BundleValidationError as e:
            raise DirectoryCaptureError(f"Invalid path '{rel_path}': {e}") from e

        # Case-fold collision check
        fold_key = _case_fold_key(normalized_path)
        if fold_key in seen_paths:
            raise DirectoryCaptureError(
                f"Path collision (case-insensitive): '{normalized_path}' vs '{seen_paths[fold_key]}'"
            )
        seen_paths[fold_key] = normalized_path

        # Read content
        try:
            content = file_path.read_bytes()
        except OSError as e:
            raise DirectoryCaptureError(f"Cannot read '{rel_path}': {e}") from e

        # Size checks
        if len(content) > MAX_FILE_SIZE:
            raise DirectoryCaptureError(f"File too large ({len(content):,} > {MAX_FILE_SIZE:,}): {rel_path}")

        total_size += len(content)
        if total_size > MAX_TREE_SIZE:
            raise DirectoryCaptureError(f"Total size exceeds {MAX_TREE_SIZE:,} bytes at file: {rel_path}")

        # Check file count
        if len(extra_files) >= MAX_EXTRA_FILES:
            raise DirectoryCaptureError(f"Too many files (limit: {MAX_EXTRA_FILES})")

        # Warn about sensitive files
        if _is_likely_sensitive(rel_path):
            warnings.append(f"Potentially sensitive file: {rel_path}")

        # Check executable bit
        mode = os.stat(file_path).st_mode
        executable = bool(mode & stat.S_IXUSR)

        # Check if binary
        is_binary = _is_binary(content)

        extra_files.append(
            CapturedFile(
                path=normalized_path,
                content=content,
                executable=executable,
                is_binary=is_binary,
            )
        )

    optic.debug(
        "Captured {} files, {} excluded, total {} bytes",
        len(extra_files) + 1,  # +1 for SKILL.md
        len(excluded_paths),
        total_size,
    )

    return DirectorySnapshot(
        skill_md_content=skill_md_content,
        extra_files=tuple(extra_files),
        total_size=total_size,
        excluded_paths=tuple(excluded_paths),
        warnings=tuple(warnings),
    )


def snapshot_to_extra_files(snapshot: DirectorySnapshot) -> list[dict[str, Any]]:
    """Convert a directory snapshot to API extra_files format.

    Args:
        snapshot: Captured directory snapshot

    Returns:
        List of dicts suitable for the folder-drafts API
    """
    result: list[dict[str, Any]] = []

    for captured in snapshot.extra_files:
        entry: dict[str, Any] = {
            "path": captured.path,
        }

        if captured.is_binary:
            # Binary files use base64 encoding
            entry["content"] = base64.b64encode(captured.content).decode("ascii")
            entry["encoding"] = "base64"
        else:
            # Text files use UTF-8
            entry["content"] = captured.content.decode("utf-8")

        if captured.executable:
            entry["executable"] = True

        result.append(entry)

    return result
