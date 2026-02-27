"""Lambda enforcer for automatic cost guard actions."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import boto3

from cost_guard.config import load_runtime_context
from cost_guard.state_store import StateStore

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

BEDROCK_DENY_POLICY_NAME = "CostGuardBedrockDenyPolicy"


def _publish_critical(topic_arn: str, payload: dict[str, Any]) -> None:
    if not topic_arn:
        return
    boto3.client("sns").publish(
        TopicArn=topic_arn,
        Subject="Cost Guard Critical",
        Message=json.dumps(payload, separators=(",", ":"), default=str),
    )


def _role_name(identifier: str) -> str:
    if identifier.startswith("arn:aws:iam::") and ":role/" in identifier:
        return identifier.split(":role/", 1)[1]
    return identifier


def _build_bedrock_deny_policy(blocked_model_ids: list[str]) -> dict[str, Any]:
    statement: dict[str, Any] = {
        "Sid": "CostGuardDenyBedrockInvoke",
        "Effect": "Deny",
        "Action": [
            "bedrock:InvokeModel",
            "bedrock:InvokeModelWithResponseStream",
            "bedrock:Converse",
            "bedrock:ConverseStream",
        ],
        "Resource": "*",
    }
    if blocked_model_ids:
        statement["Condition"] = {
            "ForAnyValue:StringLike": {"bedrock:ModelId": blocked_model_ids}
        }
    return {"Version": "2012-10-17", "Statement": [statement]}


def _disable_event_sources(lambda_client: Any, function_name: str) -> list[str]:
    disabled_uuids: list[str] = []
    paginator = lambda_client.get_paginator("list_event_source_mappings")
    for page in paginator.paginate(FunctionName=function_name):
        for mapping in page.get("EventSourceMappings", []):
            uuid = mapping.get("UUID")
            state = mapping.get("State", "")
            if not uuid:
                continue
            if state in {"Disabled", "Disabling"}:
                continue
            lambda_client.update_event_source_mapping(UUID=uuid, Enabled=False)
            disabled_uuids.append(uuid)
    return disabled_uuids


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    _ = context
    runtime = load_runtime_context()
    config = runtime.config
    state_store = StateStore(table_name=runtime.table_name, environment=runtime.environment)

    resource = event.get("resource") or {}
    resource_id = resource.get("id")
    if not resource_id:
        return {"status": "ignored", "reason": "missing_resource_id"}

    allowed_resources = {
        item.get("id"): item
        for item in config.get("resources", [])
        if item.get("enabled", True)
    }
    if resource_id not in allowed_resources:
        return {"status": "ignored", "reason": "resource_not_allowlisted", "resource_id": resource_id}

    resource_cfg = allowed_resources[resource_id]
    identifier = resource_cfg.get("identifier", resource.get("identifier"))
    enforcement = resource_cfg.get("enforcement", resource.get("enforcement"))
    now = datetime.now(tz=UTC).isoformat()

    existing_state = state_store.get_resource_state(resource_id)
    if existing_state.get("action_active") and existing_state.get("last_action") == "enforced":
        return {"status": "noop", "resource_id": resource_id, "reason": "already_enforced"}

    details: dict[str, Any] = {
        "resource_id": resource_id,
        "identifier": identifier,
        "enforcement": enforcement,
        "trigger_event": event,
    }

    if enforcement == "lambda_reserved_concurrency_zero":
        lambda_client = boto3.client("lambda")
        previous_concurrency: int | None = None
        try:
            concurrency = lambda_client.get_function_concurrency(FunctionName=identifier)
            previous_concurrency = concurrency.get("ReservedConcurrentExecutions")
        except lambda_client.exceptions.ResourceNotFoundException:  # pragma: no cover - AWS behavior
            previous_concurrency = None

        lambda_client.put_function_concurrency(
            FunctionName=identifier,
            ReservedConcurrentExecutions=0,
        )
        disabled_mappings = []
        if resource_cfg.get("disable_event_sources", True):
            disabled_mappings = _disable_event_sources(lambda_client, identifier)

        details.update(
            {
                "previous_concurrency": previous_concurrency,
                "disabled_event_source_mappings": disabled_mappings,
            }
        )
    elif enforcement == "attach_bedrock_deny_policy":
        iam_client = boto3.client("iam")
        blocked = (config.get("bedrock", {}) or {}).get("blocked_model_ids", []) or []
        policy_doc = _build_bedrock_deny_policy(blocked_model_ids=blocked)
        role_name = _role_name(identifier)
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName=BEDROCK_DENY_POLICY_NAME,
            PolicyDocument=json.dumps(policy_doc),
        )
        details.update(
            {
                "role_name": role_name,
                "policy_name": BEDROCK_DENY_POLICY_NAME,
                "blocked_model_ids": blocked,
            }
        )
    else:
        return {"status": "ignored", "resource_id": resource_id, "reason": "unknown_enforcement"}

    state_store.put_resource_state(
        resource_id,
        {
            **existing_state,
            "resource_id": resource_id,
            "type": resource_cfg.get("type"),
            "identifier": identifier,
            "enforcement": enforcement,
            "last_action": "enforced",
            "last_action_at": now,
            "action_active": True,
            "restore_details": details,
            "updated_at": now,
        },
    )
    state_store.record_event(
        severity="critical",
        resource_id=resource_id,
        message=f"Enforcement action applied: {enforcement}",
        details=details,
    )
    _publish_critical(
        runtime.critical_topic_arn,
        {
            "event": "cost_guard_enforcement_applied",
            "environment": runtime.environment,
            "resource_id": resource_id,
            "enforcement": enforcement,
            "details": details,
            "timestamp": now,
        },
    )
    return {
        "status": "enforced",
        "resource_id": resource_id,
        "enforcement": enforcement,
        "timestamp": now,
    }

