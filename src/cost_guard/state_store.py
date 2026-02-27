"""DynamoDB-backed state and event persistence for cost guard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Key


def _to_decimal(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_decimal(v) for v in value]
    return value


def _from_decimal(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _from_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_decimal(v) for v in value]
    return value


@dataclass
class StateStore:
    table_name: str
    environment: str

    def __post_init__(self) -> None:
        dynamodb = boto3.resource("dynamodb")
        self.table = dynamodb.Table(self.table_name)
        self.env_pk = f"ENV#{self.environment}"

    def _get(self, sk: str) -> dict[str, Any]:
        resp = self.table.get_item(Key={"pk": self.env_pk, "sk": sk})
        item = resp.get("Item")
        if not item:
            return {}
        return _from_decimal(item)

    def _put(self, sk: str, payload: dict[str, Any]) -> None:
        item = {"pk": self.env_pk, "sk": sk}
        item.update(payload)
        self.table.put_item(Item=_to_decimal(item))

    def get_project_state(self) -> dict[str, Any]:
        return self._get("STATE#PROJECT")

    def put_project_state(self, payload: dict[str, Any]) -> None:
        self._put("STATE#PROJECT", payload)

    def get_resource_state(self, resource_id: str) -> dict[str, Any]:
        return self._get(f"STATE#RESOURCE#{resource_id}")

    def put_resource_state(self, resource_id: str, payload: dict[str, Any]) -> None:
        self._put(f"STATE#RESOURCE#{resource_id}", payload)

    def list_resource_states(self) -> list[dict[str, Any]]:
        resp = self.table.query(
            KeyConditionExpression=Key("pk").eq(self.env_pk) & Key("sk").begins_with("STATE#RESOURCE#")
        )
        return [_from_decimal(item) for item in resp.get("Items", [])]

    def put_pricing_state(self, payload: dict[str, Any]) -> None:
        self._put("STATE#PRICING", payload)

    def get_pricing_state(self) -> dict[str, Any]:
        return self._get("STATE#PRICING")

    def put_reconciliation(self, date_key: str, payload: dict[str, Any]) -> None:
        self._put(f"RECONCILIATION#{date_key}", payload)

    def list_reconciliations(self, limit: int = 30) -> list[dict[str, Any]]:
        resp = self.table.query(
            KeyConditionExpression=Key("pk").eq(self.env_pk) & Key("sk").begins_with("RECONCILIATION#"),
            ScanIndexForward=False,
            Limit=limit,
        )
        return [_from_decimal(item) for item in resp.get("Items", [])]

    def record_event(
        self,
        severity: str,
        resource_id: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        event_id = str(uuid4())
        timestamp = now.isoformat()
        day = now.strftime("%Y-%m-%d")
        event = {
            "event_id": event_id,
            "timestamp": timestamp,
            "severity": severity,
            "resource_id": resource_id,
            "message": message,
            "details": details or {},
        }
        sk = f"EVENT#{day}#{timestamp}#{event_id}"
        self._put(sk, event)
        return event

    def list_events_for_date(self, date_key: str) -> list[dict[str, Any]]:
        prefix = f"EVENT#{date_key}#"
        resp = self.table.query(
            KeyConditionExpression=Key("pk").eq(self.env_pk) & Key("sk").begins_with(prefix),
            ScanIndexForward=True,
        )
        return [_from_decimal(item) for item in resp.get("Items", [])]

