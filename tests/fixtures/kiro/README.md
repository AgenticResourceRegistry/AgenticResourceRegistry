<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Kiro hook fixtures

`legacy-agent.json` is a synthetic, sanitized V2 profile with session-push hooks
and a user-owned stop hook. `universal-agent.json` is the unmodified output of
`kiro-cli agent upgrade <dir>` (Kiro CLI 2.35.0) on that profile. No real user
configuration or session data is included. The upgrade converts milliseconds
to seconds and adds permissions and upgrade-state metadata.
