# Frontend Interface Guide (Nuxt 4 + RAG Lambda)

This document is the frontend handoff for building UI clients against the RAG backend.
It is written for frontend developers working on the Nuxt 4 chat application so implementation can proceed without reading backend source files.
This is the canonical interface document for frontend/backend integration.

Related references:
- [Deployment Process](./deployment_process.md)

## 1. System Interface Map

```text
User Browser (Nuxt 4 SPA / SSG)
  -> Signs in with email/password via Cognito SRP (in-app login form)
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

Configuration files:
- `.env` — local development (Nuxt loads this by default)
- `.env.sandbox` — sandbox CloudFront deployment
- `.env.staging` — staging CloudFront deployment
- `.env.prod` — production CloudFront deployment
- `.env.example` — template with placeholder values

### Backend-generated handoff file

Backend deployment (`scripts/deploy/deploy_all.sh <env>`) writes a `.env`-style handoff file containing all `NUXT_PUBLIC_*` values for the deployed environment to:

```text
infra/state/<env>.frontend.handoff.env
```

That path is gitignored (only `.gitignore` is tracked under `infra/state/`). The frontend repo can copy it directly:

```bash
cp /path/to/chat-template/infra/state/staging.frontend.handoff.env .env.staging
```

The file is regenerated atomically on every successful `deploy_all.sh` run, and can also be regenerated standalone via `./scripts/deploy/generate_frontend_handoff.sh <env>`.

## 3. Authentication Flow (Cognito SRP — direct email/password)

1. User visits a protected route.
2. Nuxt route middleware (`middleware/auth.ts`) checks `useAuth()` composable state.
3. If not authenticated, middleware redirects to `/login`.
4. User enters email and password on the login page.
5. `useAuth().login(email, password)` authenticates via Cognito SRP (Secure Remote Password).
6. On success, tokens are stored in `localStorage` by `amazon-cognito-identity-js`.
7. `useAuth().getAccessToken()` returns the current token, refreshing if within 60s of expiry.
8. Chat composable includes `Authorization: Bearer <JWT>` on API calls.
9. On 401 response, composable retries once after `useAuth().refreshIfNeeded(true)`.
10. On logout, `useAuth().logout()` signs out of Cognito and clears local state.

Key files:
- `composables/useAuth.ts` — auth state management wrapping `amazon-cognito-identity-js`
- `plugins/auth.client.ts` — bootstraps auth on app initialization (client-only)
- `middleware/auth.ts` — route protection (redirects unauthenticated users to `/login`)
- `pages/login.vue` — sign-in page with email/password form

Token handling notes:
- Tokens are stored in `localStorage` by `amazon-cognito-identity-js` (its default store).
- The SDK handles automatic token refresh via the refresh token.
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

## 7. CloudFront Frontend Setup

The backend repo's `scripts/deploy/deploy_all.sh <env>` provisions everything the frontend needs and writes the resulting values to a handoff file. Most of the previous manual runbook is now automated — what remains is consuming the handoff file from the frontend repo and shipping the static assets.

### 7.1 What `deploy_all.sh` does for the frontend

In addition to the backend resources, a successful run produces:

1. **CloudFront distribution** (`frontend` stack) — S3 bucket + CloudFront for the Nuxt static output.
2. **Cognito user pool** with SRP auth enabled (`ALLOW_USER_SRP_AUTH`). The frontend uses direct email/password login — no hosted UI redirects.
3. **Bootstrap Cognito users.** Two demo users (`admin@example.com`, `user@example.com` by default) are created with auto-generated 32-char passwords. Keys, usernames, and passwords live under `cognito_users:` in `infra/secrets/<account-set>/<env>.secrets.yaml` (gitignored). Re-runs preserve passwords; on stack delete, only passwords are cleared.
4. **Lambda Function URL with auth and CORS.** The Lambda enforces Cognito JWT validation; the Function URL itself has `AuthType: NONE` so the SPA can call it directly with a `Bearer` token.
5. **Frontend handoff file.** `infra/state/<env>.frontend.handoff.env` is regenerated atomically with `NUXT_PUBLIC_*` values populated from CloudFormation outputs.

### 7.2 What you still do by hand

1. **Pick up the handoff file:**

   ```bash
   cp /path/to/chat-template/infra/state/<env>.frontend.handoff.env <frontend-repo>/.env.<env>
   ```

   The file looks like:

   ```bash
   # Generated by scripts/deploy/generate_frontend_handoff.sh — do not edit by hand
   # Generated: 2026-05-04T20:29:52Z
   # Environment: sandbox
   NUXT_PUBLIC_CHAT_API_BASE_URL=https://<lambda-id>.lambda-url.us-east-1.on.aws
   NUXT_PUBLIC_COGNITO_REGION=us-east-1
   NUXT_PUBLIC_COGNITO_USER_POOL_ID=us-east-1_xxxxxxxxx
   NUXT_PUBLIC_COGNITO_APP_CLIENT_ID=xxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

2. **Build and push frontend assets from the frontend repo.** The frontend deploy script should build the static Nuxt output, sync `.output/public/` to the frontend S3 bucket, and invalidate the CloudFront distribution.

3. **Verify the deployed site:**
   - `https://<cf-domain>` loads the SPA and shows the login form.
   - Sign in with a Cognito user (email/password).
   - Chat request returns HTTP `200` with no CORS errors in DevTools.
   - Sign out clears session and returns to the login form.

### 7.3 When to regenerate the handoff file

The handoff file is regenerated automatically by every full `deploy_all.sh <env>` run. You can also regenerate it standalone after partial deploys:

```bash
./scripts/deploy/generate_frontend_handoff.sh <env>
```

The script no-ops with a warning if any of the required upstream stacks (`frontend`, `cognito`, `rag_lambda`) are missing — useful when running `--set pr` or `--set app` deployment sets that don't touch every stack.

### 7.4 Bootstrap user credentials

The two demo users are created with `email_verified=true` and a permanent password. They sign in via the frontend's email/password form (Cognito SRP auth). Credentials are persisted under `cognito_users:` in the env secrets file:

```yaml
cognito_users:
  admin:
    username: admin@example.com
    password: <auto-generated>
  example:
    username: user@example.com
    password: <auto-generated>
```

To customise: edit `cognito_users.<key>.username` (and optionally pre-fill `password`) before deploy. To rotate a password, clear it back to `""` and re-run `deploy_cognito.sh` — the handler regenerates it. App client `ExplicitAuthFlows` is `ALLOW_USER_SRP_AUTH` + `ALLOW_REFRESH_TOKEN_AUTH` only, so non-browser CLI logins must use `admin-initiate-auth` with `ADMIN_USER_PASSWORD_AUTH` (which is currently disabled — enable in `infra/resources/cognito_template.yaml` for non-prod testing if needed).

> **Sandbox-only credentials.** These are demo accounts. Rotate via the Cognito console for any non-sandbox use, and treat the env secrets file as sensitive (it's gitignored under `infra/secrets/**/*.secrets.yaml`).

### 7.5 Re-run triggers

Re-run `deploy_cognito.sh <env> deploy -y` (or `deploy_all.sh <env>`) when:
- `cognito_users.*.username` is changed in the secrets file (new identities are created on next run; old ones are not removed).
- A user is deleted from the pool by hand and you want it recreated.

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
│   ├── auth/callback.vue    # Legacy route (redirects to /)
│   ├── settings.vue         # Model/tool preferences (protected)
│   └── library.vue          # Saved chats grid (protected)
├── components/
│   ├── AppNav.vue           # Left sidebar: brand, nav, conversations, profile
│   ├── AppContext.vue        # Right sidebar: agent steps, sources, stats
│   └── chat/
│       ├── Message.vue      # Message card: markdown, sources, reactions
│       └── Composer.vue     # Input textarea + send/stop
├── composables/
│   ├── useAuth.ts           # Cognito SRP auth via amazon-cognito-identity-js
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

Wraps `amazon-cognito-identity-js` CognitoUserPool in Nuxt-friendly patterns. Uses `useState` for SSR-safe state (client-only initialization).

Exposed:
- `user`, `isAuthenticated`, `accessToken` — reactive state
- `bootstrap()` — restore session from storage (checks for existing Cognito session)
- `login(email, password)` — authenticate via Cognito SRP
- `logout()` — sign out of Cognito and clear local state
- `getAccessToken()` — returns token, refreshing if near expiry
- `refreshIfNeeded(force?)` — explicit refresh via Cognito SDK

### 9.4 Route middleware

`middleware/auth.ts` runs before each navigation (client-only):
- Skips public routes: `/login`
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

1. Auth success path: enter email/password → sign in → token → chat page loads.
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
