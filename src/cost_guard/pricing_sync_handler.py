"""Daily pricing sync and reconciliation Lambda."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import boto3

from cost_guard.config import DEFAULT_PRICING, as_float, load_runtime_context
from cost_guard.state_store import StateStore

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)


def _publish_critical(topic_arn: str, subject: str, payload: dict[str, Any]) -> None:
    if not topic_arn:
        return
    boto3.client("sns").publish(
        TopicArn=topic_arn,
        Subject=subject[:100],
        Message=json.dumps(payload, separators=(",", ":"), default=str),
    )


def _fetch_pricing_defaults(config: dict[str, Any]) -> dict[str, float]:
    # v1 intentionally uses conservative defaults + safety markup for low complexity.
    pricing = dict(DEFAULT_PRICING)
    markup = as_float(config.get("pricing", {}).get("safety_markup_percent"), 10.0)
    multiplier = 1.0 + (markup / 100.0)
    pricing["lambda_request_per_million"] = pricing["lambda_request_per_million"] * multiplier
    pricing["lambda_gb_second"] = pricing["lambda_gb_second"] * multiplier
    return pricing


def _project_actual_cost(project_name: str) -> float | None:
    ce = boto3.client("ce")
    today = date.today()
    start = today.replace(day=1)
    end = today + timedelta(days=1)
    try:
        response = ce.get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            Filter={
                "Tags": {
                    "Key": "Project",
                    "Values": [project_name],
                    "MatchOptions": ["EQUALS"],
                }
            },
        )
    except Exception:  # pragma: no cover - depends on account setup/API permissions
        return None

    groups = response.get("ResultsByTime", [])
    if not groups:
        return None
    amount = groups[0].get("Total", {}).get("UnblendedCost", {}).get("Amount")
    if amount is None:
        return None
    try:
        return float(amount)
    except (TypeError, ValueError):
        return None


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    _ = (event, context)
    runtime = load_runtime_context()
    config = runtime.config
    state_store = StateStore(table_name=runtime.table_name, environment=runtime.environment)
    now = datetime.now(tz=UTC)

    pricing = _fetch_pricing_defaults(config)
    pricing_state = {
        "pricing": pricing,
        "source": "default_with_safety_markup",
        "updated_at": now.isoformat(),
    }
    state_store.put_pricing_state(pricing_state)

    project_state = state_store.get_project_state()
    estimated_mtd = as_float(project_state.get("estimated_mtd_usd"), 0.0)
    actual_mtd = _project_actual_cost(runtime.project_name)
    reconciliation = {
        "date_key": now.strftime("%Y-%m-%d"),
        "estimated_mtd_usd": estimated_mtd,
        "actual_mtd_usd": actual_mtd,
        "variance_usd": None if actual_mtd is None else (estimated_mtd - actual_mtd),
        "variance_percent": None
        if actual_mtd in (None, 0)
        else ((estimated_mtd - actual_mtd) / actual_mtd) * 100.0,
        "updated_at": now.isoformat(),
    }
    state_store.put_reconciliation(reconciliation["date_key"], reconciliation)

    # Notify if variance drifts significantly once actual cost is available.
    variance_percent = reconciliation.get("variance_percent")
    if variance_percent is not None and abs(variance_percent) > 30.0:
        payload = {
            "event": "cost_guard_reconciliation_variance_high",
            "environment": runtime.environment,
            "project": runtime.project_name,
            "estimated_mtd_usd": estimated_mtd,
            "actual_mtd_usd": actual_mtd,
            "variance_percent": variance_percent,
            "timestamp": now.isoformat(),
        }
        state_store.record_event(
            severity="warning",
            resource_id="project",
            message="Reconciliation variance exceeded 30%",
            details=payload,
        )
        _publish_critical(runtime.critical_topic_arn, "Cost Guard Reconciliation Warning", payload)

    return {
        "status": "ok",
        "pricing_source": pricing_state["source"],
        "reconciliation": reconciliation,
    }

