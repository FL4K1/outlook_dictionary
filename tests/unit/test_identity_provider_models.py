"""Unit tests for identity provider models."""

from __future__ import annotations

from sqlalchemy import inspect

from mip_models.identity_provider import (
    EntraTenantMapping,
    IdentityProviderCredential,
    OAuthState,
)


class TestOAuthState:
    """Tests for OAuthState model definition."""

    def test_table_name(self) -> None:
        assert OAuthState.__tablename__ == "oauth_states"

    def test_state_column(self) -> None:
        mapper = inspect(OAuthState)
        state_col = mapper.columns["state"]
        assert state_col.type.length == 255
        assert state_col.nullable is False
        assert state_col.unique is True
        assert state_col.index is True

    def test_nonce_column(self) -> None:
        mapper = inspect(OAuthState)
        nonce_col = mapper.columns["nonce"]
        assert nonce_col.type.length == 255
        assert nonce_col.nullable is False

    def test_code_verifier_column(self) -> None:
        mapper = inspect(OAuthState)
        cv_col = mapper.columns["code_verifier"]
        assert cv_col.type.length == 255
        assert cv_col.nullable is False

    def test_provider_column(self) -> None:
        mapper = inspect(OAuthState)
        provider_col = mapper.columns["provider"]
        assert provider_col.type.length == 50
        assert provider_col.nullable is False

    def test_expires_at_column(self) -> None:
        mapper = inspect(OAuthState)
        exp_col = mapper.columns["expires_at"]
        assert exp_col.nullable is False
        assert exp_col.index is True

    def test_consumed_at_column(self) -> None:
        mapper = inspect(OAuthState)
        consumed_col = mapper.columns["consumed_at"]
        assert consumed_col.nullable is True

    def test_unique_constraint_on_state(self) -> None:
        assert any(
            getattr(c, "name", None) == "uq_oauth_state_state" for c in OAuthState.__table_args__
        )


class TestIdentityProviderCredential:
    """Tests for IdentityProviderCredential model definition."""

    def test_table_name(self) -> None:
        assert IdentityProviderCredential.__tablename__ == "identity_provider_credentials"

    def test_identity_id_column(self) -> None:
        mapper = inspect(IdentityProviderCredential)
        col = mapper.columns["identity_id"]
        assert col.nullable is False
        assert col.unique is True
        assert col.index is True

    def test_tenant_id_column(self) -> None:
        mapper = inspect(IdentityProviderCredential)
        col = mapper.columns["tenant_id"]
        assert col.nullable is False
        assert col.index is True

    def test_provider_column(self) -> None:
        mapper = inspect(IdentityProviderCredential)
        col = mapper.columns["provider"]
        assert col.type.length == 50
        assert col.nullable is False

    def test_encrypted_token_columns(self) -> None:
        mapper = inspect(IdentityProviderCredential)
        access_col = mapper.columns["encrypted_access_token"]
        refresh_col = mapper.columns["encrypted_refresh_token"]
        assert access_col.nullable is False
        assert refresh_col.nullable is False

    def test_token_expires_at_column(self) -> None:
        mapper = inspect(IdentityProviderCredential)
        col = mapper.columns["token_expires_at"]
        assert col.nullable is False
        assert col.index is True

    def test_encryption_key_id_column(self) -> None:
        mapper = inspect(IdentityProviderCredential)
        col = mapper.columns["encryption_key_id"]
        assert col.type.length == 255
        assert col.nullable is False

    def test_revoked_at_column(self) -> None:
        mapper = inspect(IdentityProviderCredential)
        col = mapper.columns["revoked_at"]
        assert col.nullable is True
        assert col.index is True

    def test_unique_constraint_on_identity_id(self) -> None:
        assert any(
            getattr(c, "name", None) == "uq_identity_provider_credential_identity"
            for c in IdentityProviderCredential.__table_args__
        )


class TestEntraTenantMapping:
    """Tests for EntraTenantMapping model definition."""

    def test_table_name(self) -> None:
        assert EntraTenantMapping.__tablename__ == "entra_tenant_mappings"

    def test_entra_tenant_id_column(self) -> None:
        mapper = inspect(EntraTenantMapping)
        col = mapper.columns["entra_tenant_id"]
        assert col.type.length == 255
        assert col.nullable is False
        assert col.unique is True
        assert col.index is True

    def test_tenant_id_column(self) -> None:
        mapper = inspect(EntraTenantMapping)
        col = mapper.columns["tenant_id"]
        assert col.nullable is False
        assert col.index is True

    def test_is_active_column(self) -> None:
        mapper = inspect(EntraTenantMapping)
        col = mapper.columns["is_active"]
        assert col.nullable is False

    def test_unique_constraint_on_entra_tenant_id(self) -> None:
        assert any(
            getattr(c, "name", None) == "uq_entra_tenant_mapping_tenant"
            for c in EntraTenantMapping.__table_args__
        )
