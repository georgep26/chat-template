"""Conversation summarization functionality."""

from typing import Any, Dict, List, Union

from langchain_aws import ChatBedrockConverse
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate


def extract_text_content(content: Union[str, List[Dict[str, Any]]]) -> str:
    """
    Extract text content from a message content field.
    
    Handles both string format and list format (e.g., [{'type': 'text', 'text': '...'}]).
    
    Args:
        content: Message content, either a string or a list of content blocks
        
    Returns:
        Extracted text as a string
    """
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        # Handle list format like [{'type': 'text', 'text': 'CLEAR'}]
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    text_parts.append(block["text"])
                elif "text" in block:
                    text_parts.append(block["text"])
        return "".join(text_parts)
    else:
        # Fallback: convert to string
        return str(content)


chat_summary_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Summarize the following conversation into a compact summary that preserves all important details.",
        ),
        ("human", "{conversation}"),
    ]
)


def summarize_messages(messages: List[BaseMessage], summarization_model_config: Dict[str, Any]) -> SystemMessage:
    """
    Summarize a list of messages into a single summary message.

    Args:
        messages: List of messages to summarize

    Returns:
        SystemMessage containing the summary
    """
    # Initialize the summarization model
    model_config = summarization_model_config.get("model")
    summ_llm = ChatBedrockConverse(
        model=model_config.get("id", "us.anthropic.claude-3-7-sonnet-20250219-v1:0"),
        region_name=model_config.get("region", "us-east-1"),
        temperature=model_config.get("temperature", 0),
    )

    text = "\n".join(f"{m.type}: {extract_text_content(m.content)}" for m in messages)
    resp = (chat_summary_prompt | summ_llm).invoke({"conversation": text})
    resp_text = extract_text_content(resp.content)
    return SystemMessage(name="conversation_summary", content=resp_text)

def summarization_check(messages: List[BaseMessage], summarization_threshold: int, summarization_model_config: Dict[str, Any]) -> bool:
    if len(messages) > summarization_threshold:
        # Summarize older messages, keep recent ones
        recent_messages = messages[-summarization_threshold:]
        older_messages = messages[:-summarization_threshold]
        summary = summarize_messages(older_messages, summarization_model_config)
        return [summary] + recent_messages
    return messages