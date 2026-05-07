# RAG Chat History Strategy Plan

Created: 2026-05-05  
Status: V1 backend foundations implemented on migration branch
Scope: Session and chat-history management for the `chat-template` RAG Lambda.

Implementation note: The initial backend implementation now supports optional `conversation_id`, backend-generated UUID conversations, visible-only chat-history persistence, deployment-managed chat-history schema setup, and synchronous sanitized `steps` backed by persisted `chat_turn_events`. Durable rolling summaries, conversation management endpoints, archive/delete behavior, and streaming delivery remain future work.

## Goals

Implement durable, user-scoped chat history for the RAG Lambda in a way that supports:

- safe conversation resume across requests and browser sessions;
- correct multi-turn context for follow-up questions;
- bounded model context through trimming and summarization;
- future conversation list, rename, archive, delete, and export workflows, with archive/delete deferred beyond v1;
- a clean future path for long-term user memory without mixing it into short-term chat history;
- low-cost serverless deployment using the existing Aurora Data API path.

## Research Summary

### LangGraph memory model

LangGraph treats short-term memory as thread-scoped state. A graph compiled with a checkpointer persists state by `thread_id`, allowing later invocations with the same thread to resume with prior state. LangChain recommends database-backed checkpointers, such as Postgres, in production.

LangGraph also separates short-term memory from long-term memory. Short-term memory tracks a thread or conversation; long-term memory stores user-specific or application-level facts across conversations. This distinction maps well to our needs: chat history should be thread-scoped, while future user preferences or durable facts should live in a separate long-term memory store.

LangGraph docs also call out the need to manage long conversations by trimming messages, deleting messages, summarizing older turns, managing checkpoints, or using custom filtering strategies.

### LangChain chat history model

LangChain exposes `RunnableWithMessageHistory` for wrapping a runnable so it reads and writes a `BaseChatMessageHistory` keyed by `session_id`. The docs explicitly state that production use should rely on a persistent history implementation.

This is useful background, but it is not the cleanest direct fit for our active graph. Our current graph stores intermediate artifacts in the same `messages` list as user-visible messages. Wrapping the whole graph in `RunnableWithMessageHistory` would risk persisting rewrite output, subqueries, and retrieval context unless we first separate visible chat messages from internal graph state.

### Bedrock conversation state model

The Bedrock Converse API is stateless from the application's point of view. AWS documents that Bedrock does not store request content for conversation state; the application maintains context by sending prior user/assistant messages in subsequent requests.

Implication for this repo: our backend, not Bedrock, owns session identity, message persistence, summarization, privacy, deletion, and context-window management.

## Current Repo State

The repo already has meaningful pieces:

- `src/rag_lambda/main.py` loads prior messages, invokes the LangGraph RAG pipeline, and appends messages after the graph completes.
- `src/rag_lambda/memory/base.py` defines a `ChatHistoryStore` abstraction.
- `src/rag_lambda/memory/data_api_store.py` stores LangChain message JSON through Aurora Data API.
- `src/rag_lambda/memory/postgres_store.py` stores messages with `langchain_postgres.PostgresChatMessageHistory`.
- `src/rag_lambda/memory/factory.py` selects the configured backend.
- `src/rag_lambda/memory/chat_summary.py` can summarize older messages.
- Config files default to `memory_backend_type: "aurora_data_api"`, which aligns with the current low-cost Aurora/Data API deployment direction.
- Previous code scoped storage with `user_id:conversation_id` to avoid collisions.
- Implemented update: backend-generated UUID conversation ids now act as both the public API id and the message-history key, with ownership stored explicitly on `chat_conversations`.
- Tests in `tests/test_lambda_handler_auth.py` verify user scoping and config omission.

## Current Gaps and Risks

### 1. User turns are not currently persisted correctly

In `src/rag_lambda/main.py`, the state starts with:

```python
prior_messages + [HumanMessage(content=req.message)]
```

After graph execution, the handler computes:

```python
new_messages = final_state["messages"][len(prior_messages) + 1 :]
```

That excludes the current user message from persistence. At the same time, the graph appends internal messages such as rewrite output, subqueries, retriever context, and final answer. This means history can become polluted with internal pipeline artifacts while missing the actual user turn.

This should be the first fix before adding new features.

### 2. Graph state mixes visible chat and internal artifacts

`src/rag_lambda/graph/nodes.py` appends:

- `AIMessage(name="rewriter", ...)`
- possible clarification messages;
- `SystemMessage(name="subqueries", ...)`
- `SystemMessage(name="retriever_context", ...)`
- final assistant answer.

These are operational artifacts, not all user-visible conversation messages. Persisting the raw graph `messages` list will produce poor history and confusing future context.

### 3. Summarization is computed but not persisted as conversation state

`summarization_check` summarizes older messages when count exceeds a threshold, but the summary is only used for the current invocation. The underlying stored history is not compacted or annotated, so future requests may repeatedly summarize the same old messages.

### 4. Session metadata is underdeveloped

The current metadata table stores merged JSON by `conversation_id`, but there is no first-class conversation/session record with:

- owner user id;
- backend-generated conversation id;
- title;
- status;
- created/updated timestamps;
- message counts;
- summary state;
- last model/config used;
- retention/deletion state.

### 5. Conversation identity is implicit

The API returns the client-facing `conversation_id`, while storage currently uses the scoped id. The Data API store then converts non-UUID conversation ids into deterministic UUIDs for the current LangChain-compatible `session_id` column.

This works, but it is implicit and spread across layers. The cleaner target is to generate UUID conversation ids on the backend, store ownership explicitly, and rename the message-history key to `conversation_id`.

### 6. Per-invocation schema creation is not ideal

`DataApiHistoryStore.__init__` calls `_ensure_tables()` on creation. That is convenient, but repeated DDL checks during Lambda invocations add latency and operational noise. Schema setup should eventually move to deployment/migration logic, with runtime only verifying minimum assumptions or failing fast.

### 7. Existing chat-history data is disposable

The existing chat-history tables do not contain production data. We do not need to preserve or migrate current conversation rows. This lets us simplify implementation by rewriting the schema, updating deployment scripts to create the new tables directly, and recreating chat-history tables in each environment.

## Design Principles

1. Store only user-visible chat turns in short-term history.
2. Store graph traces, retrieval context, source chunks, and model telemetry separately from chat history.
3. Treat authenticated user identity as the server-side source of truth.
4. Generate opaque UUID conversation ids on the backend and use `conversation_id` consistently in both API and database schemas.
5. Keep Aurora Data API as the default v1 backend because it matches the current no-VPC, lower-cost deployment strategy.
6. Keep the storage abstraction compatible with LangChain message serialization without adopting LangGraph checkpointing in the initial implementation.
7. Make summarization explicit and durable.
8. Prefer a clean schema replacement over compatibility shims because current chat-history data is non-production and disposable.
9. Keep long-term memory out of scope for this implementation; leave it as a later exploration.

## Recommended Architecture

### Short-term chat history

Use the current `ChatHistoryStore` abstraction, but evolve it from a raw message append interface into a conversation-aware interface.

Recommended v1 behavior:

- `POST /chat` receives message and optional `conversation_id`.
- Auth resolves `user_id` from JWT or fallback.
- If `conversation_id` is absent, backend creates a new UUID conversation id and returns it.
- If `conversation_id` is present, backend verifies it belongs to the authenticated user.
- Backend loads durable summary plus recent visible messages.
- Backend invokes the graph with a clean context.
- Backend persists exactly one user-visible turn:
  - current human message;
  - final assistant answer;
  - response-level metadata such as trace id, retrieval filters, source ids, and model ids.
- Backend returns `conversation_id`, answer, sources, and trace id.

### Internal graph state

Refactor `MessagesState` so not every intermediate step is appended to `messages`.

Proposed state keys:

```python
class MessagesState(TypedDict, total=False):
    messages: List[BaseMessage]          # visible conversation context only
    current_user_message: HumanMessage
    rewritten_query: str
    clarification: str
    subqueries: List[str]
    retrieval_context: str
    sources: List[Dict[str, Any]]
    answer: str
    retrieval_config: Dict[str, Any]
    retrieval_filters: Dict[str, List[str]]
```

Alternative smaller step: keep the existing graph shape for now, but filter persisted messages by role/name so only the current `HumanMessage` and final user-visible `AIMessage` are stored. This is lower risk for an initial fix.

### Conversation/session identity

Use a single backend-generated conversation id for v1.

| ID | Owner | Purpose |
| --- | --- | --- |
| `user_id` | backend auth | Authenticated owner of the conversation. |
| `conversation_id` | backend API/storage | Opaque UUID generated by the backend, returned to the frontend, and used as the message-history key. |

Recommended behavior:

- The frontend may omit `conversation_id` for a new chat.
- The backend generates an opaque UUID, preferably UUIDv4 for now or UUIDv7 if we later want time-sortable ids.
- The backend stores the generated id in `chat_conversations`.
- The backend stores messages under the same UUID in `chat_history.conversation_id`.
- Future requests must include that `conversation_id`.
- All reads and writes must check `conversation_id` plus authenticated `user_id`; possession of a UUID is not authorization.

This removes the need for a separate `thread_id` while keeping user isolation explicit.

Potential hurdles with the single-id approach:

- First-request retries can create duplicate conversations if the frontend times out before receiving the generated id.
- A frontend bug that omits `conversation_id` on a follow-up message will create a new conversation.
- Concurrent sends to the same conversation can produce confusing ordering unless the frontend serializes sends or the backend adds turn ordering.
- Old conversations written with scoped string ids can be intentionally discarded because current history is non-production.

Mitigations:

- Add `request_id` or `idempotency_key` to chat requests before production use so first-turn retries do not duplicate conversations.
- Have the frontend disable concurrent sends per conversation.
- Log new-conversation creation and suspicious repeated new conversations from the same user.
- Enforce ownership in every query:

```sql
WHERE conversation_id = :conversation_id
AND user_id = :authenticated_user_id
```

### Storage schema

Keep the existing message JSON shape for `chat_history`, but rename the message-history key from `session_id` to `conversation_id` in our planned schema. If we need to use a LangChain helper that expects `session_id`, adapt at the code boundary instead of preserving the confusing database column name.

Schema migration stance:

- Rewrite the current chat-history schema rather than preserving backward compatibility.
- Drop/recreate chat-history tables during the next deploy or through an explicit schema reset step.
- Update deployment scripts to create `chat_conversations`, `chat_history`, and `chat_turn_events` in the new format.
- Remove runtime DDL from the hot Lambda path once deployment-managed schema setup exists.
- Do not build migration logic for existing scoped-string conversation ids unless we later discover data worth preserving.

Proposed `chat_conversations`:

| Column | Type | Purpose |
| --- | --- | --- |
| `conversation_id` | UUID primary key | Backend-generated id returned to the frontend and used as `chat_history.conversation_id`. |
| `user_id` | varchar | Authenticated owner. |
| `environment` | varchar | Environment isolation. |
| `title` | varchar nullable | User-visible title, derived from first message or explicit rename. |
| `status` | varchar | Initially `active`; archive/delete are future exploration items. |
| `created_at` | timestamptz | Creation time. |
| `updated_at` | timestamptz | Last update time. |
| `last_message_at` | timestamptz | Last user/assistant message time. |
| `message_count` | integer | Count of visible messages. |
| `summary` | text nullable | Durable compact summary of older turns. |
| `summary_message_count` | integer | Number of visible messages represented by the summary. |
| `metadata` | jsonb | Conversation-level metadata, including retrieval filters, model ids, source ids, app version, etc. |

Add ownership lookup index:

```sql
CREATE INDEX idx_chat_conversations_owner
ON chat_conversations (environment, user_id, conversation_id);
```

Because `conversation_id` is globally unique, it can be the primary key. The ownership index is mostly for query performance and explicit authorization checks; it does not need to be the primary identity model.

Proposed `chat_history` shape:

| Column | Current / Proposed | Purpose |
| --- | --- | --- |
| `id` | serial or identity | Message ordering. |
| `conversation_id` | UUID | Foreign key to `chat_conversations.conversation_id`. |
| `message` | JSONB | LangChain message payload. |
| `created_at` | timestamptz | Message timestamp. |

Add index:

```sql
CREATE INDEX idx_chat_history_conversation_id
ON chat_history (conversation_id, id);
```

Compatibility note: LangChain documentation often calls this value `session_id`, and `PostgresChatMessageHistory` uses a `session_id` column by default. In this repo's application schema, use `conversation_id`. If a library adapter requires `session_id`, isolate that translation inside the adapter rather than leaking both names throughout the codebase and docs.

Later, consider adding separate columns or a side table for:

- `trace_id`;
- `request_id`;
- `message_visibility`;
- `turn_index`;
- token usage;
- source/citation metadata.

### Graph transaction and trace events

Do not store retrieved chunks and intermediate graph artifacts as chat history.

Instead, model every workflow step as a structured turn event. This gives the backend a durable audit/debug trail and gives the frontend a sanitized trace of what the agent did.

Recommended concept:

- A **conversation** contains many turns.
- A **turn** is one user message and one assistant outcome.
- A **trace event** is one internal workflow step within that turn, such as rewrite, clarify, split, retrieve, answer, or summarize.

Use two levels of trace detail:

1. **Backend trace payload:** full-enough structured data for debugging, evals, latency/cost analysis, and reproducibility.
2. **Frontend trace payload:** sanitized, user-facing step summaries that can be shown in the right sidebar without exposing prompts, secrets, raw retrieved chunks, or excessive implementation detail.

For observability and debugging, add a separate `chat_turn_events` table:

| Field | Purpose |
| --- | --- |
| `event_id` | Unique event id. |
| `trace_id` | Links Lambda response, logs, and persisted trace. |
| `conversation_id` | Conversation link. |
| `turn_index` | User/assistant turn number. |
| `step_index` | Ordered step within the turn. |
| `step_name` | `rewrite`, `clarify`, `split`, `retrieve`, `answer`, `summarize`, etc. |
| `status` | `started`, `completed`, `skipped`, or `failed`. |
| `started_at` / `completed_at` | Timing. |
| `latency_ms` | Step performance. |
| `model_id` | Model used when the step invokes a model. |
| `input_summary` | Sanitized summary or compact JSON of inputs. |
| `output_summary` | Sanitized summary or compact JSON of outputs. |
| `input_payload` | Optional backend-only JSON payload. |
| `output_payload` | Optional backend-only JSON payload. |
| `error` | Redacted error details for failed steps. |
| `frontend_event` | Sanitized JSON object safe to return to the UI. |
| `metadata` | Retrieval filters, source ids, token usage, config version, etc. |
| `created_at` | Insert timestamp. |

Recommended step events:

| Step | Backend output | Frontend output |
| --- | --- | --- |
| `rewrite` | Original user question, rewritten query, model id, latency. | "Rewrote the question for retrieval" plus rewritten query when safe. |
| `clarify` | Clarity decision, clarification question if generated. | "Checked whether the question needs clarification" or the clarification question. |
| `split` | Subqueries, parse status, model id, latency. | "Split into N focused searches" plus short subquery list. |
| `retrieve` | Knowledge Base id, retrieval filters, source ids, scores, metadata, latency. | "Retrieved N source chunks" plus source titles/scores. |
| `answer` | Model id, prompt/input references, answer length, latency, token usage. | "Generated final answer from retrieved context." |
| `summarize` | Summary update status and represented message count. | Hidden from users in v1. |

Do not put full retrieved chunks, full prompts, JWT claims, DB ARNs, secrets, or raw exception traces in frontend events.

Example persisted event:

```json
{
  "trace_id": "req-abc",
  "conversation_id": "7b5e4c7e-0000-0000-0000-000000000000",
  "turn_index": 3,
  "step_index": 2,
  "step_name": "split",
  "status": "completed",
  "model_id": "amazon.nova-micro-v1:0",
  "latency_ms": 431,
  "input_summary": {
    "question": "What permits and inspections are needed for a deck?"
  },
  "output_summary": {
    "subqueries": [
      "deck permit requirements",
      "deck inspection requirements"
    ]
  },
  "frontend_event": {
    "label": "Split question",
    "status": "completed",
    "detail": "Created 2 focused searches.",
    "items": [
      "deck permit requirements",
      "deck inspection requirements"
    ]
  }
}
```

Frontend response shape can include trace events immediately:

```json
{
  "conversation_id": "7b5e4c7e-0000-0000-0000-000000000000",
  "answer": "...",
  "sources": [],
  "trace_id": "req-abc",
  "steps": [
    {
      "label": "Rewrote question",
      "status": "completed",
      "detail": "Prepared the question for retrieval."
    },
    {
      "label": "Retrieved sources",
      "status": "completed",
      "detail": "Found 5 relevant chunks."
    }
  ]
}
```

For v1, return the sanitized `steps` in the synchronous chat response and persist backend trace events to `chat_turn_events`. We want to capture both backend trace payloads and frontend step payloads in the early stages while we are still learning how the system behaves. The trace event model should still be designed so the same event objects can later be emitted incrementally over a streaming transport.

### Synchronous and streaming response modes

Support two response modes over time:

1. **Synchronous request/response mode for v1:** the Lambda runs the whole graph, persists backend trace events, then returns the final answer, sources, and complete sanitized step list.
2. **Streaming mode for later:** the Lambda emits workflow step events as each graph step starts/completes, then streams the final answer text as deltas, then emits a terminal completion event.

The key design point is that both modes should use the same internal `TraceEvent` model. Synchronous mode accumulates events in memory and returns them as `steps`. Streaming mode emits the same sanitized frontend events as they are produced.

Recommended streaming event envelope:

```json
{
  "type": "trace.step.completed",
  "trace_id": "req-abc",
  "conversation_id": "7b5e4c7e-0000-0000-0000-000000000000",
  "turn_index": 3,
  "step_index": 2,
  "step": {
    "label": "Split question",
    "status": "completed",
    "detail": "Created 2 focused searches.",
    "items": [
      "deck permit requirements",
      "deck inspection requirements"
    ]
  }
}
```

Suggested event types:

| Event type | Purpose |
| --- | --- |
| `conversation.created` | Sent early when the backend creates a new `conversation_id`. |
| `trace.step.started` | A workflow step has begun. |
| `trace.step.completed` | A workflow step completed with sanitized output. |
| `trace.step.failed` | A workflow step failed with redacted error details. |
| `sources.updated` | Sources are available after retrieval. |
| `answer.delta` | Optional token/text chunk if model streaming is enabled. |
| `answer.completed` | Final answer text and answer metadata. |
| `turn.completed` | Final event with `conversation_id`, `trace_id`, sources, and complete step list. |

Transport options:

| Option | Fit | Tradeoffs |
| --- | --- | --- |
| Lambda response streaming | Best fit if using Lambda Function URLs or compatible API Gateway path. | Good for one request producing progressive output; requires runtime and frontend support. |
| Server-Sent Events | Good browser fit for one-way progress events. | May require an adapter layer if Lambda/API Gateway path does not support long-lived responses cleanly. |
| WebSocket API | Best for bidirectional, cancellable, long-running sessions. | More infrastructure and connection-state management. |
| Polling by `trace_id` | Simplest fallback. | Less real-time; requires persisted trace events and polling endpoint. |

Recommended path:

- Design the `TraceEvent`/frontend event schema now.
- Implement synchronous accumulation first.
- Persist backend trace events and frontend step payloads to `chat_turn_events`.
- Add placeholder streaming abstractions/interfaces only; do not implement streaming delivery in v1.
- Later add a streaming writer that subscribes to the same event collector, emits workflow steps as they complete, and streams the final answer.
- Keep synchronous mode permanently for clients that do not need live progress or for easier testing/evals.

## Context Assembly Strategy

For each request:

1. Resolve authenticated user.
2. Resolve or create the conversation:
   - absent `conversation_id` means create a new conversation;
   - present `conversation_id` means verify ownership.
3. Load `conversation.summary` if present.
4. Load the last `N` visible messages from `chat_history`.
5. Build graph input:
   - optional `SystemMessage(name="conversation_summary", ...)`;
   - recent visible user/assistant messages;
   - current `HumanMessage`.
6. Run rewrite/clarify/split/retrieve/answer.
7. Persist current `HumanMessage` and final assistant `AIMessage`.
8. Capture sanitized trace steps for the response.
9. Persist backend trace events to `chat_turn_events`.
10. Update conversation metadata.
11. Run or schedule summarization if thresholds are exceeded.

Recommended v1 config:

```yaml
chat_history:
  enabled: true
  context_strategy: "summary_plus_recent"
  recent_message_limit: 12
  summarization:
    enabled: true
    trigger_message_count: 24
    keep_recent_messages: 12
    persist_summary: true
  persistence:
    backend: "aurora_data_api"
    create_schema_on_startup: false
```

The existing `summarization_threshold` can be mapped into `trigger_message_count` during implementation, then deprecated after config cleanup.

## Summarization Strategy

Use durable rolling summaries, not per-request throwaway summaries.

Recommended algorithm:

1. Load existing summary and visible messages after `summary_message_count`.
2. If unsummarized visible messages exceed `trigger_message_count`, summarize all but the most recent `keep_recent_messages`.
3. Persist the new summary and `summary_message_count` on `chat_conversations`.
4. Keep all raw messages by default for audit/export unless retention policy says otherwise.
5. Use summary + recent messages for model context.

Important: do not summarize internal graph messages, retrieved source chunks, or system prompts. Summaries should cover only user-visible human/assistant turns.

## LangGraph Checkpointer Future Exploration

Do not adopt LangGraph `PostgresSaver` as part of this implementation plan.

Rationale:

- The current deployment strategy favors Aurora Data API to avoid VPC/direct database connection costs.
- LangGraph Postgres checkpointing expects a normal PostgreSQL connection and its own setup/migration path.
- The current graph state mixes visible messages with internal artifacts; checkpointing that state would preserve more than we want as user chat history.
- We already have custom Data API storage that matches the cost strategy.

Leave it as a later exploration option if:

- we move the Lambda into a VPC with stable direct Postgres access;
- we need native LangGraph replay/time-travel/debugging;
- we cleanly separate graph state from user-visible chat history;
- the added schema and dependency complexity is worth it.

## API and Frontend Contract

### Minimum v1 API

Keep the existing `POST /chat` flow, but improve behavior:

- `conversation_id` is optional.
- If `conversation_id` is omitted, backend creates a new conversation and returns the generated id.
- If `conversation_id` is provided, backend verifies ownership before loading or appending history.
- Response returns `conversation_id`, `answer`, `sources`, `trace_id`, and sanitized `steps`.

Proposed request shape:

```json
{
  "conversation_id": "optional-existing-uuid",
  "request_id": "optional-client-generated-idempotency-key",
  "message": "What does the code say about foundations?",
  "retrieval_filters": null
}
```

Proposed response shape:

```json
{
  "conversation_id": "backend-generated-or-existing-uuid",
  "answer": "...",
  "sources": [],
  "trace_id": "...",
  "steps": [
    {
      "label": "Retrieved sources",
      "status": "completed",
      "detail": "Found 5 relevant chunks."
    }
  ]
}
```

### Future conversation APIs

Add later when frontend needs conversation management:

- `POST /conversations` creates a new conversation id.
- `GET /conversations` lists active conversations for the authenticated user.
- `GET /conversations/{conversation_id}` returns metadata and recent messages.
- `PATCH /conversations/{conversation_id}` renames the conversation.

Archive/delete endpoints are future exploration items, not v1 implementation requirements.

These endpoints can live in the same Lambda if routing remains simple, or a separate conversation-management Lambda if the chat Lambda should stay narrow.

## Implementation Phases

### Phase 1: Correct persistence semantics

Fix the current message persistence bug before adding new storage features.

Tasks:

- Persist the current `HumanMessage`.
- Persist only the final user-visible assistant answer.
- Do not persist `rewriter`, `subqueries`, or `retriever_context` messages.
- Add tests that assert stored messages are exactly human + assistant for a single turn.
- Add tests for multi-turn follow-up loading.
- Add tests that prior user messages are available to rewrite/answer nodes.

Likely minimal code path:

- In `main.py`, construct `visible_messages_to_store = [HumanMessage(content=req.message), AIMessage(content=answer)]`.
- Pass metadata separately to `append_messages`.
- Keep graph internals unchanged for now.

### Phase 2: Add conversation records and metadata

Create first-class conversation/session rows.

Tasks:

- Add deployment-managed schema setup for `chat_conversations`, `chat_history`, and `chat_turn_events`.
- Add an explicit non-production reset path that can drop/recreate old chat-history tables.
- Add `resolve_or_create_conversation(user_id, conversation_id=None, request_id=None)` in the history store.
- Generate a backend UUID when `conversation_id` is absent.
- Use `conversation_id` directly as `chat_history.conversation_id`.
- Store title, timestamps, status, message count, summary fields, and metadata.
- Generate and persist conversation titles in v1, initially from the first user message with a deterministic fallback title.
- Store retrieval filters in `chat_conversations.metadata` so conversation-level defaults/context can be inspected later.
- Update tests for user isolation by `conversation_id` plus `user_id`.
- Remove use of raw `user_id:conversation_id` as the storage id for new conversations.
- Ignore old scoped-string conversations unless we later identify data that must be preserved.

### Phase 3: Durable rolling summarization

Replace throwaway summarization with persisted summaries.

Tasks:

- Add `get_context_messages(conversation_id, user_id, recent_message_limit)` API.
- Add `get_conversation_summary(conversation_id, user_id)`.
- Add `update_summary(conversation_id, user_id, summary, summary_message_count)`.
- Summarize only visible user/assistant messages.
- Preserve raw messages initially.
- Add tests for summary + recent message assembly.

### Phase 4: Clean graph state

Separate user-visible messages from internal graph fields.

Tasks:

- Refactor `MessagesState` to include explicit fields for rewritten query, subqueries, retrieval context, sources, and answer.
- Stop appending internal artifacts to `messages`.
- Make rewrite use recent chat history explicitly for pronoun/context resolution.
- Make answer use `retrieval_context` and current question, not an internal system message stored in `messages`.
- Add unit tests for each node with synthetic state.

### Phase 5: Add graph transaction tracing

Make rewrite/clarify/split/retrieve/answer steps observable without storing them as chat transcript messages.

Tasks:

- Define a `TraceEvent` model with `trace_id`, `conversation_id`, `turn_index`, `step_index`, `step_name`, `status`, summaries, optional payloads, and frontend event data.
- Add a small trace collector object to graph state or runnable config.
- Instrument each graph node to record started/completed/failed events.
- Return sanitized `steps` in `ChatResponse`.
- Persist backend trace events and frontend step payloads in `chat_turn_events`.
- Keep the trace collector transport-neutral so it can later write to an in-memory list, database sink, streaming response writer, SSE writer, WebSocket publisher, or polling-backed store.
- Define streaming event names and placeholder writer interfaces now even though v1 only returns the accumulated final `steps` list.
- Redact or omit raw prompts, raw retrieved chunks, JWT claims, DB ARNs, secrets, and raw exception traces from frontend events.
- Add tests that trace steps are ordered, sanitized, and present for normal RAG flows.

### Phase 6: Conversation management endpoints

Add user-facing session management only after the core chat history is correct.

Tasks:

- Add list/get/rename endpoints.
- Leave archive/delete endpoints as future exploration items.
- Update `docs/frontend_interface.md`.
- Update frontend contract if frontend repo is in scope.
- Add auth tests so users cannot access another user's conversations.

### Phase 7: Update frontend interface documentation

After backend implementation, update `docs/frontend_interface.md` so the frontend repo has enough context to integrate the new chat/session behavior without reverse-engineering backend code.

Required updates:

- Request contract:
  - `conversation_id` is optional for a new conversation.
  - `request_id` is a per-send idempotency key.
  - `retrieval_filters` behavior remains unchanged unless implementation changes it.
- Response contract:
  - `conversation_id` is always returned.
  - `steps` contains sanitized graph trace events for the right-side agent activity panel.
  - `sources` remains the source/citation payload.
  - `trace_id` links frontend reports to backend logs and `chat_turn_events`.
- Conversation lifecycle:
  - first send without `conversation_id` creates a conversation;
  - follow-up sends must include `conversation_id`;
  - frontend should persist conversation ids and load conversation lists from backend once list endpoints exist;
  - frontend should prevent concurrent sends in the same conversation for v1.
- Trace/steps UI:
  - document expected step labels/statuses;
  - document that frontend events are sanitized and may omit backend-only details;
  - document future streaming event names even if v1 is synchronous.
- Error handling:
  - missing/invalid authorization;
  - nonexistent or unauthorized `conversation_id`;
  - retry behavior with duplicate `request_id`;
  - failed graph step with redacted trace event.
- Frontend implementation notes:
  - replace simulated agent steps with backend-provided `steps`;
  - update `useChat()` state to keep backend `conversation_id`;
  - update `useConversations()` once backend list/get endpoints are implemented.

## Testing Plan

Add focused tests for:

- first turn creates/resolves a conversation;
- missing `conversation_id` creates a backend-generated UUID and returns it;
- supplied `conversation_id` resumes the existing conversation;
- supplied `conversation_id` owned by another user returns 404 or 403;
- current user message is persisted;
- final assistant answer is persisted;
- internal graph messages are not persisted;
- sanitized `steps` are returned for rewrite/retrieve/answer flows;
- trace event ordering is stable by `turn_index` and `step_index`;
- frontend trace events do not include full prompts, full source chunks, secrets, DB ARNs, or JWT claims;
- failed graph steps produce redacted failed trace events;
- synchronous response `steps` can be derived from the same trace events used by future streaming mode;
- frontend retry with the same `request_id` does not create duplicate first-turn conversations;
- accidental missing `conversation_id` on a follow-up creates a new conversation and is observable in logs/metrics;
- unauthenticated/fallback identity behavior works as expected for non-prod;
- summary is used in context assembly after threshold;
- old messages are not repeatedly summarized every request;
- archive/delete behavior remains out of v1 and has no active API behavior yet;
- idempotent retries do not duplicate turns once request IDs are added.

## Operational and Security Considerations

- Do not log raw message content by default.
- Do not expose backend config in chat responses.
- Treat JWT-derived user id as the source of truth.
- Do not generate conversation ids from user ids. Generate opaque UUIDs and store ownership separately.
- Do not treat possession of a conversation id as authorization.
- Retain chat history for now; do not implement deletion in v1.
- Leave archive/soft-delete/hard-delete as future exploration before production user data accumulates.
- Add export/delete flows if the app will hold real user data.
- Add idempotency key support to avoid duplicate messages when clients retry after timeout.
- Track token usage and latency per turn when available.
- Keep backend trace payloads separate from frontend trace payloads.
- Treat full prompts, retrieved chunks, raw errors, and model inputs as backend-only debugging data.
- Consider trace retention separately from chat transcript retention because traces can be larger and more sensitive.
- Move schema creation out of the hot Lambda path when deployment migrations are ready.

## Open Questions

- Would a dedicated conversation-management Lambda be cleaner than adding more routes to the RAG Lambda?
- Should archive/soft-delete/hard-delete be explored later, and what retention policy should apply before production user data accumulates?
- Which streaming transport should be explored first after v1: Lambda response streaming, SSE adapter, WebSocket API, or polling by `trace_id`?
- Should LangGraph checkpointing be explored later for replay/time-travel debugging, separate from the v1 chat transcript and trace-event implementation?
- Should long-term memory be explored later for user preferences, saved default filters, or explicit user-approved durable facts?

## Recommended First Decision

Keep the custom Aurora Data API history store for v1, but harden it around explicit conversation/session semantics, backend-generated UUID conversation ids, conversation titles, conversation metadata, and structured graph trace events.

Focus this implementation on fixing the current persistence bug, separating visible chat history from internal graph artifacts, adding a first-class conversation metadata table, making `conversation_id` optional on first chat requests, persisting backend trace events plus frontend step payloads, and returning sanitized `steps` for frontend agent-trace display. Keep v1 synchronous with placeholder streaming abstractions. Leave archive/delete behavior, streaming delivery, LangGraph checkpointing, and long-term memory as separate future research topics.

## Research Sources

- LangGraph memory docs: https://docs.langchain.com/oss/python/langgraph/add-memory
- LangGraph persistence docs: https://docs.langchain.com/oss/python/langgraph/persistence
- LangChain short-term memory docs: https://docs.langchain.com/oss/python/langchain/short-term-memory
- LangChain `RunnableWithMessageHistory` reference: https://reference.langchain.com/v0.3/python/core/runnables/langchain_core.runnables.history.RunnableWithMessageHistory.html
- LangChain `SQLChatMessageHistory` reference: https://reference.langchain.com/python/langchain-community/chat_message_histories/sql/SQLChatMessageHistory
- Amazon Bedrock Converse API conversation docs: https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference-call.html
