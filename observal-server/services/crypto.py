# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Ravi Chopra <shivamchopra1234567890@gmail.com>
# SPDX-FileCopyrightText: 2026 SrihariLegend <sriharilegend23@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Asymmetric JWT signing, verification, JWKS publication, and key rotation."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import os
import time
from pathlib import Path
from typing import Literal, TypeAlias

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from loguru import logger as optic

SigningAlgorithm = Literal["ES256", "RS256"]
PrivateKey: TypeAlias = ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey
PublicKey: TypeAlias = ec.EllipticCurvePublicKey | rsa.RSAPublicKey
SUPPORTED_ALGORITHMS = frozenset({"ES256", "RS256"})


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


class KeyManager:
    """Manage ES256 or RS256 JWT keys while retaining old verification keys."""

    def __init__(
        self,
        key_dir: str = "~/.observal/keys",
        key_password: str | None = None,
        algorithm: SigningAlgorithm = "ES256",
        retired_key_retention_days: int = 30,
    ) -> None:
        if algorithm not in SUPPORTED_ALGORITHMS:
            raise ValueError(f"Unsupported JWT signing algorithm: {algorithm}")
        self._key_dir = Path(key_dir).expanduser()
        self._key_password = key_password.encode() if key_password else None
        self._algorithm = algorithm
        self._retired_key_retention_seconds = max(retired_key_retention_days, 1) * 86400
        self._private_key: PrivateKey | None = None
        self._public_key: PublicKey | None = None
        self._kid: str | None = None
        self._retired_keys: dict[str, PublicKey] = {}

    @property
    def algorithm(self) -> SigningAlgorithm:
        return self._algorithm

    def initialize(self) -> None:
        optic.debug("initializing JWT key manager")
        self._key_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._key_dir, 0o700)
        except OSError:
            pass

        signing_path = self._key_dir / "signing.pem"
        lock_fd = os.open(self._key_dir / ".signing.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if signing_path.exists():
                self._load_private_key(signing_path)
                if _algorithm_for_key(self.get_private_key()) != self._algorithm:
                    optic.info("JWT algorithm changed; retiring current signing key")
                    self._retire_current_key()
                    self._generate_key_pair(signing_path)
            else:
                self._generate_key_pair(signing_path)
            self._load_retired_keys()
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        optic.info("JWT signing key ready (alg={}, kid={})", self._algorithm, self._kid)

    def get_private_key(self) -> PrivateKey:
        if self._private_key is None:
            raise RuntimeError("KeyManager has not been initialized")
        return self._private_key

    def get_public_key(self) -> PublicKey:
        if self._public_key is None:
            raise RuntimeError("KeyManager has not been initialized")
        return self._public_key

    def get_kid(self) -> str:
        if self._kid is None:
            raise RuntimeError("KeyManager has not been initialized")
        return self._kid

    def get_public_key_pem(self) -> str:
        return (
            self.get_public_key()
            .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
            .decode()
        )

    def get_jwks(self) -> dict:
        self._prune_retired_keys()
        optic.trace("building JWT JWKS with {} retired keys", len(self._retired_keys))
        keys = [_public_key_to_jwk(self.get_public_key(), self.get_kid())]
        keys.extend(_public_key_to_jwk(pub, kid) for kid, pub in self._retired_keys.items())
        return {"keys": keys}

    def rotate_key(self) -> str:
        optic.info("rotating JWT signing key")
        self._retire_current_key()
        self._generate_key_pair(self._key_dir / "signing.pem")
        return self.get_kid()

    def find_public_key(self, kid: str) -> PublicKey | None:
        self._prune_retired_keys()
        if kid == self._kid:
            return self._public_key
        return self._retired_keys.get(kid)

    def sign_token(self, payload: dict) -> str:
        optic.trace("signing JWT token with kid={}", self.get_kid())
        return jwt.encode(
            payload,
            self.get_private_key(),
            algorithm=self._algorithm,
            headers={"kid": self.get_kid()},
        )

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

    def _encryption_args(self) -> serialization.KeySerializationEncryption:
        if self._key_password:
            return serialization.BestAvailableEncryption(self._key_password)
        return serialization.NoEncryption()

    def _write_private_key(self, path: Path, key: PrivateKey) -> None:
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            self._encryption_args(),
        )
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(pem)
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _generate_key_pair(self, path: Path) -> None:
        optic.debug("generating {} JWT signing key", self._algorithm)
        key: PrivateKey
        if self._algorithm == "ES256":
            key = ec.generate_private_key(ec.SECP256R1())
        else:
            key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        self._write_private_key(path, key)
        self._private_key = key
        self._public_key = key.public_key()
        self._kid = _kid_from_public_key(self._public_key)

    def _load_private_key(self, path: Path) -> None:
        optic.trace("loading JWT signing key from {}", path.name)
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
        if encrypt_existing_key:
            self._write_private_key(path, key)
            optic.info("encrypted existing JWT signing key with configured password")
        else:
            try:
                os.chmod(path, 0o600)
            except OSError:
                optic.warning("could not restrict JWT signing key permissions")
        self._private_key = key
        self._public_key = key.public_key()
        self._kid = _kid_from_public_key(self._public_key)

    def _retire_current_key(self) -> None:
        if self._public_key is None or self._kid is None:
            return
        retired_path = self._key_dir / f"retired_{self._kid}.pem"
        retired_path.write_bytes(
            self._public_key.public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        os.chmod(retired_path, 0o600)
        self._retired_keys[self._kid] = self._public_key

    def _prune_retired_keys(self) -> None:
        cutoff = time.time() - self._retired_key_retention_seconds
        for path in self._key_dir.glob("retired_*.pem"):
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                kid = path.stem.removeprefix("retired_")
                path.unlink()
                self._retired_keys.pop(kid, None)
                optic.info("removed expired retired JWT key kid={}", kid)
            except OSError:
                optic.warning("could not prune retired key file {}", path.name)

    def _load_retired_keys(self) -> None:
        self._prune_retired_keys()
        for path in self._key_dir.glob("retired_*.pem"):
            try:
                pub = serialization.load_pem_public_key(path.read_bytes())
                if not isinstance(pub, (ec.EllipticCurvePublicKey, rsa.RSAPublicKey)):
                    raise TypeError(type(pub).__name__)
                _algorithm_for_key(pub)
                self._retired_keys[_kid_from_public_key(pub)] = pub
            except (OSError, TypeError, ValueError):
                optic.warning("could not load retired key file {}", path.name)


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
) -> KeyManager:
    global _key_manager
    manager = KeyManager(
        key_dir=key_dir,
        key_password=key_password,
        algorithm=algorithm,
        retired_key_retention_days=retired_key_retention_days,
    )
    manager.initialize()
    _key_manager = manager
    return manager


def sign_token(payload: dict) -> str:
    return get_key_manager().sign_token(payload)


def verify_token(token: str) -> dict:
    return get_key_manager().verify_token(token)
