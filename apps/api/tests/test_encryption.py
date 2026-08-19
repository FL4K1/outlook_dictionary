"""Unit tests for envelope encryption service."""

from __future__ import annotations

import hashlib

import pytest

from app.common.encryption import EncryptionError, EncryptionService


class TestEncryptionService:
    """Tests for AES-256-GCM envelope encryption."""

    def test_create_with_valid_hex_dek(self) -> None:
        dek = "00" * 32
        service = EncryptionService(dek=dek)
        assert service is not None

    def test_create_with_env_dek(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dek = "11" * 32
        monkeypatch.setenv("ENCRYPTION_DEK", dek)
        service = EncryptionService()
        assert service is not None

    def test_create_without_dek_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ENCRYPTION_DEK", raising=False)
        with pytest.raises(EncryptionError, match="Encryption DEK is not configured"):
            EncryptionService()

    def test_create_with_invalid_hex_dek_raises(self) -> None:
        with pytest.raises(EncryptionError, match="Encryption DEK must be a hex-encoded string"):
            EncryptionService(dek="not-hex")

    def test_create_with_short_dek_raises(self) -> None:
        with pytest.raises(EncryptionError, match="must be 32 bytes"):
            EncryptionService(dek="00" * 16)

    def test_create_with_long_dek_raises(self) -> None:
        with pytest.raises(EncryptionError, match="must be 32 bytes"):
            EncryptionService(dek="00" * 33)

    def test_key_id_is_stable_for_same_dek(self) -> None:
        dek = "22" * 32
        service1 = EncryptionService(dek=dek)
        service2 = EncryptionService(dek=dek)
        assert service1.key_id == service2.key_id

    def test_key_id_differs_for_different_deks(self) -> None:
        service1 = EncryptionService(dek="33" * 32)
        service2 = EncryptionService(dek="44" * 32)
        assert service1.key_id != service2.key_id

    def test_key_id_is_sha256_of_dek(self) -> None:
        dek = "55" * 32
        service = EncryptionService(dek=dek)
        expected = hashlib.sha256(bytes.fromhex(dek)).hexdigest()
        assert service.key_id == expected

    def test_key_id_does_not_expose_plaintext_key(self) -> None:
        dek = "66" * 32
        service = EncryptionService(dek=dek)
        assert service.key_id != dek
        assert service.key_id not in dek

    def test_key_id_length(self) -> None:
        dek = "77" * 32
        service = EncryptionService(dek=dek)
        assert len(service.key_id) == 64

    def test_key_id_is_one_way_identifier(self) -> None:
        """key_id is a one-way identifier; original DEK cannot be recovered from it."""
        dek = "bb" * 32
        service = EncryptionService(dek=dek)
        assert service.key_id != dek
        assert all(c in "0123456789abcdef" for c in service.key_id)

    def test_encrypt_decrypt_string_roundtrip(self) -> None:
        dek = "22" * 32
        service = EncryptionService(dek=dek)
        plaintext = "sensitive-token-data"

        ciphertext = service.encrypt_string(plaintext)
        decrypted = service.decrypt_string(ciphertext)

        assert decrypted == plaintext
        assert ciphertext != plaintext.encode("utf-8")

    def test_encrypt_decrypt_bytes_roundtrip(self) -> None:
        dek = "33" * 32
        service = EncryptionService(dek=dek)
        plaintext = b"sensitive-bytes-data"

        ciphertext = service.encrypt(plaintext)
        decrypted = service.decrypt(ciphertext)

        assert decrypted == plaintext
        assert ciphertext != plaintext

    def test_encrypt_produces_different_ciphertexts(self) -> None:
        dek = "44" * 32
        service = EncryptionService(dek=dek)

        ct1 = service.encrypt_string("same-plaintext")
        ct2 = service.encrypt_string("same-plaintext")

        assert ct1 != ct2

    def test_decrypt_with_wrong_key_fails(self) -> None:
        dek1 = "55" * 32
        dek2 = "66" * 32
        service1 = EncryptionService(dek=dek1)
        service2 = EncryptionService(dek=dek2)

        ciphertext = service1.encrypt_string("secret")
        with pytest.raises(EncryptionError, match="Failed to decrypt"):
            service2.decrypt_string(ciphertext)

    def test_decrypt_with_tampered_ciphertext_fails(self) -> None:
        dek = "77" * 32
        service = EncryptionService(dek=dek)

        ciphertext = service.encrypt_string("secret")
        tampered = ciphertext[:-10] + b"tampered!!"

        with pytest.raises(EncryptionError, match="Failed to decrypt"):
            service.decrypt(tampered)

    def test_decrypt_with_too_short_ciphertext_fails(self) -> None:
        dek = "88" * 32
        service = EncryptionService(dek=dek)

        with pytest.raises(EncryptionError, match="Ciphertext too short"):
            service.decrypt(b"short")

    def test_encrypt_empty_string(self) -> None:
        dek = "99" * 32
        service = EncryptionService(dek=dek)

        ciphertext = service.encrypt_string("")
        decrypted = service.decrypt_string(ciphertext)

        assert decrypted == ""

    def test_encrypt_unicode(self) -> None:
        dek = "aa" * 32
        service = EncryptionService(dek=dek)
        plaintext = "日本語トークン"

        ciphertext = service.encrypt_string(plaintext)
        decrypted = service.decrypt_string(ciphertext)

        assert decrypted == plaintext
