"""Unit tests for History API message-record serialization."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.history_api_lambda.main import _message_record


def _sample_source(citation_number: int = 1) -> dict:
    return {
        "citation_number": citation_number,
        "document_id": "doc-abc",
        "source_type": "document",
        "score": 0.81,
        "chunk": "Free cancellation is available up to 24 hours before the appointment.",
        "cited_quotes": ["Free cancellation is available up to 24 hours before the appointment."],
        "quote_verified": True,
        "metadata": {"filename": "policy.pdf", "page_start": 4},
    }


def test_assistant_message_with_sources_round_trips():
    sources = [_sample_source(1), _sample_source(2)]
    record = _message_record(AIMessage(content="An answer.", additional_kwargs={"sources": sources}))

    assert record is not None
    assert record.role == "assistant"
    assert record.content == "An answer."
    assert record.sources == sources


def test_assistant_message_without_sources_emits_none():
    record = _message_record(AIMessage(content="An answer with no citations."))

    assert record is not None
    assert record.role == "assistant"
    assert record.sources is None


def test_assistant_message_with_empty_sources_list_emits_none():
    record = _message_record(AIMessage(content="Hi", additional_kwargs={"sources": []}))

    assert record is not None
    assert record.sources is None


def test_user_message_never_carries_sources():
    record = _message_record(HumanMessage(content="What is the cancellation policy?"))

    assert record is not None
    assert record.role == "user"
    assert record.sources is None


def test_system_message_maps_to_system_role_without_sources():
    record = _message_record(SystemMessage(content="You are a helpful assistant."))

    assert record is not None
    assert record.role == "system"
    assert record.sources is None


def test_sources_dump_excludes_none_for_user_message():
    record = _message_record(HumanMessage(content="Hello"))
    assert record is not None
    payload = record.model_dump(exclude_none=True)
    assert "sources" not in payload


def test_sources_dump_includes_sources_for_assistant():
    sources = [_sample_source(1)]
    record = _message_record(AIMessage(content="Hi", additional_kwargs={"sources": sources}))
    assert record is not None
    payload = record.model_dump(exclude_none=True)
    assert payload["sources"] == sources


def test_non_dict_entries_in_sources_are_filtered():
    payload = {"sources": [_sample_source(1), "garbage", 7, None]}
    record = _message_record(AIMessage(content="Hi", additional_kwargs=payload))

    assert record is not None
    assert record.sources == [_sample_source(1)]
