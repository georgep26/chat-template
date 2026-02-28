"""Restore utilities for reverting cost guard enforcement actions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import boto3

from cost_guard.enforcer_handler import BEDROCK_DENY_POLICY_NAME
from cost_guard.state_store import StateStore


def _restore_lambda(resource_state: dict[str, Any]) -> dict[str, Any]:
    lambda_client = boto3.client("lambda")
    identifier = resource_state.get("identifier")
    restore_details = resource_state.get("restore_details", {}) or {}
    previous_concurrency = restore_details.get("previous_concurrency")
    disabled_event_sources = restore_details.get("disabled_event_source_mappings", []) or []

    if previous_concurrency is None:
        lambda_client.delete_function_concurrency(FunctionName=identifier)
    else:
        lambda_client.put_function_concurrency(
            FunctionName=identifier,
            ReservedConcurrentExecutions=int(previous_concurrency),
        )

    reenabled = []
    for uuid in disabled_event_sources:
        try:
            lambda_client.update_event_source_mapping(UUID=uuid, Enabled=True)
            reenabled.append(uuid)
        except Exception:
            # Missing mapping should not block restoration of the remaining state.
            continue

    return {
        "restored_concurrency": previous_concurrency,
        "reenabled_event_source_mappings": reenabled,
    }


def _restore_role(resource_state: dict[str, Any]) -> dict[str, Any]:
    iam = boto3.client("iam")
    restore_details = resource_state.get("restore_details", {}) or {}
    role_name = restore_details.get("role_name") or resource_state.get("identifier")
    policy_name = restore_details.get("policy_name") or BEDROCK_DENY_POLICY_NAME
    iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
    return {"restored_role": role_name, "deleted_policy_name": policy_name}


def _restore_aurora(resource_state: dict[str, Any]) -> dict[str, Any]:
    rds_client = boto3.client("rds")
    restore_details = resource_state.get("restore_details", {}) or {}
    cluster_id = restore_details.get("cluster_id") or resource_state.get("identifier")
    rds_client.start_db_cluster(DBClusterIdentifier=cluster_id)
    return {"cluster_id": cluster_id}


def restore_resource(
    table_name: str,
    environment: str,
    resource_id: str,
) -> dict[str, Any]:
    store = StateStore(table_name=table_name, environment=environment)
    resource_state = store.get_resource_state(resource_id)
    if not resource_state:
        raise ValueError(f"Resource state not found for {resource_id}")

    enforcement = resource_state.get("enforcement")
    if enforcement == "lambda_reserved_concurrency_zero":
        details = _restore_lambda(resource_state)
    elif enforcement == "attach_bedrock_deny_policy":
        details = _restore_role(resource_state)
    elif enforcement == "aurora_stop_cluster":
        details = _restore_aurora(resource_state)
    else:
        raise ValueError(f"Unsupported enforcement action: {enforcement}")

    now = datetime.now(tz=UTC).isoformat()
    updated_state = {
        **resource_state,
        "last_action": "restored",
        "last_action_at": now,
        "action_active": False,
        "restore_result": details,
        "updated_at": now,
    }
    store.put_resource_state(resource_id, updated_state)
    store.record_event(
        severity="warning",
        resource_id=resource_id,
        message=f"Restoration completed for {resource_id}",
        details=details,
    )

    return {
        "status": "restored",
        "resource_id": resource_id,
        "enforcement": enforcement,
        "details": details,
        "timestamp": now,
    }

