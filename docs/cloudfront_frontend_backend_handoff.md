# CloudFront Frontend + Backend Handoff (Manual Deployment)

This runbook describes the manual steps to keep frontend and backend aligned when the frontend is deployed to S3 + CloudFront and the backend is a Lambda Function URL with Cognito auth.

Applies to any environment (`dev`, `staging`, `prod`). Test in `staging` first.

---

## Manual frontend deployment checklist

1. **Deploy frontend infrastructure (backend repo)**  
   In `chat-template`: `./scripts/deploy/deploy_frontend.sh <env> deploy -y`  
   (Or run `./scripts/deploy/deploy_all.sh <env> -y` without `--skip-frontend`.)

2. **Get CloudFront URL**  
   From script output or:  
   `aws cloudformation describe-stacks --stack-name chat-template-frontend-<env> --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontDomainName`].OutputValue' --output text`  
   Full URL: `https://<CloudFrontDomainName>`.

3. **Set backend handoff values**  
   In `chat-template`: update `infra/secrets/<env>_secrets.yaml` (e.g. `config_secrets.COGNITO_CALLBACK_URLS`, `COGNITO_LOGOUT_URLS`, `FUNCTION_URL_CORS_ALLOW_ORIGINS`) with the CloudFront URL. Optionally run `./scripts/setup/setup_github.sh <env>` to sync secrets to GitHub.

4. **Deploy Cognito and Lambda** (if not already aligned)  
   In `chat-template`:  
   `./scripts/deploy/deploy_cognito.sh <env> deploy -y`  
   `./scripts/deploy/deploy_rag_lambda.sh <env> deploy -y --skip-build`

5. **Build and push frontend (frontend repo)**  
   In `chat-template-frontend`: set all required `VITE_*` (and `VITE_COGNITO_REDIRECT_URI` / `VITE_COGNITO_LOGOUT_REDIRECT_URI` to the CloudFront URL). Then:  
   `./infrastructure/scripts/deploy.sh <env>`  
   This builds the app, syncs to S3, and invalidates CloudFront.

6. **Verify**  
   Open `https://<CloudFrontDomainName>`, sign in, confirm chat and CORS work, then sign out.

---

## Required values

From backend:
- Lambda Function URL (used by frontend `VITE_CHAT_API_BASE_URL` and `VITE_ALLOWED_API_ORIGIN`)
- Cognito values: user pool id, app client id, hosted UI domain

From frontend deployment:
- CloudFront domain (e.g. `d123abc.cloudfront.net`)

---

## 1) Deploy frontend infrastructure (backend repo)

Repo: **`chat-template`**

Deploy the CloudFormation stack (S3 + CloudFront) in the **correct environment account**:

```bash
./scripts/deploy/deploy_frontend.sh <env> deploy -y
```

Prerequisite: AWS CLI using the target account (e.g. CLI profile for that env). This creates or updates the stack only; it does not build or upload the frontend app.

**Capture CloudFront URL:** From the script output, or:

```bash
aws cloudformation describe-stacks --stack-name chat-template-frontend-<env> --query 'Stacks[0].Outputs'
```

---

## 2) Build and push frontend assets (frontend repo)

Repo: **`chat-template-frontend`**

Set required `VITE_*` env vars, then run the **build-and-push** script (no infrastructure deployment):

```bash
export AWS_REGION=us-east-1

export VITE_CHAT_API_BASE_URL=https://<lambda-id>.lambda-url.us-east-1.on.aws
export VITE_ALLOWED_API_ORIGIN=https://<lambda-id>.lambda-url.us-east-1.on.aws
export VITE_CHAT_API_PATH=/
export VITE_CHAT_TIMEOUT_MS=65000
export VITE_COGNITO_REGION=us-east-1
export VITE_COGNITO_USER_POOL_ID=us-east-1_xxxxx
export VITE_COGNITO_APP_CLIENT_ID=xxxxxxxx
export VITE_COGNITO_DOMAIN=<your-domain>.auth.us-east-1.amazoncognito.com
export VITE_COGNITO_REDIRECT_URI=https://<cloudfront-domain>/auth/callback
export VITE_COGNITO_LOGOUT_REDIRECT_URI=https://<cloudfront-domain>/

./infrastructure/scripts/deploy.sh <env>
```

What it does:
- Installs npm dependencies and builds the Vue app
- Looks up the existing stack to get bucket and distribution ID (stack must exist from step 1)
- Syncs `dist/` to S3 and creates CloudFront invalidation
- Prints CloudFront URL and backend handoff values

Prerequisite: Frontend stack must already exist; AWS credentials for the same environment account.

---

## 3) Update backend env secrets with CloudFront handoff values

Repo: **`chat-template`**

File: `infra/secrets/<env>_secrets.yaml`  
Section: `config_secrets`

Set:
- `COGNITO_CALLBACK_URLS=https://<cloudfront-domain>/auth/callback`
- `COGNITO_LOGOUT_URLS=https://<cloudfront-domain>/`
- `FUNCTION_URL_CORS_ALLOW_ORIGINS=https://<cloudfront-domain>`

Keep these present as needed: `COGNITO_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`.

Optional: if you use GitHub Actions deploys, sync secrets with `./scripts/setup/setup_github.sh <env>`.

---

## 4) Deploy backend Cognito with updated callback/logout URLs

Script: `./scripts/deploy/deploy_cognito.sh`

```bash
./scripts/deploy/deploy_cognito.sh <env> deploy -y
```

What it does: hydrates env config at runtime, deploys Cognito stack with callback/logout/domain from config, writes Cognito IDs back to env secrets file.

---

## 5) Deploy backend Lambda so Function URL CORS picks up CloudFront origin

```bash
./scripts/deploy/deploy_rag_lambda.sh <env> deploy -y --skip-build
```

What it does: deploys Lambda stack with CORS/auth params so the Function URL allows the CloudFront origin.

Alternative (orchestrated deploy):

```bash
./scripts/deploy/deploy_all.sh <env> -y --skip-network --skip-s3 --skip-db --skip-kb --skip-ecr --skip-cost-tags
```

---

## 6) Rebuild frontend with final deployed frontend domain values

After backend updates, run the **build-and-push** script in the frontend repo again (same as step 2), with:
- `VITE_COGNITO_REDIRECT_URI=https://<cloudfront-domain>/auth/callback`
- `VITE_COGNITO_LOGOUT_REDIRECT_URI=https://<cloudfront-domain>/`

```bash
./infrastructure/scripts/deploy.sh <env>
```

---

## 7) Verification checklist

1. Open `https://<cloudfront-domain>`.
2. Login redirects to Cognito, then returns to `/auth/callback` on same CloudFront domain.
3. Chat request succeeds (HTTP `200`).
4. Browser shows no CORS errors for API requests.
5. Logout returns to CloudFront root URL.

---

## Ongoing maintenance

Re-run this handoff whenever:
- CloudFront stack is recreated (new domain)
- You switch frontend domain
- Cognito callback/logout URLs change
- Backend CORS allowlist needs updates
