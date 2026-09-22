"""Unit tests for NaturalLanguageSearchService components."""

import datetime
from unittest.mock import AsyncMock

import pytest

from app.search.nl_service import (
    InvalidTimezoneError,
    NaturalLanguageSearchService,
    UnsupportedQueryCapabilityError,
    translate_date_range_intent,
)
from mip_ai.query_understanding.mock import DeterministicMockQueryUnderstandingProvider
from mip_models.search import DateRangeIntent, MailQueryPlan, ParticipantHint


def test_date_translation_today_utc() -> None:
    # Injected reference time: 2026-09-20 15:30:00 UTC
    fixed_now = datetime.datetime(2026, 9, 20, 15, 30, 0, tzinfo=datetime.UTC)
    dri = DateRangeIntent(kind="today")

    from_utc, to_utc = translate_date_range_intent(dri, user_timezone="UTC", time_source=fixed_now)

    assert from_utc == datetime.datetime(2026, 9, 20, 0, 0, 0, tzinfo=datetime.UTC)
    assert to_utc == datetime.datetime(2026, 9, 20, 23, 59, 59, 999999, tzinfo=datetime.UTC)


def test_date_translation_timezone_offset() -> None:
    # Injected reference time: 2026-09-20 02:00:00 UTC -> 2026-09-19 19:00:00 in LA (UTC-7)
    fixed_now = datetime.datetime(2026, 9, 20, 2, 0, 0, tzinfo=datetime.UTC)
    dri = DateRangeIntent(kind="today")

    from_utc, to_utc = translate_date_range_intent(
        dri, user_timezone="America/Los_Angeles", time_source=fixed_now
    )

    # In LA, 'today' is Sept 19: 2026-09-19 00:00:00 PDT (+7 = 07:00:00 UTC) to Sept 20 06:59:59 UTC
    assert from_utc == datetime.datetime(2026, 9, 19, 7, 0, 0, tzinfo=datetime.UTC)
    assert to_utc == datetime.datetime(2026, 9, 20, 6, 59, 59, 999999, tzinfo=datetime.UTC)


def test_date_translation_invalid_timezone() -> None:
    dri = DateRangeIntent(kind="today")
    with pytest.raises(InvalidTimezoneError):
        translate_date_range_intent(dri, user_timezone="Invalid/NonExistent_Zone")


def test_date_translation_past_days() -> None:
    fixed_now = datetime.datetime(2026, 9, 20, 12, 0, 0, tzinfo=datetime.UTC)
    dri = DateRangeIntent(kind="past_days", days=3)

    from_utc, to_utc = translate_date_range_intent(dri, user_timezone="UTC", time_source=fixed_now)

    # past_days=3 -> starts at Sept 17 00:00:00 UTC
    assert from_utc == datetime.datetime(2026, 9, 17, 0, 0, 0, tzinfo=datetime.UTC)
    assert to_utc == fixed_now


@pytest.mark.asyncio
async def test_nl_service_rejects_recipient_intent() -> None:
    mock_provider = DeterministicMockQueryUnderstandingProvider(
        canned_plan=MailQueryPlan(recipients=[ParticipantHint(name="Rahul")])
    )
    mock_search_service = AsyncMock()

    service = NaturalLanguageSearchService(
        provider=mock_provider, search_service=mock_search_service
    )
    with pytest.raises(UnsupportedQueryCapabilityError):
        await service.search_natural_language(
            tenant_id="00000000-0000-0000-0000-000000000001",
            natural_query="emails sent to Rahul",
        )


@pytest.mark.asyncio
async def test_nl_service_semantic_without_query_fails() -> None:
    mock_provider = DeterministicMockQueryUnderstandingProvider(
        canned_plan=MailQueryPlan(
            query=None,
            retrieval_intent="semantic",
        )
    )
    mock_search_service = AsyncMock()

    service = NaturalLanguageSearchService(
        provider=mock_provider, search_service=mock_search_service
    )
    with pytest.raises(UnsupportedQueryCapabilityError):
        await service.search_natural_language(
            tenant_id="00000000-0000-0000-0000-000000000001",
            natural_query="conceptual search without text",
        )


@pytest.mark.asyncio
async def test_nl_service_dispatches_to_search_service() -> None:
    fixed_now = datetime.datetime(2026, 9, 20, 12, 0, 0, tzinfo=datetime.UTC)
    mock_provider = DeterministicMockQueryUnderstandingProvider(
        canned_plan=MailQueryPlan(
            query="Kubernetes",
            sender=ParticipantHint(email="rahul@example.com"),
            is_read=False,
            date_range=DateRangeIntent(kind="today"),
            retrieval_intent="keyword",
        )
    )
    mock_search_service = AsyncMock()
    mock_search_service.search_mail.return_value = {"hits": list[dict[str, str]]()}

    service = NaturalLanguageSearchService(
        provider=mock_provider,
        search_service=mock_search_service,
        time_source=fixed_now,
    )

    await service.search_natural_language(
        tenant_id="00000000-0000-0000-0000-000000000001",
        natural_query="unread emails from rahul@example.com about Kubernetes today",
        user_timezone="UTC",
    )

    mock_search_service.search_mail.assert_called_once()
    tenant_arg, req = mock_search_service.search_mail.call_args[0]

    assert tenant_arg == "00000000-0000-0000-0000-000000000001"
    assert req.query == "Kubernetes"
    assert req.from_sender_emails == ["rahul@example.com"]
    assert req.is_read is False
    assert req.search_mode == "lexical"
    assert req.from_date == datetime.datetime(2026, 9, 20, 0, 0, 0, tzinfo=datetime.UTC)
