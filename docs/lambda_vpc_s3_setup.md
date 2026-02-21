# Lambda in VPC: S3 Access (Config Loading)

When the RAG Lambda is deployed **inside a VPC** (using `VPC_ID` and `SUBNET_IDS`), it loads `app_config.yaml` from S3 at runtime. Lambda functions in a VPC do not have internet access by default, so **the VPC must provide a path to S3** or the Lambda will hang when calling `s3.get_object()` and eventually time out.

## Symptom

- Lambda works when deployed locally (e.g. without VPC or with a VPC that has S3 access).
- After deploying via GitHub Actions (which passes `VPC_ID` and `SUBNET_IDS` from environment secrets), the Lambda **times out**.
- Logs show: `Loading configuration from: s3://...` then no further output until `platform.runtimeDone` with `status: timeout`.

This happens because the Lambda is attached to a VPC whose subnets have **no route to S3**. The S3 request never completes.

## Fix: S3 Gateway VPC Endpoint

Ensure the VPC used by the Lambda has an **S3 Gateway VPC Endpoint** and that the **route tables** for the subnets where the Lambda runs include a route for S3 (via that endpoint).

### Option A: Use this repo’s network stack

If you deploy the network with `deploy_network.sh`, the VPC template (`infra/resources/vpc_template.yaml`) already creates an S3 Gateway endpoint and associates it with the private route table. In that case, use the **private subnet IDs** from the stack outputs for the Lambda.

### Option B: Existing VPC (e.g. when using `--skip-network`)

If you use an existing VPC (e.g. from GitHub secrets with `--skip-network`):

1. In the **AWS Console**: **VPC → Endpoints → Create endpoint**.
2. **Service category**: AWS services.
3. **Service name**: `com.amazonaws.<region>.s3` (e.g. `com.amazonaws.us-east-1.s3`).
4. **Type**: Gateway.
5. **VPC**: Your Lambda VPC.
6. **Route tables**: Select the route tables for **every subnet** where the Lambda runs (so traffic to S3 goes through the gateway endpoint).
7. **Policy**: Full access or restrict to the config bucket; e.g. restrict to `arn:aws:s3:::chat-template-<env>-s3-bucket` and `arn:aws:s3:::chat-template-<env>-s3-bucket/*`.

After the endpoint is created and the route tables are associated, Lambda in that VPC can reach S3 and config loading will succeed.

### Option C: NAT Gateway

Alternatively, put the Lambda in subnets that use a **NAT Gateway** for outbound traffic. That allows access to S3 (and the internet) but is more expensive than an S3 Gateway endpoint. Prefer the S3 Gateway endpoint for config-only access.

## Timeouts in code

The config loader (`src/utils/config.py`) uses a **connect timeout** (15s) and **read timeout** (30s) for S3. If the VPC has no path to S3, you will see a timeout or connection error in the logs instead of the Lambda running for the full 5 minutes. Fix the VPC routing (S3 endpoint or NAT) as above.
