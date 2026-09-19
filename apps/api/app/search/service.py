"""Search service coordinating requests to Elasticsearch."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.api.search.schemas import (
    MailSearchRequest,
    MailSearchResponse,
    SearchHit,
    SearchParticipant,
)

if TYPE_CHECKING:
    from app.search.elasticsearch_search import ElasticsearchSearchAdapter

logger = logging.getLogger(__name__)


class SearchService:
    """Service to handle Mail Search operations against Elasticsearch."""

    def __init__(self, es_adapter: ElasticsearchSearchAdapter) -> None:
        self.es_adapter = es_adapter

    async def search_mail(self, tenant_id: str, request: MailSearchRequest) -> MailSearchResponse:
        """Constructs an Elasticsearch DSL query and retrieves the results.

        Enforces tenant isolation by injecting a mandatory tenant_id term filter.
        Excludes soft-deleted tombstones.
        """
        index_name = f"mail_messages_{tenant_id}"

        # Mandatory filters that a user cannot override
        must_filters: list[dict[str, Any]] = [
            {"term": {"tenant_id": tenant_id}},
            {"term": {"is_deleted": False}},
        ]

        if request.account_ids:
            must_filters.append({"terms": {"mail_account_id": request.account_ids}})

        if request.folder_ids:
            must_filters.append({"terms": {"folder_ids": request.folder_ids}})

        if request.is_read is not None:
            must_filters.append({"term": {"is_read": request.is_read}})

        if request.has_attachments is not None:
            must_filters.append({"term": {"has_attachments": request.has_attachments}})

        if request.from_date or request.to_date:
            range_filter: dict[str, Any] = {}
            if request.from_date:
                range_filter["gte"] = request.from_date.isoformat()
            if request.to_date:
                range_filter["lte"] = request.to_date.isoformat()
            must_filters.append({"range": {"received_date_time": range_filter}})

        query: dict[str, Any] = {"bool": {"filter": must_filters}}

        if request.query and request.query.strip():
            query["bool"]["must"] = {
                "multi_match": {
                    "query": request.query.strip(),
                    # Simple text matching against subject, body, sender, and participants
                    "fields": [
                        "subject^2",
                        "sender^1.5",
                        "participants.name",
                        "participants.email",
                        "body",
                    ],
                    "type": "best_fields",
                }
            }

        body: dict[str, Any] = {
            "query": query,
            "size": request.page_size,
            "sort": [
                {"received_date_time": {"order": "desc", "missing": "_last"}},
                {"id": "desc"},  # Tie-breaking on document ID
            ],
            "_source": [
                "id",
                "mail_account_id",
                "subject",
                "sender",
                "participants",
                "received_date_time",
                "folder_ids",
                "is_read",
                "has_attachments",
            ],
        }

        if request.search_after:
            body["search_after"] = request.search_after

        resp = await self.es_adapter.search(index_name=index_name, query=body)

        hits_envelope = resp.get("hits", {})
        hits_array = hits_envelope.get("hits", [])

        results: list[SearchHit] = []
        next_cursor = None

        for hit in hits_array:
            source = hit.get("_source", {})
            participants_data = source.get("participants", [])

            participants = [
                SearchParticipant(
                    name=p.get("name"), email=p.get("email"), role=p.get("role", "unknown")
                )
                for p in participants_data
            ]

            results.append(
                SearchHit(
                    id=source["id"],
                    mail_account_id=source["mail_account_id"],
                    subject=source.get("subject"),
                    sender=source.get("sender"),
                    participants=participants,
                    received_date_time=source.get("received_date_time"),
                    folder_ids=source.get("folder_ids", []),
                    is_read=bool(source.get("is_read")),
                    has_attachments=bool(source.get("has_attachments")),
                )
            )

        if len(results) == request.page_size and len(hits_array) > 0:
            next_cursor = hits_array[-1].get("sort")

        return MailSearchResponse(items=results, next_page_cursor=next_cursor)
