# Plan: Automate Cognito user bootstrap + frontend handoff in deploy scripts

## Context

After deploying `sandbox` to the management account we hit three manual steps before the frontend can run:
1. Choose a Cognito hosted-UI domain prefix and re-deploy Cognito with the CloudFront URL added to CallbackUrls/LogoutUrls.
2. Create at least one Cognito user (admin) and ideally a second example user; capture passwords somewhere reusable.
3. Hand a bundle of values to the frontend repo (Lambda Function URL, CloudFront URL, Cognito IDs + hosted-UI domain, redirect URIs).

Today this is a hand-written runbook (`docs/working_files/2026-05-04_sandbox_cognito_frontend_prep.md`). Every new env will repeat it. This plan folds those steps into the existing deployment scripts so `./scripts/deploy/deploy_all.sh <env>` produces a frontend-ready environment, with credentials persisted to the env secrets file and a `.env`-style handoff file written for the frontend repo to consume.

The intended outcome: a single `deploy_all.sh sandbox` (or any new env) leaves you with a Cognito pool that already trusts the CloudFront origin, two seeded users with persisted passwords, and a generated `infra/state/<env>.frontend.handoff.env` file that the frontend repo can `cp` into `.env`.

## Approach (recommended only)

### 1. Reorder `frontend` ahead of `cognito` in `infra/infra.yaml`

`resources.frontend.config` (lines 434–441) takes only `price_class`; the CFN template has no Cognito inputs. The `${COGNITO_*}` placeholders at `infra.yaml:454-456` belong to `rag_lambda`, which still runs last. Moving `frontend` ahead of `cognito` is safe and makes the CloudFront stack output available when the cognito handler runs.

New order: `network → s3_bucket → chat_db → rag_knowledge_base → rag_lambda_ecr → frontend → cognito → rag_lambda`.

### 2. Make `scripts/deploy/deploy_cognito.sh` self-resolve the CloudFront origin

Inside the existing `deploy_stack()` function, before building the param file (~line 332), add:

```bash
cf_origin=""
fe_stack=$(get_resource_stack_name "frontend" "$ENVIRONMENT")
if aws_cmd cloudformation describe-stacks --stack-name "$fe_stack" >/dev/null 2>&1; then
    cf_domain=$(aws_cmd cloudformation describe-stacks --stack-name "$fe_stack" \
        --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDomainName'].OutputValue | [0]" \
        --output text 2>/dev/null)
    [ -n "$cf_domain" ] && [ "$cf_domain" != "None" ] && cf_origin="https://$cf_domain"
fi
```

If `cf_origin` is non-empty and `CALLBACK_URLS_OVERRIDE` is not set, append `${cf_origin}/auth/callback` and `${cf_origin}/` to the existing `$CALLBACK_URLS` / `$LOGOUT_URLS` values (CommaDelimitedList). Re-runs are idempotent because the same set is computed each time.

### 3. Add `ensure_cognito_users()` to `scripts/deploy/deploy_cognito.sh`

Called after `write_cognito_outputs_to_secrets` (around line 416). Mirrors the `yq -i` pattern already used by `write_cognito_outputs_to_secrets` (lines 231-264).

Behavior:
- Reads desired users from a new `cognito_users` block in the env secrets file. If absent, defaults to:
  ```yaml
  cognito_users:
    admin:
      username: admin@example.com
    example:
      username: user@example.com
  ```
- For each user key: if `.password` is missing in the secrets file, generate one and **write it to the secrets file before any AWS call** (idempotency on partial failures — a re-run reuses the same password).
- Password generator: `LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32`, then loop until the result contains at least one lower, one upper, one digit (regex check). No fixed prefix (avoids fingerprint).
- For each user: `aws cognito-idp admin-get-user`. If exit non-zero → `admin-create-user --message-action SUPPRESS` then `admin-set-user-password --permanent`.
- **Avoid password leak via `ps`**: write a temp JSON request to `mktemp`, `chmod 600`, pass with `--cli-input-json file://<tmp>`, `trap 'rm -f $tmp' EXIT`. Same applies to `admin-set-user-password`.
- `admin-get-user` 404 detection: gate on exit code, not stderr parsing.
- Stored under top-level `cognito_users:` block (NOT `config_secrets`) so the password is never hydrated into the Lambda runtime config.
- Never `print_info` the password itself — only "password set for <username>".

### 4. Update `clear_cognito_outputs_from_secrets()` to also clear `cognito_users.*`

Current function (lines 266-281) clears the cognito IDs on stack delete. Add lines that also reset `.cognito_users.*.password = ""` for each existing key (leave `username` so the same user identity gets recreated on next deploy). This avoids the "stale password after pool recreation" hazard called out in the design review.

### 5. New script `scripts/deploy/generate_frontend_handoff.sh <env>`

Standalone, idempotent:
- Loads infra config, resolves `<env>`.
- Reads CFN outputs from `frontend`, `cognito`, `rag_lambda` stacks (Function URL, CloudFront domain, UserPoolId, AppClientId, HostedUiDomain).
- Reads runtime values from the env secrets file `config_secrets` (already populated by deploy handlers).
- Writes `infra/state/<env>.frontend.handoff.env` atomically (`mktemp` in same dir → `mv`):

  ```bash
  # Generated by scripts/deploy/generate_frontend_handoff.sh — do not edit by hand
  # Generated: <ISO timestamp>
  NUXT_PUBLIC_CHAT_API_BASE_URL=<lambda function url>
  NUXT_PUBLIC_COGNITO_REGION=<region>
  NUXT_PUBLIC_COGNITO_USER_POOL_ID=<id>
  NUXT_PUBLIC_COGNITO_APP_CLIENT_ID=<id>
  NUXT_PUBLIC_COGNITO_DOMAIN=<hosted-ui domain>
  NUXT_PUBLIC_COGNITO_REDIRECT_URI=https://<cf>/auth/callback
  NUXT_PUBLIC_COGNITO_LOGOUT_REDIRECT_URI=https://<cf>/
  ```
- Prints summary of what was written; no secrets in stdout.

### 6. Wire `generate_frontend_handoff.sh` into `deploy_all.sh`

After the existing config-deploy block (lines 233–248), call:

```bash
handoff_script="$PROJECT_ROOT/scripts/deploy/generate_frontend_handoff.sh"
if [ -f "$handoff_script" ]; then
    "$handoff_script" "$ENVIRONMENT" || print_warning "Frontend handoff generation failed (non-fatal)"
fi
```

Non-fatal because partial deploys (`--set pr`) shouldn't error if some stacks are absent; the script itself can no-op when required stacks aren't ready.

### 7. Extend `infra/secrets/template.environment.secrets.yaml`

Add a documented top-level block (template only, gitignored real files inherit it):

```yaml
# Bootstrap Cognito users created by deploy_cognito.sh.
# Passwords are auto-generated on first deploy and persisted here.
# These are demo credentials — rotate via Cognito console for any non-sandbox use.
cognito_users:
  admin:
    username: admin@example.com
    password: ""
  example:
    username: user@example.com
    password: ""
```

### 8. Update `infra/roles/deployer_role.yaml`

Append to the `CognitoDeployment` Sid (lines 300-318) so CI deploys aren't silently broken on dev/staging/prod:

```yaml
- cognito-idp:AdminCreateUser
- cognito-idp:AdminSetUserPassword
- cognito-idp:AdminGetUser
- cognito-idp:AdminDeleteUser
```

(Skip `AdminAddUserToGroup` — see "out of scope" below.)

## Files to modify

- `infra/infra.yaml` — reorder `frontend` ahead of `cognito` (step 1).
- `scripts/deploy/deploy_cognito.sh` — CloudFront URL resolution (step 2), `ensure_cognito_users()` (step 3), update `clear_cognito_outputs_from_secrets()` (step 4).
- `scripts/deploy/generate_frontend_handoff.sh` — new file (step 5).
- `scripts/deploy/deploy_all.sh` — call the handoff script after config deploy (step 6).
- `infra/secrets/template.environment.secrets.yaml` — add `cognito_users` block (step 7).
- `infra/roles/deployer_role.yaml` — extend Cognito Sid (step 8).

## Existing utilities to reuse (do not reinvent)

- `write_cognito_outputs_to_secrets` / `clear_cognito_outputs_from_secrets` in `scripts/deploy/deploy_cognito.sh:231-281` — yq pattern for in-place secrets edits.
- `get_environment_secrets_file` in `scripts/utils/config_parser.sh` — already used by every handler that touches secrets.
- `get_resource_stack_name` / `aws_cmd` already in `deploy_cognito.sh` — reuse for the frontend stack lookup.
- `print_info` / `print_warning` / `print_complete` from `scripts/utils/common.sh` — established log style; never echo passwords.
- `infra/state/.gitignore` already ignores everything — handoff file lands there with no .gitignore changes.

## Out of scope (explicitly deferred)

- **AWS Secrets Manager for Cognito user passwords.** The DB master password precedent uses gitignored secrets files; this matches.
- **User groups / `AdminAddUserToGroup`.** The current `cognito_template.yaml` declares no `Cognito::UserPoolGroup`, so a `group:` field has nowhere to land. If the frontend later needs admin-vs-user gating, add a UserPoolGroup resource and re-introduce the `group` field together.
- **Email verification / hosted-UI signup flow.** `AutoVerifiedAttributes` only kicks in for prod (`cognito_template.yaml:63`); sandbox/dev users are admin-created with `email_verified=true`.
- **Rotating existing user passwords.** Re-running the deploy reuses persisted passwords. Rotation is a separate workflow.
- **Two-phase cognito redeploy** (the design-review alternative). The reorder makes it unnecessary.

## Verification

After implementation, end-to-end test in sandbox:

1. **Idempotent re-run on existing sandbox**:
   ```bash
   ./scripts/deploy/resources/cognito.sh sandbox deploy -y
   ```
   Expect: callback URLs now include `https://<cf-domain>/auth/callback` alongside localhost; user pool unchanged; no new users created (admin-get-user returns existing); secrets file passwords unchanged.

2. **Inspect persisted state**:
   ```bash
   yq '.cognito_users' infra/secrets/adte_sandbox/sandbox.secrets.yaml
   cat infra/state/sandbox.frontend.handoff.env
   ```
   Expect: two users with non-empty passwords; handoff file with all 7 `NUXT_PUBLIC_*` keys populated and no empty values.

3. **End-to-end auth using the persisted credentials** (admin path since `USER_PASSWORD_AUTH` is disabled):
   ```bash
   USER_POOL_ID=$(yq -r '.config_secrets.COGNITO_USER_POOL_ID' infra/secrets/adte_sandbox/sandbox.secrets.yaml)
   CLIENT_ID=$(yq -r '.config_secrets.COGNITO_APP_CLIENT_ID' infra/secrets/adte_sandbox/sandbox.secrets.yaml)
   USER=$(yq -r '.cognito_users.admin.username' infra/secrets/adte_sandbox/sandbox.secrets.yaml)
   PASS=$(yq -r '.cognito_users.admin.password' infra/secrets/adte_sandbox/sandbox.secrets.yaml)

   aws cognito-idp admin-initiate-auth \
     --user-pool-id "$USER_POOL_ID" --client-id "$CLIENT_ID" \
     --auth-flow ADMIN_USER_PASSWORD_AUTH \
     --auth-parameters USERNAME="$USER",PASSWORD="$PASS" \
     --profile adte_sandbox_management_account --region us-east-1
   ```
   Expect: returns `AuthenticationResult.IdToken`. Use that token as `Authorization: Bearer <id-token>` against the Lambda Function URL — expect `200`.

4. **Process listing safety** — while a deploy runs in another shell, check `ps auxww | grep -i password` from this shell. Expect: no password values visible (the temp JSON file path will appear, not the password).

5. **Teardown safety**:
   ```bash
   ./scripts/deploy/resources/cognito.sh sandbox delete -y
   yq '.cognito_users' infra/secrets/adte_sandbox/sandbox.secrets.yaml
   ```
   Expect: passwords cleared back to `""`, usernames retained.

6. **Fresh-env walkthrough** (no real new account needed — dry-run): point a temp env name at the same management account in `infra.yaml`, run `deploy_all.sh <new-env> --dry-run`. Expect order shows `frontend` before `cognito`, no missing handlers.
