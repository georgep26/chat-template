# evals/metrics_base.py

from abc import ABC, abstractmethod


class BaseMetric(ABC):
    def __init__(self, name: str):
        self.name = name
    
    @abstractmethod
    async def evaluate(self, samples, outputs, llm=None):
        """
        samples: list of EvalSample dataclass instances
        outputs: list of dicts (answer, contexts, raw)
        llm: optional LangChain LLM (for metrics that need an LLM)
        
        Returns: list of dicts:
          { "id": sample_id, "metric": self.name, "score": float, "extra": {...} }
        """
        ...

