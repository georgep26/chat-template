"""Scheduled monitor Lambda for near-realtime cost guard decisions."""

from __future__ import annotations

from calendar import monthrange
import json
import logging
import os
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
ENFORCEABLE_ACTIONS = {
    "lambda_reserved_concurrency_zero",
    "attach_bedrock_deny_policy",
    "aurora_stop_cluster",
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


def _month_elapsed_fraction(now: datetime) -> float:
    start = month_start(now)
    elapsed_seconds = max((now - start).total_seconds(), 0.0)
    days_in_month = monthrange(now.year, now.month)[1]
    month_seconds = days_in_month * 86400.0
    if month_seconds <= 0:
        return 0.0
    return min(max(elapsed_seconds / month_seconds, 0.0), 1.0)


def _topic_name(identifier: str) -> str:
    if identifier.startswith("arn:aws:sns:"):
        return identifier.rsplit(":", maxsplit=1)[-1]
    return identifier


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
    )
    pricing["lambda_gb_second"] = as_float(
        pricing.get("lambda_gb_second"),
        LAMBDA_GB_SECOND_DEFAULT,
    )
    for key, value in list(pricing.items()):
        pricing[key] = as_float(value, 0.0) * multiplier
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
) -> tuple[list[dict[str, Any]], int]:
    if not resources:
        return [], 0

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

    return outputs, len(metric_queries)


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


def _collect_dynamodb_costs(
    cloudwatch_client: Any,
    resources: list[dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
    pricing: dict[str, float],
    state_store: StateStore,
    month_key: str,
    now: datetime,
) -> tuple[list[dict[str, Any]], int]:
    if not resources:
        return [], 0

    metric_queries: list[dict[str, Any]] = []
    query_map: dict[str, tuple[str, str, str]] = {}
    for idx, resource in enumerate(resources):
        rid = resource["id"]
        table_name = resource["identifier"]
        read_id = f"ddb_read_{idx}"
        write_id = f"ddb_write_{idx}"
        size_id = f"ddb_size_{idx}"
        metric_queries.extend(
            [
                {
                    "Id": read_id,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/DynamoDB",
                            "MetricName": "ConsumedReadCapacityUnits",
                            "Dimensions": [{"Name": "TableName", "Value": table_name}],
                        },
                        "Period": 300,
                        "Stat": "Sum",
                    },
                    "ReturnData": True,
                },
                {
                    "Id": write_id,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/DynamoDB",
                            "MetricName": "ConsumedWriteCapacityUnits",
                            "Dimensions": [{"Name": "TableName", "Value": table_name}],
                        },
                        "Period": 300,
                        "Stat": "Sum",
                    },
                    "ReturnData": True,
                },
                {
                    "Id": size_id,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/DynamoDB",
                            "MetricName": "TableSizeBytes",
                            "Dimensions": [{"Name": "TableName", "Value": table_name}],
                        },
                        "Period": 86400,
                        "Stat": "Average",
                    },
                    "ReturnData": True,
                },
            ]
        )
        query_map[read_id] = (rid, "read_units", "sum")
        query_map[write_id] = (rid, "write_units", "sum")
        query_map[size_id] = (rid, "storage_bytes", "last")

    result = cloudwatch_client.get_metric_data(
        MetricDataQueries=metric_queries,
        StartTime=start_time,
        EndTime=end_time,
        ScanBy="TimestampAscending",
    )

    totals: dict[str, dict[str, float]] = {
        resource["id"]: {"read_units": 0.0, "write_units": 0.0, "storage_bytes": 0.0}
        for resource in resources
    }
    for metric_result in result.get("MetricDataResults", []):
        query_id = metric_result.get("Id", "")
        mapped = query_map.get(query_id)
        if not mapped:
            continue
        resource_id, field, mode = mapped
        values = metric_result.get("Values", []) or []
        if not values:
            continue
        if mode == "last":
            totals[resource_id][field] = float(values[-1])
        else:
            totals[resource_id][field] = float(sum(values))

    elapsed_fraction = _month_elapsed_fraction(now)
    outputs: list[dict[str, Any]] = []
    for resource in resources:
        rid = resource["id"]
        table_name = resource["identifier"]
        read_units = totals[rid]["read_units"]
        write_units = totals[rid]["write_units"]
        storage_bytes = totals[rid]["storage_bytes"]
        request_cost = (read_units / 1_000_000.0) * as_float(
            pricing.get("dynamodb_read_request_unit_per_million"),
            0.125,
        ) + (write_units / 1_000_000.0) * as_float(
            pricing.get("dynamodb_write_request_unit_per_million"),
            0.625,
        )
        storage_gb = storage_bytes / float(1024**3)
        storage_cost = storage_gb * as_float(pricing.get("dynamodb_storage_gb_month"), 0.25) * elapsed_fraction
        estimated_mtd = request_cost + storage_cost

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
                "identifier": table_name,
                "enforcement": resource.get("enforcement"),
                "priority": as_int(resource.get("priority"), 999),
                "dynamodb_read_units_mtd": read_units,
                "dynamodb_write_units_mtd": write_units,
                "dynamodb_storage_bytes": storage_bytes,
                "interval_usd": interval_usd,
                "estimated_mtd_usd": estimated_mtd,
            }
        )
    return outputs, len(metric_queries)


def _cluster_to_instance_id(cluster_id: str) -> str:
    """Derive DB instance identifier from cluster identifier (light_db naming)."""
    if "-light-db-cluster-" in cluster_id:
        return cluster_id.replace("-light-db-cluster-", "-light-db-instance-")
    return cluster_id


def _collect_aurora_costs(
    cloudwatch_client: Any,
    resources: list[dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
    pricing: dict[str, float],
    state_store: StateStore,
    month_key: str,
) -> tuple[list[dict[str, Any]], int]:
    if not resources:
        return [], 0

    # Aurora Serverless v2: ServerlessDatabaseCapacity (ACU) with DBClusterIdentifier.
    # Fallback: CPUUtilization with DBInstanceIdentifier to approximate ACU-hours.
    period_seconds = 300
    period_hours = period_seconds / 3600.0
    acu_price = as_float(pricing.get("aurora_acu_hour_usd"), 0.12)

    metric_queries: list[dict[str, Any]] = []
    query_map: dict[str, tuple[str, str]] = {}  # query_id -> (resource_id, "acu" | "cpu")
    for idx, resource in enumerate(resources):
        rid = resource["id"]
        cluster_id = resource["identifier"]
        acu_id = f"aurora_acu_{idx}"
        metric_queries.append(
            {
                "Id": acu_id,
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/RDS",
                        "MetricName": "ServerlessDatabaseCapacity",
                        "Dimensions": [{"Name": "DBClusterIdentifier", "Value": cluster_id}],
                    },
                    "Period": period_seconds,
                    "Stat": "Average",
                },
                "ReturnData": True,
            }
        )
        query_map[acu_id] = (rid, "acu")

    result = cloudwatch_client.get_metric_data(
        MetricDataQueries=metric_queries,
        StartTime=start_time,
        EndTime=end_time,
        ScanBy="TimestampAscending",
    )

    # If ServerlessDatabaseCapacity has no data (e.g. cluster stopped or v1), try CPUUtilization fallback.
    rid_acu_hours: dict[str, float] = {r["id"]: 0.0 for r in resources}
    for metric_result in result.get("MetricDataResults", []):
        query_id = metric_result.get("Id", "")
        mapped = query_map.get(query_id)
        if not mapped:
            continue
        rid, kind = mapped
        values = metric_result.get("Values", []) or []
        if kind == "acu" and values:
            # ACU-hours = sum(ACU_avg_per_period) * period_hours
            rid_acu_hours[rid] = sum(values) * period_hours
            query_map.pop(query_id, None)

    # Fallback: resources with zero ACU hours may need CPUUtilization (e.g. cluster idled or metric missing).
    fallback_resources = [r for r in resources if rid_acu_hours[r["id"]] == 0.0]
    cpu_query_count = 0
    if fallback_resources:
        cpu_queries: list[dict[str, Any]] = []
        cpu_query_map: dict[str, str] = {}
        for idx, resource in enumerate(fallback_resources):
            rid = resource["id"]
            instance_id = _cluster_to_instance_id(resource["identifier"])
            max_acu = as_float(resource.get("max_acu"), 1.0)
            cpu_id = f"aurora_cpu_{idx}"
            cpu_queries.append(
                {
                    "Id": cpu_id,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/RDS",
                            "MetricName": "CPUUtilization",
                            "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": instance_id}],
                        },
                        "Period": period_seconds,
                        "Stat": "Average",
                    },
                    "ReturnData": True,
                }
            )
            cpu_query_map[cpu_id] = rid
        cpu_query_count = len(cpu_queries)
        if cpu_queries:
            fallback_result = cloudwatch_client.get_metric_data(
                MetricDataQueries=cpu_queries,
                StartTime=start_time,
                EndTime=end_time,
                ScanBy="TimestampAscending",
            )
            for metric_result in fallback_result.get("MetricDataResults", []):
                query_id = metric_result.get("Id", "")
                rid = cpu_query_map.get(query_id)
                if not rid:
                    continue
                values = metric_result.get("Values", []) or []
                if values:
                    resource = next((r for r in fallback_resources if r["id"] == rid), None)
                    max_acu = as_float(resource.get("max_acu"), 1.0) if resource else 1.0
                    # Approximate ACU-hours: (CPU% / 100) * max_acu * period_hours per datapoint
                    rid_acu_hours[rid] = sum(v / 100.0 * max_acu for v in values) * period_hours

    outputs: list[dict[str, Any]] = []
    for resource in resources:
        rid = resource["id"]
        cluster_id = resource["identifier"]
        acu_hours = rid_acu_hours.get(rid, 0.0)
        estimated_mtd = acu_hours * acu_price
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
                "identifier": cluster_id,
                "enforcement": resource.get("enforcement"),
                "priority": as_int(resource.get("priority"), 999),
                "aurora_acu_hours_mtd": acu_hours,
                "interval_usd": interval_usd,
                "estimated_mtd_usd": estimated_mtd,
            }
        )
    total_queries = len(metric_queries) + cpu_query_count
    return outputs, total_queries


def _collect_sns_costs(
    cloudwatch_client: Any,
    resources: list[dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
    pricing: dict[str, float],
    state_store: StateStore,
    month_key: str,
) -> tuple[list[dict[str, Any]], int]:
    if not resources:
        return [], 0

    metric_queries: list[dict[str, Any]] = []
    query_map: dict[str, str] = {}
    for idx, resource in enumerate(resources):
        rid = resource["id"]
        topic_name = _topic_name(resource["identifier"])
        pub_id = f"sns_pub_{idx}"
        metric_queries.append(
            {
                "Id": pub_id,
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/SNS",
                        "MetricName": "NumberOfMessagesPublished",
                        "Dimensions": [{"Name": "TopicName", "Value": topic_name}],
                    },
                    "Period": 300,
                    "Stat": "Sum",
                },
                "ReturnData": True,
            }
        )
        query_map[pub_id] = rid

    result = cloudwatch_client.get_metric_data(
        MetricDataQueries=metric_queries,
        StartTime=start_time,
        EndTime=end_time,
        ScanBy="TimestampAscending",
    )
    totals: dict[str, float] = {resource["id"]: 0.0 for resource in resources}
    for metric_result in result.get("MetricDataResults", []):
        query_id = metric_result.get("Id", "")
        rid = query_map.get(query_id)
        if not rid:
            continue
        totals[rid] = float(sum(metric_result.get("Values", []) or []))

    outputs: list[dict[str, Any]] = []
    request_price = as_float(pricing.get("sns_publish_request_per_million"), 0.50)
    for resource in resources:
        rid = resource["id"]
        topic_identifier = resource["identifier"]
        published = totals[rid]
        estimated_mtd = (published / 1_000_000.0) * request_price
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
                "identifier": topic_identifier,
                "enforcement": resource.get("enforcement"),
                "priority": as_int(resource.get("priority"), 999),
                "sns_messages_published_mtd": published,
                "interval_usd": interval_usd,
                "estimated_mtd_usd": estimated_mtd,
            }
        )
    return outputs, len(metric_queries)


def _collect_cloudwatch_logs_costs(
    cloudwatch_client: Any,
    resources: list[dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
    pricing: dict[str, float],
    state_store: StateStore,
    month_key: str,
) -> tuple[list[dict[str, Any]], int]:
    if not resources:
        return [], 0

    metric_queries: list[dict[str, Any]] = []
    query_map: dict[str, str] = {}
    for idx, resource in enumerate(resources):
        rid = resource["id"]
        log_group_name = resource["identifier"]
        incoming_id = f"logs_ing_{idx}"
        metric_queries.append(
            {
                "Id": incoming_id,
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/Logs",
                        "MetricName": "IncomingBytes",
                        "Dimensions": [{"Name": "LogGroupName", "Value": log_group_name}],
                    },
                    "Period": 300,
                    "Stat": "Sum",
                },
                "ReturnData": True,
            }
        )
        query_map[incoming_id] = rid

    result = cloudwatch_client.get_metric_data(
        MetricDataQueries=metric_queries,
        StartTime=start_time,
        EndTime=end_time,
        ScanBy="TimestampAscending",
    )
    totals: dict[str, float] = {resource["id"]: 0.0 for resource in resources}
    for metric_result in result.get("MetricDataResults", []):
        query_id = metric_result.get("Id", "")
        rid = query_map.get(query_id)
        if not rid:
            continue
        totals[rid] = float(sum(metric_result.get("Values", []) or []))

    ingestion_price = as_float(pricing.get("cloudwatch_logs_ingestion_gb"), 0.50)
    outputs: list[dict[str, Any]] = []
    for resource in resources:
        rid = resource["id"]
        log_group_name = resource["identifier"]
        incoming_bytes = totals[rid]
        incoming_gb = incoming_bytes / float(1024**3)
        estimated_mtd = incoming_gb * ingestion_price
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
                "identifier": log_group_name,
                "enforcement": resource.get("enforcement"),
                "priority": as_int(resource.get("priority"), 999),
                "cloudwatch_logs_incoming_bytes_mtd": incoming_bytes,
                "interval_usd": interval_usd,
                "estimated_mtd_usd": estimated_mtd,
            }
        )
    return outputs, len(metric_queries)


def _collect_s3_config_bucket_costs(
    cloudwatch_client: Any,
    bucket_name: str,
    start_time: datetime,
    end_time: datetime,
    now: datetime,
    config: dict[str, Any],
    pricing: dict[str, float],
    state_store: StateStore,
    month_key: str,
) -> tuple[list[dict[str, Any]], int]:
    """Estimate S3 cost for the cost guard config bucket (GET requests + storage)."""
    if not bucket_name:
        return [], 0

    interval_minutes = as_int(config.get("monitor", {}).get("interval_minutes"), 5)
    elapsed_fraction = _month_elapsed_fraction(now)
    days_in_month = monthrange(now.year, now.month)[1]
    day_of_month = now.day
    days_elapsed = min(day_of_month, days_in_month)

    metric_queries: list[dict[str, Any]] = [
        {
            "Id": "s3_get",
            "MetricStat": {
                "Metric": {
                    "Namespace": "AWS/S3",
                    "MetricName": "GetRequests",
                    "Dimensions": [
                        {"Name": "BucketName", "Value": bucket_name},
                        {"Name": "FilterId", "Value": "EntireBucket"},
                    ],
                },
                "Period": 300,
                "Stat": "Sum",
            },
            "ReturnData": True,
        },
        {
            "Id": "s3_size",
            "MetricStat": {
                "Metric": {
                    "Namespace": "AWS/S3",
                    "MetricName": "BucketSizeBytes",
                    "Dimensions": [
                        {"Name": "BucketName", "Value": bucket_name},
                        {"Name": "StorageType", "Value": "StandardStorage"},
                    ],
                },
                "Period": 86400,
                "Stat": "Average",
            },
            "ReturnData": True,
        },
    ]

    get_requests_mtd = 0.0
    storage_bytes = 0.0
    try:
        result = cloudwatch_client.get_metric_data(
            MetricDataQueries=metric_queries,
            StartTime=start_time,
            EndTime=end_time,
            ScanBy="TimestampAscending",
        )
        for metric_result in result.get("MetricDataResults", []):
            values = metric_result.get("Values", []) or []
            if metric_result.get("Id") == "s3_get" and values:
                get_requests_mtd = float(sum(values))
            elif metric_result.get("Id") == "s3_size" and values:
                storage_bytes = float(values[-1])
    except Exception as exc:  # pragma: no cover - AWS/network
        LOGGER.warning("S3 metrics unavailable for bucket %s: %s", bucket_name, exc)

    if get_requests_mtd <= 0:
        invocations_per_day = (24 * 60 / max(interval_minutes, 1)) + 1
        get_requests_mtd = invocations_per_day * days_elapsed

    request_price = as_float(pricing.get("s3_get_request_per_million"), 0.40)
    storage_price = as_float(pricing.get("s3_storage_gb_month"), 0.023)
    request_cost = (get_requests_mtd / 1_000_000.0) * request_price
    storage_gb = storage_bytes / float(1024**3)
    storage_cost = storage_gb * storage_price * elapsed_fraction
    estimated_mtd_usd = request_cost + storage_cost

    resource_id = "cost_guard_config_bucket"
    previous_state = state_store.get_resource_state(resource_id)
    previous_month = previous_state.get("month_key")
    previous_mtd = as_float(previous_state.get("estimated_mtd_usd"), 0.0)
    if previous_month != month_key:
        previous_mtd = 0.0
    interval_usd = max(estimated_mtd_usd - previous_mtd, 0.0)

    output: list[dict[str, Any]] = [
        {
            "id": resource_id,
            "type": "s3_bucket",
            "identifier": bucket_name,
            "enforcement": "none",
            "priority": 500,
            "s3_get_requests_mtd": get_requests_mtd,
            "s3_storage_bytes": storage_bytes,
            "interval_usd": interval_usd,
            "estimated_mtd_usd": estimated_mtd_usd,
        }
    ]
    return output, len(metric_queries)


def _collect_cloudwatch_control_costs(
    resources: list[dict[str, Any]],
    metric_query_count: int,
    pricing: dict[str, float],
    state_store: StateStore,
    month_key: str,
) -> list[dict[str, Any]]:
    if not resources:
        return []

    total_interval = (float(metric_query_count) / 1000.0) * as_float(
        pricing.get("cloudwatch_metric_per_1000"),
        0.01,
    )
    per_resource_interval = total_interval / max(len(resources), 1)
    per_resource_query_count = float(metric_query_count) / max(len(resources), 1)
    outputs: list[dict[str, Any]] = []
    for resource in resources:
        rid = resource["id"]
        previous_state = state_store.get_resource_state(rid)
        previous_month = previous_state.get("month_key")
        previous_mtd = as_float(previous_state.get("estimated_mtd_usd"), 0.0)
        if previous_month != month_key:
            previous_mtd = 0.0
        estimated_mtd = previous_mtd + per_resource_interval
        outputs.append(
            {
                "id": rid,
                "type": resource.get("type"),
                "identifier": resource.get("identifier"),
                "enforcement": resource.get("enforcement"),
                "priority": as_int(resource.get("priority"), 999),
                "cloudwatch_metric_queries_interval": per_resource_query_count,
                "interval_usd": per_resource_interval,
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


def _enforceable_resources(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        resource
        for resource in resources
        if resource.get("enforcement") in ENFORCEABLE_ACTIONS
    ]


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
    dynamodb_resources = [resource for resource in resources if resource.get("type") == "dynamodb_table"]
    sns_resources = [resource for resource in resources if resource.get("type") == "sns_topic"]
    cloudwatch_log_resources = [
        resource for resource in resources if resource.get("type") == "cloudwatch_log_group"
    ]
    cloudwatch_control_resources = [
        resource for resource in resources if resource.get("type") == "cloudwatch_control"
    ]
    aurora_resources = [resource for resource in resources if resource.get("type") == "aurora_cluster"]

    lambda_costs, lambda_metric_query_count = _collect_lambda_costs(
        cloudwatch_client=cw,
        lambda_client=lambda_client,
        resources=lambda_resources,
        start_time=month_start(now),
        end_time=now,
        pricing=pricing,
        state_store=state_store,
        month_key=current_month_key,
    )
    ddb_costs, ddb_metric_query_count = _collect_dynamodb_costs(
        cloudwatch_client=cw,
        resources=dynamodb_resources,
        start_time=month_start(now),
        end_time=now,
        pricing=pricing,
        state_store=state_store,
        month_key=current_month_key,
        now=now,
    )
    aurora_costs, aurora_metric_query_count = _collect_aurora_costs(
        cloudwatch_client=cw,
        resources=aurora_resources,
        start_time=month_start(now),
        end_time=now,
        pricing=pricing,
        state_store=state_store,
        month_key=current_month_key,
    )
    sns_costs, sns_metric_query_count = _collect_sns_costs(
        cloudwatch_client=cw,
        resources=sns_resources,
        start_time=month_start(now),
        end_time=now,
        pricing=pricing,
        state_store=state_store,
        month_key=current_month_key,
    )
    cloudwatch_logs_costs, logs_metric_query_count = _collect_cloudwatch_logs_costs(
        cloudwatch_client=cw,
        resources=cloudwatch_log_resources,
        start_time=month_start(now),
        end_time=now,
        pricing=pricing,
        state_store=state_store,
        month_key=current_month_key,
    )
    config_bucket = os.getenv("COST_GUARD_CONFIG_S3_BUCKET", "").strip()
    s3_costs, s3_metric_query_count = _collect_s3_config_bucket_costs(
        cloudwatch_client=cw,
        bucket_name=config_bucket,
        start_time=month_start(now),
        end_time=now,
        now=now,
        config=config,
        pricing=pricing,
        state_store=state_store,
        month_key=current_month_key,
    )
    cloudwatch_control_costs = _collect_cloudwatch_control_costs(
        resources=cloudwatch_control_resources,
        metric_query_count=(
            lambda_metric_query_count
            + ddb_metric_query_count
            + aurora_metric_query_count
            + sns_metric_query_count
            + logs_metric_query_count
            + s3_metric_query_count
        ),
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

    all_resources = (
        lambda_costs
        + bedrock_costs
        + ddb_costs
        + aurora_costs
        + sns_costs
        + cloudwatch_logs_costs
        + s3_costs
        + cloudwatch_control_costs
    )
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
    enforceable_culprit = choose_culprit(_enforceable_resources(all_resources))

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
        and enforceable_culprit
    ):
        action_payload = {
            "event_type": "enforce",
            "environment": runtime.environment,
            "project": runtime.project_name,
            "resource": enforceable_culprit,
            "reason": {
                "signal_usd": signal.signal_usd,
                "estimated_mtd_usd": signal.estimated_mtd_usd,
                "forecast_month_end_usd": signal.forecast_month_end_usd,
                "hard_threshold_usd": signal.hard_threshold_usd,
                "hard_breach_streak": hard_streak,
                "top_interval_resource_id": culprit.get("id") if culprit else None,
            },
            "triggered_at": now.isoformat(),
        }
        _invoke_enforcer(runtime.enforcer_function_name, action_payload)
        state_store.record_event(
            severity="critical",
            resource_id=enforceable_culprit["id"],
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
        metadata = {
            key: value
            for key, value in item.items()
            if key
            not in {
                "id",
                "type",
                "identifier",
                "enforcement",
                "priority",
                "interval_usd",
                "estimated_mtd_usd",
            }
        }

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
                "metadata": metadata,
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
