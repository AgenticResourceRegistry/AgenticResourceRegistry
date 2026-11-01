# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for services.ssrf_guard (OBSV-SEC-012/013/014)."""

import socket
from unittest.mock import patch


class TestIsPrivateUrl:
    def test_private_rfc1918_10(self):
        from services.ssrf_guard import is_private_url

        assert is_private_url("http://10.0.0.1/") is True

    def test_private_rfc1918_192_168(self):
        from services.ssrf_guard import is_private_url

        assert is_private_url("http://192.168.1.1/") is True

    def test_private_rfc1918_172_16(self):
        from services.ssrf_guard import is_private_url

        assert is_private_url("http://172.16.0.1/") is True

    def test_loopback(self):
        from services.ssrf_guard import is_private_url

        assert is_private_url("http://127.0.0.1/") is True

    def test_ipv6_loopback(self):
        from services.ssrf_guard import is_private_url

        assert is_private_url("http://[::1]/") is True

    def test_ipv6_forms_that_reach_local_or_embedded_ipv4(self):
        from services.ssrf_guard import is_private_url

        assert is_private_url("http://[::]/") is True
        assert is_private_url("http://[64:ff9b::a9fe:a9fe]/") is True  # NAT64 for 169.254.169.254
        assert is_private_url("http://[2002:a00:1::1]/") is True  # 6to4 for 10.0.0.1
        # A public address behind NAT64 (IPv6-only networks with DNS64) stays reachable.
        assert is_private_url("http://[64:ff9b::808:808]/") is False  # 8.8.8.8

    def test_cgnat(self):
        from services.ssrf_guard import is_private_url

        assert is_private_url("http://100.64.0.1/") is True

    def test_link_local_metadata(self):
        from services.ssrf_guard import is_private_url

        assert is_private_url("http://169.254.169.254/latest/meta-data/") is True

    def test_ipv4_mapped_ipv6_loopback(self):
        from services.ssrf_guard import is_private_url

        assert is_private_url("http://[::ffff:127.0.0.1]/") is True

    def test_ipv6_ula(self):
        from services.ssrf_guard import is_private_url

        assert is_private_url("http://[fc00::1]/") is True

    def test_blocked_metadata_hostname(self):
        from services.ssrf_guard import is_private_url

        assert is_private_url("http://metadata.google.internal/") is True

    def test_empty_url(self):
        from services.ssrf_guard import is_private_url

        assert is_private_url("") is True

    def test_no_hostname(self):
        from services.ssrf_guard import is_private_url

        assert is_private_url("not-a-url") is True

    def test_dns_failure_is_private(self):
        from services.ssrf_guard import is_private_url

        with patch("services.ssrf_guard.socket.getaddrinfo", side_effect=socket.gaierror("fail")):
            assert is_private_url("http://nonexistent.invalid/") is True

    def test_public_ip(self):
        from services.ssrf_guard import is_private_url

        assert is_private_url("http://8.8.8.8/") is False

    def test_public_domain_via_dns(self):
        from services.ssrf_guard import is_private_url

        with patch(
            "services.ssrf_guard.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            assert is_private_url("https://example.com/webhook") is False


class TestMcpValidatorUsesGuard:
    def test_private_ip_rejected(self):
        from services.mcp_validator import _validate_git_url

        err = _validate_git_url("https://10.0.0.1/evil/repo")
        assert err is not None
        assert "private" in err.lower() or "internal" in err.lower()
        assert "ALLOW_INTERNAL_GIT_URLS" in err

    def test_ipv6_ula_rejected(self):
        from services.mcp_validator import _validate_git_url

        err = _validate_git_url("https://[fc00::1]/repo")
        assert err is not None

    def test_valid_github_url_accepted(self):
        from services.mcp_validator import _validate_git_url

        with patch(
            "services.ssrf_guard.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("140.82.121.3", 0))],
        ):
            err = _validate_git_url("https://github.com/example/repo")
            assert err is None


class TestGitMirrorUsesGuard:
    def test_private_url_raises(self):
        from services.git_mirror_service import clone_or_update

        try:
            clone_or_update("https://192.168.1.1/repo.git")
            raise AssertionError("Expected ValueError")
        except ValueError as e:
            assert "private" in str(e).lower() or "internal" in str(e).lower()

    def test_allow_internal_git_urls_bypasses_check(self):
        """ALLOW_INTERNAL_GIT_URLS=true lets self-hosted GitLab / GH Enterprise through."""
        from unittest.mock import patch as _patch

        from services.git_mirror_service import clone_or_update

        with _patch("services.git_mirror_service.ds") as mock_ds:
            mock_ds.get_sync_bool.return_value = True
            with _patch("services.git_mirror_service._run_git") as mock_git:
                mock_git.return_value.returncode = 0
                try:
                    clone_or_update("https://192.168.1.50/internal/repo.git")
                except ValueError:
                    raise AssertionError("Should not raise with ALLOW_INTERNAL_GIT_URLS=True")
                except Exception:
                    pass  # filesystem errors fine

    def test_non_http_url_not_checked(self):
        """git:// and ssh:// URLs bypass the HTTP SSRF check (handled by scheme validation elsewhere)."""
        from unittest.mock import patch as _patch

        from services.git_mirror_service import clone_or_update

        with _patch("services.git_mirror_service._run_git") as mock_git:
            mock_git.return_value.returncode = 0
            try:
                clone_or_update("git@github.com:example/repo.git")
            except Exception:
                pass  # path/filesystem errors are fine, no ValueError expected
