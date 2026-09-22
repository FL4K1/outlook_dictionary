"""Unit tests for MailQueryPlan, DateRangeIntent, and ParticipantHint models."""

import pytest
from pydantic import ValidationError

from mip_models.search import DateRangeIntent, MailQueryPlan, ParticipantHint


def test_participant_hint_valid():
    hint = ParticipantHint(name="Rahul", email="rahul@example.com")
    assert hint.name == "Rahul"
    assert hint.email == "rahul@example.com"


def test_participant_hint_extra_forbid():
    with pytest.raises(ValidationError):
        ParticipantHint(name="Rahul", role="TO")  # type: ignore[call-arg]


def test_date_range_intent_valid_kinds():
    for kind in [
        "today",
        "yesterday",
        "this_week",
        "last_week",
        "this_month",
        "last_month",
        "this_year",
        "last_year",
    ]:
        dri = DateRangeIntent(kind=kind)  # type: ignore[arg-type]
        assert dri.kind == kind
        assert dri.days is None
        assert dri.weekday is None


def test_date_range_intent_kind_with_days_fails():
    with pytest.raises(ValidationError):
        DateRangeIntent(kind="today", days=5)


def test_date_range_intent_past_days_valid():
    dri = DateRangeIntent(kind="past_days", days=7)
    assert dri.kind == "past_days"
    assert dri.days == 7


def test_date_range_intent_past_days_invalid():
    with pytest.raises(ValidationError):
        DateRangeIntent(kind="past_days", days=0)

    with pytest.raises(ValidationError):
        DateRangeIntent(kind="past_days", days=-1)

    with pytest.raises(ValidationError):
        DateRangeIntent(kind="past_days")

    with pytest.raises(ValidationError):
        DateRangeIntent(kind="past_days", days=7, weekday=1)


def test_date_range_intent_weekday_valid():
    dri = DateRangeIntent(kind="weekday", weekday=0)  # Monday
    assert dri.weekday == 0

    dri6 = DateRangeIntent(kind="weekday", weekday=6)  # Sunday
    assert dri6.weekday == 6


def test_date_range_intent_weekday_invalid():
    with pytest.raises(ValidationError):
        DateRangeIntent(kind="weekday", weekday=7)

    with pytest.raises(ValidationError):
        DateRangeIntent(kind="weekday", weekday=-1)

    with pytest.raises(ValidationError):
        DateRangeIntent(kind="weekday")

    with pytest.raises(ValidationError):
        DateRangeIntent(kind="weekday", weekday=0, days=5)


def test_mail_query_plan_valid():
    plan = MailQueryPlan(
        query="Kubernetes",
        sender=ParticipantHint(name="Rahul"),
        is_read=False,
        retrieval_intent="keyword",
    )
    assert plan.query == "Kubernetes"
    assert plan.sender.name == "Rahul"
    assert plan.is_read is False
    assert plan.retrieval_intent == "keyword"


def test_mail_query_plan_extra_forbid():
    with pytest.raises(ValidationError):
        MailQueryPlan(query="test", elasticsearch_dsl={"query": "match_all"})  # type: ignore[call-arg]


def test_mail_query_plan_invalid_retrieval_intent():
    with pytest.raises(ValidationError):
        MailQueryPlan(retrieval_intent="fuzzy")  # type: ignore[arg-type]
