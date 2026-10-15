# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Test encryption and key rotation with 500+ realistic fake API keys.

This file is intentionally named with 'fake' so gitleaks allowlist skips it.
All keys are FAKE and generated deterministically; none are real credentials.
The 500 keys live in tests/fixtures/fake_api_keys.json (static, pre-generated).
"""

from __future__ import annotations

import hashlib
import json
import os
import string
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Deterministic helpers (used for edge-case special values below)
# ---------------------------------------------------------------------------

_RNG_SEED = "observal-test-fake-keys-seed-2026"


def _deterministic_hex(index: int, length: int) -> str:
    h = hashlib.sha512(f"{_RNG_SEED}:{index}".encode()).hexdigest()
    return h[:length]


def _deterministic_alnum(index: int, length: int) -> str:
    h = ""
    for block in range((length * 2 // 128) + 1):
        h += hashlib.sha512(f"{_RNG_SEED}:alnum:{index}:{block}".encode()).hexdigest()
    chars = string.ascii_letters + string.digits
    return "".join(chars[int(h[i : i + 2], 16) % len(chars)] for i in range(0, length * 2, 2))


def _deterministic_base64ish(index: int, length: int) -> str:
    h = ""
    for block in range((length * 2 // 128) + 1):
        h += hashlib.sha512(f"{_RNG_SEED}:b64:{index}:{block}".encode()).hexdigest()
    chars = string.ascii_letters + string.digits + "+/"
    return "".join(chars[int(h[i : i + 2], 16) % len(chars)] for i in range(0, length * 2, 2))


# ---------------------------------------------------------------------------
# 500 fake API keys across 25 provider formats (20 per provider), pre-generated
# ---------------------------------------------------------------------------

_FIXTURE = Path(__file__).parent / "fixtures" / "fake_api_keys.json"
with _FIXTURE.open() as _f:
    _RAW = json.load(_f)
FAKE_KEYS: list[tuple[str, str]] = [(row["provider"], row["key"]) for row in _RAW]
assert len(FAKE_KEYS) == 500, f"Expected 500 keys in {_FIXTURE}, got {len(FAKE_KEYS)}"


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _mock_secret_key():
    """Patch config.settings.SECRET_KEY for encryption tests."""
    with patch("config.settings.SECRET_KEY", "test-secret-key-for-encryption-suite"):
        yield


@pytest.fixture()
def _rotated_keys():
    """Patch settings for key rotation scenario: old key encrypts, new key should re-encrypt."""
    with (
        patch("config.settings.SECRET_KEY", "new-rotated-secret-key-2026"),
        patch.dict(os.environ, {"OLD_SECRET_KEY": "test-secret-key-for-encryption-suite"}),
    ):
        yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEncryptDecryptAllFormats:
    """Verify every fake key encrypts and decrypts correctly."""

    @pytest.mark.parametrize("provider,key", FAKE_KEYS, ids=[f"{p}_{i}" for i, (p, _) in enumerate(FAKE_KEYS)])
    def test_roundtrip(self, provider: str, key: str, _mock_secret_key):
        from services.dynamic_settings import decrypt_value, encrypt_value

        encrypted = encrypt_value(key)
        assert encrypted.startswith("enc:"), f"Encrypted value should start with enc: prefix for {provider}"
        assert key not in encrypted, f"Plaintext should not appear in ciphertext for {provider}"
        decrypted = decrypt_value(encrypted)
        assert decrypted == key, f"Roundtrip failed for {provider}: expected {key!r}, got {decrypted!r}"

    @pytest.mark.parametrize("provider,key", FAKE_KEYS[:20], ids=[f"empty_{i}" for i in range(20)])
    def test_empty_passthrough(self, provider: str, key: str, _mock_secret_key):
        """Empty strings should not be encrypted."""
        from services.dynamic_settings import decrypt_value, encrypt_value

        assert encrypt_value("") == ""
        assert decrypt_value("") == ""

    @pytest.mark.parametrize("provider,key", FAKE_KEYS, ids=[f"unique_{i}" for i, (_, __) in enumerate(FAKE_KEYS)])
    def test_ciphertext_unique(self, provider: str, key: str, _mock_secret_key):
        """Each encryption produces unique ciphertext (Fernet uses random IV)."""
        from services.dynamic_settings import encrypt_value

        a = encrypt_value(key)
        b = encrypt_value(key)
        # Fernet includes a timestamp and random IV, so same plaintext != same ciphertext
        assert a != b, "Two encryptions of same key should differ (Fernet uses random IV)"

    @pytest.mark.parametrize("provider,key", FAKE_KEYS, ids=[f"no_prefix_{i}" for i, (_, __) in enumerate(FAKE_KEYS)])
    def test_unencrypted_passthrough(self, provider: str, key: str, _mock_secret_key):
        """Values without enc: prefix are returned as-is (backward compat)."""
        from services.dynamic_settings import decrypt_value

        assert decrypt_value(key) == key


class TestKeyRotation:
    """Verify key rotation: old key decrypts, new key re-encrypts."""

    @pytest.mark.parametrize("provider,key", FAKE_KEYS[:50], ids=[f"rotate_{i}" for i in range(50)])
    def test_old_key_decrypts_after_rotation(self, provider: str, key: str, _rotated_keys):
        """Values encrypted with the old key should still decrypt via OLD_SECRET_KEY fallback."""
        import base64
        import hashlib

        from cryptography.fernet import Fernet

        # Encrypt with OLD key directly
        old_secret = "test-secret-key-for-encryption-suite"
        old_fernet_key = base64.urlsafe_b64encode(hashlib.sha256(old_secret.encode()).digest())
        old_f = Fernet(old_fernet_key)
        old_encrypted = "enc:" + old_f.encrypt(key.encode()).decode()

        from services.dynamic_settings import decrypt_value

        result = decrypt_value(old_encrypted)
        assert result == key, f"Key rotation decrypt failed for {provider}"

    @pytest.mark.parametrize("provider,key", FAKE_KEYS[:50], ids=[f"reenc_{i}" for i in range(50)])
    def test_reencrypt_produces_new_key_ciphertext(self, provider: str, key: str, _rotated_keys):
        """After rotation, re-encrypting with new key should produce decryptable output."""
        import base64
        import hashlib

        from cryptography.fernet import Fernet

        from services.dynamic_settings import decrypt_value, encrypt_value

        # Encrypt with OLD key
        old_secret = "test-secret-key-for-encryption-suite"
        old_fernet_key = base64.urlsafe_b64encode(hashlib.sha256(old_secret.encode()).digest())
        old_f = Fernet(old_fernet_key)
        old_encrypted = "enc:" + old_f.encrypt(key.encode()).decode()

        # Decrypt (uses old key fallback)
        plaintext = decrypt_value(old_encrypted)
        assert plaintext == key

        # Re-encrypt with new key
        new_encrypted = encrypt_value(plaintext)
        assert new_encrypted != old_encrypted

        # Decrypt with new key (primary path)
        new_secret = "new-rotated-secret-key-2026"
        new_fernet_key = base64.urlsafe_b64encode(hashlib.sha256(new_secret.encode()).digest())
        new_f = Fernet(new_fernet_key)
        ciphertext = new_encrypted[len("enc:") :].encode()
        assert new_f.decrypt(ciphertext).decode() == key

    @pytest.mark.parametrize("provider,key", FAKE_KEYS[50:100], ids=[f"nold_{i}" for i in range(50)])
    def test_decrypt_fails_gracefully_without_old_key(self, provider: str, key: str, _mock_secret_key):
        """Values encrypted with an unknown key return empty string, not crash."""
        import base64
        import hashlib

        from cryptography.fernet import Fernet

        from services.dynamic_settings import decrypt_value

        # Encrypt with a completely unknown key
        unknown_secret = "unknown-key-never-configured"
        unknown_fernet_key = base64.urlsafe_b64encode(hashlib.sha256(unknown_secret.encode()).digest())
        unknown_f = Fernet(unknown_fernet_key)
        bad_encrypted = "enc:" + unknown_f.encrypt(key.encode()).decode()

        result = decrypt_value(bad_encrypted)
        assert result == "", f"Expected empty string for undecryptable value, got {result!r}"


class TestBulkRotation:
    """Simulate the reencrypt_on_key_rotation batch process."""

    @pytest.mark.asyncio
    async def test_bulk_reencrypt_all_keys(self, _rotated_keys):
        """Simulate re-encrypting all 260 keys from old to new."""
        import base64
        import hashlib

        from cryptography.fernet import Fernet

        from services.dynamic_settings import decrypt_value, encrypt_value

        old_secret = "test-secret-key-for-encryption-suite"
        old_fernet_key = base64.urlsafe_b64encode(hashlib.sha256(old_secret.encode()).digest())
        old_f = Fernet(old_fernet_key)

        new_secret = "new-rotated-secret-key-2026"
        new_fernet_key = base64.urlsafe_b64encode(hashlib.sha256(new_secret.encode()).digest())
        new_f = Fernet(new_fernet_key)

        rotated_count = 0
        for provider, key in FAKE_KEYS:
            # Simulate: value was stored encrypted with old key
            old_encrypted = "enc:" + old_f.encrypt(key.encode()).decode()

            # Decrypt (falls back to OLD_SECRET_KEY)
            plaintext = decrypt_value(old_encrypted)
            assert plaintext == key, f"Decrypt failed for {provider} key #{rotated_count}"

            # Re-encrypt with current (new) key
            new_encrypted = encrypt_value(plaintext)
            assert new_encrypted.startswith("enc:")

            # Verify new ciphertext decrypts with new key directly
            ct = new_encrypted[len("enc:") :].encode()
            assert new_f.decrypt(ct).decode() == key

            rotated_count += 1

        assert rotated_count == 500

    @pytest.mark.asyncio
    async def test_idempotent_reencrypt(self, _rotated_keys):
        """Re-encrypting an already-rotated value should still work."""
        from services.dynamic_settings import decrypt_value, encrypt_value

        for _, key in FAKE_KEYS[:50]:
            # Encrypt with new key
            enc1 = encrypt_value(key)
            # Decrypt
            plain = decrypt_value(enc1)
            assert plain == key
            # Re-encrypt again
            enc2 = encrypt_value(plain)
            # Decrypt again
            assert decrypt_value(enc2) == key


class TestEdgeCases:
    """Edge cases for encryption with various key formats."""

    @pytest.mark.parametrize(
        "value",
        [
            "",
            " ",
            "\n",
            "\t",
            "a",
            "null",
            "undefined",
            "None",
            "0",
            "false",
            "true",
            '{"key": "value"}',
            "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIE" + "A" * 100 + "\n-----END RSA " + "PRIVATE KEY-----",
            "-----BEGIN CERTIFICATE-----\nMIIC" + "B" * 200 + "\n-----END CERTIFICATE-----",
            "Bearer " + _deterministic_alnum(9999, 64),
            "Basic " + _deterministic_base64ish(9998, 48),
            "x" * 4096,  # Large value
            "\x00\x01\x02\x03",  # Binary-ish
            "emoji: \U0001f511\U0001f510\U0001f513",  # Unicode
            "path/with/slashes/and spaces/key.pem",
            "postgres://user:p@ss@host:5432/db",  # Connection string
            "redis://default:s3cr3t@redis:6379/0",
            "https://hooks.slack.com/services/T00/B00/" + _deterministic_alnum(9997, 24),
        ],
        ids=lambda v: v[:30].replace("\n", "\\n") if v else "empty",
    )
    def test_special_values_roundtrip(self, value: str, _mock_secret_key):
        from services.dynamic_settings import decrypt_value, encrypt_value

        if not value:
            assert encrypt_value(value) == value
            return
        encrypted = encrypt_value(value)
        assert encrypted.startswith("enc:")
        assert decrypt_value(encrypted) == value
