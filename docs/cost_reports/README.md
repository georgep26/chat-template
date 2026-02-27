# Cost Reports

This folder stores generated cost reports for the cost guard system.

## Report Structure

```text
docs/cost_reports/
  index.html
  latest/
    summary.json
    resource_breakdown.json
    threshold_events.json
    report.html
    cost_trend_30d.png
    resource_breakdown_top10.png
  YYYY-MM-DD/
    summary.json
    resource_breakdown.json
    threshold_events.json
    report.html
    cost_trend_30d.png
    resource_breakdown_top10.png
```

## Generate a Report

```bash
python3 scripts/cost/generate_cost_report.py --env dev --date 2026-02-25 --out docs/cost_reports
```

## Estimate Component Cost from CloudFormation

```bash
python3 scripts/cost/estimate_component_cost.py \
  --template infra/resources/lambda_template.yaml \
  --out-json docs/cost_reports/lambda_estimate.json \
  --out-markdown docs/cost_reports/lambda_estimate.md
```

You can pass a custom scenario file with run volume assumptions:

```bash
python3 scripts/cost/estimate_component_cost.py \
  --template infra/resources/vpc_template.yaml \
  --scenario scripts/cost/scenarios/default_scenario.json
```

## Output Files

- `summary.json`: top-level budget and project signal
- `resource_breakdown.json`: per-resource cost estimates and action state
- `threshold_events.json`: warning/critical threshold and enforcement events
- `report.html`: human-readable report
- `cost_trend_30d.png`: 30-day estimated trend
- `resource_breakdown_top10.png`: top resources by estimated spend

## Retention

Daily report folders are pruned to the configured retention window (`cost_control.reports.retention_days`, default `90`).
