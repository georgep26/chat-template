# Lambda VPC And S3 Config Loading

The RAG Lambda loads `app_config.yaml` from S3 at runtime. When the Lambda is deployed outside a VPC, S3 access works through the Lambda service networking. When the Lambda is deployed inside a VPC, the VPC must provide a path to S3 or config loading can time out.

The current default chat history backend is Aurora Data API, so the Lambda does not need VPC networking for database access. Only attach the Lambda to a VPC when another runtime dependency requires it, or when using the direct `postgres` backend.

## Symptom

- Lambda works when deployed locally (e.g. without VPC or with a VPC that has S3 access).
- After deploying via GitHub Actions (which passes `VPC_ID` and `SUBNET_IDS` from environment secrets), the Lambda **times out**.
- Logs show: `Loading configuration from: s3://...` then no further output until `platform.runtimeDone` with `status: timeout`.

This happens because the Lambda is attached to a VPC whose subnets have **no route to S3**. The S3 request never completes.

## Fix: S3 Gateway VPC Endpoint

Ensure the VPC used by the Lambda has an **S3 Gateway VPC Endpoint** and that the **route tables** for the subnets where the Lambda runs include a route for S3 (via that endpoint).

### Option A: Use this repo's network stack

If `resources.network.use_defaults` is set to `false` and you deploy the custom network stack, the VPC template (`infra/resources/vpc_template.yaml`) creates an S3 Gateway endpoint and associates it with the private route table. In that case, use the private subnet IDs from the stack outputs for the Lambda.

Current `infra.yaml` sets `resources.network.use_defaults: true`. In that mode, `deploy_network.sh` does not create a CloudFormation stack. It discovers or verifies an existing VPC and writes `VPC_ID` and `SUBNET_IDS` to the configured environment secrets file. You still need to confirm that the selected subnets have a route to S3 if the Lambda is attached to that VPC.

### Option B: Existing VPC

If you use an existing/imported VPC:

1. In the **AWS Console**: **VPC → Endpoints → Create endpoint**.
2. **Service category**: AWS services.
3. **Service name**: `com.amazonaws.<region>.s3` (e.g. `com.amazonaws.us-east-1.s3`).
4. **Type**: Gateway.
5. **VPC**: Your Lambda VPC.
6. **Route tables**: Select the route tables for **every subnet** where the Lambda runs (so traffic to S3 goes through the gateway endpoint).
7. **Policy**: Full access or restrict to the config bucket; for the current naming convention, restrict to `arn:aws:s3:::chat-template-<env>-s3-<global_suffix>` and `arn:aws:s3:::chat-template-<env>-s3-<global_suffix>/*`.

After the endpoint is created and the route tables are associated, Lambda in that VPC can reach S3 and config loading will succeed.

### Option C: NAT Gateway

Alternatively, put the Lambda in subnets that use a **NAT Gateway** for outbound traffic. That allows access to S3 (and the internet) but is more expensive than an S3 Gateway endpoint. Prefer the S3 Gateway endpoint for config-only access.

## Timeouts in code

The config loader (`src/utils/config.py`) uses a **connect timeout** (15s) and **read timeout** (30s) for S3. If the VPC has no path to S3, you will see a timeout or connection error in the logs instead of the Lambda running for the full 5 minutes. Fix the VPC routing (S3 endpoint or NAT) as above.

## Deployment Notes

VPC values are resolved from the configured environment secrets file:

```text
infra/secrets/<account-set>/<env>.secrets.yaml
```

For the active account set, that is typically:

```text
infra/secrets/adte_sandbox/<env>.secrets.yaml
```

Deploying the `pr` deployment set only touches the ECR repo and Lambda. It will not recreate networking, so any VPC/S3 endpoint fixes must be made before running the PR/runtime deployment set.
