# Sub-section citation resolution plan

**Status:** Draft — design exploration, not yet approved for implementation.
**Author/owner:** to be assigned.
**Related work:**
- `docs/development_plans/2026-05-08_citation_normalization_strategy_plan.md`
- `docs/development_plans/2026-05-09_metadata_implementation_plan.md`

## Problem statement

When a user asks _"how warm do you need to keep dwelling areas?"_ against the IRC
sample, the answer correctly quotes the code text. But the **source card** shows:

- Section: `R303`
- Title: `R303 LIGHT, VENTILATION AND HEATING`

That section spans `R303.1 Habitable rooms` through `R303.10 Required heating` —
roughly 7.5 KB of body text across a dozen sub-sections. The text we cited is
specifically from `R303.10 Required heating`, but the UI gives no way to know that.

We want the source card and hover popover to display the **deepest sub-section**
that actually contains the quoted text:

- Section: `R303.10`
- Title: `R303.10 Required heating`

…while still routing retrieval through the existing section-level chunks (which
have been working well — `R303` was correctly retrieved for this query, and the
broader context helped the answer LLM reason).

## Why this matters

The IRC and similar codes are deeply hierarchical (chapter → section →
sub-section → sub-sub-section, often four levels deep). Practitioners cite
sub-sections, not parent sections. Showing `R303` when the answer is in
`R303.10` is technically correct but functionally wrong: a user verifying the
citation has to scan the entire parent section to find the relevant sentence.

The same problem will hit any structured corpus (IBC, NFPA, ASHRAE,
contracts, manuals). Solving it once at the citation layer benefits every
section-chunked document going forward.

## What we already have

1. **Quote verification** (`src/rag_lambda/citations.py`).
   The answer LLM emits `<cite source="N">VERBATIM QUOTE</cite>`. We
   substring-match the quote against the chunk's `chunk` text and attach
   verified quotes to `source.cited_quotes`. **The quote already knows where
   inside the chunk it lives** — we just don't record the offset.
2. **Boundary detection at all depths** (`section_chunking.detect_section_boundaries`).
   `_SECTION_CODE_RE` matches every `R303.1`, `R303.10`, `R311.7.5.1` etc. in
   the joined text. Right now the chunker filters most of these out via
   `max_title_words` (8) because, in many PDFs, the heading is on the same line
   as the start of the body paragraph (`"R303.10 Required heating. Where the
   winter design..."`), which inflates the matched "title" past the word cap.
   That filter protects us from sentence-style false positives, but it also
   throws away genuine sub-section starts.
3. **Heading-path metadata** (`section_chunking._heading_path_for`).
   Already computes the ancestor chain for any section id (`R303.10` →
   `["R303", "R303.10"]`).
4. **Frontend source card** (`components/chat/Message.vue`,
   `components/chat/CitationPopover.vue`, `components/AppContext.vue`).
   Reads `section`/`chapter` off the chunk metadata. Adding a sub-section
   field is a small render change.

## Three approaches considered

### A) Finer-grained chunking (one chunk per sub-section)

Split chunks at every `R30x.y` boundary instead of every parent section.

- ✅ Citation is naturally precise — each chunk already _is_ the sub-section.
- ✅ Embeddings live closer to the leaf semantics.
- ❌ Retrieval context shrinks. The current section-level chunk gave the answer
     LLM the full surrounding context (R303.1–R303.10) which often matters for
     code interpretation — e.g. exception clauses defined two sub-sections away.
- ❌ Chunk count explodes. The IRC sample alone goes from 53 to several hundred
     sub-sections, many of them very short (single-sentence definitions). More
     vectors = more cost, slower retrieval, more reranker noise.
- ❌ Sub-section sizes are extremely uneven (e.g. `R303.1` is ~1 KB, `R303.5.2`
     is two sentences). Sentence-sized chunks hurt embedding quality.
- ❌ Requires full re-ingestion of every structured PDF.

**Verdict:** wrong tool. The current retrieval is doing well at section
granularity; trading that for citation precision is a net loss.

### B) Hybrid — coarse chunks, fine sub-section index (recommended)

Keep section-level chunks for retrieval and embeddings. During ingestion,
detect _all_ sub-section boundaries that fall inside each chunk and store
them as a **sub-section index** on the chunk's sidecar metadata:

```jsonc
// per-chunk sidecar
{
  "chunk_id": "..._section_r303",
  "section": "R303",
  "section_index": [
    { "id": "R303",    "title": "LIGHT, VENTILATION AND HEATING", "char_start": 0,    "char_end": 230 },
    { "id": "R303.1",  "title": "Habitable rooms",                 "char_start": 230,  "char_end": 1140 },
    { "id": "R303.2",  "title": "Adjoining rooms",                 "char_start": 1140, "char_end": 1820 },
    // …
    { "id": "R303.10", "title": "Required heating",                "char_start": 6800, "char_end": 7540 }
  ]
}
```

At cite time, `normalize_answer_citations` already locates the quote in the
chunk (we use `.find()` for substring matching). Walk the `section_index` to
find the deepest entry whose `[char_start, char_end]` window contains the
match offset, and attach the resolved entry to the per-source response:

```jsonc
{
  "citation_number": 1,
  "section": "R303",                          // chunk-level (kept for filters)
  "cited_subsections": [
    { "id": "R303.10", "title": "Required heating", "quote_index": 0 }
  ],
  "cited_quotes": ["Where the winter design temperature ..."]
}
```

One `cited_subsections` entry per verified quote, linked by index so the UI
can show the right sub-section beside the right quote when multiple quotes
come from the same chunk.

- ✅ No retrieval changes. Embeddings, vector DB, KB sync, filtering stay
     identical. No corpus re-embedding needed (only re-ingestion to populate
     `section_index` in sidecars; the KB itself doesn't read these fields).
- ✅ Citation precision is exact and verifiable — we use the **already-verified
     quote offset**, not LLM self-reporting.
- ✅ Frontend gets per-quote sub-section info and falls back gracefully to the
     section-level metadata when no sub-section index exists (older chunks,
     non-IRC docs).
- ✅ The cost of building `section_index` is essentially free: we already scan
     for boundaries during chunking; we just stop discarding the deep ones.
- ⚠️ Requires the `max_title_words` filter loosened (or replaced with a
     different anti–false-positive heuristic) so genuine sub-section starts
     like `R303.10 Required heating.` aren't dropped just because the body
     text spilled onto the same line. Proposal: when the match has a
     leading-number pattern, treat only the part up to the first sentence
     terminator (`.`, `:`, newline) as the title and let body text live in
     the remainder of the line.
- ⚠️ Re-ingestion of the IRC sample is needed to populate sidecar
     `section_index`. Easy: re-trigger the document via S3 landing.

**Verdict:** best fit. Surgical change, fits existing pipeline shape.

### C) Frontend-only resolution

Parse the chunk text in the browser and walk regex matches to find the
nearest preceding `R\d+(\.\d+)+` heading above the quote.

- ✅ Zero backend change.
- ❌ Heading patterns are document-family specific (IRC vs IBC vs AWC vs NFPA);
     the matcher would have to grow per-corpus.
- ❌ Duplicates logic that already exists in `section_chunking`.
- ❌ Logic that decides "is this a real heading?" belongs server-side, where
     it's testable in pytest. Locked into JS makes it ad-hoc.
- ❌ Doesn't fix the same problem for any future non-frontend consumer
     (MCP retrieval tool, evals, audit exports).

**Verdict:** rejected. Backend is the right home for citation metadata.

### Bonus: LLM-emitted sub-section id

Have the answer LLM emit `<cite source="1" subsection="R303.10">QUOTE</cite>`.

- ✅ Trivial to add to the prompt.
- ❌ LLM can hallucinate ids that don't exist in the chunk.
- ❌ Requires a verification step anyway, at which point we might as well
     resolve it deterministically from the verified quote's offset.

**Verdict:** rejected. We already have the ground truth (quote location);
asking the LLM to repeat it adds a failure mode without adding information.

## Recommended approach: Option B

### Ingestion changes

1. **Two-pass heading detection** in `section_chunking.detect_section_boundaries`:
   - **Pass 1:** identify "primary" boundaries (top-level sections) — these
     become chunks, exactly as today.
   - **Pass 2:** for each primary chunk, run the same regex but with relaxed
     title constraints (strip body text from the title at first sentence
     terminator) to find all interior sub-section markers. Emit them as a
     `section_index` list keyed by char offset _relative to the chunk body_.
2. **Sidecar enrichment** — add `section_index` to the chunk metadata in
   `build_section_chunk_artifacts`. This rides on the existing sidecar JSON;
   neither Bedrock KB nor the catalog table read it.
3. **Catalog row** — optionally also stash a compact `subsection_count` and
   the deepest-level reached, for analytics. Not required for v1.

### Citation-resolver changes (`src/rag_lambda/citations.py`)

1. When a quote verifies, record the offset of the match start in the
   normalized comparison string. Map back to the original chunk text via the
   normalization deltas we already track for soft-hyphen repair.
2. If the source dict has a `section_index`, find the deepest entry whose
   `[char_start, char_end]` contains the match offset.
3. Append the resolved entry to a new per-source list:
   `source["cited_subsections"]` (parallel to `cited_quotes` by index).
4. Leave `section` / `chapter` / `heading_path` untouched — they describe
   the chunk, not the citation, and downstream filters depend on them.

### Wire response

Extend `rag_lambda.api.models.Source`:

```python
class CitedSubsection(BaseModel):
    id: str
    title: str

class Source(BaseModel):
    # existing fields …
    cited_subsections: List[CitedSubsection] = Field(default_factory=list)
```

Persistence: rides on `AIMessage.additional_kwargs.sources` exactly like
`cited_quotes` does today, so it rehydrates from history without schema
change.

### Frontend changes

Three small render edits, all reading the same new field:

- `components/chat/Message.vue` — under each verbatim quote, show
  `{subsection.id} · {subsection.title}` when present, falling back to
  `{src.section}` / `{src.title}`.
- `components/chat/CitationPopover.vue` — replace the `{source.section}`
  pill with the resolved sub-section pill when available.
- `components/AppContext.vue` — same swap on the right-rail source card.

A `useChat.ts → mapSources` change maps `cited_subsections` through to the
frontend `Source` shape.

### Behavior when no sub-section index exists

- Older chunks ingested before this change ship with no `section_index` —
  the resolver falls back to today's chunk-level metadata. No UI breakage.
- Documents whose heading detection genuinely couldn't find sub-section
  markers (e.g. a flat memo) get the same fallback.

## Open questions

1. **Multi-quote, multi-sub-section.** A single source can carry multiple
   verified quotes. The proposal stores one resolved sub-section per quote.
   The UI needs to decide whether to show all of them in the source card
   (probably yes, as separate rows) or roll them up to a single deepest
   common ancestor (probably no — loses precision).
2. **Quote spans two sub-sections.** Pick the sub-section containing the
   quote start, or the lowest common ancestor of start and end? Start is
   simpler and matches "which sub-section is this quote from" intuitively.
   LCA is safer but adds complexity. Start wins until we see a real
   counter-example.
3. **What counts as a "real" sub-section?** Code corpora are clear
   (`R303.10 …`). Manuals or contracts may have prose headings (`3.2 Scope
   of services`). The two-pass approach needs a pattern table per
   `hierarchy_kind`. We already have `section_codes` vs `chapter_heading`;
   adding a `prose_outline` variant later is straightforward.
4. **Where does the title come from?** Today's regex captures the heading
   line including body. For `section_index` entries we want just the
   heading itself — _"Required heating"_, not _"Required heating. Where
   the winter design temperature…"_. Truncate at first `.` followed by
   space, or at a hard newline, whichever comes first.
5. **Should `section` on the chunk now reflect the dominant sub-section
   when there's only one with body text?** Probably no — keep `section` as
   the chunk identity for filtering. Sub-section precision lives on the
   citation, not the chunk.

## Verification plan (when implemented)

1. Unit tests in `tests/test_section_chunking.py` for `section_index`
   construction: known synthetic R303-style text → expected list of
   `{id, title, char_start, char_end}`.
2. Unit tests in `tests/test_citations_quotes.py` for sub-section
   resolution: chunk with `section_index`, quote that lives in
   `R303.10`'s window → `cited_subsections == [{"id": "R303.10", …}]`.
3. End-to-end in sandbox: re-ingest the IRC sample (no embeddings
   regenerated; only sidecar JSON changes), re-ask "how warm…", confirm
   the source card shows `R303.10 Required heating`.
4. Regression: ensure existing R303 retrieval still surfaces the right
   chunk (no embedding change so this should be a no-op).

## Out of scope

- Multimodal sub-section detection (figure captions / table titles as
  pseudo-sub-sections). Possible follow-up.
- Changing the retrieval-time embedding granularity (Option A).
- Re-shaping the catalog table to store sub-section rows. Possible
  follow-up if we want per-sub-section analytics.
- Re-embedding the corpus. None needed for Option B.

## Decision needed

Before implementation:

1. Confirm Option B over Option A. The decision turns on whether the
   retrieval team is comfortable keeping coarse chunks long-term.
2. Confirm the "deepest enclosing sub-section" rule (vs. LCA for spanning
   quotes).
3. Confirm UI placement: replace the chunk-level pill, or stack
   sub-section above chunk-level for context?
