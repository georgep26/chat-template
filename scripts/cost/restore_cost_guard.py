#!/usr/bin/env python3
"""Restore a previously enforced cost-guard action for a resource."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Restore cost guard enforcement for a resource")
    parser.add_argument("--env", required=True, choices=["dev", "staging", "prod"], help="Target environment")
    parser.add_argument("--resource-id", required=True, help="Resource ID from cost_control.resources")
    parser.add_argument("--infra", default="infra/infra.yaml", help="Path to infra config")
    parser.add_argument("--table-name", default="", help="Optional table override")
    return parser.parse_args()


def _load_project_name(infra_path: Path) -> str:
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "PyYAML is required. Install dependencies (e.g. `pip install -r requirements.txt`)."
        ) from exc
    payload: dict[str, Any] = yaml.safe_load(infra_path.read_text(encoding="utf-8")) or {}
    return payload.get("project", {}).get("name", "chat-template")


def main() -> None:
    args = _args()
    try:
        from cost_guard.restore import restore_resource
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "Missing runtime dependency. Install project dependencies "
            "(e.g. `pip install -r requirements.txt`)."
        ) from exc
    project_name = _load_project_name(Path(args.infra))
    table_name = args.table_name or f"{project_name}-cost-guard-{args.env}"
    result = restore_resource(
        table_name=table_name,
        environment=args.env,
        resource_id=args.resource_id,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
