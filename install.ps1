# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# Observal CLI Installer for Windows (PowerShell 5.1+)
# Usage: irm https://raw.githubusercontent.com/AgenticResourceRegistry/AgenticResourceRegistry/main/install.ps1 | iex
#
# Environment variable overrides:
#   OBSERVAL_VERSION=latest    Version to install (e.g. v0.6.0)
#   OBSERVAL_BIN_DIR=C:\path   Install directory (default: %LOCALAPPDATA%\Programs\Observal)
#   OBSERVAL_BASE_URL=http://  Override download base URL (testing only)
#
# The script body runs inside a function so `irm | iex` never leaks variables
# into, or exits, the caller's session. Failures throw instead of calling exit.

function Install-ObservalCli {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is very slow with the progress bar on

    $GitHubRepo = 'AgenticResourceRegistry/AgenticResourceRegistry'

    function Write-Info([string]$Message) { Write-Host '==> ' -ForegroundColor Blue -NoNewline; Write-Host $Message }
    function Write-Warn([string]$Message) { Write-Host 'WARN: ' -ForegroundColor Yellow -NoNewline; Write-Host $Message }

    # Windows PowerShell 5.1 defaults to TLS 1.0; GitHub requires TLS 1.2+.
    if ($PSVersionTable.PSVersion.Major -lt 6) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    }

    # ── Resolve options ──────────────────────────────────────

    $Version = if ($env:OBSERVAL_VERSION) { $env:OBSERVAL_VERSION } else { 'latest' }
    $BinDir = if ($env:OBSERVAL_BIN_DIR) { $env:OBSERVAL_BIN_DIR } else { Join-Path $env:LOCALAPPDATA 'Programs\Observal' }
    $BaseUrl = $env:OBSERVAL_BASE_URL

    # ── Detect architecture ──────────────────────────────────

    $Arch = $null
    try {
        $Arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    } catch {
        # Older .NET Framework: fall back to env vars. PROCESSOR_ARCHITEW6432 is set
        # when a 32-bit / emulated process runs on a 64-bit OS.
        $Arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    }
    switch -Regex ($Arch) {
        '^(X64|AMD64)$' { $Arch = 'x64' }
        '^ARM64$' { $Arch = 'arm64' }
        default { throw "Unsupported architecture: $Arch" }
    }

    # ── Resolve version ──────────────────────────────────────

    if ($Version -eq 'latest') {
        $Release = Invoke-RestMethod -UseBasicParsing -Uri "https://api.github.com/repos/$GitHubRepo/releases/latest"
        $Version = $Release.tag_name
        if (-not $Version) { throw 'Could not determine latest version' }
    } elseif (-not $Version.StartsWith('v')) {
        $Version = "v$Version"
    }

    Write-Info "Installing Observal CLI $Version (windows/$Arch)"

    # ── Download and verify ──────────────────────────────────

    $Artifact = "observal-windows-$Arch.exe"
    if ($BaseUrl) {
        $Url = "$BaseUrl/$Artifact"
        $ChecksumUrl = "$BaseUrl/checksums.txt"
    } else {
        $Url = "https://github.com/$GitHubRepo/releases/download/$Version/$Artifact"
        $ChecksumUrl = "https://github.com/$GitHubRepo/releases/download/$Version/checksums.txt"
    }

    $TmpDir = Join-Path ([IO.Path]::GetTempPath()) ("observal-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null
    try {
        $TmpBin = Join-Path $TmpDir $Artifact
        Write-Info "Downloading $Artifact..."
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $TmpBin
        } catch {
            throw "Download failed. Check that $Version exists at https://github.com/$GitHubRepo/releases"
        }

        Write-Info 'Verifying checksum...'
        $TmpSums = Join-Path $TmpDir 'checksums.txt'
        $HaveSums = $true
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $ChecksumUrl -OutFile $TmpSums
        } catch {
            $HaveSums = $false
            Write-Warn 'Could not download checksums -- skipping verification'
        }
        if ($HaveSums) {
            $Line = Get-Content $TmpSums | Where-Object { ($_ -split '\s+')[-1] -eq $Artifact } | Select-Object -First 1
            if ($Line) {
                $Expected = (($Line -split '\s+')[0]).ToLowerInvariant()
                $Actual = (Get-FileHash -Algorithm SHA256 -Path $TmpBin).Hash.ToLowerInvariant()
                if ($Actual -ne $Expected) { throw "Checksum mismatch! Expected: $Expected Got: $Actual" }
                Write-Info 'Checksum verified'
            }
        }

        # ── Install ──────────────────────────────────────────

        New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
        $InstallPath = Join-Path $BinDir 'observal.exe'
        try {
            Move-Item -Path $TmpBin -Destination $InstallPath -Force
        } catch {
            throw "Could not write $InstallPath. Close any running 'observal' processes and retry."
        }
    } finally {
        Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue
    }

    # ── Add to PATH (user scope, no admin needed) ────────────

    $UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $Entries = @($UserPath -split ';' | Where-Object { $_ })
    if ($Entries -notcontains $BinDir) {
        $NewPath = (@($Entries) + $BinDir) -join ';'
        [Environment]::SetEnvironmentVariable('Path', $NewPath, 'User')
        Write-Info "Added $BinDir to your user PATH"
    }
    if (($env:Path -split ';') -notcontains $BinDir) {
        $env:Path = "$env:Path;$BinDir"
    }

    # ── Write install metadata ───────────────────────────────
    # Read by observal_cli/install_detector.py to pick the upgrade strategy.
    # Written as UTF-8 without BOM so Python's json.loads accepts it.

    $ConfigDir = Join-Path $HOME '.observal'
    New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
    $Metadata = [ordered]@{
        method = 'powershell'
        manager = 'powershell'
        path = $InstallPath
        version = $Version
    } | ConvertTo-Json
    try {
        [IO.File]::WriteAllText((Join-Path $ConfigDir 'install.json'), $Metadata, (New-Object Text.UTF8Encoding($false)))
    } catch {
        Write-Warn "Could not write install metadata to $ConfigDir\install.json"
    }

    Write-Info "Installed observal to $InstallPath"
    Write-Info "Run 'observal --version' to verify."
}

Install-ObservalCli
