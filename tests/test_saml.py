# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for SAML service layer."""

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography import x509
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from httpx import ASGITransport, AsyncClient
from lxml import etree

from services import saml

_NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
_METADATA_URL = "https://idp.example.test/metadata"
_SAML_NAMESPACES = 'xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" xmlns:ds="http://www.w3.org/2000/09/xmldsig#"'


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return _NOW.replace(tzinfo=None)
        return _NOW.astimezone(tz)


def _certificate_pem(private_key, not_after: datetime, common_name: str = "test.example") -> str:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(42)
        .not_valid_before(_NOW - timedelta(days=365))
        .not_valid_after(not_after)
        .sign(private_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def _private_key_pem(private_key) -> str:
    return private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _metadata_xml(certs: tuple[str, ...] = (), formats: tuple[str, ...] = ()) -> str:
    cert_nodes = "".join(f"<ds:X509Certificate>{cert}</ds:X509Certificate>" for cert in certs)
    format_nodes = "".join(f"<md:NameIDFormat>{name_id}</md:NameIDFormat>" for name_id in formats)
    return f"<md:EntityDescriptor {_SAML_NAMESPACES}>{cert_nodes}{format_nodes}</md:EntityDescriptor>"


def _stream_response(chunks: list[bytes], *, status_error=None, iteration_error=None):
    response = MagicMock()
    response.raise_for_status.side_effect = status_error

    async def aiter_bytes():
        for chunk in chunks:
            yield chunk
        if iteration_error:
            raise iteration_error

    response.aiter_bytes = aiter_bytes
    return response


def _stream_client(response, *, enter_error=None):
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response, side_effect=enter_error)
    context.__aexit__ = AsyncMock(return_value=False)
    client = MagicMock()
    client.stream.return_value = context
    return client, context


class TestSamlServiceKeyMaterial:
    def test_generated_pair_has_expected_identity_validity_and_rsa_parameters(self):
        private_pem, cert_pem = saml.generate_sp_key_pair(common_name="sp.example.test", validity_days=31)

        private_key = serialization.load_pem_private_key(private_pem.encode(), password=None)
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
        assert isinstance(private_key, rsa.RSAPrivateKey)
        assert private_key.key_size == 2048
        assert private_key.private_numbers().public_numbers.e == 65537
        assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "sp.example.test"
        assert cert.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value == "Observal"
        assert cert.issuer == cert.subject
        assert cert.signature_hash_algorithm.name == "sha256"
        assert cert.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ) == private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        validity = cert.not_valid_after_utc - cert.not_valid_before_utc
        assert timedelta(days=31) <= validity < timedelta(days=31, seconds=2)

    def test_private_key_encryption_round_trip_uses_authenticated_ciphertext(self):
        private_pem, _ = saml.generate_sp_key_pair(common_name="roundtrip.example.test")

        encrypted = saml.encrypt_private_key(private_pem, "correct-password")

        assert encrypted.startswith(saml._ENCRYPTION_PREFIX)
        assert encrypted != private_pem
        assert saml.decrypt_private_key(encrypted, "correct-password") == private_pem

    def test_key_generation_propagates_crypto_library_failure(self, monkeypatch):
        failure = RuntimeError("entropy unavailable")
        generate = MagicMock(side_effect=failure)
        monkeypatch.setattr(saml.rsa, "generate_private_key", generate)

        with pytest.raises(RuntimeError, match="entropy unavailable"):
            saml.generate_sp_key_pair()

        generate.assert_called_once_with(public_exponent=65537, key_size=2048)

    def test_derive_key_uses_exact_pbkdf2_parameters(self, monkeypatch):
        derived = b"d" * 32
        kdf = MagicMock()
        kdf.derive.return_value = derived
        constructor = MagicMock(return_value=kdf)
        monkeypatch.setattr(saml, "PBKDF2HMAC", constructor)

        assert saml._derive_key("pässword", b"fixed-salt") == derived
        kwargs = constructor.call_args.kwargs
        assert isinstance(kwargs["algorithm"], hashes.SHA256)
        assert kwargs["length"] == 32
        assert kwargs["salt"] == b"fixed-salt"
        assert kwargs["iterations"] == 600_000
        kdf.derive.assert_called_once_with("pässword".encode())

    def test_encrypt_private_key_assembles_deterministic_aesgcm_payload(self, monkeypatch):
        salt = bytes(range(16))
        nonce = bytes(range(16, 28))
        ciphertext = b"ciphertext-and-tag"
        random_bytes = MagicMock(side_effect=[salt, nonce])
        derive = MagicMock(return_value=b"k" * 32)
        cipher = MagicMock()
        cipher.encrypt.return_value = ciphertext
        aesgcm = MagicMock(return_value=cipher)
        monkeypatch.setattr(saml.os, "urandom", random_bytes)
        monkeypatch.setattr(saml, "_derive_key", derive)
        monkeypatch.setattr(saml, "AESGCM", aesgcm)

        encrypted = saml.encrypt_private_key("private-key-pem", "secret")

        expected = saml._ENCRYPTION_PREFIX + base64.b64encode(salt + nonce + ciphertext).decode()
        assert encrypted == expected
        assert random_bytes.call_args_list[0].args == (16,)
        assert random_bytes.call_args_list[1].args == (12,)
        derive.assert_called_once_with("secret", salt)
        aesgcm.assert_called_once_with(b"k" * 32)
        cipher.encrypt.assert_called_once_with(nonce, b"private-key-pem", None)

    def test_decrypt_private_key_splits_deterministic_aesgcm_payload(self, monkeypatch):
        salt = bytes(range(16))
        nonce = bytes(range(16, 28))
        ciphertext = b"ciphertext-and-tag"
        encrypted = saml._ENCRYPTION_PREFIX + base64.b64encode(salt + nonce + ciphertext).decode()
        derive = MagicMock(return_value=b"k" * 32)
        cipher = MagicMock()
        cipher.decrypt.return_value = b"private-key-pem"
        aesgcm = MagicMock(return_value=cipher)
        monkeypatch.setattr(saml, "_derive_key", derive)
        monkeypatch.setattr(saml, "AESGCM", aesgcm)

        assert saml.decrypt_private_key(encrypted, "secret") == "private-key-pem"
        derive.assert_called_once_with("secret", salt)
        aesgcm.assert_called_once_with(b"k" * 32)
        cipher.decrypt.assert_called_once_with(nonce, ciphertext, None)

    def test_empty_password_contracts_preserve_plaintext_or_encrypted_value(self, monkeypatch):
        warning = MagicMock()
        monkeypatch.setattr(saml.optic, "warning", warning)
        encrypted = saml._ENCRYPTION_PREFIX + base64.b64encode(b"payload").decode()

        assert saml.encrypt_private_key("plain-key", "") == "plain-key"
        assert saml.decrypt_private_key(encrypted, "") == encrypted
        assert warning.call_count == 2
        assert "empty password" in warning.call_args_list[0].args[0]
        assert "without password" in warning.call_args_list[1].args[0]

    def test_wrong_password_and_truncated_payload_fail_loudly(self, monkeypatch):
        private_pem, _ = saml.generate_sp_key_pair()
        encrypted = saml.encrypt_private_key(private_pem, "correct-password")

        with pytest.raises(InvalidTag):
            saml.decrypt_private_key(encrypted, "wrong-password")

        monkeypatch.setattr(saml, "_derive_key", MagicMock(return_value=b"k" * 32))
        truncated = saml._ENCRYPTION_PREFIX + base64.b64encode(b"short").decode()
        with pytest.raises(ValueError):
            saml.decrypt_private_key(truncated, "password")

    def test_unencrypted_private_key_is_returned_without_crypto_calls(self, monkeypatch):
        derive = MagicMock()
        monkeypatch.setattr(saml, "_derive_key", derive)

        assert saml.decrypt_private_key("legacy-plaintext-key", "password") == "legacy-plaintext-key"
        derive.assert_not_called()


class TestSamlServiceSettingsAndPem:
    def test_build_settings_normalizes_pem_and_selects_protocol_bindings(self):
        settings = saml.build_saml_settings(
            idp_entity_id="https://idp.example.test/entity",
            idp_sso_url="https://idp.example.test/sso",
            idp_slo_url="https://idp.example.test/slo",
            idp_x509_cert="-----BEGIN CERTIFICATE-----\n IDP CERT \n-----END CERTIFICATE-----",
            sp_entity_id="https://sp.example.test/metadata",
            sp_acs_url="https://sp.example.test/acs",
            sp_slo_url="https://sp.example.test/sls",
            sp_private_key="-----BEGIN RSA PRIVATE KEY-----\n SP KEY \n-----END RSA PRIVATE KEY-----",
            sp_x509_cert="-----BEGIN CERTIFICATE-----\n SP CERT \n-----END CERTIFICATE-----",
            strict=False,
        )

        assert settings == {
            "strict": False,
            "debug": False,
            "sp": {
                "entityId": "https://sp.example.test/metadata",
                "assertionConsumerService": {
                    "url": "https://sp.example.test/acs",
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                },
                "x509cert": "SPCERT",
                "privateKey": "SPKEY",
                "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
                "singleLogoutService": {
                    "url": "https://sp.example.test/sls",
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
            },
            "idp": {
                "entityId": "https://idp.example.test/entity",
                "singleSignOnService": {
                    "url": "https://idp.example.test/sso",
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
                "x509cert": "IDPCERT",
                "singleLogoutService": {
                    "url": "https://idp.example.test/slo",
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
            },
            "security": {
                "authnRequestsSigned": True,
                "wantAssertionsSigned": True,
                "wantMessagesSigned": False,
                "wantResponsesSigned": True,
                "wantNameIdEncrypted": False,
                "wantAssertionsEncrypted": False,
                "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
                "digestAlgorithm": "http://www.w3.org/2001/04/xmlenc#sha256",
                "requestedAuthnContext": False,
                "relaxDestinationValidation": False,
                "wantNameId": True,
            },
        }

    def test_build_settings_omits_logout_services_when_urls_are_empty(self):
        settings = saml.build_saml_settings(
            idp_entity_id="idp",
            idp_sso_url="sso",
            idp_x509_cert="idp-cert",
            sp_entity_id="sp",
            sp_acs_url="acs",
            sp_private_key="sp-key",
            sp_x509_cert="sp-cert",
        )

        assert "singleLogoutService" not in settings["sp"]
        assert "singleLogoutService" not in settings["idp"]
        assert settings["strict"] is True

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("", ""),
            ("bare-base64", "bare-base64"),
            ("-----BEGIN CERTIFICATE----- MIIC mzCC -----END CERTIFICATE-----", "MIICmzCC"),
            ("-----BEGIN PRIVATE KEY-----\r\nAA\tBB\n-----END PRIVATE KEY-----", "AABB"),
        ],
    )
    def test_strip_pem_headers_handles_empty_bare_and_single_line_material(self, value, expected):
        assert saml._strip_pem_headers(value) == expected


class TestSamlServiceCertificatesAndMetadata:
    def test_safe_xml_parser_does_not_expand_entities_and_rejects_malformed_xml(self):
        xml = '<!DOCTYPE root [<!ENTITY secret "expanded">]><root>&secret;</root>'
        root = saml._safe_xml_parse(xml)

        assert root.text is None
        assert len(root) == 1
        assert root[0].text == "&secret;"
        with pytest.raises(etree.XMLSyntaxError):
            saml._safe_xml_parse(b"<broken>")

    def test_metadata_extractors_return_nonempty_raw_values(self):
        xml = _metadata_xml(
            certs=("  CERT-A\n", "", "CERT-B"),
            formats=("urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress", " "),
        )

        assert saml._extract_metadata_certs(xml) == ["  CERT-A\n", "CERT-B"]
        assert saml._extract_metadata_nameid_formats(xml) == ["urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"]
        assert saml._extract_metadata_certs("<broken>") is None
        assert saml._extract_metadata_nameid_formats("<broken>") is None

    def test_cert_expiry_skips_empty_and_reports_malformed_certificate(self, monkeypatch):
        monkeypatch.setattr(saml, "datetime", _FrozenDateTime)

        assert saml.check_cert_expiry("", "IdP") is None
        assert saml.check_cert_expiry("not-a-certificate", "SP") == {
            "name": "sp_cert_expiry",
            "label": "SP X.509 certificate expiry",
            "status": "fail",
            "message": "SP X.509 certificate is malformed or could not be parsed.",
            "hint": "Re-import the SP certificate.",
        }

    @pytest.mark.parametrize(
        ("days", "expected"),
        [
            (
                -1,
                {
                    "name": "idp_cert_expiry",
                    "label": "IdP X.509 certificate expiry",
                    "status": "fail",
                    "message": "IdP X.509 certificate expired on 2029-12-31.",
                    "hint": "Rotate the IdP certificate immediately.",
                },
            ),
            (
                6,
                {
                    "name": "idp_cert_expiry",
                    "label": "IdP X.509 certificate expiry",
                    "status": "fail",
                    "message": "IdP X.509 certificate expires within 7 days (on 2030-01-07).",
                    "hint": "Rotate the IdP certificate before it expires.",
                },
            ),
            (
                7,
                {
                    "name": "idp_cert_expiry",
                    "label": "IdP X.509 certificate expiry",
                    "status": "pass",
                },
            ),
        ],
    )
    def test_cert_expiry_distinguishes_expired_near_expiry_and_boundary(self, monkeypatch, days, expected):
        monkeypatch.setattr(saml, "datetime", _FrozenDateTime)
        key = ec.generate_private_key(ec.SECP256R1())
        cert = _certificate_pem(key, _NOW + timedelta(days=days))

        assert saml.check_cert_expiry(cert, "IdP") == expected

    def test_cert_expiry_supports_legacy_cryptography_datetime_api(self, monkeypatch):
        monkeypatch.setattr(saml, "datetime", _FrozenDateTime)
        legacy_cert = SimpleNamespace(not_valid_after=datetime(2031, 1, 1, 12, 0))
        load = MagicMock(return_value=legacy_cert)
        monkeypatch.setattr(saml.x509, "load_pem_x509_certificate", load)

        assert saml.check_cert_expiry("BARE-CERT", "SP")["status"] == "pass"
        load.assert_called_once_with(b"-----BEGIN CERTIFICATE-----\nBARE-CERT\n-----END CERTIFICATE-----")

    @pytest.mark.parametrize(
        ("acs", "frontend", "status"),
        [
            ("https://app.example.test/acs", "https://app.example.test", "pass"),
            ("https://sp.example.test/acs", "https://app.example.test", "fail"),
            ("not-a-url", "https://app.example.test", "pass"),
            ("", "", "pass"),
        ],
    )
    def test_sp_host_consistency_compares_only_present_hostnames(self, acs, frontend, status):
        check = saml.check_sp_host_consistency(acs, frontend)

        assert check["status"] == status
        if status == "fail":
            assert check == {
                "name": "sp_host_consistency",
                "label": "SP ACS host matches frontend",
                "status": "fail",
                "message": (
                    "SP ACS host (sp.example.test) does not match deployment.frontend_url host "
                    f"(app.example.test) {chr(8212)} the IdP will send assertions to sp.example.test but this "
                    "deployment is reachable as app.example.test."
                ),
                "hint": "Either update the SP ACS URL or change deployment.frontend_url so they share the same host.",
            }

    def test_idp_cert_metadata_check_skips_absent_metadata_and_fails_bad_or_empty_metadata(self):
        assert saml.check_idp_cert_against_metadata("CERT", None) is None
        invalid = saml.check_idp_cert_against_metadata("CERT", "<broken>")
        empty = saml.check_idp_cert_against_metadata("CERT", _metadata_xml())

        assert invalid["status"] == "fail"
        assert invalid["message"] == "IdP metadata is not valid XML."
        assert empty["status"] == "fail"
        assert empty["message"] == "IdP metadata contains no X509Certificate elements."

    def test_idp_cert_metadata_check_normalizes_match_and_reports_mismatch(self):
        configured = "-----BEGIN CERTIFICATE-----\nCERT-A\n-----END CERTIFICATE-----"
        metadata = _metadata_xml(certs=(" CERT-A ", "CERT-B"))

        assert saml.check_idp_cert_against_metadata(configured, metadata)["status"] == "pass"
        mismatch = saml.check_idp_cert_against_metadata("CERT-C", metadata)
        assert mismatch["status"] == "fail"
        assert "does not match any signing certificate" in mismatch["message"]
        assert saml.check_idp_cert_against_metadata("", metadata)["status"] == "pass"

    def test_sp_cert_key_match_skips_missing_material_and_reports_parse_or_pair_failure(self):
        private_one, cert_one = saml.generate_sp_key_pair(common_name="one")
        private_two, _ = saml.generate_sp_key_pair(common_name="two")

        assert saml.check_sp_cert_key_match("", private_one) is None
        assert saml.check_sp_cert_key_match(cert_one, "") is None
        malformed = saml.check_sp_cert_key_match("not-a-cert", private_one)
        assert malformed["status"] == "fail"
        assert malformed["message"] == "SP certificate or private key could not be parsed."
        mismatch = saml.check_sp_cert_key_match(cert_one, private_two)
        assert mismatch["status"] == "fail"
        assert "do not match" in mismatch["message"]

    def test_sp_cert_key_match_accepts_bare_cert_and_non_rsa_key(self):
        key = ec.generate_private_key(ec.SECP256R1())
        cert_pem = _certificate_pem(key, _NOW + timedelta(days=30))
        bare_cert = saml._strip_pem_headers(cert_pem)

        assert saml.check_sp_cert_key_match(bare_cert, _private_key_pem(key)) == {
            "name": "sp_cert_key_match",
            "label": "SP cert/key form a valid pair",
            "status": "pass",
        }

    @pytest.mark.parametrize(
        ("metadata", "configured", "expected"),
        [
            (None, "emailAddress", None),
            ("<broken>", "emailAddress", None),
            (_metadata_xml(), "emailAddress", None),
            (
                _metadata_xml(formats=("urn:oasis:names:tc:SAML:1.1:nameid-format:EMAILADDRESS",)),
                " emailAddress ",
                "pass",
            ),
            (
                _metadata_xml(formats=("urn:example:emailAddressSuffix",)),
                "emailAddress",
                "fail",
            ),
        ],
    )
    def test_nameid_format_check_handles_missing_malformed_matching_and_unrelated_values(
        self,
        metadata,
        configured,
        expected,
    ):
        check = saml.check_nameid_format(metadata, configured)

        if expected is None:
            assert check is None
        else:
            assert check["status"] == expected
            if expected == "fail":
                assert "does not advertise" in check["message"]

    def test_nameid_last_segment_strips_and_normalizes(self):
        assert saml._nameid_last_segment("  urn:oasis:NameID-Format:EmailAddress  ") == "emailaddress"


class TestSamlServiceNetworkChecks:
    @pytest.mark.asyncio
    async def test_fetch_metadata_streams_exact_request_and_decodes_invalid_utf8(self):
        response = _stream_response([b"<metadata>", b"\xff", b"</metadata>"])
        client, context = _stream_client(response)

        result = await saml.fetch_idp_metadata_xml(client, _METADATA_URL)

        assert result == "<metadata>�</metadata>"
        client.stream.assert_called_once_with("GET", _METADATA_URL)
        context.__aenter__.assert_awaited_once_with()
        context.__aexit__.assert_awaited_once()
        response.raise_for_status.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_fetch_metadata_rejects_oversized_body_without_reading_success(self):
        response = _stream_response([b"x" * (saml._MAX_METADATA_BYTES + 1)])
        client, _ = _stream_client(response)

        assert await saml.fetch_idp_metadata_xml(client, _METADATA_URL) is None

    @pytest.mark.parametrize("failure_point", ["enter", "status", "iteration"])
    @pytest.mark.asyncio
    async def test_fetch_metadata_fails_soft_on_external_errors(self, failure_point):
        error = RuntimeError(f"{failure_point} failed")
        response = _stream_response(
            [b"partial"],
            status_error=error if failure_point == "status" else None,
            iteration_error=error if failure_point == "iteration" else None,
        )
        client, _ = _stream_client(response, enter_error=error if failure_point == "enter" else None)

        assert await saml.fetch_idp_metadata_xml(client, _METADATA_URL) is None

    @pytest.mark.asyncio
    async def test_sso_reachability_skips_missing_url_and_accepts_non_server_status(self):
        client = MagicMock()
        client.head = AsyncMock(return_value=SimpleNamespace(status_code=404))
        client.get = AsyncMock()

        assert await saml.check_idp_sso_url_reachable(client, "") is None
        assert (await saml.check_idp_sso_url_reachable(client, "https://idp.example.test/sso"))["status"] == "pass"
        client.head.assert_awaited_once_with("https://idp.example.test/sso", follow_redirects=False)
        client.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sso_reachability_retries_head_405_and_reports_server_error(self):
        client = MagicMock()
        client.head = AsyncMock(return_value=SimpleNamespace(status_code=405))
        client.get = AsyncMock(return_value=SimpleNamespace(status_code=503))

        check = await saml.check_idp_sso_url_reachable(client, "https://idp.example.test/sso")

        assert check["status"] == "fail"
        assert check["message"] == "IdP SSO URL returned HTTP 503."
        client.get.assert_awaited_once_with("https://idp.example.test/sso", follow_redirects=False)

    @pytest.mark.asyncio
    async def test_sso_reachability_fails_soft_on_network_error(self):
        client = MagicMock()
        client.head = AsyncMock(side_effect=RuntimeError("network details"))

        check = await saml.check_idp_sso_url_reachable(client, "https://idp.example.test/sso")

        assert check["status"] == "fail"
        assert check["message"] == "IdP SSO URL is unreachable from this server."
        assert "network details" not in str(check)

    @pytest.mark.asyncio
    async def test_slo_reachability_returns_skip_when_unconfigured(self):
        client = MagicMock()

        check = await saml.check_idp_slo_url_reachable(client, None)

        assert check["status"] == "skip"
        assert "No idp_slo_url configured" in check["message"]
        client.head.assert_not_called()

    @pytest.mark.asyncio
    async def test_slo_reachability_retries_head_405_and_accepts_get(self):
        client = MagicMock()
        client.head = AsyncMock(return_value=SimpleNamespace(status_code=405))
        client.get = AsyncMock(return_value=SimpleNamespace(status_code=204))

        check = await saml.check_idp_slo_url_reachable(client, "https://idp.example.test/slo")

        assert check["status"] == "pass"
        client.head.assert_awaited_once_with("https://idp.example.test/slo", follow_redirects=False)
        client.get.assert_awaited_once_with("https://idp.example.test/slo", follow_redirects=False)

    @pytest.mark.parametrize("failure", ["server", "network"])
    @pytest.mark.asyncio
    async def test_slo_reachability_reports_server_and_network_failures(self, failure):
        client = MagicMock()
        if failure == "server":
            client.head = AsyncMock(return_value=SimpleNamespace(status_code=500))
        else:
            client.head = AsyncMock(side_effect=RuntimeError("network details"))

        check = await saml.check_idp_slo_url_reachable(client, "https://idp.example.test/slo")

        assert check["status"] == "fail"
        expected = "returned HTTP 500" if failure == "server" else "is unreachable"
        assert expected in check["message"]
        assert "network details" not in str(check)

    @pytest.mark.asyncio
    async def test_dynamic_metadata_setting_controls_fetch(self, monkeypatch):
        get_sync = MagicMock(side_effect=["", _METADATA_URL])
        fetch = AsyncMock(return_value="<metadata/>")
        monkeypatch.setattr(saml.ds, "get_sync", get_sync)
        monkeypatch.setattr(saml, "fetch_idp_metadata_xml", fetch)
        client = MagicMock()

        assert await saml.get_idp_metadata_xml(client) is None
        assert await saml.get_idp_metadata_xml(client) == "<metadata/>"
        assert get_sync.call_args_list[0].args == ("saml.idp_metadata_url", "")
        assert get_sync.call_args_list[1].args == ("saml.idp_metadata_url", "")
        fetch.assert_awaited_once_with(client, _METADATA_URL)

    @pytest.mark.asyncio
    async def test_dynamic_metadata_setting_failure_propagates(self, monkeypatch):
        monkeypatch.setattr(saml.ds, "get_sync", MagicMock(side_effect=RuntimeError("settings unavailable")))

        with pytest.raises(RuntimeError, match="settings unavailable"):
            await saml.get_idp_metadata_xml(MagicMock())


class TestSamlServiceIdentityHelpers:
    def test_extract_name_id_normalizes_subject_and_preserves_attributes(self):
        auth = MagicMock()
        attributes = {"mail": ["User@Example.TEST"], "groups": ["engineering"]}
        auth.get_nameid.return_value = "  User@Example.TEST  "
        auth.get_attributes.return_value = attributes

        assert saml.extract_name_id_and_attrs(auth) == ("user@example.test", attributes)
        auth.get_nameid.assert_called_once_with()
        auth.get_attributes.assert_called_once_with()

    def test_extract_name_id_defaults_missing_toolkit_values(self):
        auth = MagicMock()
        auth.get_nameid.return_value = None
        auth.get_attributes.return_value = None

        assert saml.extract_name_id_and_attrs(auth) == ("", {})

    def test_extract_name_id_propagates_toolkit_failure(self):
        auth = MagicMock()
        auth.get_nameid.side_effect = RuntimeError("toolkit unavailable")

        with pytest.raises(RuntimeError, match="toolkit unavailable"):
            saml.extract_name_id_and_attrs(auth)
        auth.get_attributes.assert_not_called()

    @pytest.mark.parametrize(
        "attribute",
        [
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
            "displayName",
            "cn",
            "urn:oid:2.16.840.1.113730.3.1.241",
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname",
            "givenName",
            "firstName",
        ],
    )
    def test_display_name_supports_each_claim_alias(self, attribute):
        assert saml.get_display_name({attribute: ["  Alice Example  "]}) == "Alice Example"

    def test_display_name_obeys_priority_and_skips_blank_values(self):
        attributes = {
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name": ["   "],
            "displayName": [],
            "cn": ["  Common Name  "],
            "givenName": ["Lower Priority"],
        }

        assert saml.get_display_name(attributes, fallback="Fallback") == "Common Name"
        assert saml.get_display_name({}, fallback="Fallback") == "Fallback"


class TestSamlEndpoints:
    @pytest.fixture
    def saml_app(self):
        from fastapi import FastAPI

        from api.routes.sso_saml import router

        app = FastAPI()
        app.include_router(router)
        return app

    def _make_mock_config(self):
        from services.saml import generate_sp_key_pair

        private_key, cert = generate_sp_key_pair("test.example.com")
        mock_config = MagicMock()
        mock_config.idp_entity_id = "https://idp.example.com"
        mock_config.idp_sso_url = "https://idp.example.com/sso"
        mock_config.idp_slo_url = ""
        mock_config.idp_x509_cert = cert  # Use a real cert for metadata generation
        mock_config.sp_entity_id = "https://app.example.com/api/v1/sso/saml/metadata"
        mock_config.sp_acs_url = "https://app.example.com/api/v1/sso/saml/acs"
        mock_config.sp_private_key_enc = private_key
        mock_config.sp_x509_cert = cert
        mock_config.jit_provisioning = True
        mock_config.default_role = "user"
        return mock_config, private_key

    @pytest.mark.asyncio
    async def test_metadata_returns_xml_when_configured(self, saml_app):
        mock_config, private_key = self._make_mock_config()

        with (
            patch(
                "api.routes.sso_saml._get_saml_config",
                new_callable=AsyncMock,
                return_value=mock_config,
            ),
            patch(
                "api.routes.sso_saml._decrypt_sp_key",
                return_value=private_key,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=saml_app),
                base_url="http://test",
            ) as ac:
                r = await ac.get("/api/v1/sso/saml/metadata")
            assert r.status_code == 200
            assert "xml" in r.headers.get("content-type", "").lower()
            assert "EntityDescriptor" in r.text

    @pytest.mark.asyncio
    async def test_metadata_returns_404_when_not_configured(self, saml_app):
        with patch(
            "api.routes.sso_saml._get_saml_config",
            new_callable=AsyncMock,
            return_value=None,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=saml_app),
                base_url="http://test",
            ) as ac:
                r = await ac.get("/api/v1/sso/saml/metadata")
            assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_login_returns_redirect_when_configured(self, saml_app):
        mock_config, private_key = self._make_mock_config()

        with (
            patch(
                "api.routes.sso_saml._get_saml_config",
                new_callable=AsyncMock,
                return_value=mock_config,
            ),
            patch(
                "api.routes.sso_saml._decrypt_sp_key",
                return_value=private_key,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=saml_app),
                base_url="http://test",
                follow_redirects=False,
            ) as ac:
                r = await ac.get("/api/v1/sso/saml/login")
            assert r.status_code == 302
            location = r.headers.get("location", "")
            assert "idp.example.com/sso" in location

    @pytest.mark.asyncio
    async def test_login_returns_404_when_not_configured(self, saml_app):
        with patch(
            "api.routes.sso_saml._get_saml_config",
            new_callable=AsyncMock,
            return_value=None,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=saml_app),
                base_url="http://test",
            ) as ac:
                r = await ac.get("/api/v1/sso/saml/login")
            assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_acs_redirects_with_sso_error_when_not_configured(self, saml_app):
        # The ACS handler now records per-step diagnostics and redirects to
        # /login?sso_error=<id> instead of returning a bare 404. The user sees
        # which check failed ("SAML SSO is configured on server") rather than a
        # generic HTTP error.
        with patch(
            "api.routes.sso_saml._get_saml_config",
            new_callable=AsyncMock,
            return_value=None,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=saml_app),
                base_url="http://test",
            ) as ac:
                r = await ac.post("/api/v1/sso/saml/acs")
            assert r.status_code == 302
            assert "sso_error=" in r.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_acs_replay_protection_stores_assertion_id(self, saml_app):
        """First ACS call with a given response ID should store it in Redis."""
        from api.deps import get_db

        mock_config, private_key = self._make_mock_config()
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()
        mock_redis.delete = AsyncMock()

        mock_auth = MagicMock()
        mock_auth.process_response.return_value = None
        mock_auth.get_errors.return_value = []
        mock_auth.is_authenticated.return_value = True
        mock_auth.get_last_message_id.return_value = "saml-response-id-12345"
        mock_auth.get_nameid.return_value = "user@example.com"
        mock_auth.get_attributes.return_value = {"displayName": ["Test User"]}

        mock_user = MagicMock()
        mock_user.id = "user-uuid-1"
        mock_user.email = "user@example.com"
        mock_user.name = "Test User"
        mock_user.username = "testuser"
        mock_user.role = MagicMock()
        mock_user.role.value = "user"
        mock_user.auth_provider = "saml"
        mock_user.sso_subject_id = "user@example.com"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        async def override_get_db():
            yield mock_db

        saml_app.dependency_overrides[get_db] = override_get_db

        try:
            with (
                patch(
                    "api.routes.sso_saml._get_saml_config",
                    new_callable=AsyncMock,
                    return_value=mock_config,
                ),
                patch(
                    "api.routes.sso_saml._decrypt_sp_key",
                    return_value=private_key,
                ),
                patch(
                    "api.routes.sso_saml._build_auth",
                    return_value=mock_auth,
                ),
                patch(
                    "api.routes.sso_saml.get_redis",
                    return_value=mock_redis,
                ),
                patch(
                    "api.routes.sso_saml.emit_security_event",
                    new_callable=AsyncMock,
                ),
                patch(
                    "api.routes.sso_saml.create_access_token",
                    return_value=("access-tok", 3600),
                ),
                patch(
                    "api.routes.sso_saml.create_refresh_token",
                    return_value=("refresh-tok", "jti-1"),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=saml_app),
                    base_url="http://test",
                    follow_redirects=False,
                ) as ac:
                    r = await ac.post(
                        "/api/v1/sso/saml/acs",
                        data={"SAMLResponse": "dummybase64"},
                    )
                # Should succeed (redirect) and store the assertion ID
                assert r.status_code == 302
                mock_redis.get.assert_any_call("saml_assertion:saml-response-id-12345")
                mock_redis.setex.assert_any_call("saml_assertion:saml-response-id-12345", 300, "1")
        finally:
            saml_app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_acs_replay_protection_blocks_replayed_assertion(self, saml_app):
        """Second ACS call with same response ID should be rejected as replay."""
        from api.deps import get_db

        mock_config, private_key = self._make_mock_config()
        mock_redis = AsyncMock()
        # Simulate that this assertion ID was already seen
        mock_redis.get = AsyncMock(return_value=b"1")

        mock_auth = MagicMock()
        mock_auth.process_response.return_value = None
        mock_auth.get_errors.return_value = []
        mock_auth.is_authenticated.return_value = True
        mock_auth.get_last_message_id.return_value = "saml-response-id-12345"

        mock_db = AsyncMock()

        async def override_get_db():
            yield mock_db

        saml_app.dependency_overrides[get_db] = override_get_db

        try:
            with (
                patch(
                    "api.routes.sso_saml._get_saml_config",
                    new_callable=AsyncMock,
                    return_value=mock_config,
                ),
                patch(
                    "api.routes.sso_saml._decrypt_sp_key",
                    return_value=private_key,
                ),
                patch(
                    "api.routes.sso_saml._build_auth",
                    return_value=mock_auth,
                ),
                patch(
                    "api.routes.sso_saml.get_redis",
                    return_value=mock_redis,
                ),
                patch(
                    "api.routes.sso_saml.emit_security_event",
                    new_callable=AsyncMock,
                ) as mock_emit,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=saml_app),
                    base_url="http://test",
                    follow_redirects=False,
                ) as ac:
                    r = await ac.post(
                        "/api/v1/sso/saml/acs",
                        data={"SAMLResponse": "dummybase64"},
                    )
                # Replay now redirects to /login?sso_error so the user sees
                # which step failed in the ChecksList instead of a raw 400.
                # The security event still fires -- replay detection isn't
                # softened, only the UX response.
                assert r.status_code == 302
                assert "sso_error=" in r.headers.get("location", "")
                mock_emit.assert_called()
                event_arg = mock_emit.call_args[0][0]
                assert "replay" in event_arg.detail.lower()
        finally:
            saml_app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_logout_redirects_to_idp_when_slo_configured(self, saml_app):
        """Logout should redirect to IdP SLO endpoint when configured."""
        mock_config, private_key = self._make_mock_config()
        mock_config.idp_slo_url = "https://idp.example.com/slo"

        with (
            patch(
                "api.routes.sso_saml._get_saml_config",
                new_callable=AsyncMock,
                return_value=mock_config,
            ),
            patch(
                "api.routes.sso_saml._decrypt_sp_key",
                return_value=private_key,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=saml_app),
                base_url="http://test",
                follow_redirects=False,
            ) as ac:
                r = await ac.get("/api/v1/sso/saml/logout")
            assert r.status_code == 302
            location = r.headers.get("location", "")
            assert "idp.example.com/slo" in location

    @pytest.mark.asyncio
    async def test_logout_redirects_to_login_when_no_slo(self, saml_app):
        """Logout should redirect to /login when SLO is not configured."""
        mock_config, _private_key = self._make_mock_config()
        mock_config.idp_slo_url = ""

        with patch(
            "api.routes.sso_saml._get_saml_config",
            new_callable=AsyncMock,
            return_value=mock_config,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=saml_app),
                base_url="http://test",
                follow_redirects=False,
            ) as ac:
                r = await ac.get("/api/v1/sso/saml/logout")
            assert r.status_code == 302
            location = r.headers.get("location", "")
            assert "/login" in location

    @pytest.mark.asyncio
    async def test_logout_redirects_to_login_when_not_configured(self, saml_app):
        """Logout should redirect to /login when SAML is not configured at all."""
        with patch(
            "api.routes.sso_saml._get_saml_config",
            new_callable=AsyncMock,
            return_value=None,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=saml_app),
                base_url="http://test",
                follow_redirects=False,
            ) as ac:
                r = await ac.get("/api/v1/sso/saml/logout")
            assert r.status_code == 302
            assert "/login" in r.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_sls_handles_callback(self, saml_app):
        """SLS endpoint should process SLO and redirect to /login."""
        mock_config, private_key = self._make_mock_config()
        mock_config.idp_slo_url = "https://idp.example.com/slo"

        mock_auth = MagicMock()
        mock_auth.process_slo.return_value = None
        mock_auth.get_errors.return_value = []

        with (
            patch(
                "api.routes.sso_saml._get_saml_config",
                new_callable=AsyncMock,
                return_value=mock_config,
            ),
            patch(
                "api.routes.sso_saml._decrypt_sp_key",
                return_value=private_key,
            ),
            patch(
                "api.routes.sso_saml._build_auth",
                return_value=mock_auth,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=saml_app),
                base_url="http://test",
                follow_redirects=False,
            ) as ac:
                r = await ac.get("/api/v1/sso/saml/sls?SAMLResponse=dummybase64")
            assert r.status_code == 302
            assert "/login" in r.headers.get("location", "")
            mock_auth.process_slo.assert_called_once()

    @pytest.mark.asyncio
    async def test_sls_redirects_when_not_configured(self, saml_app):
        """SLS endpoint should redirect to /login when SAML is not configured."""
        with patch(
            "api.routes.sso_saml._get_saml_config",
            new_callable=AsyncMock,
            return_value=None,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=saml_app),
                base_url="http://test",
                follow_redirects=False,
            ) as ac:
                r = await ac.get("/api/v1/sso/saml/sls")
            assert r.status_code == 302
            assert "/login" in r.headers.get("location", "")


class TestSamlRelayState:
    """Tests for SAML RelayState (post-login redirect) support."""

    @pytest.fixture
    def saml_app(self):
        from fastapi import FastAPI

        from api.routes.sso_saml import router

        app = FastAPI()
        app.include_router(router)
        return app

    def _make_mock_config(self):
        from services.saml import generate_sp_key_pair

        private_key, cert = generate_sp_key_pair("test.example.com")
        mock_config = MagicMock()
        mock_config.idp_entity_id = "https://idp.example.com"
        mock_config.idp_sso_url = "https://idp.example.com/sso"
        mock_config.idp_slo_url = ""
        mock_config.idp_x509_cert = cert
        mock_config.sp_entity_id = "https://app.example.com/api/v1/sso/saml/metadata"
        mock_config.sp_acs_url = "https://app.example.com/api/v1/sso/saml/acs"
        mock_config.sp_private_key_enc = private_key
        mock_config.sp_x509_cert = cert
        mock_config.jit_provisioning = True
        mock_config.default_role = "user"
        return mock_config, private_key

    @pytest.mark.asyncio
    async def test_login_passes_relay_state(self, saml_app):
        """Login with ?next= should include RelayState in the redirect URL."""
        mock_config, private_key = self._make_mock_config()

        with (
            patch(
                "api.routes.sso_saml._get_saml_config",
                new_callable=AsyncMock,
                return_value=mock_config,
            ),
            patch(
                "api.routes.sso_saml._decrypt_sp_key",
                return_value=private_key,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=saml_app),
                base_url="http://test",
                follow_redirects=False,
            ) as ac:
                r = await ac.get("/api/v1/sso/saml/login?next=/sessions/abc")
            assert r.status_code == 302
            location = r.headers.get("location", "")
            assert "idp.example.com/sso" in location
            # RelayState should be passed to the IdP
            assert "RelayState" in location

    @pytest.mark.asyncio
    async def test_login_sanitizes_non_relative_relay_state(self, saml_app):
        """Login with absolute URL in ?next= should be sanitized to /."""
        mock_config, private_key = self._make_mock_config()

        with (
            patch(
                "api.routes.sso_saml._get_saml_config",
                new_callable=AsyncMock,
                return_value=mock_config,
            ),
            patch(
                "api.routes.sso_saml._decrypt_sp_key",
                return_value=private_key,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=saml_app),
                base_url="http://test",
                follow_redirects=False,
            ) as ac:
                r = await ac.get("/api/v1/sso/saml/login?next=https://evil.com/phish")
            assert r.status_code == 302
            location = r.headers.get("location", "")
            # Should NOT contain the evil URL in RelayState
            assert "evil.com" not in location

    @pytest.mark.asyncio
    async def test_login_defaults_relay_state_to_root(self, saml_app):
        """Login without ?next= should use / as default RelayState."""
        mock_config, private_key = self._make_mock_config()

        with (
            patch(
                "api.routes.sso_saml._get_saml_config",
                new_callable=AsyncMock,
                return_value=mock_config,
            ),
            patch(
                "api.routes.sso_saml._decrypt_sp_key",
                return_value=private_key,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=saml_app),
                base_url="http://test",
                follow_redirects=False,
            ) as ac:
                r = await ac.get("/api/v1/sso/saml/login")
            assert r.status_code == 302
            location = r.headers.get("location", "")
            assert "idp.example.com/sso" in location

    @pytest.mark.asyncio
    async def test_acs_extracts_relay_state_into_redirect(self, saml_app):
        """ACS should redirect to frontend login with saml_token param."""
        from api.deps import get_db

        mock_config, private_key = self._make_mock_config()
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()
        mock_redis.delete = AsyncMock()

        mock_auth = MagicMock()
        mock_auth.process_response.return_value = None
        mock_auth.get_errors.return_value = []
        mock_auth.is_authenticated.return_value = True
        mock_auth.get_last_message_id.return_value = "saml-response-relay-test"
        mock_auth.get_nameid.return_value = "user@example.com"
        mock_auth.get_attributes.return_value = {"displayName": ["Test User"]}

        mock_user = MagicMock()
        mock_user.id = "user-uuid-1"
        mock_user.email = "user@example.com"
        mock_user.name = "Test User"
        mock_user.username = "testuser"
        mock_user.role = MagicMock()
        mock_user.role.value = "user"
        mock_user.auth_provider = "saml"
        mock_user.sso_subject_id = "user@example.com"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        async def override_get_db():
            yield mock_db

        saml_app.dependency_overrides[get_db] = override_get_db

        try:
            with (
                patch(
                    "api.routes.sso_saml._get_saml_config",
                    new_callable=AsyncMock,
                    return_value=mock_config,
                ),
                patch(
                    "api.routes.sso_saml._decrypt_sp_key",
                    return_value=private_key,
                ),
                patch(
                    "api.routes.sso_saml._build_auth",
                    return_value=mock_auth,
                ),
                patch(
                    "api.routes.sso_saml.get_redis",
                    return_value=mock_redis,
                ),
                patch(
                    "api.routes.sso_saml.emit_security_event",
                    new_callable=AsyncMock,
                ),
                patch(
                    "api.routes.sso_saml.create_access_token",
                    return_value=("access-tok", 3600),
                ),
                patch(
                    "api.routes.sso_saml.create_refresh_token",
                    return_value=("refresh-tok", "jti-1"),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=saml_app),
                    base_url="http://test",
                    follow_redirects=False,
                ) as ac:
                    r = await ac.post(
                        "/api/v1/sso/saml/acs",
                        data={
                            "SAMLResponse": "dummybase64",
                            "RelayState": "/sessions/abc",
                        },
                    )
                assert r.status_code == 302
                location = r.headers.get("location", "")
                assert "/login?saml_token=" in location
        finally:
            saml_app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_acs_sanitizes_non_relative_relay_state(self, saml_app):
        """ACS should redirect to frontend login with saml_token, not to evil URL."""
        from api.deps import get_db

        mock_config, private_key = self._make_mock_config()
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()
        mock_redis.delete = AsyncMock()

        mock_auth = MagicMock()
        mock_auth.process_response.return_value = None
        mock_auth.get_errors.return_value = []
        mock_auth.is_authenticated.return_value = True
        mock_auth.get_last_message_id.return_value = "saml-response-relay-sanitize"
        mock_auth.get_nameid.return_value = "user@example.com"
        mock_auth.get_attributes.return_value = {"displayName": ["Test User"]}

        mock_user = MagicMock()
        mock_user.id = "user-uuid-1"
        mock_user.email = "user@example.com"
        mock_user.name = "Test User"
        mock_user.username = "testuser"
        mock_user.role = MagicMock()
        mock_user.role.value = "user"
        mock_user.auth_provider = "saml"
        mock_user.sso_subject_id = "user@example.com"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        async def override_get_db():
            yield mock_db

        saml_app.dependency_overrides[get_db] = override_get_db

        try:
            with (
                patch(
                    "api.routes.sso_saml._get_saml_config",
                    new_callable=AsyncMock,
                    return_value=mock_config,
                ),
                patch(
                    "api.routes.sso_saml._decrypt_sp_key",
                    return_value=private_key,
                ),
                patch(
                    "api.routes.sso_saml._build_auth",
                    return_value=mock_auth,
                ),
                patch(
                    "api.routes.sso_saml.get_redis",
                    return_value=mock_redis,
                ),
                patch(
                    "api.routes.sso_saml.emit_security_event",
                    new_callable=AsyncMock,
                ),
                patch(
                    "api.routes.sso_saml.create_access_token",
                    return_value=("access-tok", 3600),
                ),
                patch(
                    "api.routes.sso_saml.create_refresh_token",
                    return_value=("refresh-tok", "jti-1"),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=saml_app),
                    base_url="http://test",
                    follow_redirects=False,
                ) as ac:
                    r = await ac.post(
                        "/api/v1/sso/saml/acs",
                        data={
                            "SAMLResponse": "dummybase64",
                            "RelayState": "https://evil.com/phish",
                        },
                    )
                assert r.status_code == 302
                location = r.headers.get("location", "")
                assert "evil.com" not in location
                assert "/login?saml_token=" in location
        finally:
            saml_app.dependency_overrides.clear()
