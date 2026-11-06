# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Validate section envelopes before persisting model output or allowing Apply."""

# Required fields are the contracts requested by sections.py. Additional model
# fields remain allowed; an unrelated first dictionary value is never accepted.
_FIELDS = {
    "what_they_work_on": {"areas": list},
    "interaction_style": {"narrative": str, "key_pattern": str},
    "usage_patterns": {"narrative": str, "top_tasks": list, "tool_distribution": list, "session_profile": dict},
    "what_works": {"intro": str, "strengths": list},
    "friction_analysis": {"intro": str, "categories": list},
    "suggestions": {"config_additions": list, "features_to_try": list, "usage_patterns": list},
    "usage_cost_analysis": {"summary": str, "metrics": dict, "model_breakdown": list, "opportunities": list},
    "version_comparison": {"has_comparison": bool, "summary": str},
    "regression_detection": {"has_previous_data": bool, "summary": str, "changes": list},
    "on_the_horizon": {"intro": str, "opportunities": list},
    "fun_ending": {"headline": str, "detail": str},
    "version_impact": {"summary": str},
    "at_a_glance": {
        "health": str,
        "whats_working": str,
        "whats_hindering": str,
        "quick_win": str,
        "ambitious_workflows": str,
    },
}


def section_valid(name: str, response: object) -> bool:
    """Allow a legitimate empty suggestions set, not a missing/failed section."""
    if not isinstance(response, dict) or name not in response:
        return False
    body = response[name]
    if name == "version_impact" and body is None:
        return True  # No layer comparison was available.
    if not isinstance(body, dict):
        return False
    fields = _FIELDS.get(name)
    if fields is None or any(not isinstance(body.get(key), kind) for key, kind in fields.items()):
        return False
    # Lists rendered as objects must not carry scalar entries that crash views.
    if any(isinstance(body[key], list) and any(not isinstance(item, dict) for item in body[key]) for key in fields):
        return False
    if name == "suggestions":
        for item in body["config_additions"]:
            if not isinstance(item.get("addition"), str) or not item["addition"].strip():
                return False
        for item in body["features_to_try"]:
            if not isinstance(item.get("feature"), str):
                return False
        for item in body["usage_patterns"]:
            if not isinstance(item.get("copyable_prompt"), str):
                return False
    return True


def suggestions_failed(narrative: dict) -> bool:
    """A persisted generation error takes precedence over any partial suggestion text."""
    errors = narrative.get("section_errors") or []
    return any(isinstance(error, dict) and error.get("section") == "suggestions" for error in errors)
