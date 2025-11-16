"""Conversation summarization functionality."""

import os
from typing import List

import yaml
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate

# Load configuration
_config_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "..",
    "config",
    "app_config.yml",
)
_config_path = os.path.abspath(_config_path)

with open(_config_path, "r") as f:
    _config = yaml.safe_load(f)

_bedrock_config = _config.get("bedrock", {})
_aws_config = _config.get("aws", {})

# Summarization LLM
summ_llm = ChatBedrockConverse(
    model=_bedrock_config.get("model", {}).get("id", "us.anthropic.claude-3-7-sonnet-20250219-v1:0"),
    region_name=_bedrock_config.get("region", _aws_config.get("region", "us-east-1")),
    temperature=0,
)

summary_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Summarize the following conversation into a compact summary that preserves all important details.",
        ),
        ("human", "{conversation}"),
    ]
)


def summarize_messages(messages: List[BaseMessage]) -> SystemMessage:
    """
    Summarize a list of messages into a single summary message.

    Args:
        messages: List of messages to summarize

    Returns:
        SystemMessage containing the summary
    """
    text = "\n".join(f"{m.type}: {m.content}" for m in messages)
    resp = (summary_prompt | summ_llm).invoke({"conversation": text})
    return SystemMessage(name="conversation_summary", content=resp.content)

