# Cost Guard

Hybrid AWS cost control with:

1. Near-realtime usage monitoring (every 5 minutes by default)
2. Threshold alerts (warning + hard limit)
3. Automatic enforcement (kill-switch) for configured resources
4. JSON/HTML/PNG reporting and daily reconciliation

This implementation uses AWS-native billing as financial truth and adds a fast estimator/guardrail layer.

## Components

Infrastructure is deployed from `infra/resources/cost_guard_template.yaml`.

1. `cost-guard-monitor` Lambda
2. `cost-guard-enforcer` Lambda
3. `cost-guard-pricing-sync` Lambda
4. DynamoDB state table (`{project}-cost-guard-{env}`)
5. SNS warning + critical topics
6. EventBridge schedules (monitor interval + daily pricing sync)

All three Lambdas are deployed from one Docker image (`src/cost_guard/Dockerfile`) with different handlers via `ImageConfig.Command`.

## Runtime Flow

1. Monitor runs every `cost_control.monitor.interval_minutes`.
2. It estimates month-to-date cost by resource from:
- Lambda metrics (`Invocations`, `Duration`)
- Bedrock CloudTrail events (role-attributed estimate)
- DynamoDB CloudWatch metrics
- Aurora Serverless v2 CloudWatch metrics (`ServerlessDatabaseCapacity` or `CPUUtilization` fallback)
- SNS CloudWatch metrics
- CloudWatch Logs ingestion metrics
- CloudWatch `GetMetricData` API usage estimate
3. It compares signal vs budget thresholds.
4. Warning breach publishes SNS warning (with cooldown).
5. Hard breach for `N` consecutive intervals (default 2) triggers enforcer in `mode: enforce`.
6. Enforcer applies configured action (for enforceable targets only).
7. State/events are persisted in DynamoDB for decisions, audit, and reports.

## Self-Monitoring Coverage

By default, `infra/infra.yaml` includes Cost Guard resources in `cost_control.resources`, so reports include control-plane cost estimates:

1. Cost Guard monitor/enforcer/pricing Lambdas
2. Cost Guard DynamoDB table
3. Cost Guard SNS topics
4. Cost Guard Lambda log groups
5. CloudWatch metric API control estimate

Non-enforceable resources should use `enforcement: "none"` so they are monitored but never selected for shutdown.

## Prerequisites

1. AWS credentials/profile for target environment
2. Docker (for Lambda image build/push)
3. AWS CLI
4. Python 3
5. `yq` for local config hydration
6. Python deps for reports:
- `PyYAML`
- `boto3`
- `plotly`
- `kaleido` (static PNG export backend)

## Configuration

Primary config lives in `infra/infra.yaml` under `cost_control`.

Key settings:

1. `enabled`: enable/disable system
2. `mode`: `observe` or `enforce`
3. `budget`: monthly USD limit + warning/hard percentages
4. `monitor`: interval, consecutive breach count, cooldown
5. `notifications`: warning/critical email and SMS
6. `pricing`: pricing refresh hour and safety markup
7. `bedrock`: model controls and invocation cost map
8. `reports`: output directory, formats, retention
9. `resources`: monitored/enforceable resources list

Supported resource `type` values currently:

1. `lambda`
2. `iam_role`
3. `dynamodb_table`
4. `aurora_cluster` (Aurora Serverless v2; identifier = DB cluster identifier)
5. `sns_topic`
6. `cloudwatch_log_group`
7. `cloudwatch_control`

Enforcement actions currently supported:

1. `lambda_reserved_concurrency_zero`
2. `attach_bedrock_deny_policy`
3. `aurora_stop_cluster` (stops the DB cluster via RDS `StopDBCluster`; restore with `StartDBCluster`. AWS does not allow setting max ACU to 0; stopping the cluster is the supported kill switch.)
4. `none` (monitor-only)

## Deployment Commands

Deploy/update/delete/status/validate Cost Guard stack:

```bash
./scripts/deploy/deploy_cost_guard.sh <env> <deploy|update|delete|status|validate> [options]
```

Examples:

```bash
./scripts/deploy/deploy_cost_guard.sh dev deploy -y
./scripts/deploy/deploy_cost_guard.sh staging update --image-tag 20260228T220000Z
./scripts/deploy/deploy_cost_guard.sh prod status
./scripts/deploy/deploy_cost_guard.sh dev validate
```

Options:

1. `--region <region>`
2. `--image-tag <tag>` (default: `latest`)
3. `-y|--yes`

Notes:

1. Script performs runtime config hydration for `deploy`/`update` using `infra/secrets/<env>_secrets.yaml` (`config_secrets`).
2. Invalid or unresolved email/SMS values are sanitized and skipped.
3. ECR repo used: `{project}-cost-guard-{env}`.

## Restore Command

Revert an enforced action for a resource:

```bash
./scripts/cost/cost_guard_restore.sh --env <env> --resource-id <id>
```

Example:

```bash
./scripts/cost/cost_guard_restore.sh --env dev --resource-id rag_lambda
```

## Report Generation

Generate report bundle from DynamoDB state/events:

```bash
python3 scripts/cost/generate_cost_report.py --env <env> --date <YYYY-MM-DD> --out docs/cost_reports
```

Example:

```bash
python3 scripts/cost/generate_cost_report.py --env dev --out docs/cost_reports
```

Outputs:

1. `summary.json`
2. `resource_breakdown.json`
3. `threshold_events.json`
4. `report.html`
5. `cost_trend_30d.png`
6. `resource_breakdown_top10.png`

Layout:

1. `docs/cost_reports/latest/...`
2. `docs/cost_reports/YYYY-MM-DD/...`
3. `docs/cost_reports/index.html`

Retention is controlled by `cost_control.reports.retention_days` (default `90`).

## Cost Estimation Tool

Template/scenario estimate command:

```bash
python3 scripts/cost/estimate_component_cost.py \
  --template infra/resources/lambda_template.yaml \
  --scenario scripts/cost/scenarios/default_scenario.json \
  --out-json docs/cost_reports/lambda_estimate.json \
  --out-markdown docs/cost_reports/lambda_estimate.md
```

This is a planning estimate tool, separate from monitor state.

## Operational Recommendations

1. Start in `mode: observe` in each environment, then enable `enforce` after validation.
2. Keep critical production resources enforceable only when restore workflow is tested.
3. Keep Cost Guard control-plane resources monitor-only (`enforcement: none`).
4. Use explicit image tags for deterministic rollouts instead of reusing `latest`.
