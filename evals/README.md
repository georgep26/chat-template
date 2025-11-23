# Evaluation Framework

A comprehensive, config-driven evaluation framework for RAG (Retrieval-Augmented Generation) systems with support for multiple metrics, flexible client modes, and rich output formats.

## Features

- **Multiple Metrics**: RAGAS metrics (faithfulness, answer relevancy, context precision/recall) and custom correctness metrics
- **Flexible Client Support**: Run evaluations against local RAG implementations or AWS Lambda functions
- **LLM-as-a-Judge**: Custom correctness metrics using OpenAI or Bedrock models
- **Rich Outputs**: Generate JSON summaries, CSV per-sample results, and HTML reports
- **S3 Integration**: Optional upload of results to S3
- **Judge Validation**: Compare LLM judge scores against human labels
- **Statistical Analysis**: Bootstrap confidence intervals and comprehensive statistics

## Quick Start

### 1. Install Dependencies

The evaluation framework dependencies are included in `evals/requirements.txt` and will be installed via `environment.yml`:

```bash
conda env update -f environment.yml
```

### 2. Create Configuration File

Create an `evals_config.yaml` file. See `evals/evals_config.yaml` for a complete example.

Key configuration sections:
- `run`: Execution mode (local/lambda), concurrency, experiment name
- `rag_app`: RAG client configuration (entrypoint, request/response mappings)
- `data`: CSV file paths and column mappings
- `metrics`: Enable/configure RAGAS and correctness metrics
- `llm`: Default and judge model configurations
- `outputs`: Output formats and storage (local/S3)
- `judge_validation`: Optional judge validation dataset

### 3. Prepare Evaluation Data

Create a CSV file with evaluation questions and reference answers. Required columns:
- Question column (configurable via `data.eval_question_column`)
- Reference answer column (configurable via `data.eval_reference_column`)
- Optional ID column (configurable via `data.eval_id_column`)

Example CSV structure:
```csv
id,question,reference_answer
1,"What is the scope of Chapter 1?","Section R101 covers scope and general requirements."
2,"Where is the building official defined?","Section R104 defines duties and powers."
```

### 4. Run Evaluation

```bash
# Basic evaluation with all output formats
python -m evals.cli --config evals/evals_config.yaml --output-type html,json,csv

# Run with judge validation
python -m evals.cli --config evals/evals_config.yaml --output-type html --run-judge-validation

# Override output types via CLI
python -m evals.cli --config evals/evals_config.yaml --output-type json
```

## Configuration Reference

### Run Configuration

```yaml
run:
  mode: "local"              # "local" or "lambda"
  max_concurrent_async_tasks: 20        # Maximum number of concurrent asyncio tasks
  evaluation_run_name: "irc_rag_v2"  # Output directory name
```

### RAG App Configuration

```yaml
rag_app:
  local_entrypoint: "src.rag_lambda.main:main"  # Module:function for local mode
  lambda_function_name: "my-rag-lambda-prod"    # Lambda function name (lambda mode)
  request_template:
    question_key: "question"
    metadata_key: "metadata"
  response_keys:
    answer: "answer"
    contexts: "contexts"
```

### Data Configuration

```yaml
data:
  eval_csv_path: "data/validation_questions.csv"
  eval_id_column: "validation_question_id"
  eval_question_column: "validation_question"
  eval_reference_column: "answer"
```

### Metrics Configuration

```yaml
metrics:
  ragas:
    enabled: true
    metric_names: ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
    judge_model:
      provider: "openai"     # "openai" or "bedrock"
      model: "gpt-4o-mini"
      openai_api_key_env: "OPENAI_API_KEY"
      # For Bedrock:
      # region_name: "us-east-1"
      # model: "anthropic.claude-3-sonnet-20240229-v1:0"
  binary_correctness:
    enabled: true
    judge_model:
      provider: "openai"
      model: "gpt-4o-mini"
      openai_api_key_env: "OPENAI_API_KEY"
      # Additional LangChain parameters (temperature, max_tokens, etc.) can be added here
  atomic_correctness:
    enabled: false
    judge_model:
      provider: "openai"
      model: "gpt-4o-mini"
      openai_api_key_env: "OPENAI_API_KEY"
      # Additional LangChain parameters (temperature, max_tokens, etc.) can be added here
```

### LLM Configuration

LLM model definitions use `src.utils.llm_factory.create_llm()` to create LangChain LLM instances. The configuration accepts the same arguments as the underlying LangChain implementations (`ChatOpenAI` for OpenAI and `ChatBedrockConverse` for Bedrock), allowing you to pass through any LangChain parameters directly.

**OpenAI Configuration**:
```yaml
llm:
  default:
    provider: "openai"
    model: "gpt-4o-mini"              # LangChain ChatOpenAI 'model' parameter
    openai_api_key_env: "OPENAI_API_KEY"  # Environment variable name for API key
    temperature: 0.0                  # Optional: LangChain ChatOpenAI parameters
    max_tokens: 1000                  # Optional: any other ChatOpenAI arguments
```

**Bedrock Configuration**:
```yaml
llm:
  default:
    provider: "bedrock"
    model: "anthropic.claude-3-sonnet-20240229-v1:0"  # LangChain ChatBedrockConverse 'model' parameter
    region_name: "us-east-1"          # Required for Bedrock
    temperature: 0.0                  # Optional: LangChain ChatBedrockConverse parameters
    max_tokens: 1024                  # Optional: any other ChatBedrockConverse arguments
```

**Judge Model Configuration** (for metrics):
```yaml
metrics:
  binary_correctness:
    enabled: true
    judge_model:
      provider: "openai"
      model: "gpt-4o-mini"
      openai_api_key_env: "OPENAI_API_KEY"
      temperature: 0.0                # Any ChatOpenAI parameter
  atomic_correctness:
    enabled: false
    judge_model:
      provider: "openai"
      model: "gpt-4o-mini"
      openai_api_key_env: "OPENAI_API_KEY"
      temperature: 0.0                # Any ChatOpenAI parameter
```

The LLM factory handles provider-specific requirements (API key lookup for OpenAI, region configuration for Bedrock) and passes all other arguments directly to the LangChain constructors. This means you can use any parameter supported by `ChatOpenAI` or `ChatBedrockConverse` in your configuration.

#### LLM Factory Details

The evaluation framework uses `src.utils.llm_factory.create_llm()` to create LangChain LLM instances. This factory:

- **Unifies configuration**: Provides a consistent interface for both OpenAI and Bedrock models
- **Handles provider-specific setup**: 
  - For OpenAI: Reads API key from environment variable specified by `openai_api_key_env`
  - For Bedrock: Requires `region_name` and uses AWS credentials from the environment
- **Passes through LangChain arguments**: All other configuration parameters are passed directly to the LangChain constructors (`ChatOpenAI` or `ChatBedrockConverse`)

This design means:
- You can use any parameter from the LangChain documentation (e.g., `temperature`, `max_tokens`, `timeout`, `streaming`, etc.)
- The configuration format closely matches LangChain's native API
- No need to learn a custom configuration format beyond the provider selection

For example, to use streaming with a custom timeout:
```yaml
judge_model:
  provider: "openai"
  model: "gpt-4o-mini"
  openai_api_key_env: "OPENAI_API_KEY"
  temperature: 0.7
  max_tokens: 2000
  timeout: 30.0
  streaming: true
```

### Output Configuration

```yaml
outputs:
  types: ["html", "json", "csv"]  # Can be overridden via CLI
  local:
    base_dir: "eval_outputs"
  s3:
    enabled: false
    bucket: "my-eval-bucket"
    prefix: "rag-evals/irc/"
```

## Output Files

Results are written to `{base_dir}/{experiment_name}/`:

- **summary.json**: Aggregate statistics for all metrics (mean, std, median, min, max, 95% CI)
- **results.csv**: Per-sample scores for all metrics
- **report.html**: Visual HTML report with metric summaries

## Judge Validation

Judge validation compares LLM judge scores against human labels to measure judge accuracy:

```yaml
judge_validation:
  enabled: true
  csv_path: "data/judge_validation_samples.csv"
  id_column: "id"
  question_column: "question"
  reference_column: "reference_answer"
  model_answer_column: "model_answer"
  human_label_column: "human_label"
```

Run with `--run-judge-validation` flag to include judge validation results in the summary.

## Architecture

The framework consists of:

- **cli.py**: Command-line interface and argument parsing
- **data.py**: CSV dataset loading and sample extraction
- **client.py**: Async RAG clients (local and Lambda)
- **metrics_base.py**: Abstract base class for metrics
- **metrics_ragas.py**: RAGAS metric collection wrapper
- **metrics_custom.py**: Custom correctness metrics
- **evals_pipeline.py**: Core orchestration logic
- **judge_validation.py**: Judge vs human comparison
- **outputs.py**: Output writers (JSON/CSV/HTML/S3)
- **stats_utils.py**: Statistical aggregation and confidence intervals

Note: Configuration loading uses `src.utils.config.read_config()` and LLM creation uses `src.utils.llm_factory.create_llm()` from the shared utilities.

## Request/Response Format Mapping

The framework adapts between evaluation format and RAG Lambda format:

**Evaluation Format**:
- `question`: str
- `metadata`: dict

**RAG Lambda Format**:
- `message`: str (from `question`)
- `conversation_id`: str (generated per sample)
- `user_id`: str (from metadata or default "eval_user")
- `retrieval_filters`: Optional[Dict] (from metadata)

**Response Adaptation**:
- Lambda returns `ChatResponse` with `answer` and `sources` (each with `snippet`)
- Framework extracts `snippet` from each `Source` to build `contexts` list for RAGAS

## Notes on Evaluation Techniques

### What to Measure

1. **Task Success / Utility**: Did the system help accomplish the task?
2. **Correctness & Faithfulness**: Is the answer factually correct and grounded in context?
3. **Retrieval Quality**: Are we retrieving the right chunks/documents?
4. **Relevance & Completeness**: Is the answer on-topic and complete?
5. **Safety & Compliance**: Toxicity, bias, PII leaks, policy violations
6. **User Experience**: Latency, reliability, cost, robustness

### Evaluation Methods

- **Human Evaluation**: Gold standard, especially for calibration
- **Reference-based Metrics**: BLEU/ROUGE (less common for RAG QA)
- **LLM-as-a-Judge**: Dominant modern pattern (used by this framework)

### RAG-Specific Metrics

- **Retrieval**: Recall@K, Precision@K, Hit@K, nDCG@K, context relevance
- **Generation**: Answer correctness, faithfulness, relevance, completeness

## Integration with CI/CD

The framework can be integrated into CI/CD pipelines:

```bash
# Run evaluation and check thresholds
python -m evals.cli --config evals/evals_config.yaml --output-type json
# Parse summary.json and fail if metrics below threshold
```

## Troubleshooting

### Common Issues

1. **Missing columns in CSV**: Ensure column names match configuration
2. **Lambda invocation errors**: Check AWS credentials and function name
3. **LLM API errors**: Verify API keys and model names
4. **Import errors**: Ensure all dependencies are installed via `environment.yml`

### Debug Mode

Set environment variables for detailed logging:
```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
python -m evals.cli --config evals/evals_config.yaml --output-type json
```

## Notes

Below is a ChatGPT summarization of current techniques for reference.

1. Big picture: what you're actually trying to measure

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

Most recent "best practices" guides emphasize decoupling retrieval from generation.  ￼

2.1 Retrieval metrics

If you have labeled "gold" passages/documents, you can use classic IR metrics:
	•	Recall@K / Precision@K / Hit@K / nDCG@K – did the retriever bring the ground-truth doc into the top-K results, and where?  ￼

If you don't have detailed labels, common approaches:
	•	Context relevance scoring with an LLM-as-a-judge: "Given the query and this chunk, how relevant is it (0–5)?"
	•	Context recall/precision (RAGAS terminology): measure how much of the necessary info is actually in the retrieved context vs noise.  ￼

2.2 Generation metrics

Given the query + retrieved context + answer:
	•	Answer correctness (does it answer the question?)
	•	Faithfulness / groundedness: are the answer's claims supported by the context? (This is the main anti-hallucination metric for RAG.)  ￼
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

So they're used less for RAG, more for legacy tasks.  ￼

3.3 LLM-as-a-judge (the current default)

The dominant modern pattern is:
	1.	Define a scoring rubric (e.g., 0–1 binary, 1–5 ordinal, or letter grades).
	2.	Call a strong model with a structured prompt that sees:
	•	The question
	•	The system's answer
	•	Optionally the ground truth and/or retrieved context
	3.	Ask it to:
	•	Explain its reasoning and
	•	Output a structured score.

OpenAI's Evals and Graders APIs are essentially formalized versions of this: you define "graders" for correctness, safety, etc., and run them at scale; the same grader definitions can be reused for reinforcement fine-tuning (RFT) to optimize models on those scores.  ￼

Third-party tools (RAGAS, DeepEval, TruLens, Patronus, Arize, LangSmith, etc.) also lean heavily on LLM-as-a-judge for RAG metrics.  ￼

⸻

4. Common RAG metrics & frameworks (what people actually use)

4.1 RAGAS (very common in open source)

RAGAS defines a set of RAG-specific metrics:  ￼
	•	Faithfulness – are answer's claims supported by context?
	•	Contextual relevance – how relevant is the retrieved context to the query?
	•	Answer relevance – how well does the answer address the query?
	•	Context recall / precision – how much necessary information is in the context vs noise?

You feed in (question, answer, contexts, ground-truth) and it outputs per-sample scores + an aggregate.

4.2 DeepEval & TruLens
	•	DeepEval – "pytest for LLMs" with a bunch of built-in metrics (correctness, faithfulness, toxicity, etc.), dataset management, and CI/CD integration.  ￼
	•	TruLens – focuses on tracing & feedback (instrument calls, log prompts/contexts, and grade them with LLMs). Good for runtime monitoring plus offline eval.  ￼

4.3 Other notable tools

Recent surveys list tools like LangSmith, Arize, Evidently, Patronus AI, Traceloop, Qdrant's RAG eval toolkit, Vertex AI evals, Bedrock eval workflows, etc.  ￼

They generally provide:
	•	Dataset and experiment management
	•	Built-in graders (correctness, groundedness, toxicity, etc.)
	•	Dashboards for tracing and observability
	•	Alerting when metrics drift

⸻

5. Evaluation in production: observability & continuous testing

Evaluation isn't just an offline thing – most current best-practice guides emphasize continuous monitoring:
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

Most "how to build an eval framework" articles converge on roughly this pipeline:  ￼
	1.	Define objectives & risks
	•	For your RAG app: what does success look like?
	•	What's unacceptable (hallucinations, missing citations, unsafe advice, etc.)?
	2.	Build a representative test set
	•	User-like queries – including "happy path" and edge cases.
	•	For RAG: include questions that require using the context, multi-hop questions, long-tail queries, etc.
	3.	Choose metrics
	•	Retrieval: recall@K, context relevance, context recall/precision.
	•	Generation: correctness, faithfulness, relevance, completeness, style, safety.
	•	Ops: latency, cost, robustness.
	4.	Choose evaluators
	•	Human eval for a subset (to calibrate).
	•	LLM-as-a-judge graders for scale (possibly using OpenAI's evals/graders or frameworks like RAGAS/DeepEval).
	5.	Automate & integrate
	•	Build scripts or pipelines that:
	•	Run your app on the test set
	•	Compute metrics
	•	Produce dashboards or reports (by commit/model/prompt version)
	•	Integrate into CI/CD so changes must "pass" eval thresholds before deployment.
	6.	Close the loop
	•	Use eval results to:
	•	Tune retrieval (chunking, embedding model, reranker, filters)
	•	Adjust prompts / system instructions
	•	Choose or fine-tune models (with RFT or classic fine-tuning) targeting your graders.  ￼

⸻
