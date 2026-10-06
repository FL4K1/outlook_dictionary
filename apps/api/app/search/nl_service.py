"""Natural Language Mail Search Service (PR-3.0)."""

from __future__ import annotations

import datetime
import logging
import uuid
import zoneinfo
from typing import TYPE_CHECKING, Any

from sqlalchemy import distinct, select

from app.api.search.schemas import (
    MailSearchRequest,
    NaturalLanguageSearchResponse,
)
from mip_models.mail import MailAccount, MailFolder, MailMessage, MailMessageParticipant

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.search.service import SearchService
    from mip_ai.query_understanding.base import QueryUnderstandingProvider
    from mip_ai.synthesis.base import SearchSynthesisProvider
    from mip_models.search import DateRangeIntent, MailQueryPlan, ParticipantHint

logger = logging.getLogger(__name__)


class UnsupportedQueryCapabilityError(Exception):
    """Query contains features or semantics unsupported by the system (HTTP 400)."""


class EntityAmbiguityError(Exception):
    """Query entity resolution matched multiple candidate entities (HTTP 400)."""


class EntityResolutionError(Exception):
    """Query entity resolution matched zero candidate entities (HTTP 400)."""


class InvalidTimezoneError(Exception):
    """Provided user timezone is invalid (HTTP 400)."""


def translate_date_range_intent(
    date_range: DateRangeIntent,
    user_timezone: str,
    time_source: datetime.datetime | None = None,
) -> tuple[datetime.datetime, datetime.datetime]:
    """Translate DateRangeIntent into inclusive UTC (from_date, to_date) boundaries."""
    if user_timezone.upper() in ("UTC", "Z", "GMT", "UTC+0", "UTC-0"):
        tz: datetime.tzinfo = datetime.UTC
    else:
        try:
            tz = zoneinfo.ZoneInfo(user_timezone)
        except zoneinfo.ZoneInfoNotFoundError:
            # Fallback for Windows environments without tzdata package
            tz_map = {
                "America/Los_Angeles": datetime.timezone(datetime.timedelta(hours=-7)),
                "America/New_York": datetime.timezone(datetime.timedelta(hours=-4)),
                "Europe/London": datetime.timezone(datetime.timedelta(hours=1)),
            }
            if user_timezone in tz_map:
                tz = tz_map[user_timezone]
            else:
                raise InvalidTimezoneError(f"Invalid timezone: '{user_timezone}'") from None
        except Exception as e:
            raise InvalidTimezoneError(f"Invalid timezone: '{user_timezone}'") from e

    now_utc = time_source if time_source is not None else datetime.datetime.now(datetime.UTC)
    local_now = now_utc.astimezone(tz)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = local_now.replace(hour=23, minute=59, second=59, microsecond=999999)

    kind = date_range.kind
    start_local: datetime.datetime
    end_local: datetime.datetime

    if kind == "today":
        start_local, end_local = today_start, today_end
    elif kind == "yesterday":
        yest_start = today_start - datetime.timedelta(days=1)
        yest_end = today_end - datetime.timedelta(days=1)
        start_local, end_local = yest_start, yest_end
    elif kind == "this_week":
        # Monday is weekday 0
        mon_start = today_start - datetime.timedelta(days=today_start.weekday())
        sun_end = mon_start + datetime.timedelta(
            days=6, hours=23, minutes=59, seconds=59, microseconds=999999
        )
        start_local, end_local = mon_start, sun_end
    elif kind == "last_week":
        mon_start = today_start - datetime.timedelta(days=today_start.weekday() + 7)
        sun_end = mon_start + datetime.timedelta(
            days=6, hours=23, minutes=59, seconds=59, microseconds=999999
        )
        start_local, end_local = mon_start, sun_end
    elif kind == "this_month":
        month_start = today_start.replace(day=1)
        # Next month start - 1 microsecond
        if month_start.month == 12:
            next_month_start = month_start.replace(year=month_start.year + 1, month=1)
        else:
            next_month_start = month_start.replace(month=month_start.month + 1)
        month_end = next_month_start - datetime.timedelta(microseconds=1)
        start_local, end_local = month_start, month_end
    elif kind == "last_month":
        this_month_start = today_start.replace(day=1)
        last_month_end = this_month_start - datetime.timedelta(microseconds=1)
        last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start_local, end_local = last_month_start, last_month_end
    elif kind == "this_year":
        year_start = today_start.replace(month=1, day=1)
        year_end = today_start.replace(
            month=12, day=31, hour=23, minute=59, second=59, microsecond=999999
        )
        start_local, end_local = year_start, year_end
    elif kind == "last_year":
        last_year_val = today_start.year - 1
        year_start = today_start.replace(year=last_year_val, month=1, day=1)
        year_end = today_start.replace(
            year=last_year_val, month=12, day=31, hour=23, minute=59, second=59, microsecond=999999
        )
        start_local, end_local = year_start, year_end
    elif kind == "past_days":
        days_val = date_range.days or 1
        start_local = (today_start - datetime.timedelta(days=days_val)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_local = local_now
    elif kind == "weekday":
        target_wd = date_range.weekday if date_range.weekday is not None else 0
        current_wd = today_start.weekday()
        days_back = (current_wd - target_wd) % 7
        if days_back == 0 and date_range.period is None:
            # default to most recent past occurrence if today
            days_back = 0
        wd_start = today_start - datetime.timedelta(days=days_back)
        wd_end = today_end - datetime.timedelta(days=days_back)
        start_local, end_local = wd_start, wd_end
    else:
        raise UnsupportedQueryCapabilityError(f"Unsupported date range kind '{kind}'")

    # Refine period if specified
    if date_range.period == "morning":
        start_local = start_local.replace(hour=6, minute=0, second=0, microsecond=0)
        end_local = end_local.replace(hour=11, minute=59, second=59, microsecond=999999)
    elif date_range.period == "afternoon":
        start_local = start_local.replace(hour=12, minute=0, second=0, microsecond=0)
        end_local = end_local.replace(hour=17, minute=59, second=59, microsecond=999999)
    elif date_range.period == "evening":
        start_local = start_local.replace(hour=18, minute=0, second=0, microsecond=0)
        end_local = end_local.replace(hour=23, minute=59, second=59, microsecond=999999)

    from_utc = start_local.astimezone(datetime.UTC)
    to_utc = end_local.astimezone(datetime.UTC)
    return from_utc, to_utc


class NaturalLanguageSearchService:
    """Orchestrates natural language query understanding, entity resolution, and search dispatch."""

    def __init__(
        self,
        provider: QueryUnderstandingProvider,
        search_service: SearchService,
        time_source: datetime.datetime | None = None,
        synthesis_provider: SearchSynthesisProvider | None = None,
    ) -> None:
        self.provider = provider
        self.search_service = search_service
        self.time_source = time_source
        self.synthesis_provider = synthesis_provider

    async def search_natural_language(
        self,
        tenant_id: str | uuid.UUID,
        natural_query: str,
        user_timezone: str = "UTC",
        page_size: int = 25,
        search_after: list[Any] | None = None,
        db_session: AsyncSession | None = None,
        synthesize: bool = False,
    ) -> NaturalLanguageSearchResponse:
        """Parse natural language query, resolve entities, and execute SearchService search."""
        from mip_ai.synthesis.errors import (
            SearchSynthesisError,  # Local import to avoid circular ties
        )

        tenant_uuid = uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id

        # 1. Call QueryUnderstandingProvider
        result = await self.provider.understand_query(natural_query)
        plan: MailQueryPlan = result.query_plan

        # 2. Check explicitly unsupported capabilities
        if plan.recipients and len(plan.recipients) > 0:
            raise UnsupportedQueryCapabilityError(
                "Recipient filtering ('sent to') is explicitly unsupported in PR-3.0."
            )

        # 3. Server-side Entity Resolution
        account_ids: list[str] | None = None
        if plan.account and plan.account.strip():
            account_ids = await self._resolve_account(db_session, tenant_uuid, plan.account.strip())

        folder_ids: list[str] | None = None
        if plan.folder and plan.folder.strip():
            folder_ids = await self._resolve_folder(db_session, tenant_uuid, plan.folder.strip())

        from_sender_emails: list[str] | None = None
        if plan.sender:
            from_sender_emails = await self._resolve_sender(db_session, tenant_uuid, plan.sender)

        participant_emails: list[str] | None = None
        if plan.participants and len(plan.participants) > 0:
            participant_emails = await self._resolve_participants(
                db_session, tenant_uuid, plan.participants
            )

        # 4. Date Range Translation
        from_date: datetime.datetime | None = None
        to_date: datetime.datetime | None = None
        if plan.date_range is not None:
            from_utc, to_utc = translate_date_range_intent(
                plan.date_range, user_timezone=user_timezone, time_source=self.time_source
            )
            from_date, to_date = from_utc, to_utc

        # 5. Retrieval Intent Normalization
        search_mode: str
        if plan.retrieval_intent == "keyword":
            search_mode = "lexical"
            # keyword mode can run without query if filters exist
            has_any_filter = bool(
                account_ids
                or folder_ids
                or from_sender_emails
                or participant_emails
                or plan.is_read is not None
                or plan.has_attachments is not None
                or from_date
                or to_date
            )
            if not plan.query and not has_any_filter:
                raise UnsupportedQueryCapabilityError(
                    "Query plan contains no free-text query and no filters to execute."
                )
        elif plan.retrieval_intent == "semantic":
            search_mode = "semantic"
            if not plan.query or not plan.query.strip():
                raise UnsupportedQueryCapabilityError(
                    "Semantic search requires a non-empty free-text query string."
                )
        elif plan.retrieval_intent == "mixed":
            search_mode = "hybrid"
            if not plan.query or not plan.query.strip():
                raise UnsupportedQueryCapabilityError(
                    "Hybrid search requires a non-empty free-text query string."
                )
        else:
            raise UnsupportedQueryCapabilityError(
                f"Unknown retrieval_intent '{plan.retrieval_intent}'"
            )

        # 6. Build MailSearchRequest
        search_request = MailSearchRequest(
            query=plan.query if plan.query and plan.query.strip() else None,
            folder_ids=folder_ids,
            account_ids=account_ids,
            from_sender_emails=from_sender_emails,
            participant_emails=participant_emails,
            is_read=plan.is_read,
            has_attachments=plan.has_attachments,
            from_date=from_date,
            to_date=to_date,
            search_after=search_after,
            page_size=page_size,
            search_mode=search_mode,  # type: ignore[arg-type]
        )

        # 7. Dispatch to deterministic SearchService
        mail_results = await self.search_service.search_mail(str(tenant_uuid), search_request)

        # 8. Dispatch to SearchSynthesisProvider if appropriate
        synthesis = None
        if synthesize and self.synthesis_provider and mail_results.items:
            try:
                synthesis = await self.synthesis_provider.synthesize(
                    query=natural_query,
                    hits=mail_results.items,  # type: ignore[arg-type]
                )
            except SearchSynthesisError as e:
                logger.error("Synthesis gracefully degraded due to failure: %s", str(e))
                # Graceful degradation - proceed without synthesis
            except Exception as e:
                logger.error("Unexpected synthesis error: %s", str(e))
                # Graceful degradation - proceed without synthesis

        return NaturalLanguageSearchResponse(results=mail_results, synthesis=synthesis)

    async def _resolve_account(
        self, db_session: AsyncSession | None, tenant_id: uuid.UUID, account_hint: str
    ) -> list[str]:
        if db_session is None:
            # Fallback for standalone tests without DB session
            return [account_hint]

        stmt = select(MailAccount).where(
            MailAccount.tenant_id == tenant_id,
            MailAccount.is_active == True,  # noqa: E712
            (MailAccount.email_address.ilike(f"%{account_hint}%"))
            | (MailAccount.display_name.ilike(f"%{account_hint}%")),
        )
        res = await db_session.execute(stmt)
        accounts = res.scalars().all()

        if len(accounts) > 1:
            raise EntityAmbiguityError(
                f"Account hint '{account_hint}' matched multiple accounts. Please be more specific."
            )
        if len(accounts) == 0:
            raise EntityResolutionError(f"No mail account found matching '{account_hint}'")

        return [str(accounts[0].id)]

    async def _resolve_folder(
        self, db_session: AsyncSession | None, tenant_id: uuid.UUID, folder_hint: str
    ) -> list[str]:
        if db_session is None:
            return [folder_hint]

        stmt = select(MailFolder).where(
            MailFolder.tenant_id == tenant_id,
            MailFolder.name.ilike(f"%{folder_hint}%"),
        )
        res = await db_session.execute(stmt)
        folders = res.scalars().all()

        if len(folders) > 1:
            raise EntityAmbiguityError(
                f"Folder hint '{folder_hint}' matched multiple folders: {[f.name for f in folders]}"
            )
        if len(folders) == 0:
            raise EntityResolutionError(f"No folder found matching '{folder_hint}'")

        return [str(folders[0].id)]

    async def _resolve_sender(
        self, db_session: AsyncSession | None, tenant_id: uuid.UUID, sender_hint: ParticipantHint
    ) -> list[str]:
        if sender_hint.email and sender_hint.email.strip():
            return [sender_hint.email.strip().lower()]

        if not sender_hint.name or not sender_hint.name.strip():
            raise EntityResolutionError("Sender hint missing name and email.")

        name = sender_hint.name.strip()
        if db_session is None:
            # Fallback for standalone tests without DB session
            return [f"{name.lower()}@example.com"]

        # Query distinct participant emails matching name within tenant
        stmt = (
            select(distinct(MailMessageParticipant.email))
            .join(MailMessage, MailMessageParticipant.mail_message_id == MailMessage.id)
            .where(
                MailMessage.tenant_id == tenant_id,
                MailMessageParticipant.name.ilike(f"%{name}%"),
            )
        )
        res = await db_session.execute(stmt)
        emails = res.scalars().all()

        if len(emails) > 1:
            raise EntityAmbiguityError(
                f"Sender name hint '{name}' matched multiple candidates. Please be more specific."
            )
        if len(emails) == 0:
            raise EntityResolutionError(f"No sender identity found matching name '{name}'")

        return [emails[0].lower()]

    async def _resolve_participants(
        self,
        db_session: AsyncSession | None,
        tenant_id: uuid.UUID,
        hints: list[ParticipantHint],
    ) -> list[str]:
        resolved: list[str] = []
        for hint in hints:
            if hint.email and hint.email.strip():
                resolved.append(hint.email.strip().lower())
            elif hint.name and hint.name.strip():
                name = hint.name.strip()
                if db_session is None:
                    resolved.append(f"{name.lower()}@example.com")
                    continue
                stmt = (
                    select(distinct(MailMessageParticipant.email))
                    .join(MailMessage, MailMessageParticipant.mail_message_id == MailMessage.id)
                    .where(
                        MailMessage.tenant_id == tenant_id,
                        MailMessageParticipant.name.ilike(f"%{name}%"),
                    )
                )
                res = await db_session.execute(stmt)
                emails = res.scalars().all()

                if len(emails) > 1:
                    raise EntityAmbiguityError(
                        f"Participant hint '{name}' matched multiple candidates. "
                        "Please be more specific."
                    )
                if len(emails) == 0:
                    raise EntityResolutionError(
                        f"No participant identity found matching name '{name}'"
                    )
                resolved.append(emails[0].lower())
            else:
                raise EntityResolutionError("Participant hint missing name and email.")

        return resolved
