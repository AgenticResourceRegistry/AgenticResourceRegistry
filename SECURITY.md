<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Security Policy

Agentic Resource Registry's [security assurance case](docs/security/assurance-case.md) documents its threat model, trust boundaries, controls, evidence, and residual risks. See [release verification](docs/security/release-verification.md) before running downloaded artifacts.

## Supported Versions

| Version | Supported |
| --- | --- |
| 1.11.x | :white_check_mark: |
| Earlier versions | :x: |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

If you discover a security vulnerability in Agentic Resource Registry, please report it responsibly through one of these channels:

1. **GitHub Private Vulnerability Reporting** (preferred): Go to the [Security Advisories](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/security/advisories) page and click "Report a vulnerability".
2. **Email**: Send details to **contact@observal.io**.

### What to Include

- A description of the vulnerability and its potential impact
- Steps to reproduce the issue
- Affected version(s)
- Any suggested fix, if you have one

### What to Expect

- **Acknowledgement** within 48 hours of your report
- **Status update** within 7 days with an initial assessment
- **Resolution target** within 30 days for confirmed vulnerabilities, depending on complexity

We will coordinate disclosure with you. We ask that you give us reasonable time to address the issue before making it public.

## What Qualifies as a Security Issue

Agentic Resource Registry handles API keys, authentication tokens, and enterprise telemetry data. The following are examples of issues we consider security-relevant:

- Authentication or authorization bypasses
- API key or token exposure
- SQL injection, command injection, or path traversal
- Cross-site scripting (XSS) or cross-site request forgery (CSRF)
- Server-side request forgery (SSRF)
- Insecure defaults that could expose sensitive data
- Dependency vulnerabilities with a known exploit path

If you're unsure whether something counts, report it anyway. We'd rather triage a false positive than miss a real issue.

## Recognition

We appreciate responsible disclosure. Contributors who report valid vulnerabilities will be credited in the release notes (unless they prefer to remain anonymous).
