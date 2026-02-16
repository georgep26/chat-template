# AWS Organizations & Sub-Accounts – Consolidated Learnings

This page consolidates the key concepts and practical takeaways about **AWS Organizations**, **sub-accounts**, **billing**, and **Free Tier behavior**.

---

## 1. What AWS Organizations Is

AWS Organizations lets you centrally manage multiple AWS accounts under a single organizational structure.

Core goals:
- Strong security isolation
- Scalable governance
- Centralized billing
- Clear ownership boundaries

Each AWS **account** is a hard isolation boundary for IAM, resources, limits, and failures.

> “Using multiple accounts helps isolate workloads and control blast radius.” — AWS documentation

---

## 2. Key Roles and Structure

### Management Account (formerly payer account)
- Owns the organization
- Pays the AWS bill for **all** member accounts
- Controls organization-wide settings and policies

### Member (Sub) Accounts
- Independent environments for workloads
- Own IAM users, roles, and resources
- **Do not** pay AWS directly when part of an organization

### Organizational Units (OUs)
- Logical groupings of accounts (e.g., `dev`, `prod`, `security`)
- Used to apply policies consistently

---

## 3. Why Use Organizations & Sub-Accounts

### 3.1 Security & Blast Radius Reduction
- Compromises or misconfigurations stay within one account
- Organization-wide guardrails enforced with Service Control Policies (SCPs)

> “An SCP defines the maximum permissions available in member accounts.” — AWS documentation

---

### 3.2 Environment Separation
- Common pattern: one account per environment (`dev`, `qa`, `prod`)
- Eliminates risk of accidentally impacting production

---

### 3.3 Centralized Governance
Central control over:
- Allowed AWS regions
- Restricted services
- Root-level permissions

---

### 3.4 Scalable Team Ownership
- Teams own their accounts
- No shared credentials
- Centralized access via IAM Identity Center (SSO)

---

### 3.5 Compliance & Audits
- Dedicated accounts for:
  - Logging
  - Security tooling
  - Compliance workloads
- Account boundaries simplify audits (SOC 2, ISO, PCI, etc.)

---

## 4. How Billing Works with Sub-Accounts

### 4.1 Consolidated Billing
- AWS generates **one bill** for the entire organization
- The management account’s payment method is charged
- Member accounts cannot pay AWS directly

> “Consolidated billing enables you to combine usage and share volume discounts.” — AWS documentation

---

### 4.2 Discounts & Savings
Usage is aggregated across accounts for:
- Tiered pricing (e.g., S3)
- Savings Plans
- Reserved Instances

---

### 4.3 Cost Visibility
Even though payment is centralized, you still get:
- Per-account cost breakdowns
- Cost Explorer filtering by account
- Cost & Usage Reports (CUR)

---

### 4.4 Separate Credit Cards per Account
- **Not supported** under consolidated billing
- Member accounts cannot use their own credit cards for AWS charges

> “All charges are billed to the management account.” — AWS documentation

---

## 5. Can Sub-Accounts Pay Their Own Bills?

**No**, not while they are members of the same AWS Organization.

Options if separate payment is required:
- Remove the account from the organization
- Use separate AWS Organizations (each with its own management account)

Invoice configuration can create **separate invoices**, but payment responsibility still stays with the management account.

---

## 6. Free Tier Behavior with Organizations

AWS Free Tier behavior depends on the type of Free Tier.

---

### 6.1 Always Free
Examples:
- Lambda (1M requests/month)
- DynamoDB (25 GB storage)
- S3 (5 GB standard storage)

Behavior:
- **Per account**
- Each sub-account gets its own Always Free quota

> “Always Free offers are available to all AWS customers and do not expire.” — AWS documentation

---

### 6.2 12-Month Free Tier
Examples:
- EC2 (750 hours/month)
- RDS (750 hours/month)

Behavior:
- **Per account**, based on account creation date
- Each new sub-account gets its own 12-month clock

> “The 12-month Free Tier begins when you create your AWS account.” — AWS documentation

---

### 6.3 Free Trials & Promotional Credits
Examples:
- Bedrock credits
- Certain analytics or ML services

Behavior:
- Often **per organization or per billing entity**
- First account to use the trial may consume it for the entire org

> “Some free trials are limited to one per customer.” — AWS documentation

---

## 7. Do Sub-Accounts Burn Free Tier Faster?

- **Always Free**: ❌ No (per-account quotas)
- **12-month Free Tier**: ❌ No (per-account age)
- **Free trials**: ⚠️ Possibly (often shared)

Multiple sub-accounts generally **increase** total Free Tier capacity rather than reducing it.

---

## 8. Recommended Best Practices

- Use one account per environment or major workload
- Apply SCPs early (especially in production)
- Set per-account AWS Budgets with low-dollar alerts
- Centralize logs and security tooling in dedicated accounts
- Treat accounts as long-lived boundaries, not temporary constructs

---

## 9. Mental Model

- **Account** = security & failure boundary
- **Organization** = governance + billing wrapper
- **OU** = policy scope
- **SCPs** = guardrails, not permissions
- **Management account** = finance + control plane

---

## 10. Bottom Line

AWS Organizations is designed to let you:
- Scale teams without scaling risk
- Centralize payment without centralizing mistakes
- Enforce rules *before* incidents happen
- Align cloud structure with real organizational structure

It is optimized for **growth, safety, and governance**, not minimal setups.