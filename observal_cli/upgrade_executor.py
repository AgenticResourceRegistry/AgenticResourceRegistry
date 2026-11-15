# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""CLI upgrade executor - handles uv/pipx/pip/binary install paths.

Extracted from cmd_ops.py for testability and readability.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import tempfile
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlparse

import httpx
import typer
from rich import print as rprint

from observal_cli import config
from observal_cli.constants import REDIRECT_ALLOWLIST
from observal_cli.install_detector import SOURCE_SPEC, InstallInfo, InstallMethod, install_spec, pip_install_prefix

GITHUB_REPO = "AgenticResourceRegistry/AgenticResourceRegistry"


def execute(
    install_info: InstallInfo,
    target_version: str,
    direction: str,
    spinner,
    *,
    interactive: bool = True,
) -> None:
    """Execute the actual version change for uv/pip/binary installs.

    Args:
        install_info: Detected install method and path.
        target_version: Target version string (e.g. "0.7.0").
        direction: "upgrade" or "downgrade".
        spinner: Context manager for progress display.
        interactive: Whether checksum fallback may ask for confirmation.
    """
    if install_info.method == InstallMethod.UV_TOOL:
        _install_via_uv(target_version, direction, spinner)
    elif install_info.method == InstallMethod.PIPX:
        _install_via_pipx(target_version, direction, spinner)
    elif install_info.method == InstallMethod.PIP:
        _install_via_pip(target_version, direction, spinner, install_info)
    elif install_info.method == InstallMethod.BINARY:
        _install_binary(install_info, target_version, direction, spinner, interactive=interactive)
    else:
        rprint(f"[red]Cannot {direction} - unsupported install method: {install_info.method.value}[/red]")
        raise typer.Exit(1)

    # Post-install verification
    _verify_install(install_info, target_version, direction)


def _install_via_uv(target_version: str, direction: str, spinner) -> None:
    _install_with_source_fallback(
        lambda spec: ["uv", "tool", "install", spec, "--force"], target_version, direction, spinner
    )


def _install_via_pipx(target_version: str, direction: str, spinner) -> None:
    _install_with_source_fallback(lambda spec: ["pipx", "install", spec, "--force"], target_version, direction, spinner)


def _install_via_pip(target_version: str, direction: str, spinner, install_info: InstallInfo) -> None:
    prefix = pip_install_prefix(install_info)
    _install_with_source_fallback(lambda spec: [*prefix, spec, "--quiet"], target_version, direction, spinner)


def _install_with_source_fallback(
    argv_for, target_version: str, direction: str, spinner, repo: str | None = None
) -> None:
    """Install from PyPI; if the release is missing there, build its git tag; else leave the install alone."""
    from observal_cli.install_detector import SOURCE_REPO, tag_spec

    specs = [install_spec(target_version)]
    if specs[0] != SOURCE_SPEC:
        specs.append(tag_spec(target_version, repo or SOURCE_REPO))
    errors = []
    for spec in specs:
        with spinner(f"{direction.removesuffix('e').capitalize()}ing to v{target_version}..."):
            result = subprocess.run(argv_for(spec), capture_output=True, text=True, timeout=300 if "@" in spec else 120)
        if result.returncode == 0:
            return
        errors.append(f"{spec}: {result.stderr.strip()}")
    rprint(f"[red]{direction.capitalize()} failed; the installed CLI was left unchanged.[/red]")
    for error in errors:
        rprint(f"  {error}")
    raise typer.Exit(1)


def _install_binary(
    install_info: InstallInfo,
    target_version: str,
    direction: str,
    spinner,
    *,
    interactive: bool = True,
) -> None:
    """Download and install standalone binary with checksum verification."""
    if install_spec(target_version) == SOURCE_SPEC:
        rprint(
            f"[red]v{target_version} is a development build with no standalone binary.[/red]\n"
            f"Install it from source: uv tool install --force {SOURCE_SPEC}"
        )
        raise typer.Exit(1)
    system = platform.system().lower()
    machine = platform.machine().lower()

    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        rprint(f"[red]Unsupported architecture: {machine}[/red]")
        raise typer.Exit(1)

    os_name = {"linux": "linux", "darwin": "macos", "windows": "windows"}.get(system)
    if not os_name:
        rprint(f"[red]Unsupported OS: {system}[/red]")
        raise typer.Exit(1)

    suffix = ".exe" if system == "windows" else ""
    artifact_name = f"observal-{os_name}-{arch}{suffix}"

    # Fetch release by tag
    with spinner("Fetching release info..."):
        resp = httpx.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/v{target_version}",
            timeout=15,
            headers={"Accept": "application/vnd.github+json"},
        )
    if resp.status_code != HTTPStatus.OK:
        rprint(f"[red]Release v{target_version} not found on GitHub.[/red]")
        raise typer.Exit(1)

    release_data = resp.json()
    assets = {a["name"]: a["browser_download_url"] for a in release_data.get("assets", [])}

    if artifact_name not in assets:
        rprint(f"[red]Binary '{artifact_name}' not found in release assets.[/red]")
        raise typer.Exit(1)

    # Download checksums
    checksums = _fetch_checksums(assets)

    # Download binary with redirect validation
    bin_content = _download_binary(assets[artifact_name], spinner, artifact_name)

    # Verify checksum
    _verify_checksum(bin_content, checksums, artifact_name, interactive=interactive)

    # Backup and replace
    _replace_binary(install_info, bin_content, target_version, system, suffix)


def _fetch_checksums(assets: dict[str, str]) -> dict[str, str]:
    """Download checksums.txt from release assets."""
    checksums: dict[str, str] = {}
    if "checksums.txt" in assets:
        ck_resp = httpx.get(assets["checksums.txt"], timeout=15, follow_redirects=True)
        if ck_resp.status_code == HTTPStatus.OK:
            expected_checksum_parts = 2
            for line in ck_resp.text.strip().splitlines():
                parts = line.split()
                if len(parts) == expected_checksum_parts:
                    checksums[parts[1]] = parts[0]
    return checksums


def _download_binary(download_url: str, spinner, artifact_name: str) -> bytes:
    """Download binary with redirect domain validation."""
    with spinner(f"Downloading {artifact_name}..."):
        bin_resp = httpx.get(download_url, timeout=120, follow_redirects=True)

    if bin_resp.status_code != HTTPStatus.OK:
        rprint("[red]Download failed.[/red]")
        raise typer.Exit(1)

    # Validate final URL domain
    final_host = urlparse(str(bin_resp.url)).hostname
    if final_host and final_host not in REDIRECT_ALLOWLIST:
        rprint(f"[red]Download redirected to untrusted host: {final_host}[/red]")
        raise typer.Exit(1)

    return bin_resp.content


def _verify_checksum(
    content: bytes,
    checksums: dict[str, str],
    artifact_name: str,
    *,
    interactive: bool = True,
) -> None:
    """Verify SHA-256 checksum. Aborts on mismatch."""
    actual_hash = hashlib.sha256(content).hexdigest()
    expected_hash = checksums.get(artifact_name)
    if expected_hash:
        if actual_hash != expected_hash:
            rprint("[red]CHECKSUM MISMATCH - download may be corrupted or tampered.[/red]")
            rprint(f"  Expected: {expected_hash}")
            rprint(f"  Got:      {actual_hash}")
            raise typer.Exit(1)
        rprint(f"[dim]SHA-256 verified: {actual_hash[:16]}...[/dim]")
    else:
        rprint("[yellow]No checksum available for verification.[/yellow]")
        if not interactive:
            rprint("[red]Non-interactive installation requires a published checksum.[/red]")
            raise typer.Exit(1)
        if not typer.confirm("Install without verification?", default=False):
            raise typer.Abort()


def _replace_binary(install_info: InstallInfo, content: bytes, target_version: str, system: str, suffix: str) -> None:
    """Backup current binary and atomically replace with new one."""
    target_path = install_info.path
    if not install_info.writable:
        rprint(f"[red]Cannot write to {target_path} - permission denied.[/red]")
        raise typer.Exit(1)

    # Backup current
    backup_dir = config.CONFIG_DIR / "bin"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / "observal.prev"
    if target_path.exists():
        shutil.copy2(str(target_path), str(backup_path))

    # Write to temp file then atomic rename
    fd, tmp_path = tempfile.mkstemp(
        dir=target_path.parent,
        prefix=".observal-update-",
        suffix=suffix,
    )
    try:
        os.write(fd, content)
        os.close(fd)
        os.chmod(tmp_path, 0o755)

        if system == "windows":
            old_path = target_path.with_suffix(".old")
            target_path.rename(old_path)
            Path(tmp_path).rename(target_path)
        else:
            Path(tmp_path).rename(target_path)
    except OSError as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        rprint(f"[red]Failed to replace binary: {e}[/red]")
        raise typer.Exit(1)


def _verify_install(install_info: InstallInfo, target_version: str, direction: str) -> None:
    """Verify that the updated executable reports the requested version."""
    from packaging.version import InvalidVersion, Version

    executable = str(install_info.path)
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
    except (subprocess.TimeoutExpired, OSError) as exc:
        rprint(f"[red]{direction.capitalize()} verification failed:[/red] {exc}")
        rprint(f"[dim]Executable: {executable}[/dim]")
        raise typer.Exit(1) from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        rprint(f"[red]{direction.capitalize()} verification failed:[/red] {detail}")
        rprint(f"[dim]Executable: {executable}[/dim]")
        raise typer.Exit(1)

    output = result.stdout.strip() or result.stderr.strip()
    match = re.search(r"(?<![\w.])v?(\d+(?:\.\d+){2}(?:[-+][0-9A-Za-z.-]+)?)", output)
    if not match:
        rprint(f"[red]{direction.capitalize()} verification failed:[/red] could not parse version from {output!r}")
        rprint(f"[dim]Executable: {executable}[/dim]")
        raise typer.Exit(1)

    try:
        actual = Version(match.group(1))
        expected = Version(target_version)
    except InvalidVersion as exc:
        rprint(f"[red]{direction.capitalize()} verification failed:[/red] invalid version output {output!r}")
        rprint(f"[dim]Executable: {executable}[/dim]")
        raise typer.Exit(1) from exc

    if actual != expected:
        rprint(
            f"[red]{direction.capitalize()} verification failed:[/red] "
            f"expected v{expected}, but {executable} reports v{actual}."
        )
        rprint("[dim]Check for multiple Observal installations on PATH.[/dim]")
        raise typer.Exit(1)

    rprint(f"[green]{direction.capitalize()}d to v{actual}[/green]")
