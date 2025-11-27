"""
Prompt configuration and Bedrock model setup for RAG application.
Reads configuration from app_config.yml with environment variable overrides.
"""
import os
import yaml
from pathlib import Path
from langchain_core.prompts import PromptTemplate
from langchain_aws.chat_models.bedrock_converse import ChatBedrockConverse


def load_config():
    """Load configuration from app_config.yml with environment variable overrides."""
    config_path = Path(__file__).parent.parent / "config" / "app_config.yml"
    
    # Try to load config file, use defaults if not found
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        config = {}
    
    # Get RAG config, with environment variable overrides
    rag_config = config.get("rag", {})
    aws_config = config.get("aws", {})
    
    # Environment variables take precedence, then YAML config, then defaults
    aws_region = os.getenv("AWS_REGION") or aws_config.get("region", "us-east-1")
    # Remove ${VAR} template strings if present
    kb_id_raw = rag_config.get("kb_id", "")
    kb_id = os.getenv("KB_ID") or (kb_id_raw if not kb_id_raw.startswith("${") else "")
    
    model_id_raw = rag_config.get("model_id", "")
    model_id = os.getenv("MODEL_ID") or (model_id_raw if not model_id_raw.startswith("${") else "")
    
    pg_dsn_raw = rag_config.get("pg_dsn", "")
    pg_dsn = os.getenv("PG_DSN") or (pg_dsn_raw if not pg_dsn_raw.startswith("${") else "")
    
    default_top_k_raw = rag_config.get("default_top_k", "6")
    default_top_k_str = os.getenv("DEFAULT_TOP_K") or (default_top_k_raw if not str(default_top_k_raw).startswith("${") else "6")
    default_top_k = int(default_top_k_str)
    
    return {
        "AWS_REGION": aws_region,
        "KB_ID": kb_id,
        "MODEL_ID": model_id,
        "PG_DSN": pg_dsn,
        "DEFAULT_TOP_K": default_top_k,
    }


# Load config at module level
CONFIG = load_config()

AWS_REGION = CONFIG["AWS_REGION"]
KB_ID = CONFIG["KB_ID"]
MODEL_ID = CONFIG["MODEL_ID"]
PG_DSN = CONFIG["PG_DSN"]
DEFAULT_TOP_K = CONFIG["DEFAULT_TOP_K"]

# System prompt for the assistant
SYSTEM = (
    "You are a grounded assistant. Use only the provided CONTEXT.\n"
    "If the answer is not in CONTEXT, say you don't know.\n"
    "Return JSON with fields: {answer: string, citations: "
    "[{title,type,version,s3_uri,page,chunk}]}"
)

# Main prompt template
PROMPT = PromptTemplate.from_template(
    """<SYSTEM>
{system}
</SYSTEM>

<CHAT_HISTORY>
{chat_history}
</CHAT_HISTORY>

<CONTEXT>
{context}
</CONTEXT>

<QUESTION>
{input_prompt}
</QUESTION>"""
)

# Bedrock LLM instance
llm = ChatBedrockConverse(
    model=MODEL_ID,
    region_name=AWS_REGION,
    temperature=0.2,
    max_tokens=1024,
)

