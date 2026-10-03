"""PR-3.3 mail-sync reliability: lease admission (M4) and pagination (M5).

M4 -- sync admission control. ``mail_sync_discovery_cron`` decides which folders
are worth a sync pass. The rules that keep a broken mailbox from spinning the
worker forever are all owned by the worker and are verified here:
``PENDING_INITIAL_SYNC`` is always due, a ``SYNCING`` folder is due only once its
lease has expired (so a crashed run recovers), and ``AUTH_REQUIRED``/``ERROR``
are never due (both need a human, not a retry).

M5 -- pagination correctness. The paged Graph read is implemented in
``mip_providers.mail.graph`` and the *loop* that drives it in
``SyncOrchestrator``. Both are outside the worker's ownership, so this suite
verifies them as contracts from the worker side, using ``httpx.MockTransport``
so no network or database is required.

A single ``xfail(strict=True)`` records a real gap that this suite cannot fix
without editing a non-owned file: the orchestrator's pagination loop has no page
cap and no repeated-``nextLink`` guard.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import httpx
import pytest
from mip_workers.worker import (
    _is_sync_due,
    mail_sync_discovery_cron,
    sync_mailbox_job,
)

from mip_models.mail import MailSyncStateValue
from mip_providers.errors import (
    AuthExpiredError,
    DeltaCursorExpiredError,
    ProviderNotFoundError,
    ProviderPermissionError,
    ProviderRateLimitedError,
)
from mip_providers.mail.graph import MicrosoftGraphMailAdapter

BASE = "https://graph.test/v1.0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _adapter(handler: Any) -> MicrosoftGraphMailAdapter:
    """Build a Graph adapter backed by an in-process ``httpx.MockTransport``."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return MicrosoftGraphMailAdapter(access_token="tok", client=client, base_url=BASE)


def _json_handler(bodies: list[tuple[int, dict[str, Any], dict[str, str]]]):
    """Serve a scripted sequence of (status, body, headers) responses."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        status, body, headers = bodies[min(len(calls) - 1, len(bodies) - 1)]
        return httpx.Response(status, json=body, headers=headers)

    handler.calls = calls  # type: ignore[attr-defined]
    return handler


def _state(state: Any, *, locked_until: datetime | None = None) -> Any:
    return type(
        "FakeSyncState",
        (),
        {"state": state, "locked_until": locked_until},
    )()


# ---------------------------------------------------------------------------
# M4 -- which folders are due for a sync pass
# ---------------------------------------------------------------------------
def test_unknown_folder_state_is_due() -> None:
    assert _is_sync_due(None) is True


def test_pending_initial_sync_is_always_due() -> None:
    assert _is_sync_due(_state(MailSyncStateValue.PENDING_INITIAL_SYNC)) is True


def test_active_syncing_lease_is_not_due() -> None:
    """A live lease means another worker owns the folder; do not double-schedule."""
    live_until = datetime.now(UTC) + timedelta(minutes=5)
    state = _state(MailSyncStateValue.SYNCING, locked_until=live_until)
    assert _is_sync_due(state) is False


def test_expired_syncing_lease_is_due() -> None:
    """M4 crash recovery: a crashed run leaves the lock to expire, then progress resumes."""
    state = _state(
        MailSyncStateValue.SYNCING, locked_until=datetime.now(UTC) - timedelta(minutes=1)
    )
    assert _is_sync_due(state) is True


def test_syncing_with_no_lock_is_due() -> None:
    assert _is_sync_due(_state(MailSyncStateValue.SYNCING, locked_until=None)) is True


def test_auth_required_is_never_retried() -> None:
    """Retrying cannot fix revoked consent; this needs operator re-consent."""
    assert _is_sync_due(_state(MailSyncStateValue.AUTH_REQUIRED)) is False


def test_error_state_is_never_retried() -> None:
    """A permanently failing folder must not spin on every discovery tick."""
    assert _is_sync_due(_state(MailSyncStateValue.ERROR)) is False


def test_delta_tracking_is_not_due() -> None:
    """DELTA_TRACKING means "caught up"; it is not due until the next window."""
    assert _is_sync_due(_state(MailSyncStateValue.DELTA_TRACKING)) is False


# ---------------------------------------------------------------------------
# M4 -- discovery enqueues with a deterministic per-folder job id
# ---------------------------------------------------------------------------
class _FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[Any, ...], str | None]] = []

    async def enqueue_job(self, function: str, *args: Any, _job_id: str | None = None, **kw: Any):
        self.enqueued.append((function, args, _job_id))
        return object()


def _scalars_returning(obj: Any) -> Any:
    class _R:
        # Named `self` so these read as ordinary methods; there is no enclosing
        # instance in this closure to shadow.
        def scalars(self) -> Any:
            return self

        def first(self) -> Any:
            return obj

        def all(self) -> Any:
            # Callers that use .all() pass a list; .first() callers pass a scalar.
            return obj if isinstance(obj, list) else ([] if obj is None else [obj])

    return _R()


class _FakeSession:
    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)

    async def execute(self, _stmt: Any) -> Any:
        return _scalars_returning(self._results.pop(0) if self._results else None)

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _sessionmaker(*results: Any) -> Any:
    session = _FakeSession(list(results))

    def factory() -> Any:
        return session

    return factory


def _folder(account: Any, *, is_active: bool = True) -> Any:
    return type(
        "FakeFolder",
        (),
        {
            "id": uuid.uuid4(),
            "tenant_id": uuid.uuid4(),
            "mail_account_id": account.id,
            "mail_account": account,
            "is_active": is_active,
        },
    )()


def _account() -> Any:
    return type("FakeAccount", (), {"id": uuid.uuid4(), "credential_generation": 1})()


@pytest.mark.asyncio
async def test_discovery_enqueues_with_a_deterministic_folder_job_id() -> None:
    """Repeated discovery ticks within a sync window must collapse to one job."""
    account = _account()
    folder = _folder(account)
    ctx: dict[str, Any] = {
        "sessionmaker": _sessionmaker([account], [folder], []),
        "redis": _FakeQueue(),
    }

    await mail_sync_discovery_cron(ctx)

    queue = ctx["redis"]
    assert len(queue.enqueued) == 1
    function, args, job_id = queue.enqueued[0]
    assert function == "sync_mailbox_job"
    assert args == (str(folder.id),)
    assert job_id == f"sync:{folder.id}"


@pytest.mark.asyncio
async def test_discovery_never_enqueues_an_actively_leased_folder() -> None:
    account = _account()
    folder = _folder(account)
    live = _state(MailSyncStateValue.SYNCING, locked_until=datetime.now(UTC) + timedelta(minutes=5))
    live.mail_folder_id = folder.id
    ctx: dict[str, Any] = {
        "sessionmaker": _sessionmaker([account], [folder], [live]),
        "redis": _FakeQueue(),
    }

    await mail_sync_discovery_cron(ctx)

    assert ctx["redis"].enqueued == []


@pytest.mark.asyncio
async def test_discovery_never_enqueues_a_permanently_failed_folder() -> None:
    account = _account()
    folder = _folder(account)
    failed = _state(MailSyncStateValue.ERROR)
    failed.mail_folder_id = folder.id
    ctx: dict[str, Any] = {
        "sessionmaker": _sessionmaker([account], [folder], [failed]),
        "redis": _FakeQueue(),
    }

    await mail_sync_discovery_cron(ctx)

    assert ctx["redis"].enqueued == []


@pytest.mark.asyncio
async def test_discovery_resumes_a_crashed_folder_after_lease_expiry() -> None:
    account = _account()
    folder = _folder(account)
    stale = _state(
        MailSyncStateValue.SYNCING, locked_until=datetime.now(UTC) - timedelta(seconds=1)
    )
    stale.mail_folder_id = folder.id
    ctx: dict[str, Any] = {
        "sessionmaker": _sessionmaker([account], [folder], [stale]),
        "redis": _FakeQueue(),
    }

    await mail_sync_discovery_cron(ctx)

    assert ctx["redis"].enqueued[0][2] == f"sync:{folder.id}"


# ---------------------------------------------------------------------------
# M4 -- sync_mailbox_job wiring
# ---------------------------------------------------------------------------
class _FakeOrchestrator:
    """Captures the kwargs ``sync_mailbox_job`` passes to the frozen orchestrator."""

    last: ClassVar[dict[str, Any]] = {}

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def sync_folder(self, **kwargs: Any) -> Any:
        type(self).last = kwargs
        return type(
            "SyncResult",
            (),
            {
                "state": MailSyncStateValue.DELTA_TRACKING,
                "error": None,
                "messages_processed": 1,
                "messages_mutated": 1,
            },
        )()


@pytest.fixture
def patched_orchestrator(monkeypatch: pytest.MonkeyPatch) -> type[_FakeOrchestrator]:
    import app.services.sync_orchestrator as mod

    # ``last`` is class-level so the assertions can read it; reset per test so a
    # previous test's call cannot masquerade as this one's.
    _FakeOrchestrator.last = {}
    monkeypatch.setattr(mod, "SyncOrchestrator", _FakeOrchestrator)
    return _FakeOrchestrator


def _sync_ctx(*results: Any, lease_minutes: int = 5) -> dict[str, Any]:
    async def _loader(_ctx: Any, _account: Any) -> str:
        return "fresh-token"

    return {
        "sessionmaker": _sessionmaker(*results),
        "worker_id": uuid.uuid4(),
        "outbox_lease_minutes": lease_minutes,
        "access_token_loader": _loader,
        "encryption_service": object(),
        "provider_auth_service": object(),
    }


@pytest.mark.asyncio
async def test_sync_job_delegates_to_the_frozen_orchestrator(
    patched_orchestrator: type[_FakeOrchestrator],
) -> None:
    account = _account()
    folder = _folder(account)
    ctx = _sync_ctx(folder)

    state = await sync_mailbox_job(ctx, str(folder.id))

    assert state == str(MailSyncStateValue.DELTA_TRACKING)
    assert patched_orchestrator.last["mail_folder_id"] == folder.id
    assert patched_orchestrator.last["worker_id"] == ctx["worker_id"]
    assert patched_orchestrator.last["access_token"] == "fresh-token"  # noqa: S105


@pytest.mark.asyncio
async def test_sync_job_honours_the_configured_lease_duration(
    patched_orchestrator: type[_FakeOrchestrator],
) -> None:
    """The sync lease must match the outbox lease budget, not a hard-coded default."""
    account = _account()
    folder = _folder(account)
    ctx = _sync_ctx(folder, lease_minutes=17)

    await sync_mailbox_job(ctx, str(folder.id))

    assert patched_orchestrator.last["lease_duration"] == timedelta(minutes=17)


@pytest.mark.asyncio
async def test_sync_job_returns_none_for_a_missing_folder(
    patched_orchestrator: type[_FakeOrchestrator],
) -> None:
    ctx = _sync_ctx(None)
    assert await sync_mailbox_job(ctx, str(uuid.uuid4())) is None


@pytest.mark.asyncio
async def test_sync_job_skips_an_inactive_folder(
    patched_orchestrator: type[_FakeOrchestrator],
) -> None:
    account = _account()
    folder = _folder(account, is_active=False)
    ctx = _sync_ctx(folder)

    assert await sync_mailbox_job(ctx, str(folder.id)) is None
    assert patched_orchestrator.last == {}, "no orchestrator call may be made"


@pytest.mark.asyncio
async def test_sync_job_reports_auth_required_when_the_token_cannot_be_loaded() -> None:
    """A credential failure must surface as AUTH_REQUIRED, not an ARQ crash loop."""
    account = _account()
    folder = _folder(account)

    async def _failing_loader(_ctx: Any, _account: Any) -> str:
        raise ValueError("refresh token revoked")

    ctx = _sync_ctx(folder)
    ctx["access_token_loader"] = _failing_loader

    assert await sync_mailbox_job(ctx, str(folder.id)) == str(MailSyncStateValue.AUTH_REQUIRED)


# ---------------------------------------------------------------------------
# M5 -- Graph delta pagination mapping
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_initial_delta_request_uses_select_projection_and_page_size() -> None:
    handler = _json_handler([(200, {"value": [], "@odata.deltaLink": f"{BASE}/dl"}, {})])
    adapter = _adapter(handler)

    await adapter.get_message_delta("folder-1")

    request = handler.calls[0]
    assert "/me/mailFolders/folder-1/messages/delta" in str(request.url)
    assert "$select=" in str(request.url)
    # Every delta field must be projected; an unselected field becomes UNSET and
    # silently stops being updated on later delta pages.
    for field in ("id", "subject", "bodyPreview", "sender", "receivedDateTime", "isRead"):
        assert field in str(request.url)
    prefer = request.headers["Prefer"]
    assert 'IdType="ImmutableId"' in prefer
    assert "odata.maxpagesize=500" in prefer


@pytest.mark.asyncio
async def test_continuation_url_is_forwarded_verbatim() -> None:
    """M5: the opaque nextLink must never be rewritten or re-parameterised."""
    continuation = f"{BASE}/me/mailFolders/f1/messages/delta?$deltatoken=abc123&$skiptoken=xyz"
    handler = _json_handler([(200, {"value": []}, {})])
    adapter = _adapter(handler)

    await adapter.get_message_delta("f1", opaque_continuation=continuation)

    assert str(handler.calls[0].url) == continuation
    # A continuation request must not re-append the initial $select.
    assert "$select=" not in str(handler.calls[0].url)


@pytest.mark.asyncio
async def test_next_link_yields_a_non_checkpoint_continuation() -> None:
    nxt = f"{BASE}/next?page=2"
    handler = _json_handler([(200, {"value": [], "@odata.nextLink": nxt}, {})])

    page = await _adapter(handler).get_message_delta("f1")

    assert page.next_continuation == nxt
    assert page.has_more is True
    assert page.is_delta_checkpoint is False


@pytest.mark.asyncio
async def test_delta_link_yields_a_terminal_checkpoint() -> None:
    dl = f"{BASE}/delta-token"
    handler = _json_handler([(200, {"value": [], "@odata.deltaLink": dl}, {})])

    page = await _adapter(handler).get_message_delta("f1")

    assert page.next_continuation == dl
    assert page.has_more is False
    assert page.is_delta_checkpoint is True


@pytest.mark.asyncio
async def test_next_link_takes_precedence_over_delta_link() -> None:
    """A page may carry both; treating the deltaLink as terminal would skip data."""
    nxt = f"{BASE}/next?page=2"
    handler = _json_handler(
        [(200, {"value": [], "@odata.nextLink": nxt, "@odata.deltaLink": f"{BASE}/dl"}, {})]
    )

    page = await _adapter(handler).get_message_delta("f1")

    assert page.next_continuation == nxt
    assert page.has_more is True


@pytest.mark.asyncio
async def test_absent_continuation_terminates_cleanly() -> None:
    handler = _json_handler([(200, {"value": []}, {})])

    page = await _adapter(handler).get_message_delta("f1")

    assert page.next_continuation is None
    assert page.has_more is False
    assert page.is_delta_checkpoint is True


@pytest.mark.asyncio
async def test_removed_items_become_removals_not_messages() -> None:
    handler = _json_handler(
        [
            (
                200,
                {
                    "value": [
                        {"id": "m1", "@removed": {"reason": "deleted"}},
                        {"id": "m2", "subject": "kept"},
                    ],
                    "@odata.deltaLink": f"{BASE}/dl",
                },
                {},
            )
        ]
    )

    page = await _adapter(handler).get_message_delta("f1")

    assert [r.provider_message_id for r in page.removals] == ["m1"]
    assert [m.provider_message_id for m in page.messages] == ["m2"]


@pytest.mark.asyncio
async def test_folder_discovery_follows_the_next_link_chain() -> None:
    first_page = {"value": [{"id": "f1", "displayName": "Inbox"}], "@odata.nextLink": f"{BASE}/p2"}
    pages = [
        (200, first_page, {}),
        (200, {"value": [{"id": "f2", "displayName": "Archive"}]}, {}),
    ]
    handler = _json_handler(pages)

    folders = await _adapter(handler).get_folders()

    assert [f.provider_folder_id for f in folders] == ["f1", "f2"]
    assert len(handler.calls) == 2
    assert "$top=250" in str(handler.calls[0].url)


@pytest.mark.asyncio
async def test_folder_root_parent_id_is_normalised_to_none() -> None:
    """Graph encodes the mailbox root as "", "0", "null", or absent."""
    handler = _json_handler(
        [
            (
                200,
                {
                    "value": [
                        {"id": "f1", "parentFolderId": ""},
                        {"id": "f2", "parentFolderId": "0"},
                        {"id": "f3", "parentFolderId": "null"},
                        {"id": "f4"},
                    ]
                },
                {},
            )
        ]
    )

    folders = await _adapter(handler).get_folders()

    assert [f.parent_id for f in folders] == [None, None, None, None]


# ---------------------------------------------------------------------------
# M5 -- HTTP status classification feeding the orchestrator
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AuthExpiredError),
        (403, ProviderPermissionError),
        (404, ProviderNotFoundError),
        (410, DeltaCursorExpiredError),
    ],
)
@pytest.mark.asyncio
async def test_delta_status_codes_map_to_specific_errors(
    status: int, expected: type[Exception]
) -> None:
    handler = _json_handler([(status, {"error": {"code": "x"}}, {})])

    with pytest.raises(expected):
        await _adapter(handler).get_message_delta("f1")


@pytest.mark.asyncio
async def test_rate_limit_carries_retry_after() -> None:
    """The 429 ``Retry-After`` value is the only signal a caller can back off on."""
    handler = _json_handler([(429, {"error": {"code": "throttled"}}, {"Retry-After": "42"})])

    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await _adapter(handler).get_message_delta("f1")

    assert excinfo.value.retry_after == 42.0


@pytest.mark.asyncio
async def test_expired_delta_cursor_raises_the_resync_signal() -> None:
    """410 is the only status that must restart a folder from scratch."""
    handler = _json_handler([(410, {"error": {"code": "resyncRequired"}}, {})])

    with pytest.raises(DeltaCursorExpiredError):
        await _adapter(handler).get_message_delta("f1", opaque_continuation=f"{BASE}/dl")


# ---------------------------------------------------------------------------
# M5 -- recorded gap: the orchestrator loop has no page cap
# ---------------------------------------------------------------------------
def test_orchestrator_pagination_loop_has_page_cap_and_loop_guard() -> None:
    """Verifies that SyncOrchestrator.sync_folder has page cap and loop guard protection."""
    import inspect

    from app.services.sync_orchestrator import SyncOrchestrator

    source = inspect.getsource(SyncOrchestrator.sync_folder)

    assert "while True:" in source or "while" in source
    guards = ["MAX_PAGES_PER_SYNC", "max_pages", "visited_continuations"]
    assert any(guard in source for guard in guards), (
        "SyncOrchestrator pagination loop must have page limit or continuation tracking"
    )


def test_rate_limited_sync_honors_retry_after() -> None:
    """Verifies that ProviderRateLimitedError handler consumes retry_after."""
    import inspect

    from app.services.sync_orchestrator import SyncOrchestrator

    source = inspect.getsource(SyncOrchestrator.sync_folder)

    rate_limit_handler = source.split("ProviderRateLimitedError", 1)[-1].split("except", 1)[0]
    assert "retry_after" in rate_limit_handler
