# Frontend Interface Guide (Vue + RAG Lambda)

This document is the frontend handoff for building UI clients against the RAG backend.
It is written for frontend developers (especially Vue SPA teams) so implementation can proceed without reading backend source files.
This is the canonical interface document for frontend/backend integration.

Related references:
- [Deployment Process](./deployment_process.md)

## 1. System Interface Map

```text
User Browser (Vue SPA)
  -> Cognito Hosted UI / OAuth (Authorization Code + PKCE)
  -> Receives JWTs (ID/Access token)
  -> Calls Lambda Function URL with Bearer token
  -> RAG Lambda validates JWT, runs retrieval/generation, returns answer
```

Components:
- Cognito User Pool: authenticates end users and issues JWTs.
- Vue frontend: obtains token, sends chat requests, renders responses.
- Lambda Function URL: HTTPS endpoint exposed for backend access.
- RAG Lambda: enforces auth, scopes conversation storage by authenticated user, runs RAG pipeline.

## 2. Environment Configuration

Use env vars per frontend environment:

| Variable | Dev | Staging | Prod | Notes |
|---|---|---|---|---|
| `VITE_CHAT_API_BASE_URL` | Lambda Function URL (dev) | Lambda Function URL (staging) | Lambda Function URL (prod) | Base URL for chat POST calls |
| `VITE_COGNITO_REGION` | Required | Required | Required | Usually `us-east-1` |
| `VITE_COGNITO_USER_POOL_ID` | Required | Required | Required | User pool for this environment |
| `VITE_COGNITO_APP_CLIENT_ID` | Required | Required | Required | App client used by SPA |
| `VITE_ALLOWED_API_ORIGIN` | Frontend origin | Frontend origin | Frontend origin | Must be in backend CORS allowlist |
| `VITE_CHAT_TIMEOUT_MS` | `65000` recommended | `65000` recommended | `65000` recommended | Requests may exceed 30s |

## 3. Authentication Flow (Authorization Code + PKCE)

1. User clicks Sign In.
2. Frontend redirects to Cognito Hosted UI with PKCE challenge.
3. Cognito redirects back with auth code.
4. Frontend exchanges code + PKCE verifier for tokens.
5. Frontend stores tokens in memory/session-safe storage.
6. Frontend sends `Authorization: Bearer <JWT>` on API calls.
7. On token expiry, refresh token flow obtains new access/id token.
8. On logout, clear tokens locally and call Cognito logout endpoint.

Token handling notes:
- Prefer in-memory storage for access tokens where feasible.
- If persistence is needed, prefer `sessionStorage` over `localStorage` for short-lived access tokens.
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
| `answer` | string | Assistant response text |
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
- `401`: prompt re-login.
- `403`: show permission/identity mismatch message.
- `500`: show generic “try again” and allow retry.

Long-running request guidance:
- Show loading state after submit.
- Keep request timeout >= 60s.
- Support user cancellation (`AbortController`).

## 6. CORS Requirements

CORS is configured on the Lambda Function URL.

Frontend requirements:
- Frontend origin must be in backend allowlist.
- Requests must send `Content-Type` and `Authorization` headers.
- Browser clients will block disallowed origins even if backend is reachable.

## 7. Frontend Security Requirements

1. Treat token identity as source of truth; never trust client-computed identity alone.
2. Do not rely on `user_id` request field for authorization logic.
3. Redact tokens and sensitive payloads from logs/telemetry.
4. Keep CORS origins strict per environment.
5. Use HTTPS-only endpoints.

## 8. Vue Implementation Blueprint

### 8.1 `apiClient` service

Responsibilities:
- Inject bearer token.
- POST JSON payloads.
- Normalize error handling.
- Support cancellation and timeout.

### 8.2 `useChat()` composable

Recommended shape:
- state: `messages`, `isLoading`, `error`, `conversationId`.
- actions: `sendMessage(text)`, `resetConversation()`, `cancelRequest()`.
- behavior: optimistic append user message, append assistant on success, surface normalized error on failure.

### 8.3 Optional Pinia store

Use Pinia if multiple routes/components share chat state.
Suggested store domains:
- auth token/session state.
- chat thread state.
- UI preferences.

### 8.4 Route guards and auth bootstrap

- Guard protected routes by token/session validity.
- On app boot, restore session then fetch/refresh token if needed.
- Redirect to login when token unavailable/expired and refresh fails.

### 8.5 Cancellation + request state

- Create `AbortController` per request.
- Disable submit during inflight request.
- Allow user cancel to free UI and network resources.

## 9. QA Checklist for Frontend Team

1. Auth success path: login -> token -> successful chat.
2. Auth failure path: missing/expired token -> `401` handling and re-login prompt.
3. Conversation continuity: same `conversation_id` preserves thread context.
4. User isolation: different users with same `conversation_id` do not leak context.
5. CORS: frontend origin allowed; blocked origins fail as expected.
6. Non-200 handling: each status mapped to clear UX.
7. Latency behavior: spinner, timeout, cancel, and retry flows work.
8. Source rendering: citations/sources display safely and consistently.

## 10. Troubleshooting: "Forbidden" (Function URL authorization)

If the browser or devtools shows a **403 Forbidden** with a response body like:

```json
{
  "Message": "Forbidden. For troubleshooting Function URL authorization issues, see: https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html"
}
```

that response comes from **AWS Lambda’s Function URL** layer, not from the RAG Lambda code. Three things must be correct:

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

## 11. Backend Expectations

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
