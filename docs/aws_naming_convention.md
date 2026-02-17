# AWS Resource Naming Convention

This document defines a consistent, scalable naming convention for AWS resources across all projects and environments. The goal is to ensure names are readable, sortable, automation-friendly, and future-proof.

## Core Naming Pattern

Components are ordered from least specific to most specific so resources group cleanly in the AWS console, logs, and IAM policies.

### Naming Components

| Component  | Required | Examples                    | Description                                    |
| ---------- | -------- | --------------------------- | ---------------------------------------------- |
| org        | Optional | myorg                   | Company or umbrella organization               |
| project    | Yes      | pm-ai                       | Product or application name                    |
| env        | Yes      | dev, qa, prod               | Deployment environment                         |
| service    | Yes      | api, etl, batch, auth       | Logical workload or domain                      |
| resource   | Yes      | lambda, table, bucket        | AWS resource type                              |
| qualifier  | Optional | v1, writer, blue            | Variant, role, or deployment detail             |

## Standard Environment Names

Use these environment names exactly:

- **dev**
- **qa**
- **prod**

Avoid `development`, `production`, `stage`, or `sandbox` unless the environment is truly ephemeral. Consistency is more important than descriptiveness.

## Resource Naming Examples

### Lambda Functions

- `myorg-pm-ai-prod-api-lambda-getBookings`
- `myorg-pm-ai-dev-etl-lambda-syncListings`

### DynamoDB Tables

- `myorg-pm-ai-prod-bookings-table`
- `myorg-pm-ai-dev-listingDetails-table`

### S3 Buckets

S3 buckets must be globally unique and use lowercase letters and hyphens only.

- `myorg-pm-ai-prod-data-raw`
- `myorg-pm-ai-prod-data-curated`
- `myorg-pm-ai-dev-artifacts`

### Step Functions

- `myorg-pm-ai-prod-batch-stateMachine`
- `myorg-pm-ai-dev-scrapeListings-stateMachine`

### AWS Batch

- **Job Definitions:** `myorg-pm-ai-prod-scraper-jobdef`
- **Job Queues:** `myorg-pm-ai-prod-scraper-queue`
- **Compute Environments:** `myorg-pm-ai-prod-scraper-compute`

### IAM Roles and Policies

- `myorg-pm-ai-prod-lambda-role`
- `myorg-pm-ai-dev-batch-role`
- `myorg-pm-ai-prod-etl-policy`

IAM resource names should reflect who uses them, not what permissions they contain.

### CloudWatch Log Groups

- `/aws/lambda/myorg-pm-ai-prod-api-lambda-getBookings`

CloudWatch log groups automatically inherit the Lambda function name.

## Shortened Naming Variant

When org is omitted (optional) or when AWS name length limits are an issue, use the shorter form:

- `pm-ai-prod-api-lambda-getBookings`
- `pm-ai-dev-etl-table-listings`

## Region Handling

Do not include the AWS region in resource names. Region context should be inferred from ARNs, tags, and deployment configuration.

Exception: region-specific S3 buckets used for replication or compliance:

- `myorg-pm-ai-prod-data-us-east-1`

## Required Resource Tags

Names are for humans. Tags are required for billing, automation, governance, and cost allocation.

The following tags are mandatory for all resources:

| Tag         | Example    |
| ----------- | ---------- |
| Project     | pm-ai      |
| Environment | prod       |
| Owner       | platform   |
| CostCenter  | analytics  |
| ManagedBy   | terraform  |

## Terraform Naming Pattern

Use a centralized naming prefix to prevent drift and simplify refactoring:

```hcl
locals {
  name_prefix = "${var.org}-${var.project}-${var.env}"
}

resource "aws_lambda_function" "api" {
  function_name = "${local.name_prefix}-api-lambda-getBookings"
}
```

## Golden Rules

1. **Environment is never optional.** Every resource name must include the environment.
2. **Never casually rename production resources.** Renames can break references and automation.
3. **Names must be understandable without tribal knowledge.** A new team member should infer purpose from the name.
4. **If a resource cannot be wildcarded cleanly in IAM, reconsider the name.** IAM policies should be able to scope by prefix or pattern.
5. **Tags are non-negotiable.** All resources must have the required tags.

## Summary

This naming convention prioritizes consistency, clean automation, predictable IAM patterns, and long-term maintainability. Following it strictly from the beginning prevents costly cleanup and confusion as systems scale.
