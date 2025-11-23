# evals/metrics_base.py

from abc import ABC, abstractmethod
from typing import Optional


class BaseMetric(ABC):
    def __init__(self, name: str, judge_model=None):
        """
        Args:
            name: Name of the metric
            judge_model: Optional LangChain LLM instance for metrics that require an LLM
        """
        self.name = name
        self.judge_model = judge_model
    
    @abstractmethod
    async def evaluate(self, samples, outputs):
        """
        samples: list of EvalSample dataclass instances
        outputs: list of dicts (answer, contexts, raw)
        
        Returns: list of dicts:
          { "id": sample_id, "metric": self.name, "score": float, "extra": {...} }
        """
        ...

