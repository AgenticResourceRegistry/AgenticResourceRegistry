# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Call a remote A2A agent over the JSON-RPC binding.

The endpoint comes from the Agent Card a reviewer approved (pinned in the
discovery entry), never from a card fetched at call time. The call goes
straight to the agent; Observal's server does not proxy it.

Speaks A2A v1.0 (``SendMessage``/``GetTask``/``CancelTask``, ``A2A-Version``
header) and falls back to the v0.3 method names (``message/send`` ...) for
cards that declare a 0.x protocol version.

Credentials are never stored by Observal. When the card declares security
schemes, the token is read from ``OBSERVAL_A2A_TOKEN_<HOST>``, named after the
host of the endpoint it is sent to (``agents.acme.com`` reads
``OBSERVAL_A2A_TOKEN_AGENTS_ACME_COM``), and sent as the scheme asks. A token
therefore only ever reaches the host it is named for; there is no catch-all.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from observal_cli.delegation import tasks

if TYPE_CHECKING:
    from collections.abc import Callable

REQUEST_TIMEOUT = 60.0
POLL_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 30 * 60


class A2aError(RuntimeError):
    """A failure whose message is safe to show the calling agent."""


_HOST_RE = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")


def token_env_name(url: str) -> str | None:
    """The variable holding the token for the endpoint ``url``, named after its host.

    ``.`` becomes ``_`` and ``-`` becomes ``__``, so two hosts never share a name
    (``agents-acme.com`` is ``AGENTS__ACME_COM``, ``agents.acme.com`` is
    ``AGENTS_ACME_COM``). Hosts with any other character get no token.
    """
    host = (urlparse(url).hostname or "").lower()
    if not _HOST_RE.fullmatch(host) or ".." in host:
        return None
    return "OBSERVAL_A2A_TOKEN_" + host.replace("-", "__").replace(".", "_").upper()


def auth_headers(card: dict, url: str) -> dict[str, str]:
    """Headers for the first security scheme the card declares that we can satisfy from the environment."""
    schemes = card.get("securitySchemes") if isinstance(card.get("securitySchemes"), dict) else {}
    if not schemes:
        return {}
    name = token_env_name(url)
    token = os.environ.get(name) if name else None
    if not token:
        return {}
    for scheme in schemes.values():
        if not isinstance(scheme, dict):
            continue
        # v1.0 wraps each scheme in a oneof ({"apiKeySecurityScheme": {...}}); v0.3 uses {"type": ...}.
        api_key = scheme.get("apiKeySecurityScheme") or (scheme if scheme.get("type") == "apiKey" else None)
        if isinstance(api_key, dict) and str(api_key.get("in", api_key.get("location", ""))).lower() == "header":
            return {str(api_key.get("name") or "X-API-Key"): token}
    return {"Authorization": f"Bearer {token}"}


# Plain http is acceptable for an agent on this machine, never for credentials sent elsewhere.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class A2aClient:
    def __init__(
        self,
        interface: dict,
        card: dict,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = str(interface.get("url") or "")
        if not self.url.startswith(("https://", "http://")):
            raise A2aError("The approved Agent Card has no callable JSON-RPC endpoint.")
        version = str(interface.get("protocolVersion") or card.get("protocolVersion") or "1.0")
        self.legacy = version.startswith("0.")
        self.tenant = interface.get("tenant")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if not self.legacy:
            headers["A2A-Version"] = version if version[:1].isdigit() else "1.0"
        credentials = auth_headers(card, self.url)
        endpoint = urlparse(self.url)
        if credentials and endpoint.scheme != "https" and (endpoint.hostname or "").lower() not in LOOPBACK_HOSTS:
            raise A2aError(
                "The approved Agent Card uses plain http, so Observal will not send your token to it. "
                "Ask the agent's owner to serve it over https."
            )
        headers.update(credentials)
        self._http = httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers, transport=transport, follow_redirects=False)

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, params: dict, *, timeout: float = REQUEST_TIMEOUT) -> Any:
        payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}
        try:
            response = self._http.post(self.url, json=payload, timeout=timeout)
        except httpx.HTTPError:
            raise A2aError("The remote agent could not be reached.") from None
        if response.status_code in (401, 403):
            raise A2aError(
                f"The remote agent refused the credentials (HTTP {response.status_code}). "
                f"Set {token_env_name(self.url) or 'a token variable'} to a valid token for it."
            )
        try:
            body = response.json()
        except ValueError:
            raise A2aError(f"The remote agent returned HTTP {response.status_code} without JSON.") from None
        if not isinstance(body, dict):
            raise A2aError("The remote agent returned a malformed JSON-RPC response.")
        if body.get("error"):
            error = body["error"] if isinstance(body["error"], dict) else {}
            raise A2aError(f"The remote agent returned an error: {str(error.get('message') or 'unknown')[:300]}")
        return body.get("result")

    # ── Methods ──────────────────────────────────────────────────────────

    def _message(self, text: str, *, task_id: str | None, context_id: str | None) -> dict:
        if self.legacy:
            message: dict[str, Any] = {
                "kind": "message",
                "messageId": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
            }
        else:
            message = tasks.text_message("ROLE_USER", text)
        if task_id:
            message["taskId"] = task_id
        if context_id:
            message["contextId"] = context_id
        return message

    def send(self, text: str, *, task_id: str | None = None, context_id: str | None = None) -> dict:
        """Send a message without blocking on completion. Returns a normalised remote task."""
        message = self._message(text, task_id=task_id, context_id=context_id)
        if self.legacy:
            result = self._call("message/send", {"message": message, "configuration": {"blocking": False}})
        else:
            params: dict[str, Any] = {"message": message, "configuration": {"returnImmediately": True}}
            if self.tenant:
                params["tenant"] = self.tenant
            result = self._call("SendMessage", params)
        return normalize_result(result)

    def get(self, task_id: str) -> dict:
        params: dict[str, Any] = {"id": task_id}
        if self.tenant and not self.legacy:
            params["tenant"] = self.tenant
        return normalize_result(self._call("tasks/get" if self.legacy else "GetTask", params))

    def cancel(self, task_id: str) -> dict:
        params: dict[str, Any] = {"id": task_id}
        if self.tenant and not self.legacy:
            params["tenant"] = self.tenant
        # Short: a cancel runs inside an MCP tool call, which clients time out (Codex: 60 s).
        return normalize_result(self._call("tasks/cancel" if self.legacy else "CancelTask", params, timeout=10.0))


def _normalize_parts(parts: Any) -> list[dict]:
    out: list[dict] = []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        clean = {k: v for k, v in part.items() if k != "kind"}
        if "file" in clean and isinstance(clean["file"], dict):  # v0.3 file part
            clean = {"url": clean["file"].get("uri"), "mediaType": clean["file"].get("mimeType")}
        out.append(clean)
    return out


def normalize_result(result: Any) -> dict:
    """Turn a SendMessage/GetTask result (v1.0 or v0.3) into one task-shaped dict.

    A bare Message result means the agent answered without creating a task;
    it is reported as a completed task whose artifact is that answer.
    """
    if not isinstance(result, dict):
        raise A2aError("The remote agent returned an empty result.")
    if "task" in result and isinstance(result["task"], dict):
        result = result["task"]
    elif "message" in result and isinstance(result["message"], dict) and "status" not in result:
        result = {**result["message"], "kind": "message"}

    if result.get("kind") == "message" or ("parts" in result and "status" not in result):
        return {
            "id": result.get("taskId"),
            "contextId": result.get("contextId"),
            "status": {"state": tasks.STATE_COMPLETED},
            "artifacts": [{"artifactId": "result", "name": "result", "parts": _normalize_parts(result.get("parts"))}],
        }

    status = result.get("status") if isinstance(result.get("status"), dict) else {}
    normalized_status: dict[str, Any] = {"state": tasks.normalize_state(status.get("state"))}
    if isinstance(status.get("message"), dict):
        normalized_status["message"] = {**status["message"], "parts": _normalize_parts(status["message"].get("parts"))}
    artifacts = []
    for artifact in result.get("artifacts") or []:
        if isinstance(artifact, dict):
            artifacts.append({**artifact, "parts": _normalize_parts(artifact.get("parts"))})
    return {
        "id": result.get("id"),
        "contextId": result.get("contextId"),
        "status": normalized_status,
        "artifacts": artifacts,
    }


def _merge(task: dict, remote: dict) -> None:
    m = tasks.meta(task)
    if remote.get("id"):
        m["remoteTaskId"] = remote["id"]
    if remote.get("contextId"):
        m["remoteContextId"] = remote["contextId"]
    status = remote["status"]
    task["status"] = {**status, "timestamp": tasks.now_iso()}
    if remote.get("artifacts"):
        task["artifacts"] = remote["artifacts"]


def _cancel_remote(remote_client: A2aClient, task: dict) -> dict:
    """Cancel on the remote side too; the status says when the remote agent did not confirm."""
    text = "Canceled by the caller."
    remote_id = tasks.meta(task).get("remoteTaskId")
    if remote_id:
        try:
            _merge(task, remote_client.cancel(remote_id))
        except A2aError as exc:
            text = f"Canceled by the caller, but the remote agent did not confirm: {exc}"
    return tasks.set_status(task, tasks.STATE_CANCELED, text)


def cancel(task: dict, *, entry: dict, transport: httpx.BaseTransport | None = None) -> dict:
    """Cancel a remote task that is waiting for input; no worker is running for it."""
    try:
        remote_client = A2aClient(
            entry.get("obs:a2aInterface") or {}, entry.get("obs:agentCard") or {}, transport=transport
        )
    except A2aError as exc:
        return tasks.set_status(
            task, tasks.STATE_CANCELED, f"Canceled by the caller, but the remote agent was not told: {exc}"
        )
    try:
        return _cancel_remote(remote_client, task)
    finally:
        remote_client.close()


def run(
    task: dict,
    *,
    entry: dict,
    save: Callable[[dict], object],
    should_cancel: Callable[[], bool],
    reply: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """Send the task (or a reply to a task waiting for input) and poll until it settles."""
    m = tasks.meta(task)
    interface = entry.get("obs:a2aInterface") or {}
    card = entry.get("obs:agentCard") or {}
    try:
        remote_client = A2aClient(interface, card, transport=transport)
    except A2aError as exc:
        return tasks.set_status(task, tasks.STATE_FAILED, str(exc))
    try:
        if should_cancel():  # canceled before anything reached the remote agent
            return tasks.set_status(task, tasks.STATE_CANCELED, "Canceled by the caller.")
        text = reply if reply is not None else tasks.message_text((task.get("history") or [{}])[0])
        remote = remote_client.send(text, task_id=m.get("remoteTaskId"), context_id=m.get("remoteContextId"))
        _merge(task, remote)
        m["remoteUrl"] = remote_client.url
        if not tasks.is_final(task):  # a settled state is saved once, by the worker, after this returns
            save(task)
        deadline = time.monotonic() + timeout
        while not tasks.is_final(task):
            if should_cancel():
                return _cancel_remote(remote_client, task)
            if time.monotonic() > deadline:
                return tasks.set_status(task, tasks.STATE_FAILED, f"Timed out after {int(timeout)} seconds.")
            if not m.get("remoteTaskId"):
                return tasks.set_status(task, tasks.STATE_FAILED, "The remote agent returned no task to follow.")
            time.sleep(POLL_SECONDS)
            _merge(task, remote_client.get(m["remoteTaskId"]))
            if not tasks.is_final(task):
                save(task)
        return task
    except A2aError as exc:
        return tasks.set_status(task, tasks.STATE_FAILED, str(exc))
    finally:
        remote_client.close()
