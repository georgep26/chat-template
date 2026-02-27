"""Scheduled monitor Lambda for near-realtime cost guard decisions."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3

from cost_guard.config import DEFAULT_PRICING, as_float, as_int, load_runtime_context
from cost_guard.logic import (
    choose_culprit,
    evaluate_budget_signal,
    month_start,
    update_hard_breach_streak,
    warning_in_cooldown,
)
from cost_guard.state_store import StateStore

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

LAMBDA_REQUESTS_PER_MILLION_DEFAULT = 0.20
LAMBDA_GB_SECOND_DEFAULT = 0.0000166667
BEDROCK_EVENT_NAMES = {
    "InvokeModel",
    "InvokeModelWithResponseStream",
    "Converse",
    "ConverseStream",
}


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except ValueError:
        return None


def _safe_json(data: dict[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"), default=str)


def _build_pricing(config: dict[str, Any], pricing_state: dict[str, Any]) -> dict[str, float]:
    pricing = dict(DEFAULT_PRICING)
    dynamic = pricing_state.get("pricing", {}) if pricing_state else {}
    for key, value in dynamic.items():
        pricing[key] = as_float(value, pricing.get(key, 0.0))

    markup = as_float(config.get("pricing", {}).get("safety_markup_percent"), 10.0)
    multiplier = 1.0 + (markup / 100.0)
    pricing["lambda_request_per_million"] = as_float(
        pricing.get("lambda_request_per_million"),
        LAMBDA_REQUESTS_PER_MILLION_DEFAULT,
    ) * multiplier
    pricing["lambda_gb_second"] = as_float(
        pricing.get("lambda_gb_second"),
        LAMBDA_GB_SECOND_DEFAULT,
    ) * multiplier
    return pricing


def _match_role(identifier: str, cloudtrail_event: dict[str, Any]) -> bool:
    user_identity = cloudtrail_event.get("userIdentity", {}) or {}
    arn = (user_identity.get("arn") or "").lower()
    role_name = (
        user_identity.get("sessionContext", {})
        .get("sessionIssuer", {})
        .get("userName", "")
        .lower()
    )
    normalized = identifier.lower()
    return normalized in arn or normalized == role_name


def _extract_model_id(cloudtrail_event: dict[str, Any]) -> str:
    params = cloudtrail_event.get("requestParameters", {}) or {}
    return (
        params.get("modelId")
        or params.get("modelIdentifier")
        or params.get("modelArn")
        or "unknown-model"
    )


def _get_lambda_memory_mb(lambda_client: Any, function_name: str) -> int:
    try:
        response = lambda_client.get_function_configuration(FunctionName=function_name)
        return int(response.get("MemorySize", 1024))
    except Exception as exc:  # pragma: no cover - network/AWS fallback
        LOGGER.warning("Unable to get lambda memory for %s: %s", function_name, exc)
        return 1024


def _collect_lambda_costs(
    cloudwatch_client: Any,
    lambda_client: Any,
    resources: list[dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
    pricing: dict[str, float],
    state_store: StateStore,
    month_key: str,
) -> list[dict[str, Any]]:
    if not resources:
        return []

    metric_queries: list[dict[str, Any]] = []
    query_to_resource: dict[str, tuple[str, str]] = {}
    for resource in resources:
        rid = resource["id"]
        function_name = resource["identifier"]

        inv_id = f"inv_{rid}".replace("-", "_")
        dur_id = f"dur_{rid}".replace("-", "_")

        metric_queries.append(
            {
                "Id": inv_id,
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/Lambda",
                        "MetricName": "Invocations",
                        "Dimensions": [{"Name": "FunctionName", "Value": function_name}],
                    },
                    "Period": 300,
                    "Stat": "Sum",
                },
                "ReturnData": True,
            }
        )
        metric_queries.append(
            {
                "Id": dur_id,
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/Lambda",
                        "MetricName": "Duration",
                        "Dimensions": [{"Name": "FunctionName", "Value": function_name}],
                    },
                    "Period": 300,
                    "Stat": "Sum",
                },
                "ReturnData": True,
            }
        )
        query_to_resource[inv_id] = (rid, "invocations")
        query_to_resource[dur_id] = (rid, "duration_ms")

    result = cloudwatch_client.get_metric_data(
        MetricDataQueries=metric_queries,
        StartTime=start_time,
        EndTime=end_time,
        ScanBy="TimestampAscending",
    )

    totals: dict[str, dict[str, float]] = {resource["id"]: {"invocations": 0.0, "duration_ms": 0.0} for resource in resources}
    for metric_result in result.get("MetricDataResults", []):
        query_id = metric_result.get("Id", "")
        mapped = query_to_resource.get(query_id)
        if not mapped:
            continue
        resource_id, field = mapped
        values = metric_result.get("Values", []) or []
        totals[resource_id][field] = float(sum(values))

    outputs: list[dict[str, Any]] = []
    for resource in resources:
        rid = resource["id"]
        function_name = resource["identifier"]
        invocations = totals[rid]["invocations"]
        duration_ms = totals[rid]["duration_ms"]
        memory_mb = _get_lambda_memory_mb(lambda_client, function_name)

        request_cost = (invocations / 1_000_000.0) * pricing["lambda_request_per_million"]
        duration_seconds = duration_ms / 1000.0
        compute_cost = duration_seconds * (memory_mb / 1024.0) * pricing["lambda_gb_second"]
        estimated_mtd = request_cost + compute_cost

        previous_state = state_store.get_resource_state(rid)
        previous_month = previous_state.get("month_key")
        previous_mtd = as_float(previous_state.get("estimated_mtd_usd"), 0.0)
        if previous_month != month_key:
            previous_mtd = 0.0
        interval_usd = max(estimated_mtd - previous_mtd, 0.0)

        outputs.append(
            {
                "id": rid,
                "type": resource.get("type"),
                "identifier": function_name,
                "enforcement": resource.get("enforcement"),
                "priority": as_int(resource.get("priority"), 999),
                "memory_mb": memory_mb,
                "invocations": invocations,
                "duration_ms": duration_ms,
                "interval_usd": interval_usd,
                "estimated_mtd_usd": estimated_mtd,
            }
        )

    return outputs


def _lookup_cloudtrail_events(
    cloudtrail_client: Any,
    start_time: datetime,
    end_time: datetime,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "LookupAttributes": [{"AttributeKey": "EventSource", "AttributeValue": "bedrock.amazonaws.com"}],
        "StartTime": start_time,
        "EndTime": end_time,
        "MaxResults": 50,
    }
    while True:
        response = cloudtrail_client.lookup_events(**kwargs)
        events.extend(response.get("Events", []))
        next_token = response.get("NextToken")
        if not next_token:
            break
        kwargs["NextToken"] = next_token
    return events


def _collect_bedrock_costs(
    cloudtrail_client: Any,
    resources: list[dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
    config: dict[str, Any],
    state_store: StateStore,
    month_key: str,
) -> list[dict[str, Any]]:
    if not resources:
        return []

    bedrock_cfg = config.get("bedrock", {})
    default_invocation_cost = as_float(bedrock_cfg.get("default_invocation_cost_usd"), 0.02)
    model_cost_map = bedrock_cfg.get("model_invocation_cost_usd", {}) or {}

    role_resources = {resource["id"]: resource for resource in resources}
    role_counts: dict[str, int] = {resource["id"]: 0 for resource in resources}
    role_costs: dict[str, float] = {resource["id"]: 0.0 for resource in resources}

    for raw_event in _lookup_cloudtrail_events(cloudtrail_client, start_time, end_time):
        event_payload = raw_event.get("CloudTrailEvent")
        if not event_payload:
            continue
        try:
            parsed_event = json.loads(event_payload)
        except json.JSONDecodeError:
            continue
        if parsed_event.get("eventName") not in BEDROCK_EVENT_NAMES:
            continue
        model_id = _extract_model_id(parsed_event)
        per_invocation_cost = as_float(model_cost_map.get(model_id), default_invocation_cost)

        for rid, resource in role_resources.items():
            if _match_role(resource["identifier"], parsed_event):
                role_counts[rid] += 1
                role_costs[rid] += per_invocation_cost
                break

    outputs: list[dict[str, Any]] = []
    for rid, resource in role_resources.items():
        previous_state = state_store.get_resource_state(rid)
        previous_month = previous_state.get("month_key")
        previous_mtd = as_float(previous_state.get("estimated_mtd_usd"), 0.0)
        if previous_month != month_key:
            previous_mtd = 0.0
        interval_usd = role_costs[rid]
        estimated_mtd = previous_mtd + interval_usd
        outputs.append(
            {
                "id": rid,
                "type": resource.get("type"),
                "identifier": resource.get("identifier"),
                "enforcement": resource.get("enforcement"),
                "priority": as_int(resource.get("priority"), 999),
                "bedrock_invocations_interval": role_counts[rid],
                "interval_usd": interval_usd,
                "estimated_mtd_usd": estimated_mtd,
            }
        )
    return outputs


def _publish_sns(topic_arn: str, subject: str, payload: dict[str, Any]) -> None:
    if not topic_arn:
        return
    sns = boto3.client("sns")
    sns.publish(TopicArn=topic_arn, Subject=subject[:100], Message=_safe_json(payload))


def _invoke_enforcer(function_name: str, payload: dict[str, Any]) -> None:
    if not function_name:
        return
    lambda_client = boto3.client("lambda")
    lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="Event",
        Payload=json.dumps(payload, default=str).encode("utf-8"),
    )


def _enabled_resources(config: dict[str, Any]) -> list[dict[str, Any]]:
    resources = config.get("resources", [])
    return [resource for resource in resources if resource.get("enabled", True)]


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    _ = context
    runtime = load_runtime_context()
    config = runtime.config
    if not config.get("enabled", True):
        return {"status": "disabled"}

    now = datetime.now(tz=UTC)
    current_month_key = now.strftime("%Y-%m")
    current_day_key = now.strftime("%Y-%m-%d")

    state_store = StateStore(table_name=runtime.table_name, environment=runtime.environment)
    project_state = state_store.get_project_state()
    previous_month_key = project_state.get("month_key")
    previous_warning_at = _parse_iso8601(project_state.get("last_warning_at"))
    previous_hard_streak = as_int(project_state.get("hard_breach_streak"), 0)
    last_run_at = _parse_iso8601(project_state.get("last_run_at"))

    monitor_cfg = config.get("monitor", {})
    interval_minutes = as_int(monitor_cfg.get("interval_minutes"), 5)
    if not last_run_at:
        last_run_at = now - timedelta(minutes=interval_minutes)

    if previous_month_key != current_month_key:
        previous_hard_streak = 0
        previous_warning_at = None

    cw = boto3.client("cloudwatch")
    lambda_client = boto3.client("lambda")
    cloudtrail_client = boto3.client("cloudtrail")

    pricing_state = state_store.get_pricing_state()
    pricing = _build_pricing(config, pricing_state)

    resources = _enabled_resources(config)
    lambda_resources = [resource for resource in resources if resource.get("type") == "lambda"]
    role_resources = [resource for resource in resources if resource.get("type") == "iam_role"]

    lambda_costs = _collect_lambda_costs(
        cloudwatch_client=cw,
        lambda_client=lambda_client,
        resources=lambda_resources,
        start_time=month_start(now),
        end_time=now,
        pricing=pricing,
        state_store=state_store,
        month_key=current_month_key,
    )
    bedrock_costs = _collect_bedrock_costs(
        cloudtrail_client=cloudtrail_client,
        resources=role_resources,
        start_time=last_run_at,
        end_time=now,
        config=config,
        state_store=state_store,
        month_key=current_month_key,
    )

    all_resources = lambda_costs + bedrock_costs
    estimated_mtd_usd = sum(as_float(item.get("estimated_mtd_usd"), 0.0) for item in all_resources)

    budget_cfg = config.get("budget", {})
    budget_amount = as_float(
        budget_cfg.get("amount_usd"),
        as_float(project_state.get("budget_amount_usd"), 20.0),
    )
    warning_percent = as_float(budget_cfg.get("warning_percent"), 80.0)
    hard_percent = as_float(budget_cfg.get("hard_percent"), 100.0)
    signal = evaluate_budget_signal(
        estimated_mtd_usd=estimated_mtd_usd,
        budget_amount_usd=budget_amount,
        warning_percent=warning_percent,
        hard_percent=hard_percent,
        now=now,
    )

    hard_streak = update_hard_breach_streak(previous_hard_streak, signal.hard_breached)
    culprit = choose_culprit(all_resources)

    cooldown_minutes = as_int(monitor_cfg.get("cooldown_minutes"), 30)
    warning_sent = False
    if signal.warning_breached and not warning_in_cooldown(previous_warning_at, now, cooldown_minutes):
        message = {
            "event": "warning_threshold_reached",
            "environment": runtime.environment,
            "project": runtime.project_name,
            "signal_usd": signal.signal_usd,
            "warning_threshold_usd": signal.warning_threshold_usd,
            "hard_threshold_usd": signal.hard_threshold_usd,
            "estimated_mtd_usd": signal.estimated_mtd_usd,
            "forecast_month_end_usd": signal.forecast_month_end_usd,
            "culprit_resource_id": culprit.get("id") if culprit else "",
        }
        _publish_sns(runtime.warning_topic_arn, "Cost Guard Warning", message)
        state_store.record_event(
            severity="warning",
            resource_id=culprit.get("id") if culprit else "project",
            message="Warning threshold reached",
            details=message,
        )
        warning_sent = True

    last_enforced_at = project_state.get("last_enforced_at")
    consecutive_breach_count = as_int(monitor_cfg.get("consecutive_breach_count"), 2)
    if (
        signal.hard_breached
        and hard_streak >= consecutive_breach_count
        and config.get("mode", "observe") == "enforce"
        and culprit
    ):
        action_payload = {
            "event_type": "enforce",
            "environment": runtime.environment,
            "project": runtime.project_name,
            "resource": culprit,
            "reason": {
                "signal_usd": signal.signal_usd,
                "estimated_mtd_usd": signal.estimated_mtd_usd,
                "forecast_month_end_usd": signal.forecast_month_end_usd,
                "hard_threshold_usd": signal.hard_threshold_usd,
                "hard_breach_streak": hard_streak,
            },
            "triggered_at": now.isoformat(),
        }
        _invoke_enforcer(runtime.enforcer_function_name, action_payload)
        state_store.record_event(
            severity="critical",
            resource_id=culprit["id"],
            message="Hard threshold reached; enforcement triggered",
            details=action_payload,
        )
        last_enforced_at = now.isoformat()

    for item in all_resources:
        existing = state_store.get_resource_state(item["id"])
        breach_state = "none"
        if culprit and culprit.get("id") == item["id"]:
            if signal.hard_breached:
                breach_state = "hard"
            elif signal.warning_breached:
                breach_state = "warning"

        state_store.put_resource_state(
            item["id"],
            {
                "resource_id": item["id"],
                "type": item.get("type"),
                "identifier": item.get("identifier"),
                "enforcement": item.get("enforcement"),
                "priority": item.get("priority", 999),
                "estimated_mtd_usd": item.get("estimated_mtd_usd", 0.0),
                "interval_usd": item.get("interval_usd", 0.0),
                "forecast_month_end_usd": signal.forecast_month_end_usd,
                "breach_state": breach_state,
                "last_action": existing.get("last_action", "none"),
                "last_action_at": existing.get("last_action_at"),
                "action_active": existing.get("action_active", False),
                "metadata": {
                    "invocations": item.get("invocations"),
                    "duration_ms": item.get("duration_ms"),
                    "memory_mb": item.get("memory_mb"),
                    "bedrock_invocations_interval": item.get("bedrock_invocations_interval"),
                },
                "month_key": current_month_key,
                "updated_at": now.isoformat(),
            },
        )

    state_store.put_project_state(
        {
            "month_key": current_month_key,
            "date_key": current_day_key,
            "budget_amount_usd": budget_amount,
            "warning_percent": warning_percent,
            "hard_percent": hard_percent,
            "warning_threshold_usd": signal.warning_threshold_usd,
            "hard_threshold_usd": signal.hard_threshold_usd,
            "signal_usd": signal.signal_usd,
            "estimated_mtd_usd": signal.estimated_mtd_usd,
            "forecast_month_end_usd": signal.forecast_month_end_usd,
            "warning_breached": signal.warning_breached,
            "hard_breached": signal.hard_breached,
            "hard_breach_streak": hard_streak,
            "last_warning_at": now.isoformat() if warning_sent else project_state.get("last_warning_at"),
            "last_enforced_at": last_enforced_at,
            "last_run_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
    )

    return {
        "status": "ok",
        "environment": runtime.environment,
        "estimated_mtd_usd": signal.estimated_mtd_usd,
        "forecast_month_end_usd": signal.forecast_month_end_usd,
        "signal_usd": signal.signal_usd,
        "warning_breached": signal.warning_breached,
        "hard_breached": signal.hard_breached,
        "hard_breach_streak": hard_streak,
        "culprit_resource_id": culprit.get("id") if culprit else None,
    }

