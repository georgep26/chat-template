"""Tests for ``src/knowledge/filters.py`` Bedrock filter operator translation."""

from __future__ import annotations

import pytest

from knowledge.filters import (
    FilterError,
    build_bedrock_filter,
    merge_with_and_all,
    normalize_filter_input,
)


# Operator coverage ---------------------------------------------------------


def test_equals_translates_to_bedrock_equals():
    out = build_bedrock_filter({"key": "document_type", "equals": "pdf"})
    assert out == {"equals": {"key": "document_type", "value": "pdf"}}


def test_not_equals_translates_to_bedrock_notEquals():
    out = build_bedrock_filter({"key": "source_status", "not_equals": "failed"})
    assert out == {"notEquals": {"key": "source_status", "value": "failed"}}


def test_greater_than_and_lte_translate_correctly():
    gt = build_bedrock_filter({"key": "edition_year", "greater_than": 2020})
    lte = build_bedrock_filter({"key": "score", "less_than_or_equals": 0.5})
    assert gt == {"greaterThan": {"key": "edition_year", "value": 2020}}
    assert lte == {"lessThanOrEquals": {"key": "score", "value": 0.5}}


def test_greater_than_or_equals_and_less_than():
    gte = build_bedrock_filter({"key": "edition_year", "greater_than_or_equals": 2021})
    lt = build_bedrock_filter({"key": "page_start", "less_than": 100})
    assert gte == {"greaterThanOrEquals": {"key": "edition_year", "value": 2021}}
    assert lt == {"lessThan": {"key": "page_start", "value": 100}}


def test_in_operator_requires_non_empty_list():
    out = build_bedrock_filter({"key": "chunk_type", "in": ["page", "section"]})
    assert out == {"in": {"key": "chunk_type", "value": ["page", "section"]}}

    with pytest.raises(FilterError):
        build_bedrock_filter({"key": "chunk_type", "in": []})
    with pytest.raises(FilterError):
        build_bedrock_filter({"key": "chunk_type", "in": "page"})


def test_not_in_operator():
    out = build_bedrock_filter({"key": "access_tier", "not_in": ["restricted"]})
    assert out == {"notIn": {"key": "access_tier", "value": ["restricted"]}}


def test_starts_with_operator():
    out = build_bedrock_filter({"key": "document_id", "starts_with": "irc_"})
    assert out == {"startsWith": {"key": "document_id", "value": "irc_"}}


def test_scalar_operator_rejects_list_value():
    with pytest.raises(FilterError):
        build_bedrock_filter({"key": "document_type", "equals": ["pdf"]})


def test_unknown_operator_raises():
    with pytest.raises(FilterError):
        build_bedrock_filter({"key": "x", "regex_match": "^foo"})


def test_multiple_operators_on_one_key_raises():
    with pytest.raises(FilterError):
        build_bedrock_filter(
            {"key": "edition_year", "greater_than": 2000, "less_than": 2025}
        )


# Group composition --------------------------------------------------------


def test_and_all_wraps_children():
    out = build_bedrock_filter(
        {
            "and_all": [
                {"key": "document_type", "equals": "pdf"},
                {"key": "edition_year", "greater_than_or_equals": 2021},
            ]
        }
    )
    assert out == {
        "andAll": [
            {"equals": {"key": "document_type", "value": "pdf"}},
            {"greaterThanOrEquals": {"key": "edition_year", "value": 2021}},
        ]
    }


def test_or_all_wraps_children():
    out = build_bedrock_filter(
        {
            "or_all": [
                {"key": "code_family", "equals": "irc"},
                {"key": "code_family", "equals": "ibc"},
            ]
        }
    )
    assert out == {
        "orAll": [
            {"equals": {"key": "code_family", "value": "irc"}},
            {"equals": {"key": "code_family", "value": "ibc"}},
        ]
    }


def test_single_child_group_is_unwrapped():
    """A group with one child collapses to that child — no needless nesting."""
    out = build_bedrock_filter({"and_all": [{"key": "x", "equals": "y"}]})
    assert out == {"equals": {"key": "x", "value": "y"}}


def test_nested_groups():
    out = build_bedrock_filter(
        {
            "and_all": [
                {"key": "tenant_id", "equals": "tenant-a"},
                {
                    "or_all": [
                        {"key": "document_type", "equals": "pdf"},
                        {"key": "document_type", "equals": "word"},
                    ]
                },
            ]
        }
    )
    assert out == {
        "andAll": [
            {"equals": {"key": "tenant_id", "value": "tenant-a"}},
            {
                "orAll": [
                    {"equals": {"key": "document_type", "value": "pdf"}},
                    {"equals": {"key": "document_type", "value": "word"}},
                ]
            },
        ]
    }


def test_empty_group_raises():
    with pytest.raises(FilterError):
        build_bedrock_filter({"and_all": []})


# Legacy back-compat -------------------------------------------------------


def test_legacy_dict_single_value_becomes_equals():
    """{ "field": ["v1"] } → equals."""
    out = build_bedrock_filter({"document_type": ["pdf"]})
    assert out == {"equals": {"key": "document_type", "value": "pdf"}}


def test_legacy_dict_multi_value_becomes_or_all():
    out = build_bedrock_filter({"document_type": ["pdf", "word"]})
    assert out == {
        "orAll": [
            {"equals": {"key": "document_type", "value": "pdf"}},
            {"equals": {"key": "document_type", "value": "word"}},
        ]
    }


def test_legacy_dict_multiple_fields_become_and_all():
    out = build_bedrock_filter(
        {"document_type": ["pdf"], "code_family": ["irc", "ibc"]}
    )
    # Field order is preserved by Python 3.7+ dicts.
    assert out == {
        "andAll": [
            {"equals": {"key": "document_type", "value": "pdf"}},
            {
                "orAll": [
                    {"equals": {"key": "code_family", "value": "irc"}},
                    {"equals": {"key": "code_family", "value": "ibc"}},
                ]
            },
        ]
    }


def test_legacy_scalar_value_is_coerced_to_list():
    out = build_bedrock_filter({"document_type": "pdf"})
    assert out == {"equals": {"key": "document_type", "value": "pdf"}}


def test_empty_filter_returns_empty_dict():
    assert build_bedrock_filter(None) == {}
    assert build_bedrock_filter({}) == {}
    assert build_bedrock_filter({"document_type": []}) == {}


def test_normalize_then_build_is_idempotent():
    normalized = normalize_filter_input({"document_type": ["pdf"]})
    again = normalize_filter_input(normalized)
    assert again == normalized


def test_non_dict_input_raises():
    with pytest.raises(FilterError):
        build_bedrock_filter("not a dict")


# merge_with_and_all -------------------------------------------------------


def test_merge_drops_empty_filters():
    a = {"equals": {"key": "x", "value": "v"}}
    assert merge_with_and_all({}, a, {}) == a


def test_merge_combines_two_filters_under_and_all():
    a = {"equals": {"key": "tenant_id", "value": "tenant-a"}}
    b = {"equals": {"key": "document_type", "value": "pdf"}}
    assert merge_with_and_all(a, b) == {"andAll": [a, b]}


def test_merge_with_no_filters_returns_empty():
    assert merge_with_and_all() == {}
    assert merge_with_and_all({}, {}) == {}
