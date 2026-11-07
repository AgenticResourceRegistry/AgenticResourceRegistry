# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent marker reader - reads .observal/agent to attribute sessions."""

import json
from datetime import UTC
from pathlib import Path


def read_agent_marker(cwd: str, session_jsonl: Path | None = None) -> tuple[str | None, str | None]:
    """Return (agent_id, agent_version) from <cwd>/.observal/agent, or (None, None).

    Written by ``observal pull`` so hooks can attribute sessions to the
    pulled agent.  Only applies the pulled_at guard for brand-new sessions
    (cursor offset == 0).
    """
    try:
        marker = Path(cwd) / ".observal" / "agent"
        data = json.loads(marker.read_text())

        pulled_at = data.get("pulled_at")
        if pulled_at and session_jsonl and session_jsonl.exists():
            from datetime import datetime

            # Imported here: base.py imports far more than the harness readers that use this module.
            from observal_cli.sessions.base import highest_delivered_offset

            if highest_delivered_offset(session_jsonl.stem) == 0:
                pull_time = datetime.fromisoformat(pulled_at)
                stat = session_jsonl.stat()
                ctime = getattr(stat, "st_birthtime", None) or stat.st_ctime
                session_ctime = datetime.fromtimestamp(ctime, tz=UTC)
                if session_ctime < pull_time:
                    return None, None

        return data.get("agent_id"), data.get("agent_version")
    except Exception:
        return None, None
