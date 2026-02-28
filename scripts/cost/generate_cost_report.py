#!/usr/bin/env python3
"""Generate cost guard JSON, HTML, and PNG reports under docs/cost_reports."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate cost guard reports")
    parser.add_argument("--env", required=True, choices=["dev", "staging", "prod"], help="Target environment")
    parser.add_argument("--date", default=datetime.now(tz=UTC).strftime("%Y-%m-%d"), help="Report date key (YYYY-MM-DD)")
    parser.add_argument("--out", default="docs/cost_reports", help="Output directory for reports")
    parser.add_argument("--infra", default="infra/infra.yaml", help="Path to infra.yaml")
    parser.add_argument("--table-name", default="", help="Optional DynamoDB table name override")
    return parser.parse_args()


def _substitute(value: Any, project: str, env: str) -> Any:
    if isinstance(value, str):
        return value.replace("{project}", project).replace("{env}", env)
    if isinstance(value, list):
        return [_substitute(item, project, env) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, project, env) for key, item in value.items()}
    return value


def _load_infra(path: Path, env: str) -> dict[str, Any]:
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "PyYAML is required. Install dependencies (e.g. `pip install -r requirements.txt`)."
        ) from exc
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    project_name = payload.get("project", {}).get("name", "chat-template")
    cost_control = _substitute(payload.get("cost_control", {}) or {}, project_name, env)
    return {"project_name": project_name, "cost_control": cost_control}


def _normalize_resource(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": resource.get("resource_id") or resource.get("id"),
        "type": resource.get("type", ""),
        "identifier": resource.get("identifier", ""),
        "estimated_mtd_usd": float(resource.get("estimated_mtd_usd", 0.0)),
        "interval_usd": float(resource.get("interval_usd", 0.0)),
        "forecast_month_end_usd": float(resource.get("forecast_month_end_usd", 0.0)),
        "breach_state": resource.get("breach_state", "none"),
        "last_action": resource.get("last_action", "none"),
        "last_action_at": resource.get("last_action_at"),
    }


def main() -> None:
    args = _parse_args()
    try:
        from cost_guard.reporting import write_report_bundle
        from cost_guard.state_store import StateStore
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "Missing runtime dependency. Install project dependencies "
            "(e.g. `pip install -r requirements.txt`)."
        ) from exc
    infra = _load_infra(Path(args.infra), args.env)
    project_name = infra["project_name"]
    cost_control = infra["cost_control"]

    table_name = args.table_name or f"{project_name}-cost-guard-{args.env}"
    state_store = StateStore(table_name=table_name, environment=args.env)
    project_state = state_store.get_project_state()
    pricing_state = state_store.get_pricing_state()
    resources = [_normalize_resource(item) for item in state_store.list_resource_states()]
    events = state_store.list_events_for_date(args.date)

    budget_limit = float(
        cost_control.get("budget", {}).get(
            "amount_usd",
            project_state.get("budget_amount_usd", 20.0),
        )
    )
    warning_percent = float(
        cost_control.get("budget", {}).get(
            "warning_percent",
            project_state.get("warning_percent", 80.0),
        )
    )
    hard_percent = float(
        cost_control.get("budget", {}).get(
            "hard_percent",
            project_state.get("hard_percent", 100.0),
        )
    )
    estimated_mtd = float(project_state.get("estimated_mtd_usd", 0.0))
    forecast_month_end = float(project_state.get("forecast_month_end_usd", 0.0))

    summary = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "environment": args.env,
        "signal_usd": float(project_state.get("signal_usd", max(estimated_mtd, forecast_month_end))),
        "budget": {
            "limit_usd": budget_limit,
            "warning_percent": warning_percent,
            "hard_percent": hard_percent,
            "estimated_mtd_usd": estimated_mtd,
            "forecast_month_end_usd": forecast_month_end,
        },
        "pricing_state_updated_at": pricing_state.get("updated_at"),
        "pricing_metadata": pricing_state.get("pricing_metadata", {}),
        "resources": resources,
        "events": [
            {
                "event_id": event.get("event_id"),
                "timestamp": event.get("timestamp"),
                "severity": event.get("severity"),
                "resource_id": event.get("resource_id"),
                "message": event.get("message"),
            }
            for event in events
        ],
    }

    reports_cfg = cost_control.get("reports", {}) or {}
    formats = reports_cfg.get("formats", ["json", "html", "png"])
    retention_days = int(reports_cfg.get("retention_days", 90))
    output_dir = Path(args.out)

    write_report_bundle(
        base_dir=output_dir,
        day_key=args.date,
        summary=summary,
        resources=resources,
        events=summary["events"],
        retention_days=retention_days,
        formats=formats,
    )

    print(
        json.dumps(
            {
                "status": "ok",
                "environment": args.env,
                "date": args.date,
                "output_dir": str(output_dir),
                "resources": len(resources),
                "events": len(summary["events"]),
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
