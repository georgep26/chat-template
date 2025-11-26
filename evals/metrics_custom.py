# evals/metrics_custom.py

import json
import asyncio
from metrics_base import BaseMetric


class BinaryCorrectnessMetric(BaseMetric):
    def __init__(self, judge_model):
        if judge_model is None:
            raise ValueError("BinaryCorrectnessMetric requires a judge_model (LLM)")
        super().__init__(name="correctness_binary", judge_model=judge_model)
    
    async def evaluate(self, samples, outputs):
        async def _grade_one(sample, output):
            prompt = f"""
You are grading the factual correctness of the model answer
compared to the reference answer.

Question:
{sample.input}

Reference answer:
{sample.human_reference_answer}

Model answer:
{output["answer"]}

Return JSON with:
- score: 0 or 1
- explanation: short explanation
"""
            # LangChain LLM call (async)
            resp = await self.judge_model.ainvoke(prompt)
            content = resp.content if hasattr(resp, "content") else str(resp)
            
            # assume model returns JSON or JSON-like
            try:
                obj = json.loads(content)
            except json.JSONDecodeError:
                # fallback: heuristic; treat any non-parse as 0
                obj = {"score": 0, "explanation": "Failed to parse judge response."}
            
            return {
                "id": sample.sample_id,
                "metric": self.name,
                "score": float(obj.get("score", 0)),
                "extra": {"explanation": obj.get("explanation", "")},
            }
        
        tasks = [_grade_one(s, o) for s, o in zip(samples, outputs)]
        return await asyncio.gather(*tasks)

