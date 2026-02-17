# GitHub OIDC Identity Provider Setup (AWS Console)

This guide is for **manually** creating the GitHub OIDC identity provider in AWS (e.g. when `setup_oidc_provider.sh` is not used or fails). It explains how to create the GitHub Actions OpenID Connect (OIDC) identity provider in AWS IAM via the console.

**Prerequisite for evals role:** The GitHub OIDC identity provider must be created **before** running [scripts/deploy/deploy_evals_github_action_role.sh](../scripts/deploy/deploy_evals_github_action_role.sh). That script requires the provider ARN via `--oidc-provider-arn`. Create the provider once per AWS account; then deploy the evals role for each environment (dev, staging, prod) using the same provider ARN.

---

## Prerequisites

- AWS account with permission to create IAM identity providers (`iam:CreateOpenIDConnectProvider`, `iam:TagOpenIDConnectProvider`).
- You will need the **provider ARN** after creation; it is required when deploying the evals role via `deploy_evals_github_action_role.sh`.

---

## Steps

### 1. Open IAM Identity Providers

1. Sign in to the [AWS Management Console](https://console.aws.amazon.com/).
2. Open **IAM** (Identity and Access Management).
3. In the left navigation, under **Access management**, choose **Identity providers**.
4. Click **Add provider**.

### 2. Choose Provider Type

1. Select **OpenID Connect**.
2. Click **Next**.

### 3. Configure the Provider

Use these values so the provider matches what the evals GitHub Action role expects:

| Field | Value |
|-------|--------|
| **Provider URL** | `https://token.actions.githubusercontent.com` |
| **Audience** | `sts.amazonaws.com` |

**Provider URL notes:**

- Do **not** include `https://` in the URL if the console has a separate protocol selector; use only `token.actions.githubusercontent.com` if the form adds `https://` for you.
- Some consoles show “Provider URL” and expect the full URL; use `https://token.actions.githubusercontent.com` in that case.

**Thumbprint:**

- The console may **retrieve the thumbprint automatically** after you enter the provider URL. If it does, leave the default.
- If you must enter a thumbprint manually, use one of these (GitHub’s current and previous thumbprints):
  - `6938fd4d98bab03faadb97b34396831e3780aea1`
  - `1c58a3a8518e8759bf075b76b750d4f2df264fcd`

Click **Next**.

### 4. (Optional) Add Tags

For consistency with the template-created provider, you can add:

| Key | Value |
|-----|--------|
| **Name** | `GitHubActionsOIDCProvider` |

Click **Add provider**.

### 5. Note the Provider ARN

After creation, the provider ARN will look like:

```text
arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com
```

Use this ARN when deploying the evals GitHub Action role with an existing provider:

```bash
./scripts/deploy/deploy_evals_github_action_role.sh dev deploy \
  --oidc-provider-arn arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com
```

Replace `ACCOUNT_ID` with your AWS account ID.

---

## Verify

1. In IAM, go to **Identity providers**.
2. Open the provider whose URL is `token.actions.githubusercontent.com`.
3. Confirm **Audience** is `sts.amazonaws.com` and the thumbprint is set.

---

## Related

- [GitHub Actions OIDC with AWS](https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services) (GitHub Docs)
- [infra/README.md – GitHub Actions OIDC Setup](../infra/README.md#github-actions-oidc-setup)
- [infra/roles/README.md – GitHub Actions Role](../infra/roles/README.md)
