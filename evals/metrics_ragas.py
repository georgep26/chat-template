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

# Import Bedrock embeddings
try:
    from langchain_aws import BedrockEmbeddings
    HAS_BEDROCK_EMBEDDINGS = True
except ImportError:
    HAS_BEDROCK_EMBEDDINGS = False

# Import OpenAI embeddings (default if embedding_model not configured)
try:
    from langchain_openai import OpenAIEmbeddings
    HAS_OPENAI_EMBEDDINGS = True
except ImportError:
    HAS_OPENAI_EMBEDDINGS = False

RAGAS_METRIC_MAP = {
    "faithfulness": faithfulness,
    "answer_relevancy": answer_relevancy,
    "context_precision": context_precision,
    "context_recall": context_recall,
}


def create_embeddings(embedding_model_cfg=None):
    """
    Create embeddings based on the embedding_model configuration from evals_config.yaml.
    
    Args:
        embedding_model_cfg: Optional dict with embedding configuration. If not provided,
                            defaults to OpenAI embeddings. Expected format:
                            {
                                "provider": "bedrock" or "openai",
                                "model": "model-name" (e.g., "amazon.titan-embed-text-v2:0" for Bedrock),
                                "region_name": "us-east-1" (required for Bedrock),
                                "openai_api_key_env": "OPENAI_API_KEY" (optional for OpenAI)
                            }
    
    Returns:
        Langchain Embeddings instance (BedrockEmbeddings or OpenAIEmbeddings).
        Ragas will automatically wrap it with LangchainEmbeddingsWrapper.
    
    Note:
        If embedding_model_cfg is None or not provided, this function defaults to OpenAI embeddings.
        This default behavior should be explicitly configured in evals_config.yaml to avoid confusion.
        We return the raw Langchain embeddings instance and let ragas wrap it, rather than
        wrapping it ourselves, to ensure proper integration with ragas's internal handling.
    """
    # Default to OpenAI if no embedding_model config is provided
    if embedding_model_cfg is None:
        if not HAS_OPENAI_EMBEDDINGS:
            raise RuntimeError(
                "No embedding_model configuration provided and langchain-openai is not installed. "
                "Either provide embedding_model in evals_config.yaml or install langchain-openai: "
                "pip install langchain-openai"
            )
        # Default to OpenAI embeddings when no config is provided
        # Return raw Langchain embeddings - ragas will wrap it
        return OpenAIEmbeddings(model="text-embedding-3-small")
    
    # Get provider from config
    provider = embedding_model_cfg.get("provider")
    
    if provider == "bedrock":
        # Use Bedrock embeddings
        if not HAS_BEDROCK_EMBEDDINGS:
            raise RuntimeError(
                "langchain-aws is not installed. "
                "Install it with: pip install langchain-aws"
            )
        
        # Get required parameters from config
        model_id = embedding_model_cfg.get("model", "amazon.titan-embed-text-v2:0")
        region_name = embedding_model_cfg.get("region_name", "us-east-1")
        
        # Return raw Langchain embeddings - ragas will wrap it
        return BedrockEmbeddings(
            model_id=model_id,
            region_name=region_name,
        )
    
    elif provider == "openai" or provider is None:
        # Use OpenAI embeddings (default if provider not specified)
        if not HAS_OPENAI_EMBEDDINGS:
            raise RuntimeError(
                "langchain-openai is not installed. "
                "Install it with: pip install langchain-openai"
            )
        
        # Get model from config or use default
        model = embedding_model_cfg.get("model", "text-embedding-3-small")
        
        # Handle OpenAI API key if provided
        api_key = None
        if "openai_api_key_env" in embedding_model_cfg:
            import os
            env_var = embedding_model_cfg["openai_api_key_env"]
            api_key = os.environ.get(env_var)
            if not api_key:
                raise RuntimeError(f"Missing OpenAI API key in env var {env_var}")
        
        # Return raw Langchain embeddings - ragas will wrap it
        return OpenAIEmbeddings(model=model, api_key=api_key)
    
    else:
        raise ValueError(f"Unsupported embedding provider: {provider}. Expected 'bedrock' or 'openai'.")


class RagasMetricCollection(BaseMetric):
    def __init__(self, metric_names, judge_model, embedding_model_cfg=None):
        """
        Initialize Ragas metric collection.
        
        Args:
            metric_names: List of metric names to evaluate
            judge_model: LangChain LLM instance for judging (ChatOpenAI or ChatBedrockConverse)
            embedding_model_cfg: Optional dict with embedding configuration from evals_config.yaml.
                                If not provided, defaults to OpenAI embeddings.
                                See create_embeddings() docstring for expected format.
        """
        if judge_model is None:
            raise ValueError("RagasMetricCollection requires a judge_model (LLM)")
        super().__init__(name="ragas_collection", judge_model=judge_model)
        self.metric_names = metric_names
        # Wrap LangChain LLM with Ragas wrapper for compatibility
        # judge_model is a LangChain ChatModel (ChatOpenAI or ChatBedrockConverse)
        # and needs to be wrapped to work with Ragas
        self.ragas_llm = LangchainLLMWrapper(judge_model)
        # Create embeddings based on embedding_model_cfg from evals_config.yaml
        # If embedding_model_cfg is None, defaults to OpenAI embeddings
        self.ragas_embeddings = create_embeddings(embedding_model_cfg)
    
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
            # Pass the wrapped LLM and embeddings explicitly to Ragas evaluate
            # This ensures Ragas uses the correct LLM and embeddings instead of its defaults
            return evaluate(ds, metrics=metrics, llm=self.ragas_llm, embeddings=self.ragas_embeddings)
        
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

