# evals/llm_factory.py

import os
from langchain_openai import ChatOpenAI
from langchain_aws.chat_models.bedrock_converse import ChatBedrockConverse


def create_llm(model_cfg: dict):
    """
    model_cfg example:
    {
      "provider": "openai",
      "model_name": "gpt-4o-mini",
      "openai": { "api_key_env": "OPENAI_API_KEY" },
      "bedrock": { "region_name": "us-east-1", "model_id": "..." }
    }
    """
    provider = model_cfg["provider"]
    
    if provider == "openai":
        env_var = model_cfg.get("openai", {}).get("api_key_env", "OPENAI_API_KEY")
        api_key = os.environ.get(env_var)
        if not api_key:
            raise RuntimeError(f"Missing OpenAI API key in env var {env_var}")
        
        return ChatOpenAI(
            model=model_cfg["model_name"],
            api_key=api_key,
            temperature=0.0,
        )
    
    elif provider == "bedrock":
        bed_cfg = model_cfg["bedrock"]
        return ChatBedrockConverse(
            model=bed_cfg["model_id"],
            region_name=bed_cfg.get("region_name", "us-east-1"),
            temperature=0.0,
        )
    
    else:
        raise ValueError(f"Unsupported provider: {provider}")

