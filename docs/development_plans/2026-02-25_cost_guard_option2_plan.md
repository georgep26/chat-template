# Hybrid AWS Cost Guard Plan (Option 2), v1 with Monitoring Option 1 and v1.2 Path to Option 3

Generated: 2026-02-25. This is a historical development plan; active deployment behavior is documented in `docs/deployment_process.md` and `scripts/deploy/README.md`.

## Summary
This plan implements a hybrid cost-control system that uses AWS native billing/cost tools for official accounting and a low-cost near-realtime usage monitor for proactive warning and enforcement.

Locked decisions:
1. v1 enforcement scope: Lambda + Bedrock role controls.
2. Threshold policy: warning at 80%, hard limit at 100%, enforcement after 2 consecutive 5-minute breaches.
3. Report artifacts: JSON + HTML + PNG.
4. Monitoring strategy now: Option 1 (scheduled polling).
5. Monitoring strategy later: Option 3 (app-metered events for Bedrock precision).

## Phase 0 Deliverables (Documentation First)
1. Create plan document under `docs/development_plans/` with a generated date in the filename.
2. Create report docs index at `docs/cost_reports/README.md`.
3. Define report output convention under `docs/cost_reports/`:
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
4. Keep last 90 daily report folders by default via pruning in the report generator.

## Scope and Non-Scope
1. In scope for v1:
   1. Near-realtime monitoring every 5 minutes.
   2. Warning and hard-limit thresholds.
   3. SNS notifications for warning and critical events.
   4. Automatic enforcement for Lambda and Bedrock access.
   5. JSON + HTML + PNG reports written to `docs/cost_reports`.
   6. Daily reconciliation against AWS billing data.
2. Out of scope for v1:
   1. Metric Streams pipeline.
   2. Full RDS/NAT destructive enforcement.
   3. Perfect token-level Bedrock attribution.
3. Planned for v1.2:
   1. Option 3 app-metered usage events for precise Bedrock token/model costing.
   2. Optional additional enforcement actions for RDS and selected non-prod resources.

## Architecture
1. `cost-guard-monitor` Lambda:
   1. Runs every 5 minutes from EventBridge Scheduler.
   2. Pulls usage metrics in batch.
   3. Computes estimated interval and month-to-date spend by resource.
   4. Evaluates thresholds and emits warning/enforcement events.
2. `cost-guard-enforcer` Lambda:
   1. Consumes enforcement events.
   2. Executes idempotent actions.
   3. Stores previous state for restore.
   4. Emits critical SNS notifications.
3. `cost-guard-pricing-sync` Lambda:
   1. Runs daily.
   2. Updates regional price cache.
   3. Applies configured safety markup fallback if pricing fetch fails.
4. DynamoDB table:
   1. Stores rolling counters, breach streaks, cooldown windows, action state, and restore state.
5. SNS:
   1. Warning topic for approaching budget.
   2. Critical topic for enforcement actions and failures.
6. S3 or local report writer:
   1. v1 writes report artifacts to `docs/cost_reports` path for this repo.
   2. Optional mirrored upload to S3 in a later phase.

## Cost-Effective Monitoring Design (Option 1)
1. Use one scheduled monitor Lambda with batched metric retrieval.
2. Use CloudWatch `GetMetricData` in consolidated calls to reduce API cost and execution time.
3. Poll every 5 minutes, not every minute, to balance responsiveness and cost.
4. Track incremental deltas from previous state to avoid expensive historical recomputation.
5. Cache pricing daily instead of real-time pricing lookups.
6. Keep Lambda runtime small and memory moderate for low control-plane cost.
7. Avoid Metric Streams in v1 to prevent ongoing stream/consumer costs.

## Bedrock Overrun Protection Strategy
1. Preventive guardrails in v1:
   1. Enforce model allowlist in IAM policy for runtime roles.
   2. Optionally deny known expensive model IDs by default from config.
2. Reactive controls in v1:
   1. Estimate Bedrock spend from available usage metrics and configured conversion model.
   2. If Bedrock-related breach is detected, attach deny policy for `bedrock:InvokeModel*` to protected role or disable triggering Lambda concurrency.
3. Precision upgrade in v1.2 (Option 3):
   1. App emits metered usage events with model ID and token counts.
   2. Cost guard consumes those events for exact model/token attribution.
   3. Bedrock enforcement becomes model-specific and more accurate.

## Enforcement Policy Matrix
1. Lambda overspend:
   1. Set reserved concurrency to `0`.
   2. Disable event source mappings for that function if configured.
2. Bedrock overspend:
   1. Attach deny policy for `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` on target role.
   2. Optionally deny only model IDs listed as blocked.
3. Restore:
   1. `restore` command reverts role policy and concurrency to prior stored state.
4. Safety:
   1. Enforcer cannot target guard-system resources.
   2. Enforcer only acts on allowlisted resources from config.
   3. Enforcer requires two consecutive breaches and respects cooldown.

## AWS Native Features to Keep
1. AWS Budgets remains enabled per account for official monthly tracking and fallback alerts.
2. Cost allocation tags remain active for attribution and reporting.
3. Cost Explorer/CUR/Data Exports used for reconciliation and financial truth.
4. Optional Budget Actions can be added as secondary delayed fallback, not primary kill-switch.

## Important Changes to Public APIs / Interfaces / Types
1. Add `cost_control` section to `infra/infra.yaml`:
```yaml
cost_control:
  enabled: true
  mode: enforce
  budget:
    period: monthly
    amount_usd: 20
    warning_percent: 80
    hard_percent: 100
  monitor:
    interval_minutes: 5
    consecutive_breach_count: 2
    cooldown_minutes: 30
  notifications:
    warning_sns_topic_arn: ""
    critical_sns_topic_arn: ""
  pricing:
    refresh_hour_utc: 4
    safety_markup_percent: 10
  bedrock:
    enforce_model_allowlist: true
    allowed_model_ids:
      - "amazon.titan-embed-text-v2:0"
    blocked_model_ids: []
  reports:
    output_dir: "docs/cost_reports"
    formats: ["json", "html", "png"]
    retention_days: 90
  resources:
    - id: "rag_lambda"
      type: "lambda"
      identifier: "chat-template-dev-rag-chat"
      enforcement: "lambda_reserved_concurrency_zero"
      enabled: true
    - id: "rag_runtime_role"
      type: "iam_role"
      identifier: "chat-template-rag-lambda-execution-role-dev"
      enforcement: "attach_bedrock_deny_policy"
      enabled: true
```
2. Add new deployment script interface:
   1. `scripts/deploy/deploy_cost_guard.sh <env> <deploy|update|delete|status>`
3. Add new report command interface:
   1. `scripts/cost/generate_cost_report.py --env <env> --date <YYYY-MM-DD> --out docs/cost_reports`
4. Add new restore command interface:
   1. `scripts/cost/cost_guard_restore.sh --env <env> --resource-id <id>`
5. Add report JSON schema (stable contract):
```json
{
  "generated_at": "ISO-8601",
  "environment": "dev|staging|prod",
  "budget": {
    "limit_usd": 20.0,
    "warning_percent": 80,
    "hard_percent": 100,
    "estimated_mtd_usd": 0.0,
    "forecast_month_end_usd": 0.0
  },
  "resources": [
    {
      "id": "rag_lambda",
      "type": "lambda",
      "identifier": "chat-template-dev-rag-chat",
      "estimated_mtd_usd": 0.0,
      "interval_usd": 0.0,
      "forecast_month_end_usd": 0.0,
      "breach_state": "none|warning|hard",
      "last_action": "none|warned|enforced|restored",
      "last_action_at": "ISO-8601|null"
    }
  ],
  "events": [
    {
      "event_id": "uuid",
      "timestamp": "ISO-8601",
      "severity": "warning|critical",
      "resource_id": "rag_lambda",
      "message": "string"
    }
  ]
}
```

## Implementation Plan
1. Documentation and scaffolding:
   1. Create `docs/development_plans` and `docs/cost_reports` folders.
   2. Commit plan and reporting README with artifact conventions.
2. IaC and deploy integration:
   1. Add cost guard CloudFormation template(s) for monitor, enforcer, pricing sync, DDB, SNS, EventBridge.
   2. Add IAM least-privilege policies for monitoring and enforcement.
   3. Integrate cost guard deployment into `deploy_all.sh` with `--skip-cost-guard`.
3. Config and parser updates:
   1. Extend config parser utilities to read `cost_control`.
   2. Validate required config values and sane bounds.
4. Monitor implementation:
   1. Build metric adapters for Lambda and Bedrock-related usage.
   2. Implement estimator and culprit detection.
   3. Persist state and breach streaks in DynamoDB.
5. Notification implementation:
   1. Emit warning SNS at warning threshold with cooldown suppression.
   2. Emit critical SNS on enforcement and enforcement failures.
6. Enforcer implementation:
   1. Implement Lambda concurrency zero action.
   2. Implement Bedrock deny policy action on configured role.
   3. Implement restore path and idempotency.
7. Reporting implementation:
   1. Generate JSON artifacts each run.
   2. Generate daily HTML summary from JSON with template.
   3. Generate PNG charts using Python plotting library.
   4. Update `docs/cost_reports/index.html` and `latest/`.
8. Reconciliation:
   1. Add daily job to compare estimated vs AWS billing data.
   2. Emit variance stats in JSON and HTML.
9. v1.2 Option 3 extension:
   1. Add app-metered Bedrock event emitter.
   2. Add event ingestion path and token-precise Bedrock costing.
   3. Switch Bedrock estimate source to event-first with polling fallback.

## Test Cases and Scenarios
1. Unit tests:
   1. Threshold crossing and cooldown logic.
   2. Two-consecutive-breach enforcement logic.
   3. Idempotent enforcement and restore state.
   4. Estimator math for Lambda and Bedrock.
2. Integration tests:
   1. Monitor with mocked CloudWatch and pricing APIs.
   2. Enforcer with mocked IAM/Lambda APIs.
   3. Report generation JSON/HTML/PNG file creation and schema validity.
3. End-to-end dev scenarios:
   1. Simulated cost increase triggers warning SNS at 80%.
   2. Continued breach triggers enforcement at 100%.
   3. Lambda concurrency is set to zero within 10 minutes of sustained breach.
   4. Bedrock deny policy is attached on configured role when breach culprit is Bedrock-heavy workload.
   5. Restore command returns system to prior state.
4. Regression checks:
   1. Existing deployment scripts still run with `--skip-cost-guard`.
   2. Existing budget/tag workflows remain intact.

## Acceptance Criteria
1. Warning alert is delivered within one monitor interval after crossing warning threshold.
2. Hard-limit enforcement occurs after two consecutive breached intervals.
3. Enforcement is reversible and auditable.
4. JSON/HTML/PNG reports are generated in `docs/cost_reports/latest` and daily folder.
5. Daily reconciliation output exists and includes estimate-vs-actual variance.
6. Bedrock preventive controls block non-allowed expensive model usage by default policy.

## Rollout Plan
1. Week 1: deploy in `observe` mode on dev, collect baseline and verify report quality.
2. Week 2: enable enforcement for Lambda and Bedrock role in dev.
3. Week 3: deploy to staging with enforcement and run incident drill plus restore drill.
4. Week 4: deploy to prod in observe mode for 3 days, then enforce.
5. Week 5+: implement Option 3 Bedrock event metering for precision.

## Assumptions and Defaults
1. Monthly budget is the authoritative project budget.
2. Warning/hard thresholds are 80/100 by default.
3. Polling interval is 5 minutes.
4. v1 enforcement targets only Lambda and Bedrock role controls.
5. Reports are written to `docs/cost_reports` with 90-day retention.
6. Primary alerting is SNS email; SMS can be added later via SNS subscriptions.
7. Budget Actions are secondary safety net due billing latency.
