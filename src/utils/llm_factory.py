"""LLM factory for creating LangChain LLM instances."""

import os
from langchain_openai import ChatOpenAI
from langchain_aws.chat_models.bedrock_converse import ChatBedrockConverse


def create_llm(model_cfg: dict):
    """
    Create a LangChain LLM instance based on configuration.
    
    All arguments in model_cfg (except provider-specific handling) are passed through
    directly to the LangChain constructor, allowing full access to LangChain parameters.
    
    Args:
        model_cfg: Dictionary with LLM configuration. Example for OpenAI:
            {
              "provider": "openai",
              "model": "gpt-4o-mini",
              "openai_api_key_env": "OPENAI_API_KEY",
              "temperature": 0.0,
              "max_tokens": 1000
            }
            Example for Bedrock:
            {
              "provider": "bedrock",
              "model": "anthropic.claude-3-sonnet-20240229-v1:0",
              "region_name": "us-east-1",
              "temperature": 0.0,
              "max_tokens": 1024
            }
    
    Returns:
        LangChain LLM instance (ChatOpenAI or ChatBedrockConverse)
    
    Raises:
        RuntimeError: If OpenAI API key is missing
        ValueError: If provider is unsupported or required fields are missing
    """
    # Create a copy to avoid modifying the original config
    cfg = model_cfg.copy()
    provider = cfg.pop("provider")
    
    if provider == "openai":
        # Handle OpenAI-specific: openai_api_key_env -> api_key (environment variable lookup)
        env_var = cfg.pop("openai_api_key_env", "OPENAI_API_KEY")
        api_key = os.environ.get(env_var)
        if not api_key:
            raise RuntimeError(f"Missing OpenAI API key in env var {env_var}")
        
        # Pass all other args through to ChatOpenAI (including "model")
        return ChatOpenAI(api_key=api_key, **cfg)
    
    elif provider == "bedrock":
        # Pass all args through to ChatBedrockConverse (including "model")
        return ChatBedrockConverse(**cfg)
    
    else:
        raise ValueError(f"Unsupported provider: {provider}")

