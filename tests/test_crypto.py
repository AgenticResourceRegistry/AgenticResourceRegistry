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

import io
import json
import multiprocessing
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, TypeAlias
from unittest.mock import patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from services.crypto import (
    KeyManager,
    KeyStoreUnavailableError,
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


def _concurrent_rotation_worker(
    key_dir: str,
    algorithm: str,
    start_barrier: Barrier,
    worker_index: int,
    results: Queue,
) -> None:
    try:
        manager = KeyManager(key_dir=key_dir, algorithm=algorithm)
        manager.initialize()
        start_barrier.wait(timeout=20)
        rotated_kid = manager.rotate_key()
        token = manager.sign_token({"sub": f"worker-{worker_index}"})
        results.put((rotated_kid, token, None))
    except BaseException as exc:
        results.put((None, None, repr(exc)))
        raise


def _crash_during_rotation_worker(key_dir: str, destination_name: str, after_replace: bool) -> None:
    manager = KeyManager(key_dir=key_dir)
    manager.initialize()
    real_replace = os.replace

    def interrupt_replace(source, destination):
        if Path(destination).name == destination_name:
            if after_replace:
                real_replace(source, destination)
            os._exit(73)
        real_replace(source, destination)

    os.replace = interrupt_replace
    manager.rotate_key()


def _rotation_log_safety_worker(key_dir: str, results: Queue) -> None:
    """Isolate the Loguru sinks so a failing regression cannot print a key."""
    from loguru import logger

    logger.remove()
    captured = io.StringIO()
    logger.add(captured, level="ERROR", diagnose=True, backtrace=True)

    manager = KeyManager(key_dir=key_dir)
    manager.initialize()
    signing_path = Path(key_dir) / "signing.pem"
    original_pem = signing_path.read_bytes()
    real_fsync = os.fsync
    real_read_bytes = Path.read_bytes
    new_pem = None

    def fail_active_directory_sync(descriptor):
        nonlocal new_pem
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            current_pem = real_read_bytes(signing_path)
            if current_pem != original_pem:
                new_pem = current_pem
                raise OSError("simulated failure after active publication")
        real_fsync(descriptor)

    def fail_reconciliation_read(path):
        if new_pem is not None and Path(path) == signing_path:
            raise OSError("simulated reconciliation read failure")
        return real_read_bytes(path)

    with (
        patch.object(os, "fsync", fail_active_directory_sync),
        patch.object(Path, "read_bytes", fail_reconciliation_read),
    ):
        try:
            manager.rotate_key()
        except OSError:
            rotation_failed = True
        else:
            rotation_failed = False

    log_text = captured.getvalue()
    secret_line = new_pem.splitlines()[1].decode("ascii") if new_pem is not None else ""
    leaked = "PRIVATE KEY" in log_text or (secret_line and secret_line in log_text)
    results.put((rotation_failed, new_pem is not None, "could not reconcile JWT key state" in log_text, bool(leaked)))


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
    def test_server_settings_allow_read_only_key_store(self, monkeypatch):
        from config import Settings

        monkeypatch.delenv("JWT_KEY_READ_ONLY", raising=False)
        assert Settings(_env_file=None).JWT_KEY_READ_ONLY is False
        monkeypatch.setenv("JWT_KEY_READ_ONLY", "true")
        assert Settings(_env_file=None).JWT_KEY_READ_ONLY is True

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
        if os.name == "posix":
            lock_path = os.path.join(tmp_key_dir, ".signing.lock")
            assert oct(os.stat(lock_path).st_mode & 0o777) == "0o600"
            assert oct(os.stat(tmp_key_dir).st_mode & 0o777) == "0o700"


# ===================================================================
# Key persistence
# ===================================================================


class TestKeyPersistence:
    def test_missing_posix_lock_support_emits_warning(self, tmp_key_dir, monkeypatch):
        import io

        from loguru import logger

        import services.crypto as crypto_mod

        output = io.StringIO()
        handler_id = logger.add(output, format="{message}", level="WARNING")
        try:
            monkeypatch.setattr(crypto_mod, "fcntl", None)
            KeyManager(key_dir=tmp_key_dir).initialize()
        finally:
            logger.remove(handler_id)

        assert "without cross-process coordination" in output.getvalue()

    def test_managed_startup_does_not_bypass_lock_permission_error(self, tmp_key_dir, monkeypatch):
        if os.name == "nt":
            pytest.skip("POSIX advisory locking is unavailable")

        import services.crypto as crypto_mod

        KeyManager(key_dir=tmp_key_dir).initialize()
        lock_path = Path(tmp_key_dir) / ".signing.lock"
        real_open = os.open

        def deny_lock_open(path, flags, mode=0o777):
            if Path(path) == lock_path:
                raise PermissionError("simulated lock access denial")
            return real_open(path, flags, mode)

        monkeypatch.setattr(crypto_mod.os, "open", deny_lock_open)
        with pytest.raises(PermissionError, match="simulated lock access denial"):
            KeyManager(key_dir=tmp_key_dir).initialize()

    def test_concurrent_process_startup_shares_one_signing_key(self, tmp_key_dir):
        startup_results, verified_results = _run_concurrent_startup_workers(tmp_key_dir)
        manager = KeyManager(key_dir=tmp_key_dir, algorithm="RS256")
        manager.initialize()

        assert {kid for _, kid, _, _ in startup_results} == {manager.get_kid()}
        assert all(subjects == ["startup-worker"] * 4 for _, subjects, _ in verified_results)

    def test_read_only_store_loads_without_mutation(self, tmp_key_dir, monkeypatch):
        import services.crypto as crypto_mod

        monkeypatch.setattr(crypto_mod, "fcntl", None)
        key_dir = Path(tmp_key_dir)
        manager = KeyManager(key_dir=tmp_key_dir)
        manager.initialize()
        retired_kid = manager.get_kid()
        manager.rotate_key()
        token = manager.sign_token({"sub": "read-only"})
        retired_path = key_dir / f"retired_{retired_kid}.pem"
        old_time = time.time() - 2 * 86400
        os.utime(retired_path, (old_time, old_time))
        (key_dir / ".signing.lock").unlink(missing_ok=True)

        if os.name == "posix":
            for path in key_dir.iterdir():
                path.chmod(0o400)
            key_dir.chmod(0o500)

        def snapshot():
            return {
                path.name: (
                    path.read_bytes(),
                    path.stat().st_mtime_ns,
                    path.stat().st_mode & 0o777 if os.name == "posix" else None,
                )
                for path in sorted(key_dir.iterdir())
            }

        before = snapshot()
        real_unlink = Path.unlink

        def deny_key_store_unlink(path, *args, **kwargs):
            if path.parent == key_dir:
                raise AssertionError("read-only key store must not be pruned")
            return real_unlink(path, *args, **kwargs)

        def deny_key_store_mutation(*args, **kwargs):
            raise AssertionError("read-only key store must not be changed")

        monkeypatch.setattr(Path, "unlink", deny_key_store_unlink)
        monkeypatch.setattr(crypto_mod.os, "chmod", deny_key_store_mutation)
        monkeypatch.setattr(crypto_mod.os, "replace", deny_key_store_mutation)
        read_only = KeyManager(key_dir=tmp_key_dir, read_only=True)
        read_only.initialize()
        assert read_only.verify_token(token)["sub"] == "read-only"
        read_only.get_jwks()
        assert retired_path.exists()
        with pytest.raises(RuntimeError, match="read-only"):
            read_only.rotate_key()

        assert snapshot() == before

    @pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are unavailable on this platform")
    def test_managed_startup_fails_if_signing_key_cannot_be_restricted(self, tmp_key_dir, monkeypatch):
        import services.crypto as crypto_mod

        manager = KeyManager(key_dir=tmp_key_dir)
        manager.initialize()
        signing_path = Path(tmp_key_dir) / "signing.pem"
        signing_path.chmod(0o644)
        original_pem = signing_path.read_bytes()
        real_chmod = os.chmod

        def fail_signing_chmod(path, mode):
            if Path(path) == signing_path:
                raise PermissionError("simulated signing key permission failure")
            real_chmod(path, mode)

        monkeypatch.setattr(crypto_mod.os, "chmod", fail_signing_chmod)
        with pytest.raises(PermissionError, match="simulated signing key permission failure"):
            KeyManager(key_dir=tmp_key_dir).initialize()

        assert signing_path.read_bytes() == original_pem
        assert signing_path.stat().st_mode & 0o777 == 0o644

    @pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are unavailable on this platform")
    def test_managed_startup_rejects_key_if_chmod_does_not_restrict_it(self, tmp_key_dir, monkeypatch):
        import services.crypto as crypto_mod

        manager = KeyManager(key_dir=tmp_key_dir)
        manager.initialize()
        signing_path = Path(tmp_key_dir) / "signing.pem"
        signing_path.chmod(0o644)
        real_chmod = os.chmod

        def leave_signing_key_unchanged(path, mode):
            if Path(path) != signing_path:
                real_chmod(path, mode)

        monkeypatch.setattr(crypto_mod.os, "chmod", leave_signing_key_unchanged)
        with pytest.raises(PermissionError, match="permissions"):
            KeyManager(key_dir=tmp_key_dir).initialize()

        assert signing_path.stat().st_mode & 0o777 == 0o644

    @pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are unavailable on this platform")
    def test_read_only_manager_rejects_permissive_signing_key_without_mutating_it(self, tmp_key_dir):
        writable = KeyManager(key_dir=tmp_key_dir)
        writable.initialize()
        signing_path = Path(tmp_key_dir) / "signing.pem"
        original_pem = signing_path.read_bytes()
        signing_path.chmod(0o644)

        read_only = KeyManager(key_dir=tmp_key_dir, read_only=True)
        with pytest.raises(PermissionError, match="permissions"):
            read_only.initialize()

        assert signing_path.read_bytes() == original_pem
        assert signing_path.stat().st_mode & 0o777 == 0o644

    def test_read_only_manager_honors_retention_without_deleting_expired_keys(self, tmp_key_dir):
        manager = KeyManager(key_dir=tmp_key_dir, retired_key_retention_days=1)
        manager.initialize()
        old_kid = manager.get_kid()
        old_token = manager.sign_token({"sub": "expired"})
        manager.rotate_key()

        retired_path = Path(tmp_key_dir) / f"retired_{old_kid}.pem"
        old_time = time.time() - 2 * 86400
        os.utime(retired_path, (old_time, old_time))

        read_only = KeyManager(key_dir=tmp_key_dir, retired_key_retention_days=1, read_only=True)
        read_only.initialize()

        assert retired_path.exists()
        assert read_only.find_public_key(old_kid) is None
        assert all(key["kid"] != old_kid for key in read_only.get_jwks()["keys"])
        with pytest.raises(pyjwt.InvalidTokenError, match="Unknown key id"):
            read_only.verify_token(old_token)
        assert retired_path.exists()

    def test_read_only_manager_does_not_create_missing_store(self, tmp_path):
        missing_dir = tmp_path / "not-provisioned"
        manager = KeyManager(key_dir=str(missing_dir), read_only=True)

        with pytest.raises(FileNotFoundError, match="read-only"):
            manager.initialize()
        assert not missing_dir.exists()

    def test_read_only_manager_does_not_generate_missing_key(self, tmp_key_dir):
        manager = KeyManager(key_dir=tmp_key_dir, read_only=True)

        with pytest.raises(FileNotFoundError, match="read-only"):
            manager.initialize()
        assert not (Path(tmp_key_dir) / "signing.pem").exists()
        assert list(Path(tmp_key_dir).iterdir()) == []

    @pytest.mark.parametrize(
        "options",
        [{"algorithm": "RS256"}, {"key_password": "new-password"}],
        ids=["algorithm-change", "encryption-migration"],
    )
    def test_read_only_manager_refuses_key_migrations(self, tmp_key_dir, options):
        original = KeyManager(key_dir=tmp_key_dir)
        original.initialize()
        signing_path = Path(tmp_key_dir) / "signing.pem"
        original_pem = signing_path.read_bytes()

        read_only = KeyManager(key_dir=tmp_key_dir, read_only=True, **options)
        with pytest.raises(RuntimeError, match="read-only"):
            read_only.initialize()

        assert signing_path.read_bytes() == original_pem
        assert not list(Path(tmp_key_dir).glob("retired_*.pem"))

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

    def test_failed_initial_key_publication_leaves_no_partial_file(self, tmp_key_dir, monkeypatch):
        import services.crypto as crypto_mod

        signing_path = Path(tmp_key_dir) / "signing.pem"
        real_replace = os.replace

        def fail_signing_replace(source, destination):
            if Path(destination) == signing_path:
                raise OSError("simulated key publication failure")
            real_replace(source, destination)

        monkeypatch.setattr(crypto_mod.os, "replace", fail_signing_replace)
        with pytest.raises(OSError, match="simulated key publication failure"):
            KeyManager(key_dir=tmp_key_dir).initialize()

        assert not signing_path.exists()
        assert not list(Path(tmp_key_dir).glob(".signing.pem.*"))

        monkeypatch.setattr(crypto_mod.os, "replace", real_replace)
        recovered = KeyManager(key_dir=tmp_key_dir)
        recovered.initialize()
        token = recovered.sign_token({"sub": "recovered"})
        assert recovered.verify_token(token)["sub"] == "recovered"

    def test_failed_encryption_migration_preserves_existing_key(self, tmp_key_dir, monkeypatch):
        original = KeyManager(key_dir=tmp_key_dir)
        original.initialize()
        original_kid = original.get_kid()
        signing_path = Path(tmp_key_dir) / "signing.pem"
        original_pem = signing_path.read_bytes()

        import services.crypto as crypto_mod

        real_replace = os.replace

        def fail_signing_replace(source, destination):
            if Path(destination) == signing_path:
                raise OSError("simulated key publication failure")
            real_replace(source, destination)

        monkeypatch.setattr(crypto_mod.os, "replace", fail_signing_replace)
        protected = KeyManager(key_dir=tmp_key_dir, key_password="new-password")
        with pytest.raises(OSError, match="simulated key publication failure"):
            protected.initialize()

        assert signing_path.read_bytes() == original_pem
        assert not list(Path(tmp_key_dir).glob(".signing.pem.*"))
        reloaded = KeyManager(key_dir=tmp_key_dir)
        reloaded.initialize()
        assert reloaded.get_kid() == original_kid

    def test_unreadable_existing_key_does_not_get_replaced(self, tmp_key_dir, monkeypatch):
        signing_path = Path(tmp_key_dir) / "signing.pem"
        existing_pem = b"existing but inaccessible key"
        signing_path.write_bytes(existing_pem)
        real_lstat = Path.lstat

        def fail_signing_lstat(path, *args, **kwargs):
            if Path(path) == signing_path:
                raise PermissionError("simulated key access denial")
            return real_lstat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", fail_signing_lstat)
        with pytest.raises(PermissionError, match="simulated key access denial"):
            KeyManager(key_dir=tmp_key_dir).initialize()

        assert signing_path.read_bytes() == existing_pem
        assert not list(Path(tmp_key_dir).glob("retired_*.pem"))

    @pytest.mark.skipif(os.name == "nt", reason="symlink creation may require elevated privileges on Windows")
    def test_broken_signing_symlink_is_not_replaced(self, tmp_key_dir):
        signing_path = Path(tmp_key_dir) / "signing.pem"
        signing_path.symlink_to("missing-key.pem")

        with pytest.raises(FileNotFoundError):
            KeyManager(key_dir=tmp_key_dir).initialize()

        assert signing_path.is_symlink()
        assert not list(Path(tmp_key_dir).glob(".signing.pem.*"))

    def test_corrupt_existing_key_is_not_replaced(self, tmp_key_dir):
        signing_path = Path(tmp_key_dir) / "signing.pem"
        invalid_pem = b"not a private key"
        signing_path.write_bytes(invalid_pem)

        with pytest.raises((TypeError, ValueError)):
            KeyManager(key_dir=tmp_key_dir).initialize()

        assert signing_path.read_bytes() == invalid_pem
        assert not list(Path(tmp_key_dir).glob("retired_*.pem"))

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

    def test_rotation_key_generation_does_not_block_readers(self, tmp_key_dir, monkeypatch):
        manager = KeyManager(key_dir=tmp_key_dir)
        manager.initialize()
        old_kid = manager.get_kid()
        generation_started = Event()
        continue_generation = Event()
        generate_private_key = ec.generate_private_key

        def delayed_generation(curve):
            generation_started.set()
            assert continue_generation.wait(timeout=10), "key generation was not released"
            return generate_private_key(curve)

        monkeypatch.setattr("services.crypto.ec.generate_private_key", delayed_generation)
        with ThreadPoolExecutor(max_workers=2) as pool:
            rotation = pool.submit(manager.rotate_key)
            try:
                assert generation_started.wait(timeout=10), "key generation did not start"
                assert pool.submit(manager.get_kid).result(timeout=2) == old_kid
            finally:
                continue_generation.set()
            assert rotation.result(timeout=10) != old_kid

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

    def test_running_manager_uses_rotated_key_without_restarting(self, tmp_key_dir):
        first = KeyManager(key_dir=tmp_key_dir)
        first.initialize()
        second = KeyManager(key_dir=tmp_key_dir)
        second.initialize()
        old_token = second.sign_token({"sub": "old"})

        new_kid = first.rotate_key()
        new_token = first.sign_token({"sub": "new"})
        assert pyjwt.get_unverified_header(new_token)["kid"] == new_kid
        assert second.verify_token(new_token)["sub"] == "new"

        second_token = second.sign_token({"sub": "second"})
        assert pyjwt.get_unverified_header(second_token)["kid"] == new_kid
        assert first.verify_token(second_token)["sub"] == "second"
        assert first.verify_token(old_token)["sub"] == "old"
        assert second.verify_token(old_token)["sub"] == "old"

    @pytest.mark.parametrize(
        ("algorithm", "password"),
        [("ES256", None), ("RS256", "encrypted-test-key")],
        ids=["es256", "encrypted-rs256"],
    )
    def test_stale_manager_rotates_actual_active_key_and_keeps_intermediate_token(
        self, tmp_key_dir, algorithm, password
    ):
        first = KeyManager(key_dir=tmp_key_dir, algorithm=algorithm, key_password=password)
        first.initialize()
        second = KeyManager(key_dir=tmp_key_dir, algorithm=algorithm, key_password=password)
        second.initialize()
        tokens = [first.sign_token({"sub": "generation-1"})]

        first.rotate_key()
        tokens.append(first.sign_token({"sub": "generation-2"}))
        second.rotate_key()
        tokens.append(second.sign_token({"sub": "generation-3"}))

        restarted = KeyManager(key_dir=tmp_key_dir, algorithm=algorithm, key_password=password)
        restarted.initialize()
        assert [restarted.verify_token(token)["sub"] for token in tokens] == [
            "generation-1",
            "generation-2",
            "generation-3",
        ]
        assert {pyjwt.get_unverified_header(token)["kid"] for token in tokens} == {
            key["kid"] for key in restarted.get_jwks()["keys"]
        }
        if password:
            assert b"ENCRYPTED" in (Path(tmp_key_dir) / "signing.pem").read_bytes()

    @pytest.mark.skipif(os.name == "nt", reason="Windows multi-process key-store coordination is unsupported")
    @pytest.mark.parametrize("algorithm", ["ES256", "RS256"])
    def test_concurrent_process_rotations_retain_each_predecessor(self, tmp_key_dir, algorithm):
        seed = KeyManager(key_dir=tmp_key_dir, algorithm=algorithm)
        seed.initialize()
        initial_token = seed.sign_token({"sub": "initial"})
        initial_kid = seed.get_kid()

        context = multiprocessing.get_context("spawn")
        start_barrier = context.Barrier(3)
        results = context.Queue()
        workers = [
            context.Process(
                target=_concurrent_rotation_worker, args=(tmp_key_dir, algorithm, start_barrier, i, results)
            )
            for i in range(2)
        ]
        try:
            for worker in workers:
                start_spawned_test_process(worker)
            start_barrier.wait(timeout=20)
            rotated = [results.get(timeout=45) for _ in workers]
            assert all(error is None for _, _, error in rotated), rotated
            for worker in workers:
                worker.join(timeout=45)
                assert not worker.is_alive(), "rotation worker did not exit"
                assert worker.exitcode == 0
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                if worker.pid is not None:
                    worker.join(timeout=5)
            results.close()
            results.join_thread()

        restarted = KeyManager(key_dir=tmp_key_dir, algorithm=algorithm)
        restarted.initialize()
        expected_kids = {initial_kid, *(kid for kid, _, _ in rotated)}
        assert len(expected_kids) == 3
        assert expected_kids == {key["kid"] for key in restarted.get_jwks()["keys"]}
        assert restarted.verify_token(initial_token)["sub"] == "initial"
        for _, token, _ in rotated:
            assert restarted.verify_token(token)["sub"].startswith("worker-")

    def test_in_flight_signing_keeps_a_coherent_key_during_rotation(self, tmp_key_dir, monkeypatch):
        signer = KeyManager(key_dir=tmp_key_dir)
        signer.initialize()
        verifier = KeyManager(key_dir=tmp_key_dir)
        verifier.initialize()
        original_kid = signer.get_kid()

        started = Event()
        continue_signing = Event()
        real_encode = pyjwt.encode

        def paused_encode(*args, **kwargs):
            started.set()
            assert continue_signing.wait(timeout=10), "signing was not released"
            return real_encode(*args, **kwargs)

        monkeypatch.setattr("services.crypto.jwt.encode", paused_encode)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(signer.sign_token, {"sub": "in-flight"})
            try:
                assert started.wait(timeout=10), "signing did not start"
                replacement_kid = signer.rotate_key()
            finally:
                continue_signing.set()
            token = pending.result(timeout=10)

        assert pyjwt.get_unverified_header(token)["kid"] == original_kid
        assert verifier.verify_token(token)["sub"] == "in-flight"
        following_token = signer.sign_token({"sub": "after"})
        assert pyjwt.get_unverified_header(following_token)["kid"] == replacement_kid
        assert verifier.verify_token(following_token)["sub"] == "after"

    @pytest.mark.parametrize(
        "failed_destination", ["retired", "signing"], ids=["before-retirement", "before-activation"]
    )
    def test_failed_rotation_preserves_old_key_and_recovers_on_retry(
        self, tmp_key_dir, monkeypatch, failed_destination
    ):
        manager = KeyManager(key_dir=tmp_key_dir, retired_key_retention_days=1)
        manager.initialize()
        old_kid = manager.get_kid()
        token = manager.sign_token({"sub": "still-valid"})
        real_replace = os.replace

        def fail_publication(source, destination):
            destination_name = Path(destination).name
            should_fail = (
                destination_name.startswith("retired_")
                if failed_destination == "retired"
                else destination_name == "signing.pem"
            )
            if should_fail:
                raise OSError("simulated rotation publication failure")
            real_replace(source, destination)

        with monkeypatch.context() as patch:
            patch.setattr("services.crypto.os.replace", fail_publication)
            with pytest.raises(OSError, match="simulated rotation publication failure"):
                manager.rotate_key()

        assert manager.get_kid() == old_kid
        assert manager.verify_token(token)["sub"] == "still-valid"
        assert [key["kid"] for key in manager.get_jwks()["keys"]] == [old_kid]
        assert not list(Path(tmp_key_dir).glob(".retired_*.pem.*"))
        assert not list(Path(tmp_key_dir).glob(".signing.pem.*"))

        # When retirement was published but activation failed, a retry must
        # timestamp the *actual* retirement (not reuse an expired archive).
        retired_path = Path(tmp_key_dir) / f"retired_{old_kid}.pem"
        if retired_path.exists():
            old_time = time.time() - 2 * 86400
            os.utime(retired_path, (old_time, old_time))
        manager.rotate_key()
        restarted = KeyManager(key_dir=tmp_key_dir, retired_key_retention_days=1)
        restarted.initialize()
        assert restarted.verify_token(token)["sub"] == "still-valid"
        assert len({key["kid"] for key in restarted.get_jwks()["keys"]}) == 2

    @pytest.mark.skipif(os.name != "posix", reason="directory fsync is only used on POSIX")
    def test_failed_sync_after_active_publication_is_reconciled(self, tmp_key_dir, monkeypatch):
        manager = KeyManager(key_dir=tmp_key_dir)
        manager.initialize()
        previous_kid = manager.get_kid()
        old_token = manager.sign_token({"sub": "old"})
        signing_path = Path(tmp_key_dir) / "signing.pem"
        previous_pem = signing_path.read_bytes()
        real_fsync = os.fsync

        def fail_active_directory_sync(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode) and signing_path.read_bytes() != previous_pem:
                raise OSError("simulated directory sync failure after active publication")
            real_fsync(descriptor)

        with monkeypatch.context() as patch:
            patch.setattr("services.crypto.os.fsync", fail_active_directory_sync)
            with pytest.raises(OSError, match="simulated directory sync failure after active publication"):
                manager.rotate_key()

        assert manager.get_kid() != previous_kid
        new_token = manager.sign_token({"sub": "new"})
        assert not list(Path(tmp_key_dir).glob(".signing.pem.*"))
        assert not list(Path(tmp_key_dir).glob(".retired_*.pem.*"))

        restarted = KeyManager(key_dir=tmp_key_dir)
        restarted.initialize()
        assert restarted.verify_token(old_token)["sub"] == "old"
        assert restarted.verify_token(new_token)["sub"] == "new"
        next_kid = manager.rotate_key()
        assert next_kid not in {previous_kid, pyjwt.get_unverified_header(new_token)["kid"]}
        assert restarted.verify_token(manager.sign_token({"sub": "latest"}))["sub"] == "latest"

    @pytest.mark.skipif(os.name != "posix", reason="directory fsync is only used on POSIX")
    def test_failed_rotation_reconciliation_never_logs_private_key(self, tmp_key_dir):
        context = multiprocessing.get_context("spawn")
        results = context.Queue()
        worker = context.Process(target=_rotation_log_safety_worker, args=(tmp_key_dir, results))
        try:
            start_spawned_test_process(worker)
            rotation_failed, replacement_published, diagnostic_logged, leaked = results.get(timeout=20)
            worker.join(timeout=20)
            assert worker.exitcode == 0
            assert rotation_failed and replacement_published and diagnostic_logged
            assert not leaked, "rotation failure logged private key material"
        finally:
            if worker.is_alive():
                worker.terminate()
            if worker.pid is not None:
                worker.join(timeout=5)
            results.close()
            results.join_thread()

    @pytest.mark.skipif(os.name == "nt", reason="Windows multi-process key-store coordination is unsupported")
    @pytest.mark.parametrize(
        ("destination", "after_replace"),
        [("retired", False), ("retired", True), ("signing", False), ("signing", True)],
        ids=["before-retirement", "after-retirement", "before-activation", "after-activation"],
    )
    def test_restart_recovers_after_rotation_process_exits_at_publication(
        self, tmp_key_dir, destination, after_replace
    ):
        seed = KeyManager(key_dir=tmp_key_dir)
        seed.initialize()
        old_kid = seed.get_kid()
        old_token = seed.sign_token({"sub": "before-crash"})
        destination_name = f"retired_{old_kid}.pem" if destination == "retired" else "signing.pem"

        context = multiprocessing.get_context("spawn")
        worker = context.Process(
            target=_crash_during_rotation_worker, args=(tmp_key_dir, destination_name, after_replace)
        )
        try:
            start_spawned_test_process(worker)
            worker.join(timeout=30)
            assert not worker.is_alive(), "crashed rotation process did not exit"
            assert worker.exitcode == 73
        finally:
            if worker.is_alive():
                worker.terminate()
            if worker.pid is not None:
                worker.join(timeout=5)

        restarted = KeyManager(key_dir=tmp_key_dir)
        restarted.initialize()
        assert restarted.verify_token(old_token)["sub"] == "before-crash"
        active_changed = destination == "signing" and after_replace
        assert len(restarted.get_jwks()["keys"]) == (2 if active_changed else 1)
        assert (restarted.get_kid() != old_kid) is active_changed
        # An orphaned temporary file is permitted after a hard exit, but the
        # active key is complete and signing continues after recovery.
        new_token = restarted.sign_token({"sub": "after-crash"})
        verifier = KeyManager(key_dir=tmp_key_dir)
        verifier.initialize()
        assert verifier.verify_token(new_token)["sub"] == "after-crash"

    def test_rotation_refuses_to_overwrite_a_conflicting_retirement(self, tmp_key_dir, tmp_path):
        manager = KeyManager(key_dir=tmp_key_dir)
        manager.initialize()
        old_kid = manager.get_kid()
        foreign = KeyManager(key_dir=str(tmp_path / "foreign"))
        foreign.initialize()
        retired_path = Path(tmp_key_dir) / f"retired_{old_kid}.pem"
        foreign_public_key = foreign.get_public_key_pem().encode()
        retired_path.write_bytes(foreign_public_key)

        with pytest.raises(RuntimeError, match="Conflicting retired JWT key record"):
            manager.rotate_key()
        assert manager.get_kid() == old_kid
        assert retired_path.read_bytes() == foreign_public_key

    def test_unrelated_damaged_retired_key_does_not_block_valid_tokens(self, tmp_key_dir):
        manager = KeyManager(key_dir=tmp_key_dir)
        manager.initialize()
        old_token = manager.sign_token({"sub": "retired"})
        manager.rotate_key()
        current_token = manager.sign_token({"sub": "current"})
        (Path(tmp_key_dir) / "retired_damaged.pem").write_bytes(b"not a public key")

        assert manager.verify_token(old_token)["sub"] == "retired"
        assert manager.verify_token(current_token)["sub"] == "current"
        assert len(manager.get_jwks()["keys"]) == 2

    def test_missing_active_key_during_normal_operation_is_not_regenerated(self, tmp_key_dir):
        manager = KeyManager(key_dir=tmp_key_dir)
        manager.initialize()
        token = manager.sign_token({"sub": "recoverable"})
        signing_path = Path(tmp_key_dir) / "signing.pem"
        saved_path = Path(tmp_key_dir) / "saved-signing.pem"
        signing_path.rename(saved_path)
        try:
            with pytest.raises(KeyStoreUnavailableError):
                manager.sign_token({"sub": "must-not-be-signed"})
            with pytest.raises(KeyStoreUnavailableError):
                manager.verify_token(token)
            with pytest.raises(KeyStoreUnavailableError):
                manager.rotate_key()
            assert not signing_path.exists()
        finally:
            saved_path.rename(signing_path)
        assert manager.verify_token(token)["sub"] == "recoverable"

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

    def test_live_algorithm_change_rejects_stale_signing_configuration(self, tmp_key_dir):
        old = KeyManager(key_dir=tmp_key_dir, algorithm="ES256")
        old.initialize()
        old_token = old.sign_token({"sub": "old-algorithm"})

        current = KeyManager(key_dir=tmp_key_dir, algorithm="RS256")
        current.initialize()
        current_token = current.sign_token({"sub": "new-algorithm"})
        current_kid = current.get_kid()

        assert old.verify_token(current_token)["sub"] == "new-algorithm"
        assert current.verify_token(old_token)["sub"] == "old-algorithm"
        with pytest.raises(RuntimeError, match="algorithm conflicts"):
            old.sign_token({"sub": "must-not-be-signed"})
        with pytest.raises(RuntimeError, match="algorithm conflicts"):
            old.rotate_key()
        assert current.get_kid() == current_kid
        assert current.verify_token(current.sign_token({"sub": "still-current"}))["sub"] == "still-current"

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

    def test_init_key_manager_supports_read_only_store(self, tmp_key_dir):
        initial = KeyManager(key_dir=tmp_key_dir)
        initial.initialize()
        expected_kid = initial.get_kid()

        manager = init_key_manager(key_dir=tmp_key_dir, read_only=True)
        assert manager.get_kid() == expected_kid
        assert get_key_manager() is manager

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
    async def test_jwks_endpoint_sees_another_managers_rotation(self, jwks_client, tmp_key_dir):
        previous = await jwks_client.get("/api/v1/auth/.well-known/jwks.json")
        previous_kid = previous.json()["keys"][0]["kid"]
        rotator = KeyManager(key_dir=tmp_key_dir)
        rotator.initialize()
        new_kid = rotator.rotate_key()

        response = await jwks_client.get("/api/v1/auth/.well-known/jwks.json")
        assert response.status_code == 200
        keys = response.json()["keys"]
        assert [key["kid"] for key in keys] == [new_kid, previous_kid]
        private_fields = {"private", "d", "p", "q", "dp", "dq", "qi", "oth", "k"}
        assert all(private_fields.isdisjoint(key) for key in keys)

    @pytest.mark.asyncio
    async def test_jwks_endpoint_cache_header(self, jwks_client):
        resp = await jwks_client.get("/api/v1/auth/.well-known/jwks.json")
        directives = {directive.strip().lower() for directive in resp.headers.get("cache-control", "").split(",")}
        assert "no-cache" in directives
        assert "must-revalidate" in directives
        assert "max-age=3600" not in directives
