# evals/judge_validation.py

import json
import numpy as np
from .data import load_judge_validation_dataframe
from .llm_factory import create_llm


async def run_judge_validation(config: dict):
    if not config.get("judge_validation", {}).get("enabled", False):
        return None
    
    df = load_judge_validation_dataframe(config)
    jcfg = config["judge_validation"]
    judge_llm_cfg = config["metrics"]["correctness"]["judge_model"]
    llm = create_llm(judge_llm_cfg)
    
    async def _grade_row(row):
        prompt = f"""
You are grading correctness (0 or 1).

Question:
{row[jcfg["question_column"]]}

Reference answer:
{row[jcfg["reference_column"]]}

Model answer:
{row[jcfg["model_answer_column"]]}

Return JSON: {{ "score": 0 or 1 }}
"""
        resp = await llm.ainvoke(prompt)
        content = resp.content if hasattr(resp, "content") else str(resp)
        
        try:
            obj = json.loads(content)
            return float(obj.get("score", 0))
        except json.JSONDecodeError:
            return 0.0
    
    tasks = [_grade_row(row) for _, row in df.iterrows()]
    judge_scores = await asyncio.gather(*tasks)
    
    human = df[jcfg["human_label_column"]].astype(float).to_numpy()
    judge = np.array(judge_scores)
    
    # assume binary labels 0/1; compute accuracy
    human_bin = (human >= 0.5).astype(int)
    judge_bin = (judge >= 0.5).astype(int)
    acc = float((human_bin == judge_bin).mean())
    
    return {
        "judge_model": judge_llm_cfg["model_name"],
        "n_samples": int(human.size),
        "accuracy_vs_human": acc,
    }

