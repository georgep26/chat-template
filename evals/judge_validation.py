# evals/judge_validation.py

import json
import asyncio
import numpy as np
from .data import load_judge_validation_dataframe, extract_judge_validation_samples
from src.utils.llm_factory import create_llm


async def run_judge_validation(config: dict):
    if not config.get("judge_validation", {}).get("enabled", False):
        return None
    
    df = load_judge_validation_dataframe(config)
    samples = extract_judge_validation_samples(df, config)
    jcfg = config["judge_validation"]
    judge_llm_cfg = config["metrics"]["correctness"]["judge_model"]
    llm = create_llm(judge_llm_cfg)
    
    # Get model answers from the dataframe
    model_answer_col = jcfg["model_answer_column"]
    
    async def _grade_sample(sample, model_answer):
        prompt = f"""
You are grading correctness (0 or 1).

Question:
{sample.input}

Reference answer:
{sample.human_reference_answer}

Model answer:
{model_answer}

Return JSON: {{ "score": 0 or 1, "explanation": "short explanation" }}
"""
        resp = await llm.ainvoke(prompt)
        content = resp.content if hasattr(resp, "content") else str(resp)
        
        try:
            obj = json.loads(content)
            return float(obj.get("score", 0)), obj.get("explanation", "")
        except json.JSONDecodeError:
            return 0.0, "Failed to parse judge response."
    
    tasks = [_grade_sample(sample, row[model_answer_col]) for sample, (_, row) in zip(samples, df.iterrows())]
    results = await asyncio.gather(*tasks)
    
    # Update samples with judge scores and explanations
    judge_scores = []
    for sample, (judge_score, judge_explanation) in zip(samples, results):
        sample.judge_score = judge_score
        sample.judge_explanation = judge_explanation
        judge_scores.append(judge_score)
    
    # Compute accuracy
    human_scores = [s.human_score for s in samples if s.human_score is not None]
    judge_scores_array = np.array(judge_scores)
    human_scores_array = np.array(human_scores)
    
    if len(human_scores_array) > 0:
        # assume binary labels 0/1; compute accuracy
        human_bin = (human_scores_array >= 0.5).astype(int)
        judge_bin = (judge_scores_array >= 0.5).astype(int)
        acc = float((human_bin == judge_bin).mean())
    else:
        acc = None
    
    return {
        "judge_model": judge_llm_cfg["model_name"],
        "n_samples": int(len(samples)),
        "accuracy_vs_human": acc,
    }

