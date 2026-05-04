# Frontend Interface Guide (Nuxt 4 + RAG Lambda)

This document is the frontend handoff for building UI clients against the RAG backend.
It is written for frontend developers working on the Nuxt 4 chat application so implementation can proceed without reading backend source files.
This is the canonical interface document for frontend/backend integration.

Related references:
- [Deployment Process](./deployment_process.md)

## 1. System Interface Map

```text
User Browser (Nuxt 4 SPA / SSG)
  -> Cognito Hosted UI / OAuth (Authorization Code + PKCE)
  -> Receives JWTs (ID/Access token)
  -> Posts to Nitro server route /api/chat (SSR mode)
     OR calls Lambda Function URL directly (static generation mode)
  -> Lambda validates JWT, runs retrieval/generation, returns answer
```

Components:
- Cognito User Pool: authenticates end users and issues JWTs.
- Nuxt 4 frontend: obtains token, sends chat requests, renders responses.
- Nitro server route (SSR mode): proxies `/api/chat` to Lambda, forwards auth header. Keeps Lambda URL server-only.
- Lambda Function URL: HTTPS endpoint exposed for backend access.
- RAG Lambda: enforces auth, scopes conversation storage by authenticated user, runs RAG pipeline.

### Deployment Modes

| Mode | Build command | Output | Lambda access | CORS needed |
|---|---|---|---|---|
| **Static generation** (Phase 1) | `npm run generate` | `.output/public/` → S3 + CloudFront | Browser calls Lambda directly | Yes |
| **SSR** (Phase 2) | `npm run build` | Node server (Lambda@Edge / ECS) | Nitro proxies, browser never sees Lambda URL | No |

## 2. Environment Configuration

Environment variables use Nuxt's `runtimeConfig` convention (`NUXT_` prefix for server-only, `NUXT_PUBLIC_` for client-embedded):

### Server-only (Nitro proxy — SSR mode only)

| Variable | Notes |
|---|---|
| `NUXT_CHAT_API_BASE_URL` | Lambda Function URL (never exposed to browser in SSR mode) |
| `NUXT_CHAT_API_PATH` | Path on Lambda (default `/`) |
| `NUXT_CHAT_TIMEOUT_MS` | Request timeout in ms (default `65000`) |

### Public (embedded in client bundle)

| Variable | Dev | Staging | Prod | Notes |
|---|---|---|---|---|
| `NUXT_PUBLIC_CHAT_API_BASE_URL` | Lambda URL (dev) | Lambda URL (staging) | Lambda URL (prod) | Used in static generation mode; browser calls this directly |
| `NUXT_PUBLIC_COGNITO_REGION` | Required | Required | Required | Usually `us-east-1` |
| `NUXT_PUBLIC_COGNITO_USER_POOL_ID` | Required | Required | Required | User pool for this environment |
| `NUXT_PUBLIC_COGNITO_APP_CLIENT_ID` | Required | Required | Required | App client used by SPA |
| `NUXT_PUBLIC_COGNITO_DOMAIN` | Required | Required | Required | Cognito hosted UI domain |
| `NUXT_PUBLIC_COGNITO_REDIRECT_URI` | `http://localhost:4101/auth/callback` | CloudFront URL + `/auth/callback` | CloudFront URL + `/auth/callback` | OAuth redirect target |
| `NUXT_PUBLIC_COGNITO_LOGOUT_REDIRECT_URI` | `http://localhost:4101/` | CloudFront URL | CloudFront URL | Post-logout redirect |

Configuration files:
- `.env.local` — local development
- `.env.staging` — staging deployment
- `.env.prod` — production deployment
- `.env.example` — template with placeholder values

## 3. Authentication Flow (Authorization Code + PKCE)

1. User visits a protected route.
2. Nuxt route middleware (`middleware/auth.ts`) checks `useAuth()` composable state.
3. If not authenticated, middleware redirects to `/login`.
4. User clicks Sign In → `useAuth().login()` redirects to Cognito Hosted UI with PKCE challenge.
5. Cognito redirects back to `/auth/callback` with auth code.
6. Callback page calls `useAuth().handleCallback()` → exchanges code + PKCE verifier for tokens.
7. Tokens stored in `sessionStorage` via `oidc-client-ts` `WebStorageStateStore`.
8. `useAuth().getAccessToken()` returns the current token, refreshing if within 60s of expiry.
9. Chat composable includes `Authorization: Bearer <JWT>` on API calls.
10. On 401 response, composable retries once after `useAuth().refreshIfNeeded(true)`.
11. On logout, `useAuth().logout()` calls Cognito logout endpoint and clears local state.

Key files:
- `composables/useAuth.ts` — auth state management and UserManager wrapper
- `plugins/auth.client.ts` — bootstraps auth on app initialization (client-only)
- `middleware/auth.ts` — route protection (redirects unauthenticated users to `/login`)
- `pages/login.vue` — sign-in page
- `pages/auth/callback.vue` — OAuth callback handler

Token handling notes:
- Tokens are stored in `sessionStorage` via `oidc-client-ts`.
- `automaticSilentRenew` is enabled for background token refresh.
- Do not log raw tokens.

## 4. API Contract

Endpoint:
- Method: `POST`
- Path: `/` (Function URL root)
- Optional alias supported by backend: `/chat`
- Headers:
  - `Content-Type: application/json`
  - `Authorization: Bearer <JWT>` (required in all environments)

Request body:

```json
{
  "conversation_id": "conv-abc-123",
  "message": "What is the cancellation policy?",
  "user_id": "optional-and-deprecated",
  "metadata": {},
  "retrieval_filters": null
}
```

Request field rules:
- `conversation_id` (required): conversation/thread key from client.
- `message` (required): user prompt.
- `user_id` (optional, deprecated): if provided, must match authenticated user subject.
- `metadata` (optional): arbitrary key-value map.
- `retrieval_filters` (optional): retrieval filtering object.

Request field summary:

| Field | Type | Required | Notes |
|---|---|---|---|
| `conversation_id` | string | Yes | Client conversation/thread id |
| `message` | string | Yes | User prompt |
| `user_id` | string | No | Deprecated; must match authenticated token subject if provided |
| `metadata` | object | No | Optional key-value metadata |
| `retrieval_filters` | object | No | Optional retrieval filters |

Success response (`200`):

```json
{
  "conversation_id": "conv-abc-123",
  "answer": "Free cancellation is available up to 24 hours before the appointment.",
  "sources": [
    {
      "document_id": "doc-001",
      "source_type": "document",
      "score": 0.92,
      "chunk": "Cancellation policy text ..."
    }
  ],
  "trace_id": "optional-request-id"
}
```

Success field summary:

| Field | Type | Notes |
|---|---|---|
| `conversation_id` | string | Echo of client conversation id |
| `answer` | string | Assistant response text (may contain markdown) |
| `sources` | array | Retrieved source chunks |
| `trace_id` | string/null | Optional request trace id |

Notes:
- `config` is intentionally not returned in normal responses.

## 5. Error Handling Contract

Normalized error shape:

```json
{
  "error": "ErrorCode",
  "detail": "Human-readable detail"
}
```

Expected statuses:
- `400`: invalid JSON/body schema.
- `401`: missing/invalid/expired token.
- `403`: identity mismatch or forbidden action.
- `404`: unsupported path.
- `405`: unsupported method.
- `500`: server error.

Retry guidance:
- Retryable: transient `500` (use capped backoff), network errors.
- Non-retryable: `400`, `401`, `403`, `404`, `405`.

UX guidance:
- `401`: composable retries once with refreshed token; if still 401, error is shown in assistant message.
- `403`: show permission/identity mismatch message.
- `500`: show generic "try again" and allow retry.

Long-running request guidance:
- Show loading state with simulated agent steps (sending → retrieving → generating).
- Keep request timeout >= 60s.
- Support user cancellation (`AbortController`).

## 6. CORS Requirements

### Static generation mode (Phase 1)

CORS is configured on the Lambda Function URL. The browser calls Lambda directly.

Frontend requirements:
- Frontend origin must be in backend allowlist.
- Requests must send `Content-Type` and `Authorization` headers.
- Browser clients will block disallowed origins even if backend is reachable.

### SSR mode (Phase 2)

CORS is not needed between browser and Lambda. The browser posts to `/api/chat` on the same origin, and the Nitro server route proxies to Lambda server-side.

## 7. Manual CloudFront Frontend Setup

This runbook keeps the deployed frontend and backend aligned when the frontend is hosted as static assets in S3 + CloudFront and the backend is exposed through a Lambda Function URL with Cognito auth. It applies to any environment defined in `infra/infra.yaml`; test changes in a non-prod environment first.

### 7.1 Required values

From the backend deployment:
- Lambda Function URL: used by `NUXT_PUBLIC_CHAT_API_BASE_URL` in static generation mode.
- Cognito region, user pool ID, app client ID, and hosted UI domain.

From the frontend infrastructure deployment:
- CloudFront domain, for example `d123abc.cloudfront.net`.

### 7.2 Manual deployment checklist

1. Deploy frontend infrastructure from the backend repo:

   ```bash
   ./scripts/deploy/deploy_frontend.sh <env> deploy -y
   ```

   This creates or updates the S3 bucket and CloudFront distribution. It does not build or upload the Nuxt app. You can also run `./scripts/deploy/deploy_all.sh <env> --set app -y` if the `frontend` resource is included in the selected deployment set.

2. Capture the CloudFront URL from script output, or query the stack:

   ```bash
   aws cloudformation describe-stacks \
     --stack-name chat-template-frontend-<env> \
     --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontDomainName`].OutputValue' \
     --output text
   ```

   Full URL format:

   ```text
   https://<CloudFrontDomainName>
   ```

3. Update backend environment secrets in `chat-template`:

   ```yaml
   config_secrets:
     COGNITO_CALLBACK_URLS: "https://<cloudfront-domain>/auth/callback"
     COGNITO_LOGOUT_URLS: "https://<cloudfront-domain>/"
     FUNCTION_URL_CORS_ALLOW_ORIGINS: "https://<cloudfront-domain>"
   ```

   Keep the Cognito runtime values present as needed:

   ```yaml
   config_secrets:
     COGNITO_REGION: "us-east-1"
     COGNITO_USER_POOL_ID: "us-east-1_xxxxx"
     COGNITO_APP_CLIENT_ID: "xxxxxxxx"
   ```

   For CI/CD-enabled environments, sync updated secrets to GitHub:

   ```bash
   ./scripts/setup/setup_github.sh --deploy-secrets <env>
   ```

4. Redeploy Cognito if callback/logout URLs changed:

   ```bash
   ./scripts/deploy/deploy_cognito.sh <env> deploy -y
   ```

5. Redeploy Lambda so Function URL CORS allows the CloudFront origin:

   ```bash
   ./scripts/deploy/deploy_rag_lambda.sh <env> deploy -y --skip-build
   ```

   Or use the app deployment set when you want Cognito, frontend infrastructure, and Lambda refreshed together:

   ```bash
   ./scripts/deploy/deploy_all.sh <env> --set app -y
   ```

6. Build and push frontend assets from the frontend repo:

   ```bash
   export AWS_REGION=us-east-1
   export NUXT_PUBLIC_CHAT_API_BASE_URL=https://<lambda-id>.lambda-url.us-east-1.on.aws
   export NUXT_PUBLIC_COGNITO_REGION=us-east-1
   export NUXT_PUBLIC_COGNITO_USER_POOL_ID=us-east-1_xxxxx
   export NUXT_PUBLIC_COGNITO_APP_CLIENT_ID=xxxxxxxx
   export NUXT_PUBLIC_COGNITO_DOMAIN=<your-domain>.auth.us-east-1.amazoncognito.com
   export NUXT_PUBLIC_COGNITO_REDIRECT_URI=https://<cloudfront-domain>/auth/callback
   export NUXT_PUBLIC_COGNITO_LOGOUT_REDIRECT_URI=https://<cloudfront-domain>/

   ./infrastructure/scripts/deploy.sh <env>
   ```

   The frontend deploy script should build the static Nuxt output, sync `.output/public/` to the frontend S3 bucket, and invalidate CloudFront. The frontend stack must already exist, and AWS credentials must target the same environment account.

7. Verify the deployed site:
   - Open `https://<cloudfront-domain>`.
   - Login redirects to Cognito and returns to `/auth/callback` on the CloudFront domain.
   - Chat returns HTTP `200`.
   - Browser devtools show no CORS errors for chat requests.
   - Logout returns to the CloudFront root URL.

Re-run this setup whenever the CloudFront stack is recreated, the frontend domain changes, Cognito callback/logout URLs change, or the backend Function URL CORS allowlist needs to include a new origin.

## 8. Frontend Security Requirements

1. Treat token identity as source of truth; never trust client-computed identity alone.
2. Do not rely on `user_id` request field for authorization logic.
3. Redact tokens and sensitive payloads from logs/telemetry.
4. Keep CORS origins strict per environment (static generation mode).
5. Use HTTPS-only endpoints.
6. In SSR mode, Lambda URL is server-only (`runtimeConfig` private key) — never embedded in client bundle.

## 9. Nuxt 4 Implementation Architecture

### 9.1 Project structure

```
chat-template-frontend/
├── nuxt.config.ts           # Nuxt config, runtimeConfig, modules
├── app.vue                  # Root: NuxtLayout + NuxtPage
├── layouts/default.vue      # Three-column shell: AppNav + slot + AppContext
├── pages/
│   ├── index.vue            # Chat thread (protected)
│   ├── login.vue            # Sign-in page (public, no layout)
│   ├── auth/callback.vue    # OAuth callback (public, no layout)
│   ├── settings.vue         # Model/tool preferences (protected)
│   └── library.vue          # Saved chats grid (protected)
├── components/
│   ├── AppNav.vue           # Left sidebar: brand, nav, conversations, profile
│   ├── AppContext.vue        # Right sidebar: agent steps, sources, stats
│   └── chat/
│       ├── Message.vue      # Message card: markdown, sources, reactions
│       └── Composer.vue     # Input textarea + send/stop
├── composables/
│   ├── useAuth.ts           # Cognito OIDC via oidc-client-ts
│   ├── useChat.ts           # Chat state, API calls, source mapping
│   ├── useConversations.ts  # Conversation list (localStorage-synced)
│   └── useChatSettings.ts   # Model/tool selection (localStorage-synced)
├── plugins/
│   └── auth.client.ts       # Bootstrap auth on app init
├── middleware/
│   └── auth.ts              # Route protection
├── server/api/
│   └── chat.post.ts         # Nitro proxy to Lambda
├── assets/css/main.css      # Aurora design system (1450+ lines)
└── infrastructure/
    └── scripts/deploy.sh    # Build + S3 sync + CloudFront invalidation
```

### 9.2 `useChat()` composable

State (via `useState` — SSR-safe, shared across components):
- `messages` — array of `ChatMessage` objects
- `isStreaming` — whether a request is in flight
- `conversationId` — per-session, persisted in `sessionStorage`
- `liveSources` / `liveSteps` — mirrored to the right sidebar panel

Actions:
- `send(text)` — append user message, post to `/api/chat`, append assistant response with mapped sources
- `stop()` — abort in-flight request via `AbortController`
- `reset()` — clear messages and generate new conversation ID
- `toggleReaction(id, value)` — toggle thumbs up/down on a message

Source mapping (Lambda → UI):

| Lambda field | UI field |
|---|---|
| `document_id` | `title` |
| `chunk` | `excerpt` |
| `source_type` | `sourceType` (displayed as metadata) |
| `score` | `score` (displayed as metadata) |
| — | `url` (empty string — KB sources have no URL) |

### 9.3 `useAuth()` composable

Replaces the previous Pinia auth store. Uses `useState` for SSR-safe state and wraps `oidc-client-ts` UserManager (client-only).

Exposed:
- `user`, `isAuthenticated`, `accessToken` — reactive state
- `bootstrap()` — restore session from storage
- `login(redirectPath?)` — redirect to Cognito
- `handleCallback()` — exchange auth code for tokens
- `logout()` — Cognito logout + clear local state
- `getAccessToken()` — returns token, refreshing if near expiry
- `refreshIfNeeded(force?)` — explicit refresh

### 9.4 Route middleware

`middleware/auth.ts` runs before each navigation (client-only):
- Skips public routes: `/login`, `/auth/callback`
- Bootstraps auth if not yet bootstrapped
- Refreshes token if authenticated
- Redirects to `/login` if not authenticated

### 9.5 Server route (`server/api/chat.post.ts`)

In SSR mode, proxies chat requests to Lambda:
- Reads `Authorization` header from client request
- Translates client field names (`conversationId` → `conversation_id`)
- Forwards to Lambda Function URL (from private `runtimeConfig`)
- Returns Lambda JSON response
- Maps Lambda error responses to Nuxt `createError`

In static generation mode, this route is not available at runtime. The `useChat` composable calls Lambda directly.

## 10. QA Checklist for Frontend Team

1. Auth success path: login → Cognito redirect → callback → token → chat page loads.
2. Auth failure path: expired token → 401 → auto-refresh → retry; if refresh fails → redirect to login.
3. Conversation continuity: same `conversation_id` preserves thread context across messages.
4. User isolation: different users with same `conversation_id` do not leak context.
5. CORS (static mode): frontend origin allowed; blocked origins fail as expected.
6. Non-200 handling: each status mapped to clear UX (error shown in assistant message).
7. Latency behavior: agent step indicators (sending → retrieving → generating), cancel flow works.
8. Source rendering: sources display `document_id`, `chunk`, `source_type`, and `score` safely.
9. Responsive: three-column layout at desktop → two-column at 1180px → single column at 800px.
10. Sign out: clears session, redirects to login.

## 11. Troubleshooting: "Forbidden" (Function URL authorization)

If the browser or devtools shows a **403 Forbidden** with a response body like:

```json
{
  "Message": "Forbidden. For troubleshooting Function URL authorization issues, see: https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html"
}
```

that response comes from **AWS Lambda's Function URL** layer, not from the RAG Lambda code. Three things must be correct:

1. **Function URL AuthType** must be **NONE** (so AWS does not require SigV4). If it is **AWS_IAM**, AWS rejects the request before it reaches the Lambda.
2. **Resource-based policy** must allow URL API invoke: a statement with `Principal: "*"`, `Action: lambda:InvokeFunctionUrl`, and `FunctionUrlAuthType: NONE`.
3. **Resource-based policy** must allow function invoke via URL: a statement with `Principal: "*"`, `Action: lambda:InvokeFunction`, and `InvokedViaFunctionUrl: true`.

**Intended setup:** The browser calls the URL with no SigV4; the RAG Lambda then enforces auth by validating the Cognito JWT in the `Authorization: Bearer <token>` header.

**Fix (recommended): run the fix script from the repo root:**

```bash
./scripts/deploy/fix_lambda_function_url_auth.sh staging us-east-1
```

This script prints the current URL config and policy, sets the Function URL AuthType to NONE, and adds the public-invoke permission if missing. Use your target environment and region (e.g. `staging`, `us-east-1`).

**Fix (manual):** Run both commands (replace function name and region if needed):

```bash
aws lambda update-function-url-config --function-name chat-template-staging-rag-chat --auth-type NONE --region us-east-1
aws lambda add-permission --function-name chat-template-staging-rag-chat --statement-id FunctionURLPublicInvoke --action lambda:InvokeFunctionUrl --principal "*" --function-url-auth-type NONE --region us-east-1
aws lambda add-permission --function-name chat-template-staging-rag-chat --statement-id FunctionURLPublicInvokeFunction --action lambda:InvokeFunction --principal "*" --invoked-via-function-url --region us-east-1
```

If your AWS CLI does not support `--invoked-via-function-url`, use the repo fix script (it has a boto3 fallback) or upgrade AWS CLI.

**Verify:** To see what is currently set:

```bash
aws lambda get-function-url-config --function-name chat-template-staging-rag-chat --region us-east-1
aws lambda get-policy --function-name chat-template-staging-rag-chat --region us-east-1
```

**Future deploys:** The RAG Lambda deploy script now runs `update-function-url-config --auth-type NONE` and both required permission statements (`InvokeFunctionUrl` and `InvokeFunction` via URL) after every stack create/update.

After both are correct, 401/403 responses will come from the RAG Lambda (e.g. missing/invalid JWT), with a JSON body like `{"error": "MissingAuthorization", "detail": "..."}` instead of the AWS Message above.

## 12. Backend Expectations

Stable promises for frontend:
- Single chat endpoint (`POST /` with optional `/chat` alias).
- JSON request/response contract above.
- Normalized JSON error bodies for expected failure modes.
- Auth enforced by bearer JWT in all environments.

Known limits:
- Responses are synchronous (no streaming in current version).
- Request duration can be long for some RAG queries.
- Rate limiting and WAF are not in initial rollout.

Change management for new fields/endpoints:
- Backend changes should be additive when possible.
- Deprecations should be documented in this file and release notes.
- Frontend should ignore unknown response fields safely.
