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
     OR calls RAG Lambda Function URL directly (static generation mode)
  -> RAG Lambda validates JWT, runs retrieval/generation, persists turn, returns answer
  -> History API Lambda (separate Function URL) serves /conversations list + reads
     + rename/delete from the same Aurora tables
  -> Library API Lambda (separate Function URL) serves /documents list + detail
     from the document_catalog tables
```

Components:
- Cognito User Pool: authenticates end users and issues JWTs.
- Nuxt 4 frontend: obtains token, sends chat requests, renders responses, manages conversation list/history via the History API, lists the knowledge-base catalog via the Library API.
- Nitro server route (SSR mode): proxies `/api/chat`, `/api/history/*`, and `/api/library/*` to the corresponding Lambdas, forwards auth header. Keeps Lambda URLs server-only.
- RAG Lambda: enforces auth, scopes conversation storage by authenticated user, runs RAG pipeline. Exposed via its own Function URL.
- History API Lambda: enforces auth, exposes per-user `/conversations` list/get-messages/rename/delete endpoints. Exposed via its own Function URL.
- Library API Lambda: enforces auth, exposes read-only `/documents` list and detail endpoints for the document catalog. Exposed via its own Function URL.
- The chat Lambdas read/write the same Aurora `chat_conversations` / `chat_history` tables scoped by `(environment, user_id)` from the JWT subject. The Library API reads the `document_catalog` / `document_chunks` tables scoped by `tenant_id` (from JWT custom claim or the configured default).

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
| `NUXT_HISTORY_API_BASE_URL` | History API Lambda Function URL |
| `NUXT_HISTORY_API_TIMEOUT_MS` | History API request timeout (default `15000`) |
| `NUXT_LIBRARY_API_BASE_URL` | Library API Lambda Function URL |
| `NUXT_LIBRARY_API_TIMEOUT_MS` | Library API request timeout (default `15000`) |

### Public (embedded in client bundle)

| Variable | Dev | Staging | Prod | Notes |
|---|---|---|---|---|
| `NUXT_PUBLIC_CHAT_API_BASE_URL` | Lambda URL (dev) | Lambda URL (staging) | Lambda URL (prod) | RAG Lambda URL; used in static generation mode; browser calls this directly |
| `NUXT_PUBLIC_HISTORY_API_BASE_URL` | History API URL (dev) | History API URL (staging) | History API URL (prod) | History API Lambda URL for `/conversations` endpoints (list, get messages, rename, delete) |
| `NUXT_PUBLIC_LIBRARY_API_BASE_URL` | Library API URL (dev) | Library API URL (staging) | Library API URL (prod) | Library API Lambda URL for `/documents` endpoints (list, detail) |
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
  "conversation_id": "optional-existing-conversation-uuid",
  "request_id": "optional-client-generated-idempotency-key",
  "message": "What is the cancellation policy?",
  "user_id": "optional-and-deprecated",
  "metadata": {},
  "retrieval_filters": null
}
```

Request field rules:
- `conversation_id` (optional): omit for the first message in a new conversation. The backend generates an opaque UUID and returns it. Include the returned value on follow-up messages.
- `request_id` (optional): client-generated idempotency key for a send attempt, especially useful when creating a new conversation.
- `message` (required): user prompt.
- `user_id` (optional, deprecated): ignored for authenticated clients. The JWT subject is the source of truth.
- `metadata` (optional): arbitrary key-value map.
- `retrieval_filters` (optional): retrieval filtering object.

Request field summary:

| Field | Type | Required | Notes |
|---|---|---|---|
| `conversation_id` | string | No | Backend UUID returned by a prior chat response; omit to create a new conversation |
| `request_id` | string | No | Idempotency key for this send attempt |
| `message` | string | Yes | User prompt |
| `user_id` | string | No | Deprecated; backend overwrites with authenticated token subject |
| `metadata` | object | No | Optional key-value metadata |
| `retrieval_filters` | object | No | Optional retrieval filters |

Success response (`200`):

```json
{
  "conversation_id": "7b5e4c7e-0000-4000-8000-000000000000",
  "answer": "Free cancellation is available up to 24 hours before the appointment [1].",
  "sources": [
    {
      "citation_number": 1,
      "document_id": "doc-001",
      "source_type": "document",
      "score": 0.92,
      "chunk": "Cancellation policy text ...",
      "cited_quotes": [
        "Free cancellation is available up to 24 hours before the appointment"
      ],
      "quote_verified": true,
      "metadata": {
        "page_number": 3
      }
    }
  ],
  "trace_id": "optional-request-id",
  "steps": [
    {
      "label": "Retrieved sources",
      "status": "completed",
      "detail": "Found 5 relevant source chunks.",
      "metadata": {
        "source_count": 5
      }
    }
  ]
}
```

Success field summary:

| Field | Type | Notes |
|---|---|---|
| `conversation_id` | string | Backend conversation UUID; always present |
| `answer` | string | Assistant response text (may contain markdown) |
| `sources` | array | Cited source chunks only; empty when the answer has no source citations |
| `trace_id` | string/null | Request trace id linking frontend reports, backend logs, and `chat_turn_events` |
| `steps` | array | Sanitized workflow trace events for the agent activity UI |

Notes:
- `config` is intentionally not returned in normal responses.
- Answer citations use display numbers like `[1]`; these numbers map to `sources[].citation_number`, not the original retrieval rank.
- `steps` are frontend-safe summaries. They must not be treated as a complete backend audit trail.
- Current expected step statuses are `completed` and `failed`; future streaming may also emit `started`.

Per-source cited-quote fields:

| Field | Type | Notes |
|---|---|---|
| `cited_quotes` | string[] | Verbatim spans the model quoted from `chunk` to support the citation. Empty when the source was cited without a quote, or when every emitted quote failed verbatim verification (in that case the `[N]` badge still appears in `answer`). Order matches the order the model used them in the answer text. |
| `quote_verified` | boolean | `true` when every entry in `cited_quotes` is a verbatim (whitespace- and case-insensitive) substring of `chunk`. Today the backend drops any non-matching quote, so this flag is informational and will stay `true` whenever `cited_quotes` is non-empty. Frontend may rely on it as a stable schema for future verification policies. |

Quotes are intended for in-line "evidence" rendering and document-viewer search/highlight. The backend already inlines each verified quote in the `answer` text as `"…" [N]`, so the frontend can either render the answer as-is or strip the quote-pair markers and surface the quotes in source cards / hover previews.

## 4.5 History API Contract (separate Lambda)

The history API lives on a separate Lambda Function URL (`NUXT_PUBLIC_HISTORY_API_BASE_URL`). It exposes per-user conversation list/read/rename/delete endpoints. It validates the same Cognito JWT and reads from the same Aurora `chat_conversations` and `chat_history` tables. All queries are scoped by `(environment, user_id)` from the JWT subject.

Headers (all endpoints):
- `Authorization: Bearer <JWT>` (required)
- `Content-Type: application/json` (for POST)

| Method | Path | Description |
|---|---|---|
| `GET` | `/conversations?limit=50&offset=0` | List user's conversations, newest first, excluding soft-deleted |
| `GET` | `/conversations/{conversation_id}/messages` | Ordered messages for an owned conversation |
| `POST` | `/conversations/{conversation_id}/rename` | Update the conversation title (body: `{"title": "..."}`) |
| `DELETE` | `/conversations/{conversation_id}` | Soft-delete (sets `status='deleted'`) |
| `OPTIONS` | (any) | CORS preflight, returns 204 |

`GET /conversations` response (`200`):

```json
{
  "conversations": [
    {
      "conversation_id": "7b5e4c7e-0000-4000-8000-000000000000",
      "title": "Cancellation policy",
      "status": "active",
      "message_count": 4,
      "created_at": "2026-05-04T20:29:52Z",
      "last_message_at": "2026-05-04T20:31:11Z"
    }
  ],
  "next_offset": null
}
```

- `next_offset` is the offset to pass next time when more rows may be available; `null` when the page returned fewer rows than `limit`.
- `limit` is clamped to `[1, 200]`, default `50`. `offset` is clamped to `[0, 10000]`, default `0`.

`GET /conversations/{id}/messages` response (`200`):

```json
{
  "conversation_id": "7b5e4c7e-0000-4000-8000-000000000000",
  "messages": [
    { "role": "user", "content": "What is the cancellation policy?" },
    {
      "role": "assistant",
      "content": "Free cancellation is available up to 24 hours before the appointment.",
      "sources": [
        {
          "citation_number": 1,
          "document_id": "abc-123",
          "source_type": "document",
          "score": 0.82,
          "chunk": "...",
          "cited_quotes": ["Free cancellation is available up to 24 hours before the appointment."],
          "quote_verified": true,
          "metadata": { "filename": "policy.pdf", "page_start": 4 }
        }
      ]
    }
  ]
}
```

- Messages are returned in chronological order.
- `role` is one of `user`, `assistant`, `system`.
- `sources` is only present on `assistant` messages that produced citations, and only for turns persisted by a RAG Lambda build that attaches sources to the AI message (`additional_kwargs.sources`). Older turns will load without a `sources` field and render without source cards.
- Per-message `steps` are still not included; live step events remain in `chat_turn_events`.

`POST /conversations/{id}/rename` request:

```json
{ "title": "New title (1-255 chars)" }
```

Response (`200`):

```json
{
  "conversation": {
    "conversation_id": "7b5e4c7e-0000-4000-8000-000000000000",
    "title": "New title",
    "status": "active",
    "message_count": 4,
    "created_at": "2026-05-04T20:29:52Z",
    "last_message_at": "2026-05-04T20:31:11Z"
  }
}
```

`DELETE /conversations/{id}` response (`200`):

```json
{ "conversation_id": "7b5e4c7e-...", "deleted": true }
```

- Delete is soft (`status='deleted'`); subsequent `GET /conversations` excludes the row.
- Subsequent reads of a deleted conversation return `404`.

Status codes (all endpoints, same shape as Section 5):
- `200` — success
- `204` — OPTIONS preflight
- `400` — bad request body or query param out of range
- `401` — missing/invalid/expired token
- `404` — path not matched, or conversation not owned / not found (we deliberately do not distinguish "not yours" from "doesn't exist")
- `500` — server error

## 4.6 Library API Contract (separate Lambda)

The Library API lives on a separate Lambda Function URL (`NUXT_PUBLIC_LIBRARY_API_BASE_URL`). It exposes read-only document catalog endpoints. It validates the same Cognito JWT and reads from the Aurora `document_catalog` table. All queries are scoped by `tenant_id` (from the JWT custom claim `custom:tenant_id`, falling back to the configured `ingestion.default_tenant_id`).

v1 only returns documents whose `access_tier` is `public` or `internal`. Restricted documents and per-document ACL grants (`document_access_grants`) are out of scope until Phase 5.

Headers (all endpoints):
- `Authorization: Bearer <JWT>` (required)

| Method | Path | Description |
|---|---|---|
| `GET` | `/documents?limit=50&offset=0` | List authorized documents, newest first, excluding `source_status='failed'` |
| `GET` | `/documents/{document_id}` | Single document detail; 404 if not in tenant or not in allowed tiers |
| `OPTIONS` | (any) | CORS preflight, returns 204 |

`GET /documents` response (`200`):

```json
{
  "documents": [
    {
      "document_id": "pdf_2021_international_residential_code_2_phase2_chu_63457d8b7e",
      "tenant_id": "default",
      "collection_id": "default",
      "document_title": "2021 International Residential Code",
      "document_description": null,
      "filename": "2021_International_Residential_Code-2.pdf",
      "document_type": "pdf",
      "source_status": "processing",
      "access_tier": "public",
      "location": "s3://bucket/kb_index/.../",
      "source_location": "s3://bucket/kb_raw/.../original.pdf",
      "publish_date": null,
      "version_label": null,
      "jurisdiction": null,
      "code_family": "irc",
      "uploaded_at": "2026-05-09T12:00:00Z",
      "ingested_at": "2026-05-09T12:15:00Z",
      "metadata": {}
    }
  ],
  "next_offset": null
}
```

- `next_offset` is the offset to pass next time when more rows may be available; `null` when the page returned fewer rows than `limit`.
- `limit` is clamped to `[1, 200]`, default `50`. `offset` is clamped to `[0, 10000]`, default `0`.
- `metadata` is a JSONB overflow object. Optional display fields like `publisher`, `page_count`, `edition_year`, `tags` may live here.

`GET /documents/{id}` response (`200`):

```json
{ "document": { "...": "same row shape as list entry" } }
```

Status codes:
- `200` — success
- `204` — OPTIONS preflight
- `400` — query param out of range
- `401` — missing/invalid/expired token
- `404` — path not matched, or document not in tenant / not in allowed access tiers
- `500` — server error

## 4.7 Knowledge API Contract (separate Lambda)

The Knowledge API exposes agent-callable retrieval primitives on a separate Lambda Function URL (`NUXT_PUBLIC_KNOWLEDGE_API_BASE_URL`). The same primitives are used internally by the RAG Lambda graph; this API is what the frontend (and any future MCP agent) should call when it needs vector search, chunk lookup, or a presigned URL to an original document. JWT auth and tenant scoping are identical to the History and Library APIs.

Headers (all endpoints):

- `Authorization: Bearer <id_token>`
- `Content-Type: application/json` (for `POST`)

Endpoints:

- `POST /search` — vector search over the Knowledge Base.
- `GET /documents/{document_id}/chunks` — list chunk metadata for a document (`limit`, `offset` query params).
- `GET /chunks/{chunk_id}` — fetch a single chunk's metadata + content (markdown).
- `GET /documents/{document_id}/raw-url` — issue a short-lived presigned S3 URL to the original document (view or download).

### `GET /documents/{document_id}/raw-url`

Returns a presigned `GET` URL into the raw-document S3 prefix. The URL inherits the Lambda role's delegated permissions, so the caller who receives it does not need their own AWS credentials — they just need to use the URL before it expires.

Query parameters:

| Param | Type | Default | Notes |
|---|---|---|---|
| `disposition` | `inline` \| `attachment` | `inline` | Sets `Response-Content-Disposition`. Use `attachment` to force a download with the original filename. |
| `page` | integer | _none_ | Optional page hint. Appended after the signed query as `#page=N`. Native browser PDF viewers and PDF.js will respect it. |
| `ttl` | integer (seconds) | `300` | URL lifetime. Clamped server-side to `[60, 900]`. |

Success response (`200`):

```json
{
  "url": "https://kb-bucket.s3.us-east-1.amazonaws.com/kb_raw/doc-1/original/2021-irc.pdf?X-Amz-…#page=107",
  "expires_at": "2026-05-10T03:05:00Z",
  "filename": "2021-irc.pdf",
  "content_type": "application/pdf",
  "disposition": "inline",
  "ttl_seconds": 300
}
```

Response field summary:

| Field | Type | Notes |
|---|---|---|
| `url` | string | Presigned S3 GET URL, valid until `expires_at`. Treat it as user-scoped and short-lived — do not store, log, or share. |
| `expires_at` | string (ISO8601, UTC) | When the signed URL stops working. Re-request a fresh one when expired. |
| `filename` | string | Original filename from the catalog row. Use it for download UX (e.g. `download` attribute on `<a>`). |
| `content_type` | string | Inferred from filename extension; `application/octet-stream` for unknown types. |
| `disposition` | string | Echo of the requested disposition. |
| `ttl_seconds` | integer | Effective (post-clamp) TTL the server signed. |

Status codes:

- `200` — URL issued
- `204` — OPTIONS preflight
- `400` — invalid `disposition`, `page`, or `ttl`
- `401` — missing/invalid/expired token
- `404` — document not in tenant / not in allowed access tiers / unknown id
- `502` — backend could not safely build a URL (e.g. catalog row points outside `kb_raw/`)
- `500` — server error

Usage notes:

- Treat the URL as **single-user, short-lived**. Do not embed it in cached or shared responses; the frontend should fetch a fresh URL each time the user opens or downloads a document.
- For "open in viewer" flows, set `disposition=inline` and embed the URL in a PDF.js viewer iframe (or a native `<embed>`). Append `#page=N` server-side via the `page` query — the fragment survives the signed query.
- For "download" flows, set `disposition=attachment`. The browser will use the `filename` from `Response-Content-Disposition`.
- Cited-quote highlighting: pair the `cited_quotes` from the chat response with PDF.js search parameters (e.g. `?file=<url>#page=107&search=<quote>&highlight=true&phrase=true`) so the cited span is highlighted on open. The backend does not pre-render highlights — it returns the URL and the quote text; the frontend wires them together.

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
- `403`: forbidden action.
- `404`: unsupported path or nonexistent/unauthorized `conversation_id`.
- `405`: unsupported method.
- `500`: server error.

Retry guidance:
- Retryable: transient `500` (use capped backoff), network errors.
- Non-retryable: `400`, `401`, `403`, `404`, `405`.
- On a network timeout during a first send, retry with the same `request_id` so the backend can avoid creating another conversation row.

UX guidance:
- `401`: composable retries once with refreshed token; if still 401, error is shown in assistant message.
- `403`: show permission/identity mismatch message.
- `500`: show generic "try again" and allow retry.

Long-running request guidance:
- Use backend-provided `steps` when the response completes.
- Until streaming is implemented, keep lightweight local pending UI while the request is in flight.
- Keep request timeout >= 60s.
- Support user cancellation (`AbortController`).

Conversation lifecycle:
- First send without `conversation_id` creates a new backend conversation and returns its UUID.
- Follow-up sends must include the returned `conversation_id`.
- The frontend should persist the returned `conversation_id` in chat state immediately.
- Prevent concurrent sends in the same conversation for v1.
- Conversation list/get-messages/rename/delete are exposed by the **History API Lambda** — see Section 4.5.

Future streaming event names:
- `conversation.created`
- `trace.step.started`
- `trace.step.completed`
- `trace.step.failed`
- `sources.updated`
- `answer.delta`
- `answer.completed`
- `turn.completed`

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
- `conversationId` — backend conversation UUID, initially `null`, persisted in `sessionStorage`
- `liveSources` / `liveSteps` — mirrored to the right sidebar panel

Actions:
- `send(text)` — append user message, post to `/api/chat`, store returned `conversation_id`, append assistant response, map `sources`, and replace `liveSteps` with backend `steps`
- `stop()` — abort in-flight request via `AbortController`
- `reset()` — clear messages and clear `conversationId`; the next send creates a backend conversation
- `toggleReaction(id, value)` — toggle thumbs up/down on a message

Source mapping (Lambda → UI):

| Lambda field | UI field |
|---|---|
| `citation_number` | displayed citation number / source badge |
| `document_id` | `title` |
| `chunk` | `excerpt` |
| `source_type` | `sourceType` (displayed as metadata) |
| `score` | `score` (displayed as metadata) |
| `metadata` | optional source details, such as page/section/title when available |
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
- Translates client field names (`conversationId` → `conversation_id`, `requestId` → `request_id`)
- Forwards to Lambda Function URL (from private `runtimeConfig`)
- Returns Lambda JSON response
- Maps Lambda error responses to Nuxt `createError`

In static generation mode, this route is not available at runtime. The `useChat` composable calls Lambda directly.

## 10. QA Checklist for Frontend Team

1. Auth success path: enter email/password → sign in → token → chat page loads.
2. Auth failure path: expired token → 401 → auto-refresh → retry; if refresh fails → redirect to login.
3. New conversation path: first send omits `conversation_id`; backend returns a UUID; frontend stores it.
4. Conversation continuity: follow-up sends include the returned `conversation_id` and preserve thread context.
5. User isolation: a user cannot load another user's backend `conversation_id`; backend returns 404.
6. CORS (static mode): frontend origin allowed; blocked origins fail as expected.
7. Non-200 handling: each status mapped to clear UX (error shown in assistant message).
8. Latency behavior: local pending indicator works until response; returned `steps` replace simulated agent steps.
9. Source rendering: cited sources display `citation_number`, `document_id`, `chunk`, `source_type`, `score`, and optional `metadata` safely. Answers with no citations should show no source cards.
10. Responsive: three-column layout at desktop → two-column at 1180px → single column at 800px.
11. Sign out: clears session, redirects to login.

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
