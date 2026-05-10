"""Bedrock Knowledge Base retrieval filter builder.

Translates a normalized filter input shape into the AWS Bedrock
``RetrievalFilter`` JSON shape used by ``vectorSearchConfiguration.filter``.

Two input shapes are supported:

1. Operator form (recommended for new callers):

    {"key": "edition_year", "greater_than_or_equals": 2021}
    {"key": "chunk_type", "in": ["page", "section"]}
    {"and_all": [ ... ]}
    {"or_all":  [ ... ]}

2. Legacy dict form (RAG Lambda back-compat):

    {"document_type": ["codes", "town_documents"], "access_tier": ["public"]}

  Each key produces an equality filter; multiple values become an ``orAll``
  group; multiple keys are combined with ``andAll``. This is the exact shape
  produced by the previous ``convert_filters_to_kb_format`` helper in
  ``rag_lambda/graph/retrieval.py``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


class FilterError(ValueError):
    """Raised when a filter input cannot be translated into a Bedrock filter."""


# Operator name -> Bedrock JSON key.
_OPERATORS = {
    "equals": "equals",
    "not_equals": "notEquals",
    "greater_than": "greaterThan",
    "greater_than_or_equals": "greaterThanOrEquals",
    "less_than": "lessThan",
    "less_than_or_equals": "lessThanOrEquals",
    "in": "in",
    "not_in": "notIn",
    "starts_with": "startsWith",
}

_LIST_OPERATORS = {"in", "not_in"}
_GROUP_OPERATORS = {"and_all": "andAll", "or_all": "orAll"}


def normalize_filter_input(value: Any) -> Dict[str, Any]:
    """Coerce caller input to the operator form, accepting the legacy dict shape."""
    if value is None or value == {}:
        return {}
    if not isinstance(value, dict):
        raise FilterError(f"Filter must be a JSON object, got {type(value).__name__}.")

    if _is_operator_form(value):
        return value

    return _legacy_to_operator_form(value)


def build_bedrock_filter(value: Any) -> Dict[str, Any]:
    """Build the ``vectorSearchConfiguration.filter`` payload (or ``{}``)."""
    normalized = normalize_filter_input(value)
    if not normalized:
        return {}
    return _translate_node(normalized)


def merge_with_and_all(*filters: Dict[str, Any]) -> Dict[str, Any]:
    """Combine multiple translated Bedrock filters under ``andAll``.

    Empty filters are dropped. A single non-empty filter is returned as-is.
    """
    nonempty = [f for f in filters if f]
    if not nonempty:
        return {}
    if len(nonempty) == 1:
        return nonempty[0]
    return {"andAll": nonempty}


# Internals ---------------------------------------------------------------


def _is_operator_form(value: Dict[str, Any]) -> bool:
    if "key" in value:
        return True
    return any(group_key in value for group_key in _GROUP_OPERATORS)


def _translate_node(node: Dict[str, Any]) -> Dict[str, Any]:
    for group_key, bedrock_key in _GROUP_OPERATORS.items():
        if group_key in node:
            children = node[group_key]
            if not isinstance(children, list) or not children:
                raise FilterError(
                    f"'{group_key}' must be a non-empty list of filter clauses."
                )
            translated = [_translate_node(child) for child in children]
            if len(translated) == 1:
                return translated[0]
            return {bedrock_key: translated}

    key = node.get("key")
    if not isinstance(key, str) or not key:
        raise FilterError("Leaf filter clauses must include a non-empty 'key'.")

    operator_entries = [op for op in _OPERATORS if op in node]
    if not operator_entries:
        raise FilterError(
            f"Filter for key '{key}' is missing a recognized operator. "
            f"Supported: {sorted(_OPERATORS)}."
        )
    if len(operator_entries) > 1:
        raise FilterError(
            f"Filter for key '{key}' has multiple operators ({operator_entries}); "
            "split into separate clauses combined with and_all/or_all."
        )

    op = operator_entries[0]
    bedrock_op = _OPERATORS[op]
    raw_value = node[op]
    if op in _LIST_OPERATORS:
        if not isinstance(raw_value, list) or not raw_value:
            raise FilterError(f"Operator '{op}' for key '{key}' requires a non-empty list.")
        value = list(raw_value)
    else:
        if isinstance(raw_value, (list, dict)):
            raise FilterError(
                f"Operator '{op}' for key '{key}' requires a scalar value."
            )
        value = raw_value

    return {bedrock_op: {"key": key, "value": value}}


def _legacy_to_operator_form(legacy: Dict[str, Any]) -> Dict[str, Any]:
    field_clauses: List[Dict[str, Any]] = []
    for field_name, raw_values in legacy.items():
        values = _coerce_legacy_values(raw_values)
        if not values:
            continue
        if len(values) == 1:
            field_clauses.append({"key": field_name, "equals": values[0]})
        else:
            field_clauses.append(
                {
                    "or_all": [
                        {"key": field_name, "equals": v} for v in values
                    ]
                }
            )

    if not field_clauses:
        return {}
    if len(field_clauses) == 1:
        return field_clauses[0]
    return {"and_all": field_clauses}


def _coerce_legacy_values(raw: Any) -> List[Any]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [v for v in raw if v not in (None, "")]
    if isinstance(raw, str) and raw == "":
        return []
    return [raw]


def collect_keys(node: Dict[str, Any]) -> Iterable[str]:
    """Yield every ``key`` referenced anywhere in the (operator-form) filter tree.

    Useful for callers that want to inspect which metadata fields are
    constrained — for instance, for telemetry or input validation.
    """
    if not isinstance(node, dict):
        return
    for group_key in _GROUP_OPERATORS:
        if group_key in node:
            children = node[group_key]
            if isinstance(children, list):
                for child in children:
                    yield from collect_keys(child)
            return
    key = node.get("key")
    if isinstance(key, str) and key:
        yield key
