# evals/metrics_ragas.py

from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_precision,
    context_recall,
)
from metrics_base import BaseMetric
import asyncio

RAGAS_METRIC_MAP = {
    "faithfulness": faithfulness,
    "answer_relevancy": answer_relevancy,
    "context_precision": context_precision,
    "context_recall": context_recall,
}


class RagasMetricCollection(BaseMetric):
    def __init__(self, metric_names, judge_model):
        if judge_model is None:
            raise ValueError("RagasMetricCollection requires a judge_model (LLM)")
        super().__init__(name="ragas_collection", judge_model=judge_model)
        self.metric_names = metric_names
    
    async def evaluate(self, samples, outputs):
        data = {
            "question": [s.input for s in samples],
            "answer": [o["answer"] for o in outputs],
            "contexts": [o["contexts"] for o in outputs],
            "ground_truth": [s.human_reference_answer for s in samples],
        }
        
        ds = Dataset.from_dict(data)
        metrics = [RAGAS_METRIC_MAP[m] for m in self.metric_names]
        
        loop = asyncio.get_running_loop()
        
        def _run():
            return evaluate(ds, metrics=metrics)
        
        result = await loop.run_in_executor(None, _run)
        df = result.to_pandas()
        
        # df will have per-sample columns for each metric
        results = []
        for sample, row in zip(samples, df.itertuples()):
            for metric_name in self.metric_names:
                score = getattr(row, metric_name)
                results.append({
                    "id": sample.sample_id,
                    "metric": metric_name,
                    "score": float(score),
                    "extra": {},
                })
        
        return results

