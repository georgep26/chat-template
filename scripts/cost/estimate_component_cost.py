#!/usr/bin/env python3
"""Estimate component costs from CloudFormation templates and scenario inputs."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_RATES = {
    "lambda_request_per_million": 0.20,
    "lambda_gb_second": 0.0000166667,
    "nat_gateway_hourly": 0.045,
    "nat_gateway_data_gb": 0.045,
    "interface_vpc_endpoint_hourly": 0.01,
    "interface_vpc_endpoint_data_gb": 0.01,
    "aurora_serverless_acu_hour": 0.12,
    "cloudwatch_logs_ingest_gb": 0.50,
}

# Match AWS Cost Calculator: 730 hours in a month / 24 hours per day
DAYS_PER_MONTH = 730.0 / 24.0  # ~30.4167

DEFAULT_SCENARIO = {
    "runs_per_day": 1,
    "lambda_invocations_per_run": 1000,
    "lambda_avg_duration_ms": 500,
    "lambda_memory_mb": 1024,
    "nat_gateway_data_gb_per_day": 0,
    "vpc_endpoint_data_gb_per_day": 0,
    "aurora_acu_hours_per_day": 1.0,
    "cloudwatch_logs_gb_per_day": 0.1,
}


@dataclass
class CostEstimate:
    component: str
    resource_type: str
    per_run_usd: float
    daily_usd: float
    monthly_usd: float
    notes: str
    assumptions: dict[str, Any]
    calculation_summary: list[str]


def _cf_constructor(loader: Any, node: Any) -> Any:
    """Parse CloudFormation intrinsic tags (!Ref, !Not, !Equals, etc.) so YAML load succeeds."""
    import yaml
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "PyYAML is required. Install dependencies (e.g. `pip install -r requirements.txt`)."
        ) from exc
    # CloudFormation templates use intrinsic tags (!Ref, !Not, !Equals, etc.); SafeLoader rejects them.
    loader = yaml.SafeLoader
    for tag in (
        "!Ref", "!Sub", "!GetAtt", "!Not", "!Equals", "!And", "!Or", "!If",
        "!FindInMap", "!Select", "!Split", "!Join", "!ImportValue", "!Condition",
    ):
        loader.add_constructor(tag, _cf_constructor)
    return yaml.load(path.read_text(encoding="utf-8"), Loader=loader) or {}


def _resolve_ref(value: Any, parameter_defaults: dict[str, Any]) -> Any:
    if isinstance(value, dict) and "Ref" in value:
        return parameter_defaults.get(value["Ref"], value)
    return value


def _build_parameter_defaults(template: dict[str, Any]) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for param_name, param in (template.get("Parameters", {}) or {}).items():
        defaults[param_name] = param.get("Default")
    return defaults


def _load_scenario(path: Path | None) -> dict[str, Any]:
    scenario = dict(DEFAULT_SCENARIO)
    if not path:
        return scenario
    loaded = json.loads(path.read_text(encoding="utf-8"))
    scenario.update(loaded)
    return scenario


def _estimate_lambda(
    name: str,
    props: dict[str, Any],
    parameter_defaults: dict[str, Any],
    scenario: dict[str, Any],
    rates: dict[str, float],
) -> CostEstimate:
    memory_mb = _resolve_ref(props.get("MemorySize"), parameter_defaults)
    if isinstance(memory_mb, (int, float)):
        resolved_memory_mb = float(memory_mb)
    else:
        resolved_memory_mb = float(scenario["lambda_memory_mb"])

    invocations_per_run = float(scenario["lambda_invocations_per_run"])
    avg_duration_ms = float(scenario["lambda_avg_duration_ms"])
    runs_per_day = float(scenario["runs_per_day"])

    req_per_m = rates["lambda_request_per_million"]
    gb_s = rates["lambda_gb_second"]
    memory_gb = resolved_memory_mb / 1024.0
    duration_s = avg_duration_ms / 1000.0

    single_invocation_request_usd = (1.0 / 1_000_000.0) * req_per_m
    single_invocation_compute_usd = duration_s * memory_gb * gb_s
    single_invocation_usd = single_invocation_request_usd + single_invocation_compute_usd

    request_cost_per_run = (invocations_per_run / 1_000_000.0) * req_per_m
    compute_cost_per_run = (
        invocations_per_run * duration_s * memory_gb * gb_s
    )
    per_run = request_cost_per_run + compute_cost_per_run
    daily = per_run * runs_per_day
    monthly = daily * DAYS_PER_MONTH

    assumptions = {
        "lambda_request_per_million_usd": req_per_m,
        "lambda_gb_second_usd": gb_s,
        "single_invocation_usd": round(single_invocation_usd, 8),
        "single_invocation_breakdown": {
            "request_usd": round(single_invocation_request_usd, 8),
            "compute_usd": round(single_invocation_compute_usd, 8),
        },
        "unit_description": f"One invocation at {resolved_memory_mb} MB, {avg_duration_ms} ms",
    }
    calculation_summary = [
        f"Request cost per run = {invocations_per_run:,.0f} invocations × (${req_per_m}/1M) = ${request_cost_per_run:.6f}",
        f"Compute cost per run = {invocations_per_run:,.0f} × ({duration_s}s × {memory_gb:.4f} GB) × ${gb_s:.8f}/GB-s = ${compute_cost_per_run:.6f}",
        f"Per run = request + compute = ${per_run:.6f}",
        f"Daily = per_run × runs_per_day = ${per_run:.6f} × {runs_per_day:,.0f} = ${daily:.6f}",
        f"Monthly = daily × {DAYS_PER_MONTH:.4f} (730 hrs/24, AWS convention) = ${monthly:.6f}",
    ]
    return CostEstimate(
        component=name,
        resource_type="AWS::Lambda::Function",
        per_run_usd=per_run,
        daily_usd=daily,
        monthly_usd=monthly,
        notes=f"memory_mb={resolved_memory_mb}, invocations_per_run={invocations_per_run}, avg_duration_ms={avg_duration_ms}",
        assumptions=assumptions,
        calculation_summary=calculation_summary,
    )


def _estimate_nat_gateway(name: str, scenario: dict[str, Any], rates: dict[str, float]) -> CostEstimate:
    data_gb_day = float(scenario["nat_gateway_data_gb_per_day"])
    hourly = rates["nat_gateway_hourly"]
    data_gb_rate = rates["nat_gateway_data_gb"]
    daily = (24.0 * hourly) + (data_gb_day * data_gb_rate)
    monthly = daily * DAYS_PER_MONTH
    assumptions = {
        "nat_gateway_hourly_usd": hourly,
        "nat_gateway_data_gb_usd": data_gb_rate,
        "unit_description": "Per hour + per GB processed",
    }
    calculation_summary = [
        f"Hourly = 24 × ${hourly}/hr = ${24.0 * hourly:.6f}/day",
        f"Data = {data_gb_day} GB/day × ${data_gb_rate}/GB = ${data_gb_day * data_gb_rate:.6f}/day",
        f"Daily = ${daily:.6f}",
        f"Monthly = daily × {DAYS_PER_MONTH:.4f} = ${monthly:.6f}",
    ]
    return CostEstimate(
        component=name,
        resource_type="AWS::EC2::NatGateway",
        per_run_usd=0.0,
        daily_usd=daily,
        monthly_usd=monthly,
        notes=f"nat_data_gb_per_day={data_gb_day}",
        assumptions=assumptions,
        calculation_summary=calculation_summary,
    )


def _estimate_interface_endpoint(
    name: str,
    scenario: dict[str, Any],
    rates: dict[str, float],
) -> CostEstimate:
    data_gb_day = float(scenario["vpc_endpoint_data_gb_per_day"])
    hourly = rates["interface_vpc_endpoint_hourly"]
    data_gb_rate = rates["interface_vpc_endpoint_data_gb"]
    daily = (24.0 * hourly) + (data_gb_day * data_gb_rate)
    monthly = daily * DAYS_PER_MONTH
    assumptions = {
        "interface_vpc_endpoint_hourly_usd": hourly,
        "interface_vpc_endpoint_data_gb_usd": data_gb_rate,
        "unit_description": "Per AZ per hour + per GB processed",
    }
    calculation_summary = [
        f"Hourly = 24 × ${hourly}/hr = ${24.0 * hourly:.6f}/day",
        f"Data = {data_gb_day} GB/day × ${data_gb_rate}/GB = ${data_gb_day * data_gb_rate:.6f}/day",
        f"Daily = ${daily:.6f}",
        f"Monthly = daily × {DAYS_PER_MONTH:.4f} = ${monthly:.6f}",
    ]
    return CostEstimate(
        component=name,
        resource_type="AWS::EC2::VPCEndpoint",
        per_run_usd=0.0,
        daily_usd=daily,
        monthly_usd=monthly,
        notes=f"interface_endpoint_data_gb_per_day={data_gb_day}",
        assumptions=assumptions,
        calculation_summary=calculation_summary,
    )


def _estimate_aurora(name: str, scenario: dict[str, Any], rates: dict[str, float]) -> CostEstimate:
    acu_hours_per_day = float(scenario["aurora_acu_hours_per_day"])
    acu_rate = rates["aurora_serverless_acu_hour"]
    daily = acu_hours_per_day * acu_rate
    monthly = daily * DAYS_PER_MONTH
    per_run = daily / max(float(scenario["runs_per_day"]), 1.0)
    assumptions = {
        "aurora_serverless_acu_hour_usd": acu_rate,
        "unit_description": "Per ACU-hour (Aurora capacity unit)",
    }
    calculation_summary = [
        f"Daily = {acu_hours_per_day} ACU-hours × ${acu_rate}/ACU-hr = ${daily:.6f}",
        f"Monthly = daily × {DAYS_PER_MONTH:.4f} = ${monthly:.6f}",
    ]
    return CostEstimate(
        component=name,
        resource_type="AWS::RDS::DBCluster",
        per_run_usd=per_run,
        daily_usd=daily,
        monthly_usd=monthly,
        notes=f"acu_hours_per_day={acu_hours_per_day}",
        assumptions=assumptions,
        calculation_summary=calculation_summary,
    )


def _estimate_logs(name: str, scenario: dict[str, Any], rates: dict[str, float]) -> CostEstimate:
    logs_gb_day = float(scenario["cloudwatch_logs_gb_per_day"])
    ingest_rate = rates["cloudwatch_logs_ingest_gb"]
    daily = logs_gb_day * ingest_rate
    monthly = daily * DAYS_PER_MONTH
    per_run = daily / max(float(scenario["runs_per_day"]), 1.0)
    assumptions = {
        "cloudwatch_logs_ingest_gb_usd": ingest_rate,
        "unit_description": "Per GB ingested",
    }
    calculation_summary = [
        f"Daily = {logs_gb_day} GB ingested × ${ingest_rate}/GB = ${daily:.6f}",
        f"Monthly = daily × {DAYS_PER_MONTH:.4f} = ${monthly:.6f}",
    ]
    return CostEstimate(
        component=name,
        resource_type="AWS::Logs::LogGroup",
        per_run_usd=per_run,
        daily_usd=daily,
        monthly_usd=monthly,
        notes=f"logs_gb_per_day={logs_gb_day}",
        assumptions=assumptions,
        calculation_summary=calculation_summary,
    )


def _estimate_template(
    template: dict[str, Any],
    scenario: dict[str, Any],
    rates: dict[str, float],
) -> list[CostEstimate]:
    defaults = _build_parameter_defaults(template)
    estimates: list[CostEstimate] = []
    resources = template.get("Resources", {}) or {}
    for logical_id, resource in resources.items():
        rtype = resource.get("Type")
        props = resource.get("Properties", {}) or {}
        if rtype == "AWS::Lambda::Function":
            estimates.append(_estimate_lambda(logical_id, props, defaults, scenario, rates))
        elif rtype == "AWS::EC2::NatGateway":
            estimates.append(_estimate_nat_gateway(logical_id, scenario, rates))
        elif rtype == "AWS::EC2::VPCEndpoint":
            if props.get("VpcEndpointType") == "Interface":
                estimates.append(_estimate_interface_endpoint(logical_id, scenario, rates))
        elif rtype == "AWS::RDS::DBCluster":
            estimates.append(_estimate_aurora(logical_id, scenario, rates))
        elif rtype == "AWS::Logs::LogGroup":
            estimates.append(_estimate_logs(logical_id, scenario, rates))
    return estimates


def _to_output(
    estimates: list[CostEstimate],
    template_path: Path,
    scenario: dict[str, Any],
    rates: dict[str, float],
    rates_metadata: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    entries = [
        {
            "component": item.component,
            "resource_type": item.resource_type,
            "per_run_usd": round(item.per_run_usd, 6),
            "daily_usd": round(item.daily_usd, 6),
            "monthly_usd": round(item.monthly_usd, 6),
            "notes": item.notes,
            "assumptions": item.assumptions,
            "calculation_summary": item.calculation_summary,
        }
        for item in estimates
    ]
    total_per_run = sum(item.per_run_usd for item in estimates)
    total_daily = sum(item.daily_usd for item in estimates)
    total_monthly = sum(item.monthly_usd for item in estimates)
    payload = {
        "template": str(template_path),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "scenario": scenario,
        "rates": rates,
        "totals": {
            "per_run_usd": round(total_per_run, 6),
            "daily_usd": round(total_daily, 6),
            "monthly_usd": round(total_monthly, 6),
        },
        "components": entries,
    }
    if rates_metadata is not None:
        payload["rates_metadata"] = rates_metadata
    return payload


def _markdown_report(payload: dict[str, Any]) -> str:
    totals = payload["totals"]
    rows = []
    for comp in payload["components"]:
        rows.append(
            f"| {comp['component']} | {comp['resource_type']} | "
            f"${comp['per_run_usd']:.6f} | ${comp['daily_usd']:.6f} | ${comp['monthly_usd']:.6f} | {comp['notes']} |"
        )
    sections = [
        "# Cost Estimate",
        "",
        f"Template: `{payload['template']}`",
        "",
        f"- Total per run: **${totals['per_run_usd']:.6f}**",
        f"- Total daily: **${totals['daily_usd']:.6f}**",
        f"- Total monthly: **${totals['monthly_usd']:.6f}**",
        "",
    ]
    # Price source: pricing_api (timestamp) or default_stored_price (date)
    rates_meta = payload.get("rates_metadata") or {}
    if rates_meta:
        sections.append("## Price source (per rate)")
        sections.append("")
        sections.append("| Rate key | Source | At |")
        sections.append("|---|---|---|")
        for key in sorted(rates_meta.keys()):
            m = rates_meta[key]
            source = m.get("source", "unknown")
            at = m.get("at", "")
            sections.append(f"| {key} | {source} | {at} |")
        sections.append("")
        sections.append("---")
        sections.append("")
    sections.extend([
        "## Summary by component",
        "",
        "| Component | Resource Type | Per Run USD | Daily USD | Monthly USD | Notes |",
        "|---|---|---:|---:|---:|---|",
        *rows,
        "",
        "---",
        "",
        "## Assumptions and calculation (by component)",
        "",
        "Rates use **pricing_api** (when available) or **default_stored_price**; see Price source table above.",
        "",
    ])
    for comp in payload["components"]:
        sections.append(f"### {comp['component']} ({comp['resource_type']})")
        sections.append("")
        sections.append("**Base unit prices / assumptions**")
        assumptions = comp.get("assumptions", {})
        for k, v in assumptions.items():
            if isinstance(v, dict):
                sections.append(f"- {k}:")
                for k2, v2 in v.items():
                    sections.append(f"  - {k2}: {v2}")
            else:
                sections.append(f"- {k}: {v}")
        sections.append("")
        sections.append("**Calculation to monthly**")
        for step in comp.get("calculation_summary", []):
            sections.append(f"- {step}")
        sections.append("")
    return "\n".join(sections)


def _fetch_rates_with_metadata(region: str):
    """Fetch rates from Pricing API first; fallback to DEFAULT_RATES. Returns (rates, metadata)."""
    from datetime import timezone
    now = datetime.now(tz=timezone.utc)
    at_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    default_meta = {"source": "default_stored_price", "at": at_str}
    try:
        import sys
        from pathlib import Path
        src = Path(__file__).resolve().parents[2] / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
        from cost_guard.pricing_api import fetch_pricing_with_metadata
        rates, metadata = fetch_pricing_with_metadata(
            default_pricing=dict(DEFAULT_RATES),
            region=region,
            at_time=now,
        )
        return rates, metadata
    except Exception:
        rates = dict(DEFAULT_RATES)
        metadata = {k: dict(default_meta) for k in rates}
        return rates, metadata


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Estimate component costs from a CloudFormation template")
    parser.add_argument("--template", required=True, help="CloudFormation template path")
    parser.add_argument("--scenario", default="", help="Optional scenario JSON file")
    parser.add_argument("--region", default="us-east-1", help="AWS region for pricing (Pricing API is us-east-1)")
    parser.add_argument("--out-json", default="", help="Optional JSON output path")
    parser.add_argument("--out-markdown", default="", help="Optional Markdown output path")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    template_path = Path(args.template)
    scenario_path = Path(args.scenario) if args.scenario else None

    template = _load_yaml(template_path)
    scenario = _load_scenario(scenario_path)
    rates, rates_metadata = _fetch_rates_with_metadata(args.region)
    estimates = _estimate_template(template=template, scenario=scenario, rates=rates)
    payload = _to_output(
        estimates=estimates,
        template_path=template_path,
        scenario=scenario,
        rates=rates,
        rates_metadata=rates_metadata,
    )
    markdown = _markdown_report(payload)

    if args.out_json:
        Path(args.out_json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.out_markdown:
        Path(args.out_markdown).write_text(markdown, encoding="utf-8")

    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
