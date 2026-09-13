"""Deterministic Microsoft Graph API mock harness for E2E testing (PR-2.6).

Provides an in-process FastAPI application simulating /v1.0/me/mailFolders
and /v1.0/me/mailFolders/{folder_id}/messages/delta endpoints without external HTTP.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Fake Microsoft Graph Harness")

# State registry for fake responses
_folders_db: list[dict[str, Any]] = [
    {"id": "inbox_id_123", "displayName": "Inbox", "parentFolderId": None},
    {"id": "sent_id_456", "displayName": "Sent Items", "parentFolderId": None},
]

# Configurable delta responses per folder_id or token
_delta_responses_db: dict[str, list[dict[str, Any]]] = {}

# Simulated auth failure count or trigger flag
_auth_expired_trigger: dict[str, int] = {}
_fail_auth_count: int = 0
_auth_fail_counter: int = 0


def reset_fake_graph_harness() -> None:
    """Reset the harness state between tests."""
    global _folders_db
    global _delta_responses_db
    global _auth_expired_trigger
    global _fail_auth_count
    global _auth_fail_counter
    _folders_db = [
        {"id": "inbox_id_123", "displayName": "Inbox", "parentFolderId": None},
        {"id": "sent_id_456", "displayName": "Sent Items", "parentFolderId": None},
    ]
    _delta_responses_db.clear()
    _auth_expired_trigger.clear()
    _fail_auth_count = 0
    _auth_fail_counter = 0


def set_fail_auth_attempts(attempts: int) -> None:
    """Configure harness to return 401 AuthExpiredError for N requests."""
    global _fail_auth_count, _auth_fail_counter
    _fail_auth_count = attempts
    _auth_fail_counter = 0


def register_delta_pages(folder_id: str, pages: list[dict[str, Any]]) -> None:
    """Register a list of raw delta page responses for a given folder_id."""
    _delta_responses_db[folder_id] = pages


@app.get("/v1.0/me/mailFolders")
@app.get("/me/mailFolders")
async def get_mail_folders(
    authorization: str | None = Header(None),
    prefer: str | None = Header(None),
) -> dict[str, Any]:
    global _auth_fail_counter
    if _auth_fail_counter < _fail_auth_count:
        _auth_fail_counter += 1
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "InvalidAuthenticationToken",
                    "message": "Access token has expired.",
                }
            },
        )  # type: ignore[return-value]

    return {"value": _folders_db}


@app.get("/v1.0/me/mailFolders/{folder_id}/messages/delta")
@app.get("/me/mailFolders/{folder_id}/messages/delta")
async def get_messages_delta(
    folder_id: str,
    request: Request,
    authorization: str | None = Header(None),
    prefer: str | None = Header(None),
    skiptoken: str | None = Query(None, alias="$skiptoken"),
    deltatoken: str | None = Query(None, alias="$deltatoken"),
    select: str | None = Query(None, alias="$select"),
) -> dict[str, Any]:
    global _auth_fail_counter
    if _auth_fail_counter < _fail_auth_count:
        _auth_fail_counter += 1
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "InvalidAuthenticationToken",
                    "message": "Access token has expired.",
                }
            },
        )  # type: ignore[return-value]

    # Look up custom delta page responses
    pages = _delta_responses_db.get(folder_id)
    if pages:
        if skiptoken:
            # Match skiptoken index or fallback to second page
            try:
                idx = int(skiptoken.replace("page_", ""))
                if idx < len(pages):
                    return pages[idx]
            except ValueError:
                pass
            return pages[-1]
        # Return first page
        return pages[0]

    # Default fallback single page delta
    return {
        "value": [
            {
                "id": f"msg_default_{folder_id}_1",
                "subject": "Default Integration Subject",
                "body": {"contentType": "text", "content": "Default Body Content"},
                "bodyPreview": "Default Body Content",
                "receivedDateTime": "2026-09-12T10:00:00Z",
                "hasAttachments": False,
                "isRead": False,
                "sender": {
                    "emailAddress": {"name": "Alice Sender", "address": "alice@example.com"}
                },
                "toRecipients": [
                    {"emailAddress": {"name": "Bob Recipient", "address": "bob@example.com"}}
                ],
            }
        ],
        "@odata.deltaLink": f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder_id}/messages/delta?$deltatoken=delta_token_final_1",
    }
