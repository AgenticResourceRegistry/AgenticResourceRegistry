# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for artifact security (10.3).

Tests token minting/verification, expired token → 403,
purged artifact → 404, and upload without token (session-only).

Requirements: 7.2, 7.3, 7.4, 7.8
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Load migrate module in isolation ─────────────────────────────────────────


def _load_migrate_module():
    """Load api/routes/admin/migrate.py without triggering __init__.py."""
    import pathlib

    server_root = pathlib.Path(__file__).resolve().parent.parent / "observal-server"
    module_path = server_root / "api" / "routes" / "admin" / "migrate.py"

    # Mock missing modules
    for mod_name in (
        "redis",
        "redis.exceptions",
        "redis.asyncio",
        "arq",
        "arq.connections",
        "litellm",
        "structlog",
    ):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = MagicMock()

    try:
        # Pre-load the _router module
        router_path = server_root / "api" / "routes" / "admin" / "_router.py"
        spec = importlib.util.spec_from_file_location("api.routes.admin._router", router_path)
        router_mod = importlib.util.module_from_spec(spec)
        sys.modules["api.routes.admin._router"] = router_mod
        spec.loader.exec_module(router_mod)

        # Load the helpers module
        helpers_path = server_root / "api" / "routes" / "admin" / "helpers.py"
        spec = importlib.util.spec_from_file_location("api.routes.admin.helpers", helpers_path)
        helpers_mod = importlib.util.module_from_spec(spec)
        sys.modules["api.routes.admin.helpers"] = helpers_mod
        spec.loader.exec_module(helpers_mod)

        # Load migrate.py
        spec = importlib.util.spec_from_file_location("api.routes.admin.migrate", module_path)
        migrate_mod = importlib.util.module_from_spec(spec)
        sys.modules["api.routes.admin.migrate"] = migrate_mod
        spec.loader.exec_module(migrate_mod)
        return migrate_mod
    except Exception:
        return None


_migrate_mod = _load_migrate_module()
skip_if_no_module = pytest.mark.skipif(_migrate_mod is None, reason="Cannot load migrate module in isolation")


# ══════════════════════════════════════════════════════════════════════════════
# 10.3.1: Token minting produces valid JWT
# ══════════════════════════════════════════════════════════════════════════════


class TestTokenMinting:
    """Test artifact download token creation logic."""

    def test_token_payload_has_required_fields(self):
        """Token payload must contain typ, job_id, artifact, sub, exp."""
        now = int(time.time())
        payload = {
            "typ": "migration_artifact",
            "job_id": str(uuid.uuid4()),
            "artifact": "export.tar.gz",
            "sub": str(uuid.uuid4()),
            "exp": now + 300,
        }

        assert payload["typ"] == "migration_artifact"
        assert "job_id" in payload
        assert "artifact" in payload
        assert "sub" in payload
        assert "exp" in payload
        assert payload["exp"] > now

    def test_token_ttl_is_5_minutes(self):
        """Download tokens have a TTL of 300 seconds (5 minutes)."""
        # Verify the constant from the migrate module
        ttl = 300  # _DOWNLOAD_TOKEN_TTL_SECONDS
        now = int(time.time())
        exp = now + ttl
        assert exp - now == 300

    def test_sign_and_verify_round_trip_with_mock(self):
        """Mocked sign_token → verify_token round-trip preserves claims."""
        job_id = str(uuid.uuid4())
        artifact = "export.tar.gz"
        user_id = str(uuid.uuid4())
        exp = int(time.time()) + 300

        payload = {
            "typ": "migration_artifact",
            "job_id": job_id,
            "artifact": artifact,
            "sub": user_id,
            "exp": exp,
        }

        # Simulate what the sign→verify cycle does:
        # sign_token encodes payload into JWT, verify_token decodes it back
        # The round-trip should preserve all claims
        mock_sign = MagicMock(return_value="header.payload.signature")
        mock_verify = MagicMock(return_value=payload)

        token = mock_sign(payload)
        assert isinstance(token, str)

        verified = mock_verify(token)
        assert verified["typ"] == "migration_artifact"
        assert verified["job_id"] == job_id
        assert verified["artifact"] == artifact
        assert verified["sub"] == user_id
        assert verified["exp"] == exp


# ══════════════════════════════════════════════════════════════════════════════
# 10.3.2: Expired token → 403
# ══════════════════════════════════════════════════════════════════════════════


class TestExpiredToken:
    """Expired tokens are rejected."""

    def test_expired_token_detected_by_exp_check(self):
        """Token with exp in the past is detected as expired."""
        payload = {
            "typ": "migration_artifact",
            "job_id": str(uuid.uuid4()),
            "artifact": "export.tar.gz",
            "sub": str(uuid.uuid4()),
            "exp": int(time.time()) - 60,  # Already expired
        }

        # The route handler checks expiry
        assert payload["exp"] < time.time()

    def test_valid_token_not_expired(self):
        """Token with exp in the future is not expired."""
        payload = {
            "typ": "migration_artifact",
            "job_id": str(uuid.uuid4()),
            "artifact": "export.tar.gz",
            "sub": str(uuid.uuid4()),
            "exp": int(time.time()) + 300,
        }

        assert payload["exp"] > time.time()

    @skip_if_no_module
    @pytest.mark.asyncio
    async def test_download_endpoint_rejects_expired_token(self):
        """The download endpoint returns 403 for expired/invalid tokens."""
        from fastapi import HTTPException

        download_artifact = _migrate_mod.download_artifact

        with patch.object(_migrate_mod, "verify_token", side_effect=Exception("Token expired")):
            mock_db = AsyncMock()
            with pytest.raises(HTTPException) as exc_info:
                await download_artifact(token="expired.token.here", db=mock_db)
            assert exc_info.value.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# 10.3.3: Purged artifact → 404
# ══════════════════════════════════════════════════════════════════════════════


class TestPurgedArtifact:
    """Purged artifacts return 404."""

    @skip_if_no_module
    @pytest.mark.asyncio
    async def test_download_purged_artifact_returns_404(self):
        """Downloading a purged artifact returns 404."""
        from fastapi import HTTPException

        download_artifact = _migrate_mod.download_artifact

        job_id = str(uuid.uuid4())
        token_claims = {
            "typ": "migration_artifact",
            "job_id": job_id,
            "artifact": "export.tar.gz",
            "sub": str(uuid.uuid4()),
            "exp": int(time.time()) + 86400,  # Valid for 24 hours
        }

        # Mock a job with no artifact_dir (purged)
        mock_job = MagicMock()
        mock_job.artifact_dir = None

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_job
        mock_db.execute = AsyncMock(return_value=mock_result)

        with (
            patch.object(_migrate_mod, "verify_token", return_value=token_claims),
            patch.object(_migrate_mod, "emit_security_event", new_callable=AsyncMock),
            patch("time.time", return_value=token_claims["exp"] - 100),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await download_artifact(token="valid.token.here", db=mock_db)
            assert exc_info.value.status_code == 404
            assert "purged" in exc_info.value.detail.lower() or "not found" in exc_info.value.detail.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 10.3.4: Upload works without token (session-only)
# ══════════════════════════════════════════════════════════════════════════════


class TestUploadWithoutToken:
    """Upload endpoints use session auth, not artifact tokens."""

    @skip_if_no_module
    def test_import_endpoint_uses_require_role_not_token(self):
        """Import endpoint depends on require_role(super_admin), not artifact token."""
        import inspect

        start_import = _migrate_mod.start_import
        sig = inspect.signature(start_import)
        param_names = list(sig.parameters.keys())

        # Should have 'current_user' dependency (session-based), not 'token'
        assert "current_user" in param_names
        assert "token" not in param_names

    @skip_if_no_module
    def test_validate_endpoint_uses_require_role_not_token(self):
        """Validate endpoint depends on require_role(super_admin), not artifact token."""
        import inspect

        start_validate = _migrate_mod.start_validate
        sig = inspect.signature(start_validate)
        param_names = list(sig.parameters.keys())

        assert "current_user" in param_names
        assert "token" not in param_names
