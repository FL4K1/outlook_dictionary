"""Deterministic Mock Query Understanding Provider for testing."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mip_ai.query_understanding.base import (
    QueryUnderstandingProvider,
    QueryUnderstandingResult,
)
from mip_models.search import DateRangeIntent, MailQueryPlan, ParticipantHint

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class DeterministicMockQueryUnderstandingProvider(QueryUnderstandingProvider):
    """Mock query understanding provider for tests and local development."""

    def __init__(
        self,
        canned_plan: MailQueryPlan | None = None,
        plan_factory: Callable[[str], MailQueryPlan] | None = None,
        model: str = "mock-query-plan-v1",
    ) -> None:
        self._canned_plan = canned_plan
        self._plan_factory = plan_factory
        self._model = model

    @property
    def model_id(self) -> str:
        return self._model

    async def understand_query(self, query: str) -> QueryUnderstandingResult:
        if self._canned_plan is not None:
            return QueryUnderstandingResult(
                query_plan=self._canned_plan,
                model_id=self._model,
                latency_ms=1.0,
                provider="mock",
                raw_response=self._canned_plan.model_dump_json(),
            )

        if self._plan_factory is not None:
            plan = self._plan_factory(query)
            return QueryUnderstandingResult(
                query_plan=plan,
                model_id=self._model,
                latency_ms=1.0,
                provider="mock",
                raw_response=plan.model_dump_json(),
            )

        # Simple deterministic parser heuristic for fallback testing
        lower = query.lower().strip()
        plan = MailQueryPlan()

        if "unread" in lower:
            plan.is_read = False
        if "attachment" in lower or "pdf" in lower:
            plan.has_attachments = True
        if "last week" in lower:
            plan.date_range = DateRangeIntent(kind="last_week")
        elif "today" in lower:
            plan.date_range = DateRangeIntent(kind="today")

        if "from " in lower:
            parts = lower.split("from ")
            if len(parts) > 1:
                sender_name = parts[1].split()[0]
                plan.sender = ParticipantHint(name=sender_name.capitalize())

        if "about " in lower:
            parts = lower.split("about ")
            if len(parts) > 1:
                plan.query = parts[1].split()[0]
        elif not plan.sender and not plan.is_read and not plan.date_range:
            plan.query = query

        return QueryUnderstandingResult(
            query_plan=plan,
            model_id=self._model,
            latency_ms=1.0,
            provider="mock",
            raw_response=plan.model_dump_json(),
        )
