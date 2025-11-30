# evals/metrics_ragas.py

from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_precision,
    context_recall,
)
from ragas.llms import LangchainLLMWrapper
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
        # Wrap LangChain LLM with Ragas wrapper for compatibility
        # judge_model is a LangChain ChatModel (ChatOpenAI or ChatBedrockConverse)
        # and needs to be wrapped to work with Ragas
        self.ragas_llm = LangchainLLMWrapper(judge_model)
    
    async def evaluate(self, samples, outputs):
        # Ragas metrics expect specific column names:
        # - user_input: the question/query
        # - response: the generated answer
        # - retrieved_contexts: list of context strings
        # - reference: ground truth answer (for context_precision and context_recall)
        data = {
            "user_input": [s.input for s in samples],
            "response": [o["answer"] for o in outputs],
            "retrieved_contexts": [o["contexts"] for o in outputs],
            "reference": [s.human_reference_answer for s in samples],
        }
        
        ds = Dataset.from_dict(data)
        metrics = [RAGAS_METRIC_MAP[m] for m in self.metric_names]
        
        loop = asyncio.get_running_loop()
        
        def _run():
            # Pass the wrapped LLM explicitly to Ragas evaluate
            # This ensures Ragas uses the correct LLM instead of its default
            return evaluate(ds, metrics=metrics, llm=self.ragas_llm)
        
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
                    "ai_evaluation_explanation": {},
                })
        
        return results

