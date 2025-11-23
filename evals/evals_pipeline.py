# evals/evals_pipeline.py

import asyncio
from pathlib import Path
from collections import defaultdict

from .data import load_eval_dataframe, extract_eval_samples
from .client import build_rag_client
from src.utils.llm_factory import create_llm
from .metrics_ragas import RagasMetricCollection
from .metrics_custom import BinaryCorrectnessMetric
from .metrics_base import BaseMetric
from .outputs import (
    build_aggregate_summary,
    write_json_summary,
    write_csv_results,
    write_html_report,
    upload_to_s3,
)
from .judge_validation import run_judge_validation


def build_metrics(config: dict):
    metrics = []
    mcfg = config.get("metrics", {})
    
    ragas_cfg = mcfg.get("ragas", {})
    if ragas_cfg.get("enabled", False):
        metrics.append(
            RagasMetricCollection(metric_names=ragas_cfg["metric_names"])
        )
    
    corr_cfg = mcfg.get("correctness", {})
    if corr_cfg.get("enabled", False) and corr_cfg["implementation"] == "binary":
        metrics.append(
            BinaryCorrectnessMetric(judge_model_cfg=corr_cfg["judge_model"])
        )
    
    # further implementations (atomic, etc.) can be added here
    
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
        samples, max_concurrency=run_cfg["max_concurrency"]
    )
    
    # 4) Build metrics & LLMs
    metrics = build_metrics(config)
    # default LLM if needed
    default_llm = create_llm(config["llm"]["default"])
    
    # 5) Run metrics (per-sample scores)
    per_sample_results = []
    for metric in metrics:
        # pick LLM for this metric
        if metric.name.startswith("correctness"):
            llm_cfg = config["metrics"]["correctness"]["judge_model"]
            llm = create_llm(llm_cfg)
        else:
            llm = default_llm
        
        res = await metric.evaluate(samples, model_outputs, llm=llm)
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
    upload_to_s3(config, generated_paths)

