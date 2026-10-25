# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 SrihariLegend <sriharilegend23@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the asymmetric key management service (services/crypto.py).

Covers:
- Key generation (creates valid EC P-256 key pair)
- Key persistence (loads existing key on restart)
- JWKS format output
- Token signing and verification round-trip (raw + PyJWT)
- Key rotation (old tokens still verify with old public key)
- Password-protected keys
"""

from __future__ import annotations

import json
import multiprocessing
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from services.crypto import (
    KeyManager,
    _b64url,
    _b64url_decode,
    _kid_from_public_key,
    get_key_manager,
    init_key_manager,
)

from .spawn_support import start_spawned_test_process

if TYPE_CHECKING:
    from multiprocessing.queues import Queue
    from multiprocessing.synchronize import Barrier

_StartupReadyResult: TypeAlias = tuple[int, str | None, str | None, str | None]
_StartupVerifiedResult: TypeAlias = tuple[int, list[str] | None, str | None]


def _concurrent_startup_worker(
    key_dir: str,
    start_barrier: Barrier,
    worker_index: int,
    verify_queue: Queue,
    ready_queue: Queue,
    verified_queue: Queue,
) -> None:
    try:
        start_barrier.wait(timeout=20)
        manager = KeyManager(key_dir=key_dir, algorithm="RS256")
        manager.initialize()
        token = manager.sign_token({"sub": "startup-worker"})
        ready_queue.put((worker_index, manager.get_kid(), token, None))
    except BaseException as exc:
        ready_queue.put((worker_index, None, None, repr(exc)))
        raise

    try:
        tokens = verify_queue.get(timeout=45)
        subjects = [manager.verify_token(token)["sub"] for token in tokens]
        verified_queue.put((worker_index, subjects, None))
    except BaseException as exc:
        verified_queue.put((worker_index, None, repr(exc)))
        raise


def _run_concurrent_startup_workers(
    key_dir: str,
    additional_tokens: tuple[str, ...] = (),
) -> tuple[list[_StartupReadyResult], list[_StartupVerifiedResult]]:
    worker_count = 4
    if os.name == "nt":
        pytest.skip("Windows multi-process key-store coordination is unsupported")

    context = multiprocessing.get_context("spawn")
    start_barrier = context.Barrier(worker_count + 1)
    ready_queue = context.Queue()
    verified_queue = context.Queue()
    verify_queues = [context.Queue() for _ in range(worker_count)]
    workers = [
        context.Process(
            target=_concurrent_startup_worker,
            args=(key_dir, start_barrier, index, verify_queues[index], ready_queue, verified_queue),
        )
        for index in range(worker_count)
    ]

    try:
        for worker in workers:
            start_spawned_test_process(worker)
        start_barrier.wait(timeout=20)
        ready_results = [ready_queue.get(timeout=45) for _ in workers]
        assert all(error is None for _, _, _, error in ready_results), ready_results

        tokens = [*additional_tokens, *(token for _, _, token, _ in ready_results)]
        for verify_queue in verify_queues:
            verify_queue.put(tokens)
        verified_results = [verified_queue.get(timeout=45) for _ in workers]
        assert all(error is None for _, _, error in verified_results), verified_results

        for worker in workers:
            worker.join(timeout=45)
            assert not worker.is_alive(), "key-manager startup worker did not exit"
            assert worker.exitcode == 0

        return sorted(ready_results), sorted(verified_results)
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=5)
        for queue in [ready_queue, verified_queue, *verify_queues]:
            queue.close()
            queue.join_thread()


@pytest.fixture()
def tmp_key_dir(tmp_path):
    """Provide a temporary directory for key storage."""
    d = tmp_path / "keys"
    d.mkdir()
    return str(d)


@pytest.fixture()
def km(tmp_key_dir):
    """Return an initialized KeyManager with a fresh key pair."""
    manager = KeyManager(key_dir=tmp_key_dir)
    manager.initialize()
    return manager


# ===================================================================
# Key generation
# ===================================================================


class TestKeyGeneration:
    def test_generates_ec_key_pair(self, km):
        priv = km.get_private_key()
        pub = km.get_public_key()
        assert isinstance(priv, ec.EllipticCurvePrivateKey)
        assert isinstance(pub, ec.EllipticCurvePublicKey)
        # Must be P-256
        assert priv.curve.name == "secp256r1"

    def test_kid_is_deterministic(self, km):
        kid1 = km.get_kid()
        kid2 = _kid_from_public_key(km.get_public_key())
        assert kid1 == kid2

    def test_kid_is_hex_string(self, km):
        kid = km.get_kid()
        assert len(kid) == 16
        int(kid, 16)  # should not raise

    def test_public_key_pem_format(self, km):
        pem = km.get_public_key_pem()
        assert pem.startswith("-----BEGIN PUBLIC KEY-----")
        assert pem.strip().endswith("-----END PUBLIC KEY-----")

    def test_signing_pem_created_on_disk(self, tmp_key_dir):
        manager = KeyManager(key_dir=tmp_key_dir)
        manager.initialize()
        assert os.path.exists(os.path.join(tmp_key_dir, "signing.pem"))

    def test_key_file_permissions(self, tmp_key_dir):
        manager = KeyManager(key_dir=tmp_key_dir)
        manager.initialize()
        pem_path = os.path.join(tmp_key_dir, "signing.pem")
        mode = oct(os.stat(pem_path).st_mode & 0o777)
        assert mode == "0o600"


# ===================================================================
# Key persistence
# ===================================================================


class TestKeyPersistence:
    def test_concurrent_process_startup_shares_one_signing_key(self, tmp_key_dir):
        startup_results, verified_results = _run_concurrent_startup_workers(tmp_key_dir)
        manager = KeyManager(key_dir=tmp_key_dir, algorithm="RS256")
        manager.initialize()

        assert {kid for _, kid, _, _ in startup_results} == {manager.get_kid()}
        assert all(subjects == ["startup-worker"] * 4 for _, subjects, _ in verified_results)

    def test_concurrent_process_startup_serializes_algorithm_migration(self, tmp_key_dir):
        seed = KeyManager(key_dir=tmp_key_dir, algorithm="ES256")
        seed.initialize()
        old_kid = seed.get_kid()
        old_token = seed.sign_token({"sub": "before-algorithm-migration"})

        startup_results, verified_results = _run_concurrent_startup_workers(tmp_key_dir, (old_token,))
        manager = KeyManager(key_dir=tmp_key_dir, algorithm="RS256")
        manager.initialize()

        assert len({kid for _, kid, _, _ in startup_results}) == 1
        assert {kid for _, kid, _, _ in startup_results} == {manager.get_kid()}
        assert all(
            subjects == ["before-algorithm-migration", *(["startup-worker"] * 4)] for _, subjects, _ in verified_results
        )
        assert manager.verify_token(old_token)["sub"] == "before-algorithm-migration"
        assert (Path(tmp_key_dir) / f"retired_{old_kid}.pem").exists()

    def test_concurrent_process_startup_preserves_existing_key(self, tmp_key_dir):
        seed = KeyManager(key_dir=tmp_key_dir, algorithm="RS256")
        seed.initialize()
        existing_kid = seed.get_kid()

        startup_results, verified_results = _run_concurrent_startup_workers(tmp_key_dir)

        assert {kid for _, kid, _, _ in startup_results} == {existing_kid}
        assert all(subjects == ["startup-worker"] * 4 for _, subjects, _ in verified_results)

    def test_loads_existing_key_on_restart(self, tmp_key_dir):
        # First boot: generate
        km1 = KeyManager(key_dir=tmp_key_dir)
        km1.initialize()
        kid1 = km1.get_kid()
        pem1 = km1.get_public_key_pem()

        # Second boot: load
        km2 = KeyManager(key_dir=tmp_key_dir)
        km2.initialize()
        kid2 = km2.get_kid()
        pem2 = km2.get_public_key_pem()

        assert kid1 == kid2
        assert pem1 == pem2

    def test_password_protected_key(self, tmp_key_dir):
        password = "test-password-1234"
        km1 = KeyManager(key_dir=tmp_key_dir, key_password=password)
        km1.initialize()
        kid1 = km1.get_kid()

        # Reload with correct password
        km2 = KeyManager(key_dir=tmp_key_dir, key_password=password)
        km2.initialize()
        assert km2.get_kid() == kid1

    def test_existing_unencrypted_key_loads_when_password_is_added(self, tmp_key_dir):
        original = KeyManager(key_dir=tmp_key_dir)
        original.initialize()
        original_kid = original.get_kid()

        protected = KeyManager(key_dir=tmp_key_dir, key_password="new-password")
        protected.initialize()
        assert protected.get_kid() == original_kid
        assert b"ENCRYPTED" in (Path(tmp_key_dir) / "signing.pem").read_bytes()
        protected.rotate_key()
        assert b"ENCRYPTED" in (Path(tmp_key_dir) / "signing.pem").read_bytes()

        restarted = KeyManager(key_dir=tmp_key_dir, key_password="new-password")
        restarted.initialize()
        assert restarted.get_kid() == protected.get_kid()

    def test_wrong_password_fails(self, tmp_key_dir):
        km1 = KeyManager(key_dir=tmp_key_dir, key_password="correct")
        km1.initialize()

        km2 = KeyManager(key_dir=tmp_key_dir, key_password="wrong")
        with pytest.raises((ValueError, TypeError)):
            km2.initialize()


# ===================================================================
# JWKS format
# ===================================================================


class TestJWKS:
    def test_jwks_structure(self, km):
        jwks = km.get_jwks()
        assert "keys" in jwks
        assert len(jwks["keys"]) == 1

    def test_jwk_fields(self, km):
        jwk = km.get_jwks()["keys"][0]
        assert jwk["kty"] == "EC"
        assert jwk["crv"] == "P-256"
        assert jwk["use"] == "sig"
        assert jwk["alg"] == "ES256"
        assert jwk["kid"] == km.get_kid()
        assert "x" in jwk
        assert "y" in jwk

    def test_jwk_coordinates_decode(self, km):
        jwk = km.get_jwks()["keys"][0]
        x_bytes = _b64url_decode(jwk["x"])
        y_bytes = _b64url_decode(jwk["y"])
        # P-256 coordinates are 32 bytes each
        assert len(x_bytes) == 32
        assert len(y_bytes) == 32

    def test_jwks_includes_retired_keys_after_rotation(self, km):
        old_kid = km.get_kid()
        km.rotate_key()
        jwks = km.get_jwks()
        assert len(jwks["keys"]) == 2
        kids = {k["kid"] for k in jwks["keys"]}
        assert old_kid in kids
        assert km.get_kid() in kids


# ===================================================================
# Token signing and verification
# ===================================================================


class TestTokenRoundTrip:
    def test_sign_and_verify(self, km):
        payload = {"sub": "user-123", "role": "admin"}
        token = km.sign_token(payload)
        decoded = km.verify_token(token)
        assert decoded["sub"] == "user-123"
        assert decoded["role"] == "admin"

    def test_token_is_three_part_jws(self, km):
        token = km.sign_token({"test": True})
        assert len(token.split(".")) == 3

    def test_token_header_contains_kid(self, km):
        token = km.sign_token({"test": True})
        header = pyjwt.get_unverified_header(token)
        assert header["alg"] == "ES256"
        assert header["typ"] == "JWT"
        assert header["kid"] == km.get_kid()

    def test_tampered_payload_fails_verification(self, km):
        token = km.sign_token({"sub": "user-123"})
        parts = token.split(".")
        fake_payload = _b64url(json.dumps({"sub": "admin"}).encode())
        tampered = f"{parts[0]}.{fake_payload}.{parts[2]}"
        with pytest.raises(pyjwt.InvalidTokenError):
            km.verify_token(tampered)

    def test_invalid_token_format(self, km):
        with pytest.raises(pyjwt.InvalidTokenError):
            km.verify_token("not.a.valid.token.at.all")

    def test_unknown_kid_fails(self, km):
        token = km.sign_token({"sub": "user-123"})
        parts = token.split(".")
        header = pyjwt.get_unverified_header(token)
        header["kid"] = "unknown-kid-1234"
        fake_header = _b64url(json.dumps(header, separators=(",", ":")).encode())
        forged = f"{fake_header}.{parts[1]}.{parts[2]}"
        with pytest.raises(pyjwt.InvalidTokenError, match="Unknown key id"):
            km.verify_token(forged)

    def test_roundtrip_with_nested_payload(self, km):
        payload = {"data": {"items": [1, 2, 3]}, "count": 3}
        token = km.sign_token(payload)
        decoded = km.verify_token(token)
        assert decoded["data"]["items"] == [1, 2, 3]


# ===================================================================
# Key rotation
# ===================================================================


class TestKeyRotation:
    def test_rotation_generates_new_kid(self, km):
        old_kid = km.get_kid()
        km.rotate_key()
        assert km.get_kid() != old_kid

    def test_old_token_still_verifies_after_rotation(self, km):
        token_before = km.sign_token({"sub": "user-rotate", "test": True})
        km.rotate_key()
        assert km.verify_token(token_before)["sub"] == "user-rotate"

    def test_new_token_verifies_after_rotation(self, km):
        km.rotate_key()
        assert km.verify_token(km.sign_token({"sub": "user-new"}))["sub"] == "user-new"

    def test_retired_key_persisted_on_disk(self, tmp_key_dir):
        km = KeyManager(key_dir=tmp_key_dir)
        km.initialize()
        old_kid = km.get_kid()
        km.rotate_key()
        assert os.path.exists(os.path.join(tmp_key_dir, f"retired_{old_kid}.pem"))

    def test_retired_keys_loaded_on_restart(self, tmp_key_dir):
        first = KeyManager(key_dir=tmp_key_dir)
        first.initialize()
        token = first.sign_token({"sub": "persist-test"})
        first.rotate_key()
        current_kid = first.get_kid()

        restarted = KeyManager(key_dir=tmp_key_dir)
        restarted.initialize()
        assert restarted.get_kid() == current_kid
        assert restarted.verify_token(token)["sub"] == "persist-test"

    def test_multiple_rotations(self, km):
        kids = [km.get_kid()]
        for _ in range(3):
            km.rotate_key()
            kids.append(km.get_kid())
        assert len(set(kids)) == 4
        assert len(km.get_jwks()["keys"]) == 4

    def test_find_public_key(self, km):
        old_kid = km.get_kid()
        km.rotate_key()
        assert km.find_public_key(old_kid) is not None
        assert km.find_public_key(km.get_kid()) is not None
        assert km.find_public_key("nonexistent") is None

    def test_expired_retired_key_is_removed(self, tmp_key_dir):
        manager = KeyManager(key_dir=tmp_key_dir, retired_key_retention_days=1)
        manager.initialize()
        token = manager.sign_token({"sub": "expired-key"})
        old_kid = manager.get_kid()
        manager.rotate_key()
        retired_path = Path(tmp_key_dir) / f"retired_{old_kid}.pem"
        old_time = time.time() - 2 * 86400
        os.utime(retired_path, (old_time, old_time))

        restarted = KeyManager(key_dir=tmp_key_dir, retired_key_retention_days=1)
        restarted.initialize()
        assert not retired_path.exists()
        with pytest.raises(pyjwt.InvalidTokenError, match="Unknown key id"):
            restarted.verify_token(token)


# ===================================================================
# Algorithm agility
# ===================================================================


class TestAlgorithmAgility:
    def test_rejects_unsupported_algorithm(self, tmp_key_dir):
        with pytest.raises(ValueError, match="Unsupported JWT signing algorithm"):
            KeyManager(key_dir=tmp_key_dir, algorithm="HS256")

    def test_rejects_undersized_rsa_key(self, tmp_key_dir):
        weak_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        (Path(tmp_key_dir) / "signing.pem").write_bytes(
            weak_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        with pytest.raises(TypeError, match="Unsupported RSA key size"):
            KeyManager(key_dir=tmp_key_dir, algorithm="RS256").initialize()

    def test_generates_rs256_key_and_jwk(self, tmp_key_dir):
        manager = KeyManager(key_dir=tmp_key_dir, algorithm="RS256")
        manager.initialize()

        assert isinstance(manager.get_private_key(), rsa.RSAPrivateKey)
        assert manager.verify_token(manager.sign_token({"sub": "rsa"}))["sub"] == "rsa"
        jwk = manager.get_jwks()["keys"][0]
        assert jwk["kty"] == "RSA"
        assert jwk["alg"] == "RS256"
        assert "n" in jwk and "e" in jwk

    @pytest.mark.parametrize(("old_algorithm", "new_algorithm"), [("ES256", "RS256"), ("RS256", "ES256")])
    def test_switching_algorithm_retains_old_tokens_after_restart(self, tmp_key_dir, old_algorithm, new_algorithm):
        old = KeyManager(key_dir=tmp_key_dir, algorithm=old_algorithm)
        old.initialize()
        old_token = old.sign_token({"sub": "old"})

        current = KeyManager(key_dir=tmp_key_dir, algorithm=new_algorithm)
        current.initialize()
        restarted = KeyManager(key_dir=tmp_key_dir, algorithm=new_algorithm)
        restarted.initialize()

        assert restarted.verify_token(old_token)["sub"] == "old"
        assert {key["alg"] for key in restarted.get_jwks()["keys"]} == {"ES256", "RS256"}

    def test_rejects_algorithm_confusion(self, km):
        token = km.sign_token({"sub": "user"})
        parts = token.split(".")
        header = pyjwt.get_unverified_header(token)
        header["alg"] = "RS256"
        forged = f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}.{parts[1]}.{parts[2]}"
        with pytest.raises(pyjwt.InvalidAlgorithmError):
            km.verify_token(forged)


# ===================================================================
# Module-level singleton
# ===================================================================


class TestSingleton:
    def test_init_and_get(self, tmp_key_dir):
        km = init_key_manager(key_dir=tmp_key_dir)
        assert get_key_manager() is km

    def test_get_before_init_raises(self, monkeypatch):
        import services.crypto as mod

        monkeypatch.setattr(mod, "_key_manager", None)
        with pytest.raises(RuntimeError, match="not initialized"):
            get_key_manager()


# ===================================================================
# Base64url helpers
# ===================================================================


class TestBase64Url:
    def test_roundtrip(self):
        data = b"hello world"
        encoded = _b64url(data)
        decoded = _b64url_decode(encoded)
        assert decoded == data

    def test_no_padding(self):
        encoded = _b64url(b"a")
        assert "=" not in encoded


# ===================================================================
# JWKS HTTP endpoint
# ===================================================================


class TestJWKSEndpoint:
    """Test the GET /api/v1/auth/.well-known/jwks.json route."""

    @pytest.fixture()
    def jwks_app(self, tmp_key_dir):
        """Build a minimal FastAPI app with just the JWKS route."""
        import services.crypto as crypto_mod

        init_key_manager(key_dir=tmp_key_dir)

        from api.routes.jwks import router

        app = FastAPI()
        app.include_router(router)
        yield app
        # Cleanup singleton so other tests are not affected
        crypto_mod._key_manager = None

    @pytest.fixture()
    async def jwks_client(self, jwks_app):
        transport = ASGITransport(app=jwks_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    @pytest.mark.asyncio
    async def test_jwks_endpoint_returns_200(self, jwks_client):
        resp = await jwks_client.get("/api/v1/auth/.well-known/jwks.json")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_jwks_endpoint_returns_valid_jwks(self, jwks_client):
        resp = await jwks_client.get("/api/v1/auth/.well-known/jwks.json")
        data = resp.json()
        assert "keys" in data
        assert len(data["keys"]) >= 1
        key = data["keys"][0]
        assert key["kty"] == "EC"
        assert key["crv"] == "P-256"
        assert key["alg"] == "ES256"
        assert "kid" in key

    @pytest.mark.asyncio
    async def test_jwks_endpoint_cache_header(self, jwks_client):
        resp = await jwks_client.get("/api/v1/auth/.well-known/jwks.json")
        assert "max-age" in resp.headers.get("cache-control", "")
