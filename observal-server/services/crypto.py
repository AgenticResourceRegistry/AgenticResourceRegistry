# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Ravi Chopra <shivamchopra1234567890@gmail.com>
# SPDX-FileCopyrightText: 2026 SrihariLegend <sriharilegend23@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Asymmetric JWT signing, verification, JWKS publication, and key rotation."""

from __future__ import annotations

import base64
import errno
import hashlib
import os
import tempfile
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Iterator

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    fcntl = None
else:
    fcntl = _fcntl

import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from loguru import logger as optic

SigningAlgorithm = Literal["ES256", "RS256"]
PrivateKey: TypeAlias = ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey
PublicKey: TypeAlias = ec.EllipticCurvePublicKey | rsa.RSAPublicKey
SUPPORTED_ALGORITHMS = frozenset({"ES256", "RS256"})
KEY_STORE_LOCK_TIMEOUT_SECONDS = 5.0
_KEY_STORE_LOCK_RETRY_SECONDS = 0.05


class KeyStoreUnavailableError(RuntimeError):
    """Raised when managed JWT key material is unavailable during normal operation."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _algorithm_for_key(key: PrivateKey | PublicKey) -> SigningAlgorithm:
    if isinstance(key, (ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey)):
        if key.curve.name != "secp256r1":
            raise TypeError(f"Unsupported EC curve: {key.curve.name}")
        return "ES256"
    if isinstance(key, (rsa.RSAPrivateKey, rsa.RSAPublicKey)):
        if key.key_size < 2048:
            raise TypeError(f"Unsupported RSA key size: {key.key_size}")
        return "RS256"
    raise TypeError(f"Unsupported signing key type: {type(key).__name__}")


def _kid_from_public_key(pub: PublicKey) -> str:
    raw = pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(raw).hexdigest()[:16]


def _public_key_to_jwk(pub: PublicKey, kid: str) -> dict:
    algorithm = _algorithm_for_key(pub)
    if algorithm == "ES256":
        numbers = pub.public_numbers()
        return {
            "kty": "EC",
            "crv": "P-256",
            "x": _b64url(numbers.x.to_bytes(32, "big")),
            "y": _b64url(numbers.y.to_bytes(32, "big")),
            "kid": kid,
            "use": "sig",
            "alg": algorithm,
        }

    numbers = pub.public_numbers()
    return {
        "kty": "RSA",
        "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
        "kid": kid,
        "use": "sig",
        "alg": algorithm,
    }


@dataclass(frozen=True, slots=True)
class _ActiveKey:
    private_key: PrivateKey
    public_key: PublicKey
    kid: str
    algorithm: SigningAlgorithm
    pem: bytes


@dataclass(frozen=True, slots=True)
class _RetiredKey:
    public_key: PublicKey
    kid: str
    signature: tuple[int, int, int, int, int]
    pem: bytes


@dataclass(frozen=True, slots=True)
class _RetiredKeyIdentity:
    kid: str
    expires_at: float


def _is_required_retired_file(
    required_kid: str | None,
    retired_kid: str,
    identity: _RetiredKeyIdentity | None,
    now: float,
) -> bool:
    if required_kid is None:
        return False
    identity_is_retained = identity is not None and identity.expires_at >= now
    return (required_kid == retired_kid and (identity is None or identity_is_retained)) or (
        identity_is_retained and identity is not None and identity.kid == required_kid
    )


class KeyManager:
    """Manage ES256 or RS256 JWT keys while retaining old verification keys."""

    def __init__(
        self,
        key_dir: str = "~/.observal/keys",
        key_password: str | None = None,
        algorithm: SigningAlgorithm = "ES256",
        retired_key_retention_days: int = 30,
        read_only: bool = False,
    ) -> None:
        if algorithm not in SUPPORTED_ALGORITHMS:
            raise ValueError(f"Unsupported JWT signing algorithm: {algorithm}")
        self._key_dir = Path(key_dir).expanduser()
        self._key_password = key_password.encode() if key_password else None
        self._algorithm = algorithm
        self._read_only = read_only
        self._retired_key_retention_seconds = max(retired_key_retention_days, 1) * 86400
        # Always take the local lock before the filesystem lock. Readers publish
        # snapshots in one step; the key, kid and algorithm can never be mixed.
        self._state_lock = RLock()
        self._active: _ActiveKey | None = None
        self._retired_files: dict[str, _RetiredKey] = {}
        self._invalid_retired_files: dict[str, tuple[tuple[int, int, int, int, int], bytes]] = {}
        # Preserve locally validated key identities through retired-file loss/corruption.
        self._retired_key_identities: dict[str, _RetiredKeyIdentity] = {}
        self._retired_keys: dict[str, PublicKey] = {}
        self._warned_uncoordinated = False

    @property
    def algorithm(self) -> SigningAlgorithm:
        return self._algorithm

    def initialize(self) -> None:
        optic.debug("initializing JWT key manager")
        signing_path = self._key_dir / "signing.pem"
        with self._state_lock:
            if self._read_only:
                if not self._key_dir.is_dir():
                    raise FileNotFoundError(f"JWT key directory does not exist in read-only mode: {self._key_dir}")
                try:
                    signing_path.stat()
                except FileNotFoundError as exc:
                    raise FileNotFoundError(
                        f"JWT signing key does not exist in read-only mode: {signing_path}"
                    ) from exc
                self._load_private_key(signing_path, allow_migration=False)
                if self._require_active().algorithm != self._algorithm:
                    raise RuntimeError("Configured JWT algorithm does not match the key in the read-only key store")
                self._load_retired_keys(prune=False)
            else:
                self._key_dir.mkdir(parents=True, exist_ok=True)
                try:
                    os.chmod(self._key_dir, 0o700)
                except OSError:
                    optic.warning("could not restrict JWT key directory permissions")

                with self._signing_lock(create=True):
                    try:
                        signing_path.lstat()
                    except FileNotFoundError:
                        self._generate_key_pair(signing_path)
                    else:
                        self._load_private_key(signing_path)
                        if self._require_active().algorithm != self._algorithm:
                            optic.info("JWT algorithm changed; retiring current signing key")
                            self._retire_current_key()
                            self._generate_key_pair(signing_path)
                    self._load_retired_keys()
            optic.info("JWT signing key ready (alg={}, kid={})", self._algorithm, self._require_active().kid)

    def _require_active(self) -> _ActiveKey:
        if self._active is None:
            raise RuntimeError("KeyManager has not been initialized")
        return self._active

    def _refresh_active_key(self) -> None:
        """Observe the authoritative private key; never generate or migrate on reads."""
        path = self._key_dir / "signing.pem"
        try:
            if os.name == "posix":
                self._ensure_private_key_permissions(path, read_only=True)
            pem = path.read_bytes()
            # Read-only deployments adopt externally provisioned keys only after restart.
            if self._read_only and self._active is not None and pem != self._active.pem:
                raise KeyStoreUnavailableError("JWT signing-key store is unavailable")
            if self._active is None or pem != self._active.pem:
                self._load_private_key(path, allow_migration=False, pem=pem)
        except KeyStoreUnavailableError:
            raise
        except (OSError, TypeError, ValueError, RuntimeError, UnsupportedAlgorithm):
            raise KeyStoreUnavailableError("JWT signing-key store is unavailable") from None

    def _snapshot(
        self, *, kid: str | None = None, include_retired: bool = False
    ) -> tuple[_ActiveKey, dict[str, PublicKey]]:
        with self._state_lock:
            self._require_active()
            try:
                with nullcontext() if self._read_only else self._signing_lock(shared=True):
                    self._refresh_active_key()
                    active = self._require_active()
                    if include_retired or (kid is not None and kid != active.kid):
                        self._load_retired_keys(
                            prune=False,
                            required_kid=kid,
                            require_complete_set=include_retired,
                        )
            except KeyStoreUnavailableError:
                raise
            except (OSError, TypeError, ValueError, RuntimeError, UnsupportedAlgorithm):
                raise KeyStoreUnavailableError("JWT signing-key store is unavailable") from None
            return active, self._retired_keys if include_retired or (kid is not None and kid != active.kid) else {}

    def get_private_key(self) -> PrivateKey:
        return self._snapshot()[0].private_key

    def get_public_key(self) -> PublicKey:
        return self._snapshot()[0].public_key

    def get_kid(self) -> str:
        return self._snapshot()[0].kid

    def get_public_key_pem(self) -> str:
        active, _ = self._snapshot()
        return active.public_key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

    def get_jwks(self) -> dict:
        active, retired = self._snapshot(include_retired=True)
        optic.trace("building JWT JWKS with {} retired keys", len(retired))
        keys = [_public_key_to_jwk(active.public_key, active.kid)]
        keys.extend(_public_key_to_jwk(pub, kid) for kid, pub in retired.items())
        return {"keys": keys}

    def rotate_key(self) -> str:
        if self._read_only:
            raise RuntimeError("Cannot rotate JWT signing key in read-only mode")
        optic.info("rotating JWT signing key")
        new_key = self._generate_private_key()
        with self._state_lock, self._signing_lock():
            self._refresh_active_key()
            if self._require_active().algorithm != self._algorithm:
                raise RuntimeError("Configured JWT signing algorithm conflicts with the active key in the shared store")
            try:
                self._retire_current_key()
                pem = self._write_private_key(self._key_dir / "signing.pem", new_key)
                self._set_active_key(new_key, pem)
                self._load_retired_keys()
            except Exception:
                # An error after os.replace may mean the new key was published.
                # Reconcile under the same lock; never automatically rotate again.
                optic.warning("JWT rotation failed; active key may have changed; inspect the key store before retrying")
                try:
                    self._refresh_active_key()
                    self._load_retired_keys(prune=False)
                except Exception:
                    # Loguru's exception diagnostics can include the private key from local frames.
                    optic.error("could not reconcile JWT key state after failed rotation")
                raise
            return self._require_active().kid

    def find_public_key(self, kid: str) -> PublicKey | None:
        active, retired = self._snapshot(kid=kid)
        return active.public_key if kid == active.kid else retired.get(kid)

    def sign_token(self, payload: dict) -> str:
        active, _ = self._snapshot()
        if active.algorithm != self._algorithm:
            raise RuntimeError("Configured JWT signing algorithm conflicts with the active key in the shared store")
        optic.trace("signing JWT token with kid={}", active.kid)
        return jwt.encode(payload, active.private_key, algorithm=active.algorithm, headers={"kid": active.kid})

    def verify_token(self, token: str) -> dict:
        optic.trace("verifying JWT token")
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise jwt.InvalidTokenError("Token is missing a key id")
        pub = self.find_public_key(kid)
        if pub is None:
            raise jwt.InvalidTokenError(f"Unknown key id: {kid}")
        expected_algorithm = _algorithm_for_key(pub)
        if header.get("alg") != expected_algorithm:
            raise jwt.InvalidAlgorithmError("Token algorithm does not match its signing key")
        return jwt.decode(token, pub, algorithms=[expected_algorithm])

    @contextmanager
    def _signing_lock(self, *, shared: bool = False, create: bool = False) -> Iterator[None]:
        if fcntl is None:
            if not self._warned_uncoordinated:
                optic.warning("fcntl is unavailable; JWT key operations are running without cross-process coordination")
                self._warned_uncoordinated = True
            yield
            return

        lock_path = self._key_dir / ".signing.lock"
        flags = (os.O_CREAT | os.O_RDWR) if create else (os.O_RDONLY if shared else os.O_RDWR)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError:
            if create:
                raise
            raise KeyStoreUnavailableError("JWT signing-key store is unavailable") from None

        try:
            if create:
                try:
                    os.chmod(lock_path, 0o600)
                except OSError:
                    optic.warning("could not restrict JWT signing lock permissions")

            operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            deadline = time.monotonic() + KEY_STORE_LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno == errno.EINTR:
                        if time.monotonic() >= deadline:
                            raise KeyStoreUnavailableError("JWT signing-key store is unavailable") from None
                        continue
                    if not isinstance(exc, BlockingIOError) and exc.errno not in {
                        errno.EACCES,
                        errno.EAGAIN,
                        errno.EWOULDBLOCK,
                    }:
                        raise KeyStoreUnavailableError("JWT signing-key store is unavailable") from None
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise KeyStoreUnavailableError("JWT signing-key store is unavailable") from None
                    time.sleep(min(_KEY_STORE_LOCK_RETRY_SECONDS, remaining))

            try:
                yield
            finally:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    raise KeyStoreUnavailableError("JWT signing-key store is unavailable") from None
        finally:
            try:
                os.close(descriptor)
            except OSError:
                raise KeyStoreUnavailableError("JWT signing-key store is unavailable") from None

    def _atomic_write(self, path: Path, data: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            else:  # pragma: no cover - platform-specific fallback
                os.chmod(temporary_path, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            if os.name == "posix":
                directory_descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    def _encryption_args(self) -> serialization.KeySerializationEncryption:
        if self._key_password:
            return serialization.BestAvailableEncryption(self._key_password)
        return serialization.NoEncryption()

    def _write_private_key(self, path: Path, key: PrivateKey) -> bytes:
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            self._encryption_args(),
        )
        self._atomic_write(path, pem)
        return pem

    def _set_active_key(self, key: PrivateKey, pem: bytes) -> None:
        pub = key.public_key()
        self._active = _ActiveKey(key, pub, _kid_from_public_key(pub), _algorithm_for_key(key), pem)

    def _generate_private_key(self) -> PrivateKey:
        optic.debug("generating {} JWT signing key", self._algorithm)
        if self._algorithm == "ES256":
            return ec.generate_private_key(ec.SECP256R1())
        return rsa.generate_private_key(public_exponent=65537, key_size=3072)

    def _generate_key_pair(self, path: Path) -> None:
        key = self._generate_private_key()
        pem = self._write_private_key(path, key)
        self._set_active_key(key, pem)

    def _ensure_private_key_permissions(self, path: Path, *, read_only: bool) -> None:
        if os.name != "posix":
            if read_only:
                optic.warning("cannot verify JWT signing key permissions on a non-POSIX platform; restrict its ACL")
            else:
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    optic.warning("could not restrict JWT signing key permissions")
            return

        if not read_only:
            os.chmod(path, 0o600)
        permissions = path.stat().st_mode & 0o777
        if permissions & 0o077:
            raise PermissionError(f"JWT signing key permissions are too permissive: {path}")

    def _load_private_key(self, path: Path, *, allow_migration: bool = True, pem: bytes | None = None) -> None:
        optic.trace("loading JWT signing key from {}", path.name)
        if pem is None:
            pem = path.read_bytes()
        encrypt_existing_key = False
        try:
            key = serialization.load_pem_private_key(pem, password=self._key_password)
        except TypeError:
            if not self._key_password:
                raise
            key = serialization.load_pem_private_key(pem, password=None)
            encrypt_existing_key = True
        if not isinstance(key, (ec.EllipticCurvePrivateKey, rsa.RSAPrivateKey)):
            raise TypeError(f"Unsupported private key type: {type(key).__name__}")
        _algorithm_for_key(key)
        self._ensure_private_key_permissions(path, read_only=not allow_migration)
        if encrypt_existing_key:
            if not allow_migration:
                mode = "read-only mode" if self._read_only else "normal operation"
                raise RuntimeError(f"Cannot encrypt existing JWT signing key during {mode}")
            pem = self._write_private_key(path, key)
            optic.info("encrypted existing JWT signing key with configured password")
        self._set_active_key(key, pem)

    def _retire_current_key(self) -> None:
        active = self._require_active()
        retired_path = self._key_dir / f"retired_{active.kid}.pem"
        pem = active.public_key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        try:
            retired_path.lstat()
        except FileNotFoundError:
            pass
        else:
            existing = serialization.load_pem_public_key(retired_path.read_bytes())
            if (
                existing.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
                != pem
            ):
                raise RuntimeError(f"Conflicting retired JWT key record: {retired_path.name}")
        self._atomic_write(retired_path, pem)

    def _retired_key_paths(self) -> list[Path]:
        """Enumerate retired keys without suppressing directory I/O errors."""
        with os.scandir(self._key_dir) as entries:
            return [
                self._key_dir / entry.name
                for entry in entries
                if entry.name.startswith("retired_") and entry.name.endswith(".pem")
            ]

    def _prune_retired_keys(self) -> None:
        """Only called while holding exclusive coordination (never from a read)."""
        cutoff = time.time() - self._retired_key_retention_seconds
        for path in self._retired_key_paths():
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                path.unlink()
                self._retired_key_identities.pop(path.name, None)
                optic.info("removed expired retired JWT key file {}", path.name)
            except OSError:
                optic.warning("could not prune retired key file {}", path.name)

    def _load_retired_keys(
        self,
        *,
        prune: bool = True,
        required_kid: str | None = None,
        require_complete_set: bool = False,
    ) -> None:
        if prune:
            self._prune_retired_keys()
        now = time.time()
        cutoff = now - self._retired_key_retention_seconds
        active_kid = self._require_active().kid
        files: dict[str, _RetiredKey] = {}
        invalid_files: dict[str, tuple[tuple[int, int, int, int, int], bytes]] = {}
        eligible: dict[str, PublicKey] = {}
        observed_paths: set[str] = set()
        required_key_unavailable = False
        for path in self._retired_key_paths():
            retired_kid = path.name[len("retired_") : -len(".pem")]
            known_identity = self._retired_key_identities.get(path.name)
            try:
                metadata = path.stat()
            except OSError:
                optic.warning("could not inspect retired JWT key file {}", path.name)
                if _is_required_retired_file(required_kid, retired_kid, known_identity, now):
                    required_key_unavailable = True
                continue
            observed_paths.add(path.name)
            if metadata.st_mtime < cutoff:
                self._retired_key_identities.pop(path.name, None)
                continue
            # Managed replacements change inode; timestamps also track expiry and mode changes.
            signature = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)
            if known_identity is not None:
                # Retention follows the current file mtime even when its bytes are now damaged.
                known_identity = _RetiredKeyIdentity(
                    kid=known_identity.kid,
                    expires_at=metadata.st_mtime + self._retired_key_retention_seconds,
                )
                self._retired_key_identities[path.name] = known_identity
            required_for_file = _is_required_retired_file(required_kid, retired_kid, known_identity, now)
            cached = self._retired_files.get(path.name)
            try:
                pem = path.read_bytes()
            except OSError:
                optic.warning("could not read retired JWT key file {}", path.name)
                if required_for_file:
                    required_key_unavailable = True
                continue
            if cached is not None and cached.signature == signature and cached.pem == pem:
                entry = cached
            else:
                invalid_signature = (signature, pem)
                if self._invalid_retired_files.get(path.name) == invalid_signature:
                    invalid_files[path.name] = invalid_signature
                    if required_for_file:
                        required_key_unavailable = True
                    continue
                try:
                    pub = serialization.load_pem_public_key(pem)
                    if not isinstance(pub, (ec.EllipticCurvePublicKey, rsa.RSAPublicKey)):
                        raise TypeError(f"Unsupported retired JWT key type: {type(pub).__name__}")
                    _algorithm_for_key(pub)
                except (TypeError, ValueError, UnsupportedAlgorithm):
                    optic.warning("could not load retired JWT key file {}", path.name)
                    invalid_files[path.name] = invalid_signature
                    if required_for_file:
                        required_key_unavailable = True
                    continue
                entry = _RetiredKey(pub, _kid_from_public_key(pub), signature, pem)
            if retired_kid != entry.kid or (known_identity is not None and known_identity.kid != entry.kid):
                optic.warning("retired JWT key identity changed in file {}", path.name)
                invalid_files[path.name] = (signature, pem)
                if required_for_file or entry.kid == required_kid:
                    required_key_unavailable = True
                continue
            files[path.name] = entry
            self._retired_key_identities[path.name] = _RetiredKeyIdentity(
                kid=entry.kid,
                expires_at=metadata.st_mtime + self._retired_key_retention_seconds,
            )
            if entry.kid != active_kid:
                eligible[entry.kid] = entry.public_key
        self._retired_files = files
        self._invalid_retired_files = invalid_files
        self._retired_keys = eligible
        for name, identity in tuple(self._retired_key_identities.items()):
            if name not in observed_paths and identity.expires_at < now:
                del self._retired_key_identities[name]
        known_retained_key_unavailable = any(
            identity.kid != active_kid and identity.expires_at >= now and identity.kid not in eligible
            for identity in self._retired_key_identities.values()
        )
        if (
            required_kid is not None
            and required_kid != active_kid
            and required_kid not in eligible
            and (
                required_key_unavailable
                or any(
                    identity.kid == required_kid and identity.expires_at >= now
                    for identity in self._retired_key_identities.values()
                )
            )
        ) or (require_complete_set and known_retained_key_unavailable):
            raise KeyStoreUnavailableError("JWT signing-key store is unavailable") from None


_key_manager: KeyManager | None = None


def get_key_manager() -> KeyManager:
    if _key_manager is None:
        raise RuntimeError("KeyManager not initialized. Call init_key_manager() during app startup.")
    return _key_manager


def init_key_manager(
    key_dir: str = "~/.observal/keys",
    key_password: str | None = None,
    algorithm: SigningAlgorithm = "ES256",
    retired_key_retention_days: int = 30,
    read_only: bool = False,
) -> KeyManager:
    global _key_manager
    manager = KeyManager(
        key_dir=key_dir,
        key_password=key_password,
        algorithm=algorithm,
        retired_key_retention_days=retired_key_retention_days,
        read_only=read_only,
    )
    manager.initialize()
    _key_manager = manager
    return manager


def sign_token(payload: dict) -> str:
    return get_key_manager().sign_token(payload)


def verify_token(token: str) -> dict:
    return get_key_manager().verify_token(token)
