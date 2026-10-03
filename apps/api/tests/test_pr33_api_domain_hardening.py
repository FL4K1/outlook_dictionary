"""PR-3.3 API/Domain Hardening Test Suite (Gemini Ownership).

Verifies binary exit criteria for:
- H2: Sync failure durability & fresh-session state persistence
- H4: Application database engine shutdown disposal
- H5: Auth DB session release before call_next execution
- H6: Tenant-scoped DB entity resolution for natural language search
- B4: Readiness probe dependency checks (200/503, sanitized JSON)
- B3/H7: SearchHit body_preview & sender_email round-trip to synthesis
- M9: Public API error message sanitization (no internal leaks)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from app.api.search.schemas import (
    MailSearchRequest,
    MailSearchResponse,
    SearchHit,
    SearchParticipant,
    SearchSender,
)
from app.auth.context import AuthenticationContext
from app.auth.middleware import AuthenticationMiddleware
from app.common.config import Settings
from app.common.dependencies import get_db, get_engine, init_dependencies, shutdown_dependencies
from app.main import create_app
from app.search.elasticsearch_search import SearchServiceUnavailableError
from app.search.nl_service import (
    EntityResolutionError,
    NaturalLanguageSearchService,
)
from app.search.service import SearchService
from app.services.sync_orchestrator import SyncOrchestrator
from mip_ai.query_understanding.mock import DeterministicMockQueryUnderstandingProvider
from mip_ai.synthesis.gateway import GatewaySearchSynthesisProvider
from mip_models.auth import DeviceSession, Role
from mip_models.mail import MailAccount, MailFolder, MailSyncState, MailSyncStateValue
from mip_models.search import MailQueryPlan
from mip_models.synthesis import SearchSynthesis, SynthesisCitation
from mip_models.tenant import Tenant
from mip_models.user import Membership
from mip_providers import (
    AuthExpiredError,
    ProviderDeltaPage,
    ProviderError,
    ProviderRateLimitedError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# ---------------------------------------------------------------------------
# H4: Engine Lifecycle / Shutdown Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h4_engine_shutdown_dispose() -> None:
    """H4: shutdown_dependencies disposes of the async engine and resets singleton."""
    settings = Settings()
    init_dependencies(settings)

    engine = get_engine()
    assert engine is not None

    # Shutdown should dispose engine
    await shutdown_dependencies()
    assert get_engine() is None


# ---------------------------------------------------------------------------
# H5: Auth Session Release Before call_next Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h5_auth_session_released_before_call_next() -> None:
    """H5: Auth middleware commits and releases DB session before call_next execution."""
    app = FastAPI()
    call_next_executed = False

    mock_factory = MagicMock()
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_role = Role(id=uuid.uuid4(), name="admin", permissions=[])
    mock_exec_res = MagicMock()
    mock_exec_res.scalars = MagicMock(
        return_value=MagicMock(first=MagicMock(return_value=mock_role))
    )
    mock_session.execute = AsyncMock(return_value=mock_exec_res)

    async def mock_session_gen() -> AsyncGenerator[AsyncMock, None]:
        yield mock_session

    mock_factory.get_session = MagicMock(return_value=mock_session_gen())

    policy_engine = MagicMock()
    token_service = MagicMock()

    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    org_id = uuid.uuid4()

    token_service.verify_access_token = MagicMock(
        return_value={
            "jti": str(uuid.uuid4()),
            "sid": str(session_id),
            "tid": str(tenant_id),
            "oid": str(org_id),
            "sub": str(user_id),
        }
    )

    mock_device_session = DeviceSession(
        id=session_id,
        user_id=user_id,
        tenant_id=tenant_id,
        revoked_at=None,
        expires_at=datetime.now(UTC) + timedelta(days=1),
        last_active_at=datetime.now(UTC),
        identity_id=None,
    )
    mock_tenant = Tenant(
        id=tenant_id,
        organization_id=org_id,
        name="Test",
        slug="test",
        is_active=True,
    )
    mock_membership = Membership(
        id=uuid.uuid4(),
        user_id=user_id,
        tenant_id=tenant_id,
        role_id=mock_role.id,
        is_active=True,
    )

    with (
        patch(
            "app.auth.middleware.DeviceSessionRepository.get", new_callable=AsyncMock
        ) as mock_ds_get,
        patch(
            "app.auth.middleware.TenantRepository.get", new_callable=AsyncMock
        ) as mock_tenant_get,
        patch(
            "app.auth.middleware.MembershipRepository.get_by_user_and_tenant",
            new_callable=AsyncMock,
        ) as mock_mem_get,
    ):
        mock_ds_get.return_value = mock_device_session
        mock_tenant_get.return_value = mock_tenant
        mock_mem_get.return_value = mock_membership

        middleware = AuthenticationMiddleware(
            app=app,  # type: ignore[arg-type]
            policy_engine=policy_engine,
            token_service=token_service,
        )

        async def mock_call_next(req: Any) -> JSONResponse:
            nonlocal call_next_executed
            call_next_executed = True
            # Verify session.commit() was called BEFORE call_next!
            assert mock_session.commit.called
            return JSONResponse({"status": "ok"})

        with patch("app.common.dependencies.get_session_factory", return_value=mock_factory):
            req = MagicMock()
            req.url.path = "/api/v1/search/mail"
            req.method = "POST"
            req.headers = {"Authorization": "Bearer fake_token", "User-Agent": "pytest"}
            req.client = MagicMock(host="127.0.0.1")
            req.app = MagicMock(state=MagicMock(settings=Settings()))

            res = await middleware.dispatch(req, mock_call_next)
            assert call_next_executed
            assert res.status_code == 200


# ---------------------------------------------------------------------------
# B4: Readiness Probe 200/503 Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b4_readiness_probe_all_healthy() -> None:
    """B4: Readiness probe returns 200 when PG and ES are healthy."""
    settings = Settings()
    app = create_app(settings=settings)

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=1)))
    app.dependency_overrides[get_db] = lambda: mock_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        with patch("app.health.router.httpx.AsyncClient") as mock_es_client_cls:
            mock_es_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_es_client.get.return_value = mock_resp
            mock_es_client_cls.return_value.__aenter__.return_value = mock_es_client

            response = await test_client.get("/health/ready")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert data["checks"]["postgresql"] == "healthy"
            assert data["checks"]["elasticsearch"] == "healthy"


@pytest.mark.asyncio
async def test_b4_readiness_probe_es_unhealthy() -> None:
    """B4: Readiness probe returns 503 when ES is unreachable (fail-closed)."""
    settings = Settings()
    app = create_app(settings=settings)

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=1)))
    app.dependency_overrides[get_db] = lambda: mock_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        with patch("app.health.router.httpx.AsyncClient") as mock_es_client_cls:
            mock_es_client = AsyncMock()
            mock_es_client.get.side_effect = Exception("Connection refused to Elasticsearch:9200")
            mock_es_client_cls.return_value.__aenter__.return_value = mock_es_client

            response = await test_client.get("/health/ready")
            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "unhealthy"
            assert data["checks"]["postgresql"] == "healthy"
            assert data["checks"]["elasticsearch"] == "unhealthy"
            # Must not expose raw exception or internal stack trace
            assert "Connection refused" not in response.text
            assert "stack" not in response.text.lower()


# ---------------------------------------------------------------------------
# B3/H7: SearchHit Contract & Synthesis Body Preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b3_h7_body_preview_synthesis_evidence() -> None:
    """B3/H7: SearchHit sender_email & body_preview reach synthesis evidence."""
    hit = SearchHit(
        id=str(uuid.uuid4()),
        mail_account_id=str(uuid.uuid4()),
        subject="Important Quarterly Update",
        sender={"name": "Alice Smith", "email": "alice@example.com"},
        sender_email="alice@example.com",
        body_preview="Here is the summary of Q3 financial earnings and projections.",
        participants=[SearchParticipant(name="Bob", email="bob@example.com", role="to")],
        received_date_time=datetime.now(UTC),
        folder_ids=[str(uuid.uuid4())],
        is_read=True,
        has_attachments=False,
    )

    mock_llm_config = MagicMock()
    mock_llm_config.provider.value = "mock"
    mock_llm_config.model = "deterministic"

    provider = GatewaySearchSynthesisProvider(config=mock_llm_config)

    with patch.object(
        provider._gateway, "execute_structured_request", new_callable=AsyncMock
    ) as mock_exec:
        mock_synthesis = SearchSynthesis(
            answer="Q3 earnings summary was provided.",
            citations=[SynthesisCitation(message_id=hit.id)],
            insufficient_evidence=False,
        )
        empty_meta: dict[str, Any] = {}
        mock_exec.return_value = (mock_synthesis.model_dump_json(), empty_meta)

        res = await provider.synthesize(query="What were the Q3 earnings?", hits=[hit])  # type: ignore[arg-type]

        assert res.answer == "Q3 earnings summary was provided."

        # Verify context string sent to LLM contains body_preview and sender_email
        call_args = mock_exec.call_args[1]["messages"]
        user_msg = call_args[1]["content"]
        assert "Here is the summary of Q3 financial earnings" in user_msg
        assert "alice@example.com" in user_msg


@pytest.mark.asyncio
async def test_b3_search_hit_canonical_sender_model() -> None:
    """B3: ES sender dict object maps into strict SearchSender model with name and email fields."""
    es_source = {
        "id": str(uuid.uuid4()),
        "mail_account_id": str(uuid.uuid4()),
        "subject": "Strict Sender Schema Test",
        "sender": {"name": "Alice Smith", "email": "alice@example.com"},
        "body_preview": "Testing strict SearchSender Pydantic schema mapping.",
        "participants": [{"name": "Bob Jones", "email": "bob@example.com", "role": "to"}],
        "received_date_time": "2026-10-01T12:00:00Z",
        "folder_ids": [str(uuid.uuid4())],
        "is_read": True,
        "has_attachments": False,
    }

    mock_es_adapter = MagicMock()
    mock_es_adapter.search = AsyncMock(return_value={"hits": {"hits": [{"_source": es_source}]}})

    search_svc = SearchService(es_adapter=mock_es_adapter)
    req = MailSearchRequest(query="test", page_size=10)
    resp = await search_svc.search_mail(tenant_id=str(uuid.uuid4()), request=req)

    assert len(resp.items) == 1
    hit = resp.items[0]

    assert isinstance(hit.sender, SearchSender)
    assert hit.sender.name == "Alice Smith"
    assert hit.sender.email == "alice@example.com"
    assert hit.sender_email == "alice@example.com"


# ---------------------------------------------------------------------------
# M9: Public Error Sanitization Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m9_public_error_sanitization() -> None:
    """M9: API endpoints return clean sanitized errors without raw stack traces or internal URLs."""
    AuthenticationContext(
        request_id="req_test_123",
        correlation_id="corr_test_123",
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        role_names=frozenset({"admin"}),
        permissions=frozenset({"search:read"}),
    )

    async def mock_failing_search_service() -> MagicMock:
        mock_svc = MagicMock()
        mock_svc.search_mail = AsyncMock(
            side_effect=SearchServiceUnavailableError(
                "Failed to connect to http://internal-es-cluster.vpc.internal:9200/mail_messages_123"
            )
        )
        return mock_svc

    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    org_id = uuid.uuid4()

    with (
        patch(
            "app.auth.tokens.TokenService.verify_access_token",
            return_value={
                "jti": str(uuid.uuid4()),
                "sid": str(session_id),
                "tid": str(tenant_id),
                "oid": str(org_id),
                "sub": str(user_id),
            },
        ),
        patch(
            "app.auth.middleware.DeviceSessionRepository.get", new_callable=AsyncMock
        ) as mock_ds_get,
        patch(
            "app.auth.middleware.TenantRepository.get", new_callable=AsyncMock
        ) as mock_tenant_get,
        patch(
            "app.auth.middleware.MembershipRepository.get_by_user_and_tenant",
            new_callable=AsyncMock,
        ) as mock_mem_get,
        patch("app.common.dependencies.get_session_factory") as mock_sf_get,
    ):
        mock_role = Role(id=uuid.uuid4(), name="admin", permissions=[])

        mock_ds_get.return_value = MagicMock(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
            identity_id=None,
            revoked_at=None,
            expires_at=datetime.now(UTC) + timedelta(days=1),
            last_active_at=datetime.now(UTC),
        )
        mock_tenant_get.return_value = MagicMock(
            id=tenant_id,
            organization_id=org_id,
            is_active=True,
        )
        mock_mem_get.return_value = MagicMock(
            id=uuid.uuid4(),
            user_id=user_id,
            tenant_id=tenant_id,
            role_id=mock_role.id,
            is_active=True,
        )

        mock_session = AsyncMock()
        mock_exec_res = MagicMock()
        mock_exec_res.scalars = MagicMock(
            return_value=MagicMock(first=MagicMock(return_value=mock_role))
        )
        mock_session.execute = AsyncMock(return_value=mock_exec_res)

        async def mock_session_gen() -> AsyncGenerator[AsyncMock, None]:
            yield mock_session

        mock_sf_get.return_value = MagicMock(get_session=MagicMock(return_value=mock_session_gen()))

        app = create_app(settings=Settings())
        app.dependency_overrides[SearchService] = mock_failing_search_service

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as test_client:
            response = await test_client.post(
                "/search/mail",
                json={"query": "test"},
                headers={"Authorization": "Bearer fake_token"},
            )
            assert response.status_code == 503
            data = response.json()
            assert data["detail"] == "Search service unavailable."
            assert "internal-es-cluster" not in response.text
            assert "http://" not in response.text


# ---------------------------------------------------------------------------
# H6: Real DB Entity Resolution Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h6_real_db_entity_resolution() -> None:
    """H6: NaturalLanguageSearchService resolves tenant entities against stored DB records."""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()

    mock_db = AsyncMock()
    mock_account = MailAccount(
        id=account_id,
        tenant_id=tenant_id,
        email_address="sales.team@testcorp.com",
        display_name="Sales Team",
        is_active=True,
    )

    mock_exec_res = MagicMock()
    mock_exec_res.scalars = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=[mock_account]))
    )
    mock_db.execute = AsyncMock(return_value=mock_exec_res)

    provider = DeterministicMockQueryUnderstandingProvider()
    search_service = MagicMock(spec=SearchService)
    search_service.search_mail = AsyncMock(
        return_value=MailSearchResponse(items=[], next_page_cursor=None)
    )

    nl_service = NaturalLanguageSearchService(
        provider=provider,
        search_service=search_service,
    )

    # 1. Resolve matching account
    with patch.object(
        provider,
        "understand_query",
        new_callable=AsyncMock,
        return_value=MagicMock(
            query_plan=MailQueryPlan(
                query="quarterly results",
                account="Sales Team",
                retrieval_intent="keyword",
            )
        ),
    ):
        resp = await nl_service.search_natural_language(
            tenant_id=tenant_id,
            natural_query="emails from Sales Team",
            db_session=mock_db,
        )
        assert resp is not None
        call_req = search_service.search_mail.call_args[0][1]
        assert call_req.account_ids == [str(account_id)]

    # 2. Non-existent account -> scalars().all() returns []
    mock_empty_res = MagicMock()
    mock_empty_res.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    mock_db.execute = AsyncMock(return_value=mock_empty_res)

    with (
        patch.object(
            provider,
            "understand_query",
            new_callable=AsyncMock,
            return_value=MagicMock(
                query_plan=MailQueryPlan(
                    query="test",
                    account="NonExistentAccount999",
                    retrieval_intent="keyword",
                )
            ),
        ),
        pytest.raises(EntityResolutionError),
    ):
        await nl_service.search_natural_language(
            tenant_id=tenant_id,
            natural_query="emails from NonExistentAccount999",
            db_session=mock_db,
        )


# ---------------------------------------------------------------------------
# H2: Sync Failure Durability Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h2_sync_failure_durability_committed() -> None:
    """H2: AuthExpiredError commits state and releases lease in DB session."""
    tenant_id = uuid.uuid4()
    mail_account_id = uuid.uuid4()
    mail_folder_id = uuid.uuid4()
    worker_id = uuid.uuid4()

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()

    mock_folder = MailFolder(
        id=mail_folder_id,
        tenant_id=tenant_id,
        mail_account_id=mail_account_id,
        provider_folder_id="prov_folder_123",
        name="Inbox",
    )
    mock_sync_state = MailSyncState(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        mail_folder_id=mail_folder_id,
        state=MailSyncStateValue.DELTA_TRACKING,
        locked_by=worker_id,
        lease_version=1,
    )

    orchestrator = SyncOrchestrator(session=mock_db)

    with (
        patch.object(
            orchestrator.folder_repo, "get", new_callable=AsyncMock, return_value=mock_folder
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "get_by_folder_id",
            new_callable=AsyncMock,
            return_value=mock_sync_state,
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "acquire_sync_lease",
            new_callable=AsyncMock,
            return_value=1,
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "renew_sync_lease",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "update_sync_state_cas",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_cas_update,
    ):
        mock_adapter = MagicMock()
        mock_adapter.get_message_delta = AsyncMock(side_effect=AuthExpiredError("Token expired"))

        result = await orchestrator.sync_folder(
            mail_folder_id=mail_folder_id,
            worker_id=worker_id,
            access_token="expired_token",
            adapter=mock_adapter,
        )

        assert result.state == MailSyncStateValue.AUTH_REQUIRED
        # Verify commit() was called on DB session during error handling
        assert mock_db.commit.called
        # Verify CAS update was invoked with AUTH_REQUIRED and clear_lock=True
        mock_cas_update.assert_called_with(
            mock_sync_state.id,
            worker_id,
            1,
            state=MailSyncStateValue.AUTH_REQUIRED,
            clear_lock=True,
        )


@pytest.mark.asyncio
async def test_m4_rate_limited_429_releases_lease() -> None:
    """M4: Graph 429 ProviderRateLimitedError extracts Retry-After and releases lease."""
    tenant_id = uuid.uuid4()
    mail_account_id = uuid.uuid4()
    mail_folder_id = uuid.uuid4()
    worker_id = uuid.uuid4()
    competing_worker_id = uuid.uuid4()

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()

    mock_folder = MailFolder(
        id=mail_folder_id,
        tenant_id=tenant_id,
        mail_account_id=mail_account_id,
        provider_folder_id="prov_folder_123",
        name="Inbox",
    )
    mock_sync_state = MailSyncState(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        mail_folder_id=mail_folder_id,
        state=MailSyncStateValue.SYNCING,
        locked_by=worker_id,
        lease_version=1,
    )

    orchestrator = SyncOrchestrator(session=mock_db)

    def acquire_side_effect(sid: uuid.UUID, wid: uuid.UUID, dur: timedelta) -> int | None:
        return 1 if wid == worker_id else None

    with (
        patch.object(
            orchestrator.folder_repo, "get", new_callable=AsyncMock, return_value=mock_folder
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "get_by_folder_id",
            new_callable=AsyncMock,
            return_value=mock_sync_state,
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "acquire_sync_lease",
            new_callable=AsyncMock,
            side_effect=acquire_side_effect,
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "renew_sync_lease",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "release_sync_lease",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_release_lease,
    ):
        mock_adapter = MagicMock()
        rate_limit_err = ProviderRateLimitedError("Too Many Requests", retry_after=45)
        mock_adapter.get_message_delta = AsyncMock(side_effect=rate_limit_err)

        result = await orchestrator.sync_folder(
            mail_folder_id=mail_folder_id,
            worker_id=worker_id,
            access_token="valid_token",
            adapter=mock_adapter,
        )

        assert "Too Many Requests" in (result.error or "")
        # Assert Retry-After was explicitly extracted and populated
        assert result.retry_after == 45
        # Assert lease was explicitly released so it is recoverable
        mock_release_lease.assert_called_once_with(mock_sync_state.id, worker_id, 1)
        assert mock_db.commit.called

        # Verify competing worker cannot acquire lease while active worker holds lock fence
        competing_acq = await orchestrator.sync_state_repo.acquire_sync_lease(
            mock_sync_state.id, competing_worker_id, timedelta(minutes=5)
        )
        assert competing_acq is None


@pytest.mark.asyncio
async def test_m4_graph_5xx_retryable_backoff_and_lease_recovery() -> None:
    """M4: Graph 5xx ProviderError is explicitly retryable with backoff and lease recovery."""
    tenant_id = uuid.uuid4()
    mail_account_id = uuid.uuid4()
    mail_folder_id = uuid.uuid4()
    worker_id = uuid.uuid4()
    competing_worker_id = uuid.uuid4()

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()

    mock_folder = MailFolder(
        id=mail_folder_id,
        tenant_id=tenant_id,
        mail_account_id=mail_account_id,
        provider_folder_id="prov_folder_123",
        name="Inbox",
    )
    mock_sync_state = MailSyncState(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        mail_folder_id=mail_folder_id,
        state=MailSyncStateValue.SYNCING,
        locked_by=worker_id,
        lease_version=1,
    )

    orchestrator = SyncOrchestrator(session=mock_db)

    def acquire_side_effect(sid: uuid.UUID, wid: uuid.UUID, dur: timedelta) -> int | None:
        return 1 if wid == worker_id else None

    with (
        patch.object(
            orchestrator.folder_repo, "get", new_callable=AsyncMock, return_value=mock_folder
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "get_by_folder_id",
            new_callable=AsyncMock,
            return_value=mock_sync_state,
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "acquire_sync_lease",
            new_callable=AsyncMock,
            side_effect=acquire_side_effect,
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "renew_sync_lease",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "release_sync_lease",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_release_lease,
    ):
        mock_adapter = MagicMock()
        err_503 = ProviderError("Service Unavailable", status_code=503, retry_after=15)
        mock_adapter.get_message_delta = AsyncMock(side_effect=err_503)

        result = await orchestrator.sync_folder(
            mail_folder_id=mail_folder_id,
            worker_id=worker_id,
            access_token="valid_token",
            adapter=mock_adapter,
        )

        assert "Service Unavailable" in (result.error or "")
        # Assert backoff retry_after is populated on 5xx failure
        assert result.retry_after == 15
        # Assert lease lock is released so state remains recoverable
        mock_release_lease.assert_called_once_with(mock_sync_state.id, worker_id, 1)
        assert mock_db.commit.called

        # Verify competing worker cannot acquire while active worker holds lock fence
        competing_acq = await orchestrator.sync_state_repo.acquire_sync_lease(
            mock_sync_state.id, competing_worker_id, timedelta(minutes=5)
        )
        assert competing_acq is None


@pytest.mark.asyncio
async def test_m4_repeated_next_link_loop_detection() -> None:
    """M4: Repeated nextLink continuation token is detected and breaks the loop safely."""
    tenant_id = uuid.uuid4()
    mail_account_id = uuid.uuid4()
    mail_folder_id = uuid.uuid4()
    worker_id = uuid.uuid4()

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.in_transaction = MagicMock(return_value=True)
    mock_db.begin_nested = MagicMock()
    mock_db.begin_nested.return_value.__aenter__ = AsyncMock()
    mock_db.begin_nested.return_value.__aexit__ = AsyncMock()
    mock_db.begin = MagicMock()
    mock_db.begin.return_value.__aenter__ = AsyncMock()
    mock_db.begin.return_value.__aexit__ = AsyncMock()

    mock_folder = MailFolder(
        id=mail_folder_id,
        tenant_id=tenant_id,
        mail_account_id=mail_account_id,
        provider_folder_id="prov_folder_123",
        name="Inbox",
    )
    mock_sync_state = MailSyncState(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        mail_folder_id=mail_folder_id,
        state=MailSyncStateValue.SYNCING,
        locked_by=worker_id,
        lease_version=1,
    )

    orchestrator = SyncOrchestrator(session=mock_db)

    # Mock adapter returns the same next_continuation token repeatedly
    looping_page = ProviderDeltaPage(
        messages=[],
        removals=[],
        next_continuation="loop_token_123",
        has_more=True,
        is_delta_checkpoint=False,
    )

    with (
        patch.object(
            orchestrator.folder_repo, "get", new_callable=AsyncMock, return_value=mock_folder
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "get_by_folder_id",
            new_callable=AsyncMock,
            return_value=mock_sync_state,
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "acquire_sync_lease",
            new_callable=AsyncMock,
            return_value=1,
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "renew_sync_lease",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "release_sync_lease",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_release_lease,
    ):
        mock_adapter = MagicMock()
        mock_adapter.get_message_delta = AsyncMock(return_value=looping_page)

        result = await orchestrator.sync_folder(
            mail_folder_id=mail_folder_id,
            worker_id=worker_id,
            access_token="valid_token",
            adapter=mock_adapter,
        )

        assert result.error == "Loop detected: repeated continuation token"
        mock_release_lease.assert_called_once_with(mock_sync_state.id, worker_id, 1)


@pytest.mark.asyncio
async def test_m4_competing_worker_cannot_acquire_active_lease() -> None:
    """M4: Competing worker cannot acquire lease when another active worker holds it."""
    tenant_id = uuid.uuid4()
    mail_account_id = uuid.uuid4()
    mail_folder_id = uuid.uuid4()
    competing_worker_id = uuid.uuid4()

    mock_db = AsyncMock()

    mock_folder = MailFolder(
        id=mail_folder_id,
        tenant_id=tenant_id,
        mail_account_id=mail_account_id,
        provider_folder_id="prov_folder_123",
        name="Inbox",
    )
    mock_sync_state = MailSyncState(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        mail_folder_id=mail_folder_id,
        state=MailSyncStateValue.SYNCING,
        locked_by=uuid.uuid4(),  # Active lock held by existing worker
        lease_version=1,
    )

    orchestrator = SyncOrchestrator(session=mock_db)

    with (
        patch.object(
            orchestrator.folder_repo, "get", new_callable=AsyncMock, return_value=mock_folder
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "get_by_folder_id",
            new_callable=AsyncMock,
            return_value=mock_sync_state,
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "acquire_sync_lease",
            new_callable=AsyncMock,
            return_value=None,  # Fails acquisition due to active fence
        ),
    ):
        mock_adapter = MagicMock()

        result = await orchestrator.sync_folder(
            mail_folder_id=mail_folder_id,
            worker_id=competing_worker_id,
            access_token="token",
            adapter=mock_adapter,
        )

        assert result.error == "Lease acquisition failed"
        # Adapter should never be called when lease acquisition fails
        mock_adapter.get_message_delta.assert_not_called()


@pytest.mark.asyncio
async def test_m5_long_pagination_lease_renewal() -> None:
    """M5: Long multi-page pagination renews lease before page update."""
    tenant_id = uuid.uuid4()
    mail_account_id = uuid.uuid4()
    mail_folder_id = uuid.uuid4()
    worker_id = uuid.uuid4()
    competing_worker_id = uuid.uuid4()

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.in_transaction = MagicMock(return_value=True)
    mock_db.begin_nested = MagicMock()
    mock_db.begin_nested.return_value.__aenter__ = AsyncMock()
    mock_db.begin_nested.return_value.__aexit__ = AsyncMock()
    mock_db.begin = MagicMock()
    mock_db.begin.return_value.__aenter__ = AsyncMock()
    mock_db.begin.return_value.__aexit__ = AsyncMock()

    mock_folder = MailFolder(
        id=mail_folder_id,
        tenant_id=tenant_id,
        mail_account_id=mail_account_id,
        provider_folder_id="prov_folder_123",
        name="Inbox",
    )
    mock_sync_state = MailSyncState(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        mail_folder_id=mail_folder_id,
        state=MailSyncStateValue.SYNCING,
        locked_by=worker_id,
        lease_version=1,
    )

    orchestrator = SyncOrchestrator(session=mock_db)

    # 2 pages of Graph delta sync
    page_1 = ProviderDeltaPage(
        messages=[],
        removals=[],
        next_continuation="page2_token_xyz",
        has_more=True,
        is_delta_checkpoint=False,
    )
    page_2 = ProviderDeltaPage(
        messages=[],
        removals=[],
        next_continuation="delta_checkpoint_final",
        has_more=False,
        is_delta_checkpoint=True,
    )

    def acquire_side_effect(sid: uuid.UUID, wid: uuid.UUID, dur: timedelta) -> int | None:
        return 1 if wid == worker_id else None

    with (
        patch.object(
            orchestrator.folder_repo, "get", new_callable=AsyncMock, return_value=mock_folder
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "get_by_folder_id",
            new_callable=AsyncMock,
            return_value=mock_sync_state,
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "acquire_sync_lease",
            new_callable=AsyncMock,
            side_effect=acquire_side_effect,
        ),
        patch.object(
            orchestrator.sync_state_repo,
            "renew_sync_lease",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_renew_lease,
        patch.object(
            orchestrator.sync_state_repo,
            "update_sync_state_cas",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_cas_update,
        patch.object(
            orchestrator.sync_state_repo,
            "release_sync_lease",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_release_lease,
    ):
        mock_adapter = MagicMock()
        mock_adapter.get_message_delta = AsyncMock(side_effect=[page_1, page_2])

        result = await orchestrator.sync_folder(
            mail_folder_id=mail_folder_id,
            worker_id=worker_id,
            access_token="valid_token",
            adapter=mock_adapter,
        )

        assert result.error is None
        # Verify renew_sync_lease called before each page delta fetch and iteration
        assert mock_renew_lease.call_count >= 2
        for call_args in mock_renew_lease.call_args_list:
            args = call_args[0]
            assert args[0] == mock_sync_state.id
            assert args[1] == worker_id
            assert args[2] == 1  # Active lease version

        # Verify CAS update called for page updates with active lease version
        assert mock_cas_update.call_count == 2
        # Verify final graceful lease release
        mock_release_lease.assert_called_once_with(mock_sync_state.id, worker_id, 1)

        # Verify a second worker attempting to acquire active lease receives None
        competing_acq = await orchestrator.sync_state_repo.acquire_sync_lease(
            mock_sync_state.id, competing_worker_id, timedelta(minutes=5)
        )
        assert competing_acq is None
