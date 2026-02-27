"""Configuration helpers for cost guard runtime and local tooling."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "mode": "observe",
    "budget": {
        "period": "monthly",
        "amount_usd": 20.0,
        "warning_percent": 80.0,
        "hard_percent": 100.0,
    },
    "monitor": {
        "interval_minutes": 5,
        "consecutive_breach_count": 2,
        "cooldown_minutes": 30,
    },
    "pricing": {
        "refresh_hour_utc": 4,
        "safety_markup_percent": 10.0,
    },
    "bedrock": {
        "enforce_model_allowlist": True,
        "allowed_model_ids": ["amazon.titan-embed-text-v2:0"],
        "blocked_model_ids": [],
        "default_invocation_cost_usd": 0.02,
        "model_invocation_cost_usd": {},
    },
    "reports": {
        "output_dir": "docs/cost_reports",
        "formats": ["json", "html", "png"],
        "retention_days": 90,
    },
    "resources": [],
}

DEFAULT_PRICING: dict[str, float] = {
    "lambda_request_per_million": 0.20,
    "lambda_gb_second": 0.0000166667,
}


@dataclass(frozen=True)
class RuntimeContext:
    project_name: str
    environment: str
    table_name: str
    warning_topic_arn: str
    critical_topic_arn: str
    enforcer_function_name: str
    config: dict[str, Any]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def parse_cost_guard_config(raw_json: str | None) -> dict[str, Any]:
    if not raw_json:
        return deepcopy(DEFAULT_CONFIG)
    parsed: dict[str, Any] = json.loads(raw_json)
    return _deep_merge(DEFAULT_CONFIG, parsed)


def load_runtime_context() -> RuntimeContext:
    project_name = os.getenv("PROJECT_NAME", "chat-template")
    environment = os.getenv("ENVIRONMENT", "dev")
    table_name = os.getenv("STATE_TABLE_NAME", f"{project_name}-cost-guard-{environment}")
    warning_topic_arn = os.getenv("WARNING_TOPIC_ARN", "")
    critical_topic_arn = os.getenv("CRITICAL_TOPIC_ARN", "")
    enforcer_function_name = os.getenv("ENFORCER_FUNCTION_NAME", "")
    raw_json = os.getenv("COST_GUARD_CONFIG_JSON", "{}")
    config = parse_cost_guard_config(raw_json)
    return RuntimeContext(
        project_name=project_name,
        environment=environment,
        table_name=table_name,
        warning_topic_arn=warning_topic_arn,
        critical_topic_arn=critical_topic_arn,
        enforcer_function_name=enforcer_function_name,
        config=config,
    )


def as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

