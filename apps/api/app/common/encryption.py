"""Envelope encryption service for provider credentials.

Implements AES-256-GCM authenticated encryption for OAuth tokens at rest.
Uses a Data Encryption Key (DEK) approach where:
- DEK encrypts/decrypts credential payloads
- KEK/master key protects the DEK (external to this service)

This service must never log plaintext tokens or expose them through errors.
"""

from __future__ import annotations

import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.common.logging import get_logger

logger = get_logger(__name__)


class EncryptionError(Exception):
    """Raised when encryption or decryption fails."""


class EncryptionService:
    """AES-256-GCM envelope encryption for provider credentials.

    The DEK must be 32 bytes (256 bits). The service derives a nonce
    from the ciphertext or uses a random nonce per encryption operation.

    A stable ``key_id`` is derived from the DEK so callers can persist
    an identifier alongside encrypted material for later key lookup
    and rotation tracking.
    """

    def __init__(self, dek: str | None = None) -> None:
        """Initialize the encryption service.

        Args:
            dek: Hex-encoded 32-byte (64-character) data encryption key.
                 If None, reads from ENCRYPTION_DEK environment variable.
                 Development/testing should use a deterministic key.

        Raises:
            EncryptionError: If DEK is missing or invalid.
        """
        if dek is None:
            dek = os.environ.get("ENCRYPTION_DEK", "")

        if not dek:
            msg = "Encryption DEK is not configured."
            raise EncryptionError(msg)

        try:
            self._dek = bytes.fromhex(dek)
        except ValueError as exc:
            msg = "Encryption DEK must be a hex-encoded string."
            raise EncryptionError(msg) from exc

        if len(self._dek) != 32:
            msg = f"Encryption DEK must be 32 bytes (64 hex chars), got {len(self._dek)} bytes."
            raise EncryptionError(msg)

        self._aesgcm = AESGCM(self._dek)
        self._key_id = hashlib.sha256(self._dek).hexdigest()

    @property
    def key_id(self) -> str:
        """Stable identifier for the configured DEK.

        Derived from the SHA-256 hash of the raw DEK bytes. Safe to
        persist alongside encrypted credentials for key rotation tracking.
        """
        return self._key_id

    def encrypt(self, plaintext: str | bytes) -> bytes:
        """Encrypt plaintext using AES-256-GCM.

        Args:
            plaintext: The data to encrypt.

        Returns:
            Encrypted ciphertext with embedded nonce (12 bytes prepended).

        Raises:
            EncryptionError: If encryption fails.
        """
        if isinstance(plaintext, str):
            plaintext = plaintext.encode("utf-8")

        try:
            nonce = os.urandom(12)
            ciphertext = self._aesgcm.encrypt(nonce, plaintext, None)
            return nonce + ciphertext
        except Exception as exc:
            logger.error("encryption_failed", error=str(exc))
            msg = "Failed to encrypt data."
            raise EncryptionError(msg) from exc

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt ciphertext using AES-256-GCM.

        Args:
            ciphertext: Encrypted data with 12-byte nonce prepended.

        Returns:
            Decrypted plaintext bytes.

        Raises:
            EncryptionError: If decryption fails or authentication fails.
        """
        try:
            if len(ciphertext) < 12:
                msg = "Ciphertext too short to contain nonce."
                raise EncryptionError(msg)

            nonce = ciphertext[:12]
            encrypted_data = ciphertext[12:]
            plaintext = self._aesgcm.decrypt(nonce, encrypted_data, None)
            return plaintext
        except EncryptionError:
            raise
        except Exception as exc:
            logger.error("decryption_failed", error=str(exc))
            msg = "Failed to decrypt data."
            raise EncryptionError(msg) from exc

    def encrypt_string(self, plaintext: str) -> bytes:
        """Encrypt a string and return ciphertext bytes.

        Convenience wrapper around encrypt() for string inputs.
        """
        return self.encrypt(plaintext)

    def decrypt_string(self, ciphertext: bytes) -> str:
        """Decrypt ciphertext bytes and return a string.

        Convenience wrapper around decrypt() that returns str.
        """
        return self.decrypt(ciphertext).decode("utf-8")
