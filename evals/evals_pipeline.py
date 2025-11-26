# evals/evals_pipeline.py

import asyncio
from pathlib import Path
from collections import defaultdict

from data import load_eval_dataframe, extract_eval_samples
from client import build_rag_client
from src.utils.llm_factory import create_llm
from src.utils.aws_utils import upload_to_s3
from metrics_ragas import RagasMetricCollection
from metrics_custom import BinaryCorrectnessMetric
from metrics_base import BaseMetric
from outputs import (
    build_aggregate_summary,
    write_json_summary,
    write_csv_results,
    write_html_report,
)
from judge_validation import run_judge_validation


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
        metrics.append(
            RagasMetricCollection(
                metric_names=ragas_cfg["metric_names"],
                judge_model=judge_llm
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
        # TODO: Create AtomicCorrectnessMetric when implemented
        # from .metrics_custom import AtomicCorrectnessMetric
        # metrics.append(AtomicCorrectnessMetric(judge_model=judge_llm))
    
    # Context relevance metric
    ctx_rel_cfg = mcfg.get("context_relevance", {})
    if ctx_rel_cfg.get("enabled", False):
        if "judge_model" not in ctx_rel_cfg:
            raise ValueError("context_relevance metric requires a judge_model configuration")
        judge_llm = create_llm(ctx_rel_cfg["judge_model"])
        # TODO: Create ContextRelevanceMetric when implemented
        # metrics.append(ContextRelevanceMetric(judge_model=judge_llm))
    
    return metrics


async def run_evaluation(config: dict, run_judge_validation: bool = False):
    run_cfg = config["run"]
    outputs_cfg = config["outputs"]
    evaluation_run_name = run_cfg.get("evaluation_run_name", "evaluation_run_default")
    base_dir = Path(outputs_cfg.get("local", {}).get("base_dir", "eval_outputs"))
    
    # 1) Load data
    df = load_eval_dataframe(config)
    samples = extract_eval_samples(df, config)
    
    # 2) Build RAG client
    client = build_rag_client(config)
    
    # 3) Generate outputs
    model_outputs = await client.generate_batch(
        samples, max_concurrency=run_cfg["max_concurrent_async_tasks"]
    )
    
    # 4) Build metrics (LLMs are created within build_metrics)
    metrics = build_metrics(config)
    
    # 5) Run metrics (per-sample scores)
    per_sample_results = []
    for metric in metrics:
        res = await metric.evaluate(samples, model_outputs)
        per_sample_results.extend(res)
    
    # 6) Aggregate
    summary = build_aggregate_summary(per_sample_results)
    # Add run metadata to summary
    summary["run"] = {
        "evaluation_run_name": evaluation_run_name,
        "mode": run_cfg.get("mode", "unknown"),
    }
    
    # 7) Optional judge validation
    judge_val_result = None
    if run_judge_validation:
        judge_val_result = await run_judge_validation(config)
        if judge_val_result:
            summary["judge_validation"] = judge_val_result
    
    # 8) Outputs
    generated_paths = []
    if "json" in outputs_cfg["types"]:
        p = write_json_summary(summary, base_dir, evaluation_run_name)
        generated_paths.append(p)
    
    if "csv" in outputs_cfg["types"]:
        p = write_csv_results(per_sample_results, base_dir, evaluation_run_name)
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

