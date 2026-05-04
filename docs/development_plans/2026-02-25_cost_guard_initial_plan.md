# Option 2 Hybrid Cost Guard Plan (AWS Native + Near-Realtime Usage Enforcement)

Generated: 2026-02-25. This is a historical development plan; active deployment behavior is documented in `docs/deployment_process.md` and `scripts/deploy/README.md`.

## Summary
This plan keeps AWS billing tools as the source of financial truth (Budgets, Cost Explorer, CUR/Data Exports), and adds a low-cost near-realtime usage guard that estimates spend from live usage metrics every 5 minutes, sends warning notifications, and enforces hard limits by disabling overspending resources.

This integrates with current deployment/config patterns in [`infra/infra.yaml`](../../infra/infra.yaml), [`scripts/deploy/deploy_all.sh`](../../scripts/deploy/deploy_all.sh), and [`scripts/deploy/deploy_cost_analysis_tags.sh`](../../scripts/deploy/deploy_cost_analysis_tags.sh).

## Cost-Effective Usage Monitoring Solutions (Within Option 2)
1. Scheduled polling monitor (recommended for v1): EventBridge schedule every 5 minutes invokes one monitor Lambda that batches CloudWatch `GetMetricData`, computes incremental usage cost, stores state in DynamoDB, and triggers warning/enforcement. Lowest complexity and very low control-plane cost.
2. Metric stream pipeline: CloudWatch Metric Streams to Kinesis/Firehose plus consumer Lambda for sub-minute detection. Better latency, but materially higher cost and operational complexity.
3. App-metered events: app emits per-request usage events (tokens, invocations, payload) to SQS/EventBridge; guard consumes events. Cheapest at high precision for Bedrock/token-heavy flows, but requires app instrumentation and has coverage gaps for unmanaged resources.

Decision for implementation: use `1` for v1, add selective `3` for Bedrock token precision in v1.2, avoid `2` unless scale/latency requires it.

## Architecture (Decision Complete)
## Control Plane Components
1. `cost-guard-monitor` Lambda: polls usage metrics, estimates spend, detects threshold breaches, identifies culprit resource.
2. `cost-guard-enforcer` Lambda: executes kill actions idempotently with cooldown and safe-guards.
3. `cost-guard-pricing-sync` Lambda: refreshes unit-price cache daily from AWS pricing APIs.
4. EventBridge schedules: monitor every 5 min, pricing sync daily, reconciliation daily.
5. DynamoDB table: stores per-resource rolling counters, breach state, last action, cooldown, previous limits.
6. SNS topics: warning and critical topics; subscriptions include email and optional SMS.
7. S3 report bucket/prefix: daily breakdown reports and reconciliation artifacts.
8. IAM roles/policies: least privilege; enforcer can only mutate explicitly tagged resources.
9. Optional SSM Parameter(s): runtime override for emergency pause (`observe_only=true`, `global_kill_switch=false`).

## Monitoring and Enforcement Cycle
1. Monitor pulls config and protected resource list.
2. Monitor fetches usage metrics in one batch call.
3. Monitor computes interval cost, month-to-date estimated cost, and projected month-end burn.
4. Monitor compares against warning and hard thresholds.
5. If warning crossed and not already sent in cooldown window, publish SNS warning.
6. If hard threshold crossed for 2 consecutive intervals, enqueue enforcement event with culprit resource.
7. Enforcer runs mapped action for culprit resource.
8. Enforcer writes action audit record to DynamoDB and emits critical SNS.
9. Daily reconciler compares estimated spend to AWS billing data and writes variance report.

## Threshold and Culprit Logic
1. Project warning threshold: default 80% of monthly budget.
2. Project hard threshold: default 100% of monthly budget.
3. Resource warning/hard thresholds: inherited from project unless explicitly overridden.
4. Culprit scoring: highest incremental cost in last interval with minimum 40% share of total interval increase.
5. Fallback action: if no clear culprit, disable resources by predefined priority list.
6. Anti-flap defaults: 2 consecutive breaches, 30-minute cooldown, idempotent action keys.
7. Safety defaults: monitoring resources are immutable targets; allowlist for critical infra.

## Resource Action Policy (Default)
1. Lambda overspend: set reserved concurrency to `0`; disable event source mappings for that function.
2. Bedrock-driven overspend from Lambda: attach deny policy for `bedrock:InvokeModel*` to execution role of culprit workload.
3. CloudWatch Logs overspend: treat as derivative cost; enforce on source Lambda/workload, not log group directly.
4. Aurora overspend: reduce max ACU to configured emergency floor and optionally pause non-prod cluster.
5. NAT overspend: warn-only in v1; enforcement disables upstream egress-producing compute, not NAT deletion.
6. Re-enable flow: explicit `cost-guard restore` command restores previous settings from DynamoDB state.

## Important Changes to Public Interfaces / Types
## `infra.yaml` schema additions
Add a new `cost_control` section in [`infra/infra.yaml`](../../infra/infra.yaml):

```yaml
cost_control:
  enabled: true
  mode: observe # observe | enforce
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
    warning_topic_email: ops@example.com
    warning_topic_sms: ""
    critical_topic_email: ops@example.com
  pricing:
    refresh_daily_utc_hour: 4
    fallback_markup_percent: 10
  resources:
    - id: rag-chat-lambda
      type: lambda
      identifier: chat-template-dev-rag-chat
      enforcement: lambda_reserved_concurrency_zero
      priority: 10
      enabled: true
    - id: rag-bedrock-access
      type: iam_role
      identifier: chat-template-rag-lambda-execution-role-dev
      enforcement: attach_bedrock_deny_policy
      priority: 20
      enabled: true
```

## New deploy interfaces
1. New script: `scripts/deploy/deploy_cost_guard.sh <env> <deploy|update|delete|status>`.
2. New `deploy_all.sh` flag: `--skip-cost-guard`.
3. New report command: `scripts/cost/cost_guard_report.sh <env> --date YYYY-MM-DD`.
4. New restore command: `scripts/cost/cost_guard_restore.sh <env> --resource-id <id>`.

## New report/output contracts
1. Daily report JSON: per-resource `estimated_mtd_usd`, `interval_usd`, `forecast_month_end_usd`, `action_state`, `last_action_at`.
2. Daily report CSV: one row per resource per day for finance/ops review.
3. Alert payload: threshold type, budget values, culprit resource, recommended/triggered action, correlation id.

## Detailed Development Phases
## Phase 1: Config and IaC Foundation
1. Define `cost_control` schema and parser updates.
2. Add cost guard CloudFormation template(s) and IAM least-privilege policies.
3. Integrate deployment into deployment order after app resources in [`scripts/deploy/deploy_all.sh`](../../scripts/deploy/deploy_all.sh).
4. Ensure cost allocation tags include all configured tags and align script defaults with infra config.

## Phase 2: Monitor Engine (Observe-Only)
1. Implement monitor Lambda metric adapters for Lambda, CloudWatch Logs, RDS, NAT, and optional Bedrock token adapter.
2. Implement pricing cache loader with daily sync and fallback table.
3. Implement estimator engine and culprit detection.
4. Write DynamoDB state updates and daily S3 summary reports.
5. Run in `observe` mode only for dev.

## Phase 3: Alerts
1. Create SNS warning and critical topics and subscriptions.
2. Implement warning suppression and cooldown logic.
3. Add alert correlation IDs and structured payloads.
4. Validate alert timing against synthetic load tests.

## Phase 4: Enforcement Engine
1. Implement enforcer Lambda with idempotent action executor.
2. Implement action handlers for Lambda concurrency, event-source disable, Bedrock deny-policy attach, RDS emergency cap.
3. Implement restore command and previous-state rollback storage.
4. Add global emergency override and resource allowlist controls.

## Phase 5: Reconciliation and Accuracy
1. Implement daily reconciler against AWS billing datasets.
2. Produce variance report (`estimated vs actual`) with service/resource granularity.
3. Tune coefficients for services with noisy estimates.
4. Define acceptable variance target and alert if variance breaches target.

## Phase 6: Cost Estimation Tool (Code/Template Analysis)
1. Build `scripts/cost/estimate_component_cost.py` to parse CloudFormation resources and usage scenario file.
2. Use AWS pricing APIs (and calculator API where practical) to compute per-run, daily, monthly estimates.
3. Emit Markdown + JSON outputs for CI/PR comments.
4. Add scenario presets for this project’s components (RAG Lambda, Bedrock calls, RDS, logs, NAT).

## Test Cases and Scenarios
1. Unit tests: estimator formulas, threshold transitions, culprit ranking, cooldown behavior.
2. Unit tests: enforcement idempotency and restore correctness.
3. Integration tests: mocked AWS SDK calls for monitor/enforcer with deterministic metric fixtures.
4. End-to-end dev test: generate synthetic Lambda traffic; verify warning alert before hard stop.
5. End-to-end dev test: force hard breach; verify Lambda reserved concurrency set to `0` within 10 minutes.
6. End-to-end dev test: restore flow re-enables prior state safely.
7. Reconciliation test: compare estimated and billed values for a fixed past date window.
8. Negative tests: missing metrics, pricing API failure, SNS delivery failure, DynamoDB throttling.

## Acceptance Criteria
1. Per-resource daily report is generated and queryable.
2. Warning notification is sent within one monitoring interval after warning threshold breach.
3. Hard-limit enforcement occurs within two monitoring intervals after sustained breach.
4. Enforcement is idempotent and reversible via restore command.
5. Reconciliation job runs daily and publishes variance report.
6. No enforcement action can target guard infrastructure itself.

## Rollout and Monitoring
1. Dev week 1: observe-only, alert-only.
2. Dev week 2: enforce non-critical resources.
3. Staging week 3: enforce full policy with rollback drills.
4. Prod week 4: start observe-only for 3 days, then enforce with conservative thresholds.
5. Dashboard: guard health, last run, breach count, actions taken, estimate-vs-actual variance.
6. Operational runbook: override, restore, escalation, and post-incident review template.

## Assumptions and Defaults Chosen
1. Budget period is monthly and project-level budget is authoritative.
2. Warning threshold defaults to 80%, hard threshold defaults to 100%.
3. Monitoring cadence is 5 minutes for v1.
4. Enforcement starts in non-prod first, then production.
5. Primary notification channel is SNS email; SMS is optional and opt-in.
6. NAT is warning-only in v1 to avoid destructive network actions.
7. Bedrock precision is enhanced through app-metered usage in v1.2 if CloudWatch metrics alone are insufficient.
