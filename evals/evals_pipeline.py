# evals/evals_pipeline.py
#
# Example usage:
#   python evals/evals_pipeline.py --config evals/evals_config.yaml
#
#   python evals/evals_pipeline.py --config evals/evals_config.yaml --output-type html,json,csv --notes "Test evaluation run"

import argparse
import asyncio
import json
import csv
from datetime import datetime
from pathlib import Path
from typing import Optional
from collections import defaultdict

from data import load_eval_dataframe, extract_eval_samples
from client import build_rag_client
from src.utils.llm_factory import create_llm
from src.utils.config import read_config
from src.utils.aws_utils import upload_to_s3
from src.utils.logger import get_logger

log = get_logger(__name__)
from metrics_ragas import RagasMetricCollection
from metrics_custom import BinaryCorrectnessMetric, AtomicCorrectnessMetric
from metrics_base import BaseMetric
from outputs import (
    build_aggregate_summary,
    write_json_summary,
    write_csv_results,
    write_html_report,
)


def build_metrics(config: dict):
    """Build metrics from config, creating LLMs for metrics that require them."""
    metrics = []
    mcfg = config.get("metrics", {})
    
    # RAGAS metrics
    ragas_cfg = mcfg.get("ragas", {})
    if ragas_cfg.get("enabled", False):
        if "judge_model" not in ragas_cfg:
            raise ValueError("RAGAS metrics require a judge_model configuration")
        judge_llm = create_llm(ragas_cfg["judge_model"])
        # Get embedding_model config if provided, otherwise defaults to OpenAI
        embedding_model_cfg = ragas_cfg.get("embedding_model")
        metrics.append(
            RagasMetricCollection(
                metric_names=ragas_cfg["metric_names"],
                judge_model=judge_llm,
                embedding_model_cfg=embedding_model_cfg
            )
        )
    
    # Binary correctness metric
    binary_corr_cfg = mcfg.get("binary_correctness", {})
    if binary_corr_cfg.get("enabled", False):
        if "judge_model" not in binary_corr_cfg:
            raise ValueError("binary_correctness metric requires a judge_model configuration")
        judge_llm = create_llm(binary_corr_cfg["judge_model"])
        metrics.append(
            BinaryCorrectnessMetric(judge_model=judge_llm)
        )
    
    # Atomic correctness metric
    atomic_corr_cfg = mcfg.get("atomic_correctness", {})
    if atomic_corr_cfg.get("enabled", False):
        if "judge_model" not in atomic_corr_cfg:
            raise ValueError("atomic_correctness metric requires a judge_model configuration")
        judge_llm = create_llm(atomic_corr_cfg["judge_model"])
        metrics.append(AtomicCorrectnessMetric(judge_model=judge_llm))
    
    # Context relevance metric
    ctx_rel_cfg = mcfg.get("context_relevance", {})
    if ctx_rel_cfg.get("enabled", False):
        if "judge_model" not in ctx_rel_cfg:
            raise ValueError("context_relevance metric requires a judge_model configuration")
        judge_llm = create_llm(ctx_rel_cfg["judge_model"])
        # TODO: Create ContextRelevanceMetric when implemented
        # metrics.append(ContextRelevanceMetric(judge_model=judge_llm))
    
    return metrics


def load_rag_results(samples, output_dir: Path) -> Optional[dict]:
    """
    Load RAG results from rag_results.csv if it exists.
    Returns a dict mapping sample_id to model_outputs dict, or None if file doesn't exist.
    """
    rag_results_path = output_dir / "rag_results.csv"
    
    if not rag_results_path.exists():
        return None
    
    # Load CSV and create a mapping by sample_id
    results_by_id = {}
    with open(rag_results_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sample_id = row["sample_id"]
            answer = row["answer"]
            contexts = json.loads(row["contexts"]) if row["contexts"] else []
            raw = json.loads(row["raw"]) if row["raw"] else {}
            
            results_by_id[sample_id] = {
                "answer": answer,
                "contexts": contexts,
                "raw": raw,
            }
    
    return results_by_id


def save_rag_results(samples, results_by_id: dict, output_dir: Path):
    """
    Save RAG results to rag_results.csv.
    results_by_id: dict mapping sample_id to model_outputs dict
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    rag_results_path = output_dir / "rag_results.csv"
    
    fieldnames = ["sample_id", "answer", "contexts", "raw"]
    
    with open(rag_results_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for sample in samples:
            sample_id = str(sample.sample_id)
            if sample_id in results_by_id:
                output = results_by_id[sample_id]
                writer.writerow({
                    "sample_id": sample.sample_id,
                    "answer": output.get("answer", ""),
                    "contexts": json.dumps(output.get("contexts", [])),
                    "raw": json.dumps(output.get("raw", {})),
                })
    
    return rag_results_path


async def run_evaluation(config: dict, notes: Optional[str] = None):
    run_cfg = config["run"]
    outputs_cfg = config["outputs"]
    evaluation_run_name = run_cfg.get("evaluation_run_name", "evaluation_run_default")
    base_dir = Path(outputs_cfg.get("local", {}).get("base_dir", "eval_outputs"))
    
    log.info(f"Starting evaluation: {evaluation_run_name}")
    
    # 1) Load data
    df = load_eval_dataframe(config)
    num_validation_questions = len(df)  # Number of validation questions initially read from CSV
    samples = extract_eval_samples(df, config)
    log.info(f"Loaded {len(samples)} samples")
    
    # 2) Check for persisted RAG results
    persist_rag_outputs = run_cfg.get("persist_rag_outputs", False)
    output_dir = base_dir / evaluation_run_name
    persisted_results = None
    
    if persist_rag_outputs:
        persisted_results = load_rag_results(samples, output_dir)
    
    # 3) Identify missing samples and generate outputs for them
    missing_samples = []
    
    if persisted_results is not None:
        # Find samples that don't have persisted results
        for sample in samples:
            sample_id = str(sample.sample_id)
            if sample_id not in persisted_results:
                missing_samples.append(sample)
    else:
        # No persisted results, need to generate for all samples
        missing_samples = samples
    
    # Generate outputs for missing samples
    if missing_samples:
        log.info(f"Generating outputs for {len(missing_samples)} samples")
        client = build_rag_client(config)
        new_model_outputs = await client.generate_batch(
            missing_samples, max_concurrency=run_cfg["max_concurrent_async_tasks"]
        )
        
        # Merge new results with persisted results
        if persisted_results is None:
            persisted_results = {}
        
        for sample, output in zip(missing_samples, new_model_outputs):
            sample_id = str(sample.sample_id)
            persisted_results[sample_id] = output
        
        # Save complete results if persistence is enabled
        if persist_rag_outputs:
            save_rag_results(samples, persisted_results, output_dir)
    
    # 4) Construct model_outputs list in the same order as samples
    model_outputs = []
    for sample in samples:
        sample_id = str(sample.sample_id)
        if persisted_results and sample_id in persisted_results:
            model_outputs.append(persisted_results[sample_id])
        else:
            # This shouldn't happen if logic above is correct, but handle gracefully
            raise RuntimeError(f"Missing RAG result for sample_id: {sample_id}")
    
    # 5) Build metrics (LLMs are created within build_metrics)
    metrics = build_metrics(config)
    log.info(f"Running {len(metrics)} metrics")
    
    # 6) Run metrics (per-sample scores)
    per_sample_results = []
    for metric in metrics:
        res = await metric.evaluate(samples, model_outputs)
        per_sample_results.extend(res)
    
    # 7) Aggregate
    summary = build_aggregate_summary(per_sample_results)
    # Add run metadata to summary
    # Determine mode from rag_app configuration
    rag_cfg = config.get("rag_app", {})
    if "local_entrypoint" in rag_cfg and rag_cfg["local_entrypoint"]:
        mode = "local"
    elif "lambda_function_name" in rag_cfg and rag_cfg["lambda_function_name"]:
        mode = "lambda"
    else:
        mode = "unknown"
    
    summary["run"] = {
        "evaluation_run_name": evaluation_run_name,
        "mode": mode,
        "run_timestamp": datetime.now().isoformat(),
        "num_validation_questions": num_validation_questions,
    }
    
    # Add notes if provided
    if notes:
        summary["run"]["notes"] = notes
    
    # 8) Outputs
    generated_paths = []
    if "json" in outputs_cfg["types"]:
        p = write_json_summary(summary, base_dir, evaluation_run_name)
        generated_paths.append(p)
    
    if "csv" in outputs_cfg["types"]:
        p = write_csv_results(per_sample_results, samples, model_outputs, base_dir, evaluation_run_name)
        generated_paths.append(p)
    
    if "html" in outputs_cfg["types"]:
        p = write_html_report(summary, base_dir, evaluation_run_name)
        generated_paths.append(p)
    
    # 9) Optional S3 upload
    s3_cfg = outputs_cfg.get("s3", {})
    if s3_cfg.get("enabled", False):
        base_s3_uri = s3_cfg["s3_uri"].rstrip('/')
        s3_uri = f"{base_s3_uri}/{evaluation_run_name}/"
        experiment_dir = base_dir / evaluation_run_name
        upload_to_s3(s3_uri, experiment_dir)
    
    log.info(f"Evaluation completed: {evaluation_run_name}")


def main(
    eval_config: str,
    output_type: Optional[str],
    notes: Optional[str] = None,
    environment: Optional[str] = None,
):
    config = read_config(eval_config)
    
    # Apply defaults (previously in load_config)
    config.setdefault("outputs", {})
    config["outputs"].setdefault("types", ["html", "json", "csv"])
    config.setdefault("run", {})
    config["run"].setdefault("max_concurrent_async_tasks", 10)

    # CLI override for output types
    if output_type:
        config["outputs"]["types"] = [
            s.strip() for s in output_type.split(",") if s.strip()
        ]

    # Override Lambda function name when --environment is set (e.g. from CI: dev, staging, prod)
    if environment:
        config.setdefault("rag_app", {})
        config["rag_app"]["lambda_function_name"] = f"chat-template-{environment}-rag-chat"

    asyncio.run(run_evaluation(config=config, notes=notes))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG evaluation runner")
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to evals_config.yaml"
    )
    parser.add_argument(
        "--output-type",
        type=str,
        default=None,
        help="Comma-separated list of outputs to generate (html,json,csv). "
             "Overrides evals_config.outputs.types if provided."
    )
    parser.add_argument(
        "--notes",
        type=str,
        default=None,
        help="Notes to include in the evaluation run summary (will appear in summary.json under 'run.notes')"
    )
    parser.add_argument(
        "--environment",
        type=str,
        default=None,
        choices=["dev", "staging", "prod"],
        help="Environment (dev, staging, prod). When set, overrides rag_app.lambda_function_name to chat-template-<env>-rag-chat."
    )
    args = parser.parse_args()
    main(args.config, args.output_type, args.notes, args.environment)

