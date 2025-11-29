# evals/judge_validation.py

import argparse
import json
import asyncio
import numpy as np
from data import load_judge_validation_dataframe, extract_judge_validation_samples
from src.utils.llm_factory import create_llm
from src.utils.config import read_config


async def run_judge_validation(config: dict):
    """Run judge validation evaluation."""
    jcfg = config["judge_validation"]
    
    # Get judge model configuration
    judge_llm_cfg = config.get("judge_model")
    if not judge_llm_cfg:
        raise ValueError("judge_validation requires judge_model to be configured")
    llm = create_llm(judge_llm_cfg)
    
    # Load data
    df = load_judge_validation_dataframe(config)
    samples = extract_judge_validation_samples(df, config)
    
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
    
    result = {
        "judge_model": judge_llm_cfg.get("model", "unknown"),
        "n_samples": int(len(samples)),
        "accuracy_vs_human": acc,
    }
    
    # Print results
    print("\n" + "="*60)
    print("Judge Validation Results")
    print("="*60)
    print(f"Judge Model: {result['judge_model']}")
    print(f"Number of Samples: {result['n_samples']}")
    if result['accuracy_vs_human'] is not None:
        print(f"Accuracy vs Human: {result['accuracy_vs_human']:.4f}")
    else:
        print("Accuracy vs Human: N/A (no human scores available)")
    print("="*60 + "\n")
    
    return result


def main(config_path: str):
    """Main entry point for judge validation script."""
    config = read_config(config_path)
    
    # Validate required config sections
    if "judge_validation" not in config:
        raise ValueError("Config must contain 'judge_validation' section")
    if "judge_model" not in config:
        raise ValueError("Config must contain 'judge_model' section")
    
    asyncio.run(run_judge_validation(config))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Judge validation evaluation runner")
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to judge_validation.yaml"
    )
    args = parser.parse_args()
    main(args.config)

