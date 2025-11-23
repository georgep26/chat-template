# Evaluation 

## Notes
Below is a ChatGPT summarization of current techniques for reference.

1. Big picture: what you’re actually trying to measure

Most evaluation setups for RAG & LLM apps try to cover a few core dimensions:
	1.	Task success / utility
	•	Did the system actually help the user accomplish the task (answer the question, complete the workflow, etc.)?
	2.	Correctness & faithfulness (groundedness)
	•	Is the answer factually correct?
	•	Is every claim supported by the retrieved context (for RAG)? This is faithfulness/groundedness.  ￼
	3.	Retrieval quality (specific to RAG)
	•	Are we retrieving the right chunks / documents? How often is the answer actually present in the retrieved context?  ￼
	4.	Relevance & completeness
	•	Is the answer on-topic and does it address all parts of the query?
	•	Is the context relevant or noisy?
	5.	Safety & compliance
	•	Toxicity, bias, PII leaks, policy violations, etc.  ￼
	6.	User experience & operations
	•	Latency, reliability, cost per request, robustness to weird inputs, etc.  ￼

Modern frameworks usually encode these as multiple metrics rather than a single score.

⸻

2. RAG-specific: evaluate retrieval and generation separately

Most recent “best practices” guides emphasize decoupling retrieval from generation.  ￼

2.1 Retrieval metrics

If you have labeled “gold” passages/documents, you can use classic IR metrics:
	•	Recall@K / Precision@K / Hit@K / nDCG@K – did the retriever bring the ground-truth doc into the top-K results, and where?  ￼

If you don’t have detailed labels, common approaches:
	•	Context relevance scoring with an LLM-as-a-judge: “Given the query and this chunk, how relevant is it (0–5)?”
	•	Context recall/precision (RAGAS terminology): measure how much of the necessary info is actually in the retrieved context vs noise.  ￼

2.2 Generation metrics

Given the query + retrieved context + answer:
	•	Answer correctness (does it answer the question?)
	•	Faithfulness / groundedness: are the answer’s claims supported by the context? (This is the main anti-hallucination metric for RAG.)  ￼
	•	Answer relevance & completeness: is it on-topic, and does it cover all aspects of the question?
	•	Citation quality (if you show citations to the user): are links pointing to genuinely relevant passages?

Most modern frameworks implement these using LLM-as-a-judge: a strong model grades outputs with a rubric instead of using ROUGE/BLEU.  ￼

⸻

3. Evaluation methods: human, reference-based, and LLM-as-a-judge

3.1 Human evaluation

Still the gold standard, especially at the start:
	•	Set up a rubric (e.g., 1–5 for correctness, faithfulness, style, safety).
	•	Have subject matter experts rate a representative sample of queries/answers.
	•	Use this to calibrate/validate your automated metrics.

3.2 Reference-based metrics (BLEU/ROUGE/etc.)

For classic NLP datasets (summarization, translation), people used BLEU/ROUGE/METEOR. These are now considered weak for RAG QA, because:
	•	There may be many valid answers with different wording.
	•	You primarily care about factual correctness and groundedness, not n-gram overlap.

So they’re used less for RAG, more for legacy tasks.  ￼

3.3 LLM-as-a-judge (the current default)

The dominant modern pattern is:
	1.	Define a scoring rubric (e.g., 0–1 binary, 1–5 ordinal, or letter grades).
	2.	Call a strong model with a structured prompt that sees:
	•	The question
	•	The system’s answer
	•	Optionally the ground truth and/or retrieved context
	3.	Ask it to:
	•	Explain its reasoning and
	•	Output a structured score.

OpenAI’s Evals and Graders APIs are essentially formalized versions of this: you define “graders” for correctness, safety, etc., and run them at scale; the same grader definitions can be reused for reinforcement fine-tuning (RFT) to optimize models on those scores.  ￼

Third-party tools (RAGAS, DeepEval, TruLens, Patronus, Arize, LangSmith, etc.) also lean heavily on LLM-as-a-judge for RAG metrics.  ￼

⸻

4. Common RAG metrics & frameworks (what people actually use)

4.1 RAGAS (very common in open source)

RAGAS defines a set of RAG-specific metrics:  ￼
	•	Faithfulness – are answer’s claims supported by context?
	•	Contextual relevance – how relevant is the retrieved context to the query?
	•	Answer relevance – how well does the answer address the query?
	•	Context recall / precision – how much necessary information is in the context vs noise?

You feed in (question, answer, contexts, ground-truth) and it outputs per-sample scores + an aggregate.

4.2 DeepEval & TruLens
	•	DeepEval – “pytest for LLMs” with a bunch of built-in metrics (correctness, faithfulness, toxicity, etc.), dataset management, and CI/CD integration.  ￼
	•	TruLens – focuses on tracing & feedback (instrument calls, log prompts/contexts, and grade them with LLMs). Good for runtime monitoring plus offline eval.  ￼

4.3 Other notable tools

Recent surveys list tools like LangSmith, Arize, Evidently, Patronus AI, Traceloop, Qdrant’s RAG eval toolkit, Vertex AI evals, Bedrock eval workflows, etc.  ￼

They generally provide:
	•	Dataset and experiment management
	•	Built-in graders (correctness, groundedness, toxicity, etc.)
	•	Dashboards for tracing and observability
	•	Alerting when metrics drift

⸻

5. Evaluation in production: observability & continuous testing

Evaluation isn’t just an offline thing – most current best-practice guides emphasize continuous monitoring:
	•	Instrumentation / tracing: log prompts, retrieved docs, model responses, and scores.  ￼
	•	Online metrics:
	•	Latency, error rate, cost
	•	Implicit feedback (clicks, abandonment, retries)
	•	Explicit feedback (thumbs up/down, 1–5 ratings)
	•	Shadow testing & A/B tests:
	•	Route some traffic to a new retriever or prompt and compare metrics vs baseline.

Some frameworks (including OpenAI Evals + Graders and several third-party tools) are designed to slot into CI/CD so you can run eval suites automatically when you change prompts, models, or retrieval settings.  ￼

⸻

6. A simple mental model for designing your own eval framework

Most “how to build an eval framework” articles converge on roughly this pipeline:  ￼
	1.	Define objectives & risks
	•	For your RAG app: what does success look like?
	•	What’s unacceptable (hallucinations, missing citations, unsafe advice, etc.)?
	2.	Build a representative test set
	•	User-like queries – including “happy path” and edge cases.
	•	For RAG: include questions that require using the context, multi-hop questions, long-tail queries, etc.
	3.	Choose metrics
	•	Retrieval: recall@K, context relevance, context recall/precision.
	•	Generation: correctness, faithfulness, relevance, completeness, style, safety.
	•	Ops: latency, cost, robustness.
	4.	Choose evaluators
	•	Human eval for a subset (to calibrate).
	•	LLM-as-a-judge graders for scale (possibly using OpenAI’s evals/graders or frameworks like RAGAS/DeepEval).
	5.	Automate & integrate
	•	Build scripts or pipelines that:
	•	Run your app on the test set
	•	Compute metrics
	•	Produce dashboards or reports (by commit/model/prompt version)
	•	Integrate into CI/CD so changes must “pass” eval thresholds before deployment.
	6.	Close the loop
	•	Use eval results to:
	•	Tune retrieval (chunking, embedding model, reranker, filters)
	•	Adjust prompts / system instructions
	•	Choose or fine-tune models (with RFT or classic fine-tuning) targeting your graders.  ￼

⸻
