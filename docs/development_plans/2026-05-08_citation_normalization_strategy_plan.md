# Citation Normalization Strategy Plan

Created: 2026-05-08  
Status: V1 deterministic post-processing implemented  
Scope: Normalize RAG answer citations so the final answer and returned sources include only sources actually cited by the answer.

Implementation note: V1 now uses model-facing sentinel citations in the form `<[N]>`, normalizes them to user-facing `[N]` citations by first use, returns only cited sources, and persists only cited `source_ids` with the visible assistant turn.

## Goals

Build a citation strategy for the RAG Lambda that:

- lets the answer LLM cite retrieved chunks while answering;
- renumbers citations by first use in the final answer, so `<[3]>`, `<[9]>`, `<[1]>` becomes `[1]`, `[2]`, `[3]`;
- returns only cited sources in `ChatResponse.sources`;
- persists only cited user-facing source ids with the visible assistant turn;
- returns no sources when the answer has no citations, such as an "I don't know" response or an answer grounded only in prior conversation history;
- keeps the implementation compatible with the current custom LangGraph RAG flow;
- leaves a path to stronger provider-native or structured citation handling later.

## Current Repo State

Current active flow:

1. `src/rag_lambda/graph/retrieval.py` retrieves source chunks from Bedrock Knowledge Bases.
2. Retrieved chunks are formatted into numbered `<source number="N">` blocks and inserted as `SystemMessage(name="retriever_context")`.
3. `src/rag_lambda/graph/prompts.py` asks the answer LLM to cite factual claims using sentinel source markers like `<[1]>`.
4. `src/rag_lambda/graph/nodes.py` extracts the final answer text from the answer LLM.
5. `src/rag_lambda/main.py` normalizes sentinel citations, converts only cited `final_state["sources"]` items into API `Source` objects, stores cited `source_ids` in persistence metadata, and returns only cited sources to the frontend.

Remaining gap: quote validation is still prompt-based. The backend now filters and renumbers cited sources, but it does not yet prove that quoted text appears verbatim in the cited chunk.

## Research Summary

### Bedrock Knowledge Bases retrieval

The Bedrock `Retrieve` API returns source chunks as retrieval results with `content`, `metadata`, `location`, and `score`. AWS documents that metadata attributes are returned as JSON values, which matches our plan to preserve chunk metadata for citations and frontend display.

AWS also notes that source chunks are returned directly in relevance order. That is useful for retrieval, but it is not the same as the citation order users should see in a final answer.

Reference: [AWS Bedrock Retrieve docs](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-retrieve.html)

### Bedrock RetrieveAndGenerate citations

Bedrock `RetrieveAndGenerate` can return generated answers with citation events and retrieved references. Its citation objects identify the generated response span that a citation applies to and include the retrieved references used for that span.

This is the managed version of what we are trying to approximate. However, `RetrieveAndGenerate` couples retrieval and generation inside Bedrock. Our repo currently has a custom graph with rewrite, clarify, split, trace events, chat history, and custom prompt construction. Switching wholesale to `RetrieveAndGenerate` would reduce control over this workflow.

Reference: [AWS Bedrock RetrieveAndGenerate docs](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-retrieve-generate.html)

### LangChain structured output

LangChain supports structured output through provider-native strategies or tool-calling strategies. Structured output can return validated Pydantic objects or JSON-like dictionaries instead of free-form text.

This is relevant because one possible strategy is to ask the answer model for a structured object containing final answer segments plus source references. That would avoid regex parsing, but it increases model/provider coupling and complicates streaming.

Reference: [LangChain structured output docs](https://docs.langchain.com/oss/python/langchain/structured-output)

### Claude native citations

Claude supports document citations. Anthropic documents that native citations are more reliable than prompt-based citation approaches because citation pointers are parsed into response formats and guaranteed to refer to provided documents. Anthropic also documents response content blocks that include cited text and document indices.

Important constraints:

- Citation document indices are based on the list of documents in the request.
- Citation metadata can include cited text and location fields.
- Native citations and structured outputs are incompatible in the same request.
- Bedrock exposes citation-related support for Claude models, including `SearchResultBlock`, but this is model-specific and not a generic path for all generation models.

References:

- [Claude citations docs](https://platform.claude.com/docs/en/build-with-claude/citations)
- [AWS Bedrock SearchResultBlock API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_SearchResultBlock.html)

## Candidate Approaches

### Option A: Deterministic post-processing of sentinel citations

Keep the current answer prompt strategy: the answer LLM sees retrieved source blocks numbered by retrieval order and emits internal citation markers like `<[1]>`, `<[3]>`, `<[9]>`.

After answer generation:

1. Parse sentinel citation markers from the final answer. The v1 marker format is `<[N]>` where `N` is the retrieval source number.
2. Ignore invalid citation numbers that do not map to retrieved sources.
3. Build an ordered unique list of cited retrieval source numbers based on first appearance.
4. Create a mapping from original retrieval source number to final display citation number.
5. Rewrite the answer text to use final display citation numbers in the user-facing `[N]` format.
6. Filter `sources` down to only cited source objects.
7. Add a `citation_number` field to returned source objects.
8. Persist only the normalized answer and cited source ids in user-visible message metadata.

Example:

```text
Raw answer:
The duct system moves conditioned air through the building <[9]>. The source says "duct systems distribute conditioned air" <[9]>. It may include supply and return ducts <[3]>.

Retrieved source order:
[1] irrelevant
[2] irrelevant
[3] duct components
...
[9] duct definition

Normalized answer:
The duct system moves conditioned air through the building [1]. The source says "duct systems distribute conditioned air" [1]. It may include supply and return ducts [2].

Returned sources:
[1] original source 9
[2] original source 3
```

Pros:

- Smallest change from current architecture.
- Model-agnostic: works with Nova, Claude, or any text model that follows sentinel citation instructions.
- Deterministic renumbering.
- Easy to unit test.
- Works with synchronous request/response immediately.
- Keeps retrieval and answer nodes decoupled from provider-specific citation APIs.

Cons:

- Still trusts prompt-based citations, though the sentinel marker makes parsing deterministic.
- Does not prove that a cited quote actually exists in the cited chunk.
- Requires careful handling of invalid or malformed sentinel syntax, but avoids collisions with ordinary bracketed text.
- Streaming needs extra design because displayed citation numbers may change after the full answer is known.

Recommendation: use this as v1.

### Option B: Structured answer object from the answer node

Ask the answer LLM to return structured output, such as:

```python
class CitedAnswer(BaseModel):
    answer_markdown: str
    cited_sources: list[int]
    quotes: list[CitedQuote]
```

The backend would validate the object, renumber cited sources, and render final markdown itself.

Pros:

- Avoids regex parsing of free-form citations.
- Easier to require `cited_sources` to match answer content.
- Can collect quotes and source references as first-class fields.
- Better long-term shape if the frontend wants source cards tied to exact answer spans.

Cons:

- Requires model support for tool calling or structured output.
- May be less compatible with our current Bedrock/Nova model mix.
- Adds more prompt/schema complexity.
- Native Claude citations cannot be used with structured output.
- Streaming structured output is possible but more complex than plain answer streaming.

Recommendation: do not use for v1. Reconsider if prompt-based citation parsing becomes unreliable or if the frontend needs richer citation cards.

### Option C: Add a citation verifier/normalizer graph node

Keep the answer node focused on natural language. Add a second node after `answer` that verifies and normalizes citations.

Possible verifier behavior:

- deterministic citation parsing and renumbering;
- quote validation by checking quoted substrings against cited chunks;
- optional LLM repair pass if citations are missing, invalid, or unsupported;
- remove unsupported citations or ask the model to regenerate with valid citations.

Pros:

- Clean graph boundary: answer generation and citation validation are separate workflow steps.
- Easier to trace in `chat_turn_events`.
- Can start deterministic and later add LLM-based repair without changing answer generation.
- Better fit once we add frontend-visible agent steps.

Cons:

- More graph state and plumbing.
- If it includes an LLM repair call, it adds latency and cost.
- If deterministic-only, it is similar to Option A with a little more ceremony.

Recommendation: implement v1 as deterministic logic, but structure it so it can become a `normalize_citations` graph node later.

### Option D: Provider-native citations with Claude / Bedrock citation blocks

Instead of prompt-based bracket citations, provide retrieved chunks as citable documents or Bedrock search result blocks and let Claude return citation-rich content blocks. The backend would map provider citation indices back to retrieved sources, convert them to `[1]`, `[2]`, `[3]`, and return only used references.

Pros:

- Strongest citation reliability when using supported Claude models.
- Provider returns cited text and source pointers.
- Better quote support with less prompt engineering.
- Streaming citation deltas are supported by the provider.

Cons:

- Provider/model-specific.
- Requires changing how we call Bedrock/Claude through LangChain or possibly bypassing LangChain for the answer call.
- Not compatible with structured output in the same request.
- May not work with Nova models or other configured generation models.
- Requires new response parsing logic for citation content blocks.

Recommendation: keep as a future advanced mode if we standardize generation on supported Claude models.

### Option E: Use Bedrock RetrieveAndGenerate

Let Bedrock perform retrieval and generation, then consume its citation response.

Pros:

- Managed source attribution.
- Bedrock response already returns generated response spans and retrieved references.
- Less custom prompt and citation work.

Cons:

- Does not fit the current custom graph cleanly.
- Reduces control over rewrite, split, trace, custom history, and custom metadata formatting.
- Harder to support our frontend step trace exactly as planned.

Recommendation: not recommended for this repo's main RAG Lambda path, but useful as a benchmark/evaluation path.

## Recommended V1 Design

Implement Option A first, with code organized so Option C is easy later.

### 1. Preserve retrieval source numbers internally

In `src/rag_lambda/graph/retrieval.py`, each source should carry:

```python
{
    "retrieval_source_number": 1,
    "document_id": "...",
    "source_type": "document",
    "score": 0.91,
    "chunk": "...",
    "metadata": {...},
}
```

`retrieval_source_number` is the number shown to the LLM in the prompt. It is not necessarily the final citation number shown to the user.

### 2. Add citation normalization helper

Add a helper, likely in a new module:

```text
src/rag_lambda/citations.py
```

Suggested public function:

```python
def normalize_answer_citations(
    answer: str,
    sources: list[dict],
) -> CitationNormalizationResult:
    ...
```

Suggested result:

```python
class CitationNormalizationResult(BaseModel):
    answer: str
    sources: list[dict]
    citation_map: dict[int, int]  # retrieval_source_number -> citation_number
    invalid_citations: list[int]
```

Parsing rules:

- Treat `<[12]>` as a citation candidate.
- Treat repeated adjacent markers like `<[1]><[3]>` as two citations.
- Ignore ordinary bracketed text like `[note]`, `[2024]`, or `[1]` because only `<[N]>` is treated as an internal citation marker.
- Only accept citations that map to an existing `retrieval_source_number`.
- Build final citation numbers in first-use order.
- Replace every valid sentinel marker with its final display citation, e.g. `<[9]>` -> `[1]`.
- Remove invalid citation markers or keep them only in trace/debug metadata, not in final user-visible answer.
- If there are no valid citations, return `sources=[]`.

Initial examples:

| Raw answer | Cited retrieval numbers | Final answer | Returned sources |
| --- | --- | --- | --- |
| `A <[9]>. B <[3]>.` | 9, 3 | `A [1]. B [2].` | old 9, old 3 |
| `A <[3]>. B <[3]>.` | 3 | `A [1]. B [1].` | old 3 |
| `I don't know.` | none | `I don't know.` | none |
| `Based on our prior chat, yes.` | none | unchanged | none |
| `A <[999]>.` | invalid | `A.` or repair path | none |

### 3. Add `citation_number` to API sources

Update `src/rag_lambda/api/models.py`:

```python
class Source(BaseModel):
    citation_number: int
    document_id: str
    source_type: str
    score: float
    chunk: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
```

Decision: keep `retrieval_source_number` out of the frontend response unless needed for debug. It can stay in trace metadata.

### 4. Normalize before persistence and response construction

In `src/rag_lambda/main.py`, normalize immediately after extracting raw `answer` and raw `final_state["sources"]`, before:

- creating `Source` response models;
- setting `source_ids`;
- appending visible messages;
- creating `ChatResponse`.

Persist:

- normalized answer text as the visible `AIMessage`;
- `source_ids` from normalized/cited sources only;
- optional `citation_map` in message metadata if useful for debugging.

Do not persist unused retrieval sources in user-visible message metadata.

Open decision: `chat_turn_events` currently may store retrieval diagnostics for all retrieved candidates. If "stored to DB" must strictly mean no unused source ids anywhere, update the retrieval trace event to store only counts/scores or hash/source indexes. If internal observability is allowed, keep retrieval diagnostics in `chat_turn_events` but never expose unused sources through `ChatResponse`.

### 5. Update answer prompt

Update the current citation instructions to use the sentinel marker and add these constraints:

- "Use retrieval source numbers exactly as shown in the source blocks, but write citations as `<[N]>`. The backend will convert them to display citations."
- "Do not use plain `[N]` citations in the model response. Plain `[N]` may appear in quoted source text and is reserved for the postprocessed, user-facing answer."
- "Do not cite conversation history with sentinel source markers. If answering only from conversation history, include no source citations."

This makes it clear that citations are only for retrieved chunks.

### 6. Quote handling in v1

Keep quote generation prompt-based for v1. The answer can include quotes inline:

```text
The duct system distributes conditioned air through the building <[1]>.

Source quote: "Duct systems distribute conditioned air." <[1]>
```

Do not add a structured quote model in v1 unless needed. Add deterministic quote validation later:

- parse quoted text near each citation;
- check whether quote text appears in the cited chunk after whitespace normalization;
- if quote is missing, emit a trace warning or run a repair prompt.

## Recommended Later Enhancements

### Add citation verifier node

Once v1 is working, promote citation normalization into an explicit graph node:

```text
answer -> normalize_citations -> END
```

This node can produce frontend-visible steps such as:

- "Checked citations"
- "Filtered cited sources"
- "Renumbered sources"

### Add quote validation

Add a deterministic quote checker:

- extract quoted spans from the answer;
- associate quotes with the nearest following sentinel citation;
- normalize whitespace and punctuation;
- check if the quote appears in the cited chunk;
- store validation results in trace events.

If validation fails, either:

- leave answer unchanged but flag trace metadata; or
- run a citation repair prompt.

### Add native Claude citation mode

If generation standardizes on Claude through Bedrock, evaluate provider-native citations:

- pass retrieved chunks as citable custom content or search result blocks;
- parse citation blocks from the response;
- map provider document indices to retrieved sources;
- render bracket citations and cited source cards from provider citation data.

This could become the most reliable long-term path, but it should be model-specific behind config:

```yaml
rag_chat:
  generation:
    citation_mode: prompt_postprocess | claude_native
```

### Streaming implications

Synchronous v1 can normalize after the full answer is generated.

For future streaming, choose one of these strategies:

1. Stream raw draft tokens, then send a final `answer.completed` event with normalized text and cited sources. This is simplest but can cause the final displayed message to change.
2. Buffer answer tokens server-side until normalization completes, then stream the final normalized answer. This preserves stable citations but delays visible text.
3. Assign citation numbers on first observed sentinel marker during streaming and rewrite outgoing citation tokens on the fly. This is more complex and brittle with partial tokens.
4. If using provider-native citations, map citation delta events as they arrive and update source cards progressively.

Recommended streaming path: for the first streaming implementation, stream workflow progress events live, but send the final answer after normalization. Add token streaming later once citation normalization behavior is stable.

## Implementation Checklist

1. Add `src/rag_lambda/citations.py`.
2. Add unit tests for citation parsing, renumbering, source filtering, repeated citations, invalid citations, and no-citation answers.
3. Add `retrieval_source_number` to graph source dictionaries.
4. Add `citation_number` and `metadata` to the API `Source` model.
5. Normalize answer and sources in `main.py` before persistence and response creation.
6. Persist normalized answer and cited source ids only.
7. Update answer prompt with conversation-history/no-source-citation guidance.
8. Update frontend docs after implementation:
   - `docs/frontend_interface.md`
   - source rendering expectations;
   - no sources when no citations;
   - citation numbers match displayed answer numbers, not retrieval rank.
9. Add tests around `main.py` response and persistence metadata.
10. Use local debugger with `RAG_DEBUG_ANSWER_PROMPT=true` to inspect prompt and output behavior.

## Testing Plan

Unit tests:

- `normalize_answer_citations("A <[9]>. B <[3]>.", sources)` returns `A [1]. B [2].`.
- repeated citations preserve the same new number.
- invalid citations do not create source records.
- answers with no citations return no sources.
- multi-source clauses like `<[9]><[3]>` normalize to `[1][2]`.
- source order follows first citation use, not retrieval order.

Integration-style tests:

- fake graph returns an answer with sentinel citations to non-contiguous retrieval source numbers;
- `main()` returns normalized answer and only cited sources;
- persistence metadata contains only cited `source_ids`;
- no-citation answer stores an empty `source_ids` list.

Manual debugger checks:

- baseline answer with retrieval citations;
- follow-up answer using chat history only, with no sources;
- answer that cites multiple chunks;
- answer that says it does not know, with no sources.

## Open Questions

1. Should `chat_turn_events` keep all retrieved source ids for internal debugging, or should it only store counts/scores to avoid persisting unused source identifiers?
2. Should the frontend receive `citation_number` only, or both `citation_number` and `retrieval_source_number` in a debug-only field?
3. Should invalid citations trigger a repair LLM call in v1, or should v1 simply strip/log invalid citations?
4. Should quoted evidence become a structured field on `Source`/`Citation`, or remain part of the answer markdown until we add quote validation?

## Recommendation

Implement deterministic post-processing now:

- let the answer LLM cite retrieval source numbers with sentinel markers;
- normalize and renumber citations after the full answer is generated;
- filter sources to only cited sources;
- persist only normalized answer and cited source ids;
- keep the code organized so a future verifier node or Claude-native citation mode can replace the internals without changing the frontend contract.
