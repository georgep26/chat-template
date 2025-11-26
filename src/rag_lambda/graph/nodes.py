"""LangGraph nodes for RAG pipeline."""

import os
from typing import Any, Dict, List, Optional, Union

import yaml
from langchain_aws import ChatBedrockConverse
from langchain_community.retrievers.bedrock import AmazonKnowledgeBasesRetriever
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from .prompts import answer_prompt, clarify_prompt, rewrite_prompt, split_prompt
from .state import MessagesState


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


def rewrite_node(state: MessagesState, config: Optional[RunnableConfig] = None) -> MessagesState:
    """Rewrite user query for better retrieval."""
    # Get config from LangGraph configurable
    config = config or {}
    app_config = config.get("configurable", {})
    rag_chat_config = app_config.get("rag_chat", {})
    rewrite_config = rag_chat_config.get("rewrite", {})
    model_config = rewrite_config.get("model", {})
    
    # Initialize LLM
    llm = ChatBedrockConverse(
        model=model_config.get("id", "us.anthropic.claude-3-7-sonnet-20250219-v1:0"),
        region_name=model_config.get("region", "us-east-1"),
        temperature=model_config.get("temperature", 0.0),
    )
    
    last_user = [m for m in state["messages"] if isinstance(m, HumanMessage)][-1]
    rewritten = (rewrite_prompt | llm).invoke({"query": last_user.content})
    rewritten_text = extract_text_content(rewritten.content)
    state["messages"].append(AIMessage(name="rewriter", content=rewritten_text))
    return state


def clarify_node(state: MessagesState, config: Optional[RunnableConfig] = None) -> MessagesState:
    """Ask clarifying questions for underspecified queries."""
    # Get config from LangGraph configurable
    config = config or {}
    app_config = config.get("configurable", {})
    rag_chat_config = app_config.get("rag_chat", {})
    clarify_config = rag_chat_config.get("clarify", {})
    model_config = clarify_config.get("model", {})
    
    # Initialize LLM
    llm = ChatBedrockConverse(
        model=model_config.get("id", "us.anthropic.claude-3-7-sonnet-20250219-v1:0"),
        region_name=model_config.get("region", "us-east-1"),
        temperature=model_config.get("temperature", 0.0),
    )
    
    user = [m for m in state["messages"] if m.type == "human"][-1]
    resp = (clarify_prompt | llm).invoke({"question": user.content})
    resp_text = extract_text_content(resp.content)
    if resp_text.strip().upper() != "CLEAR":
        # Ask a clarifying question - graph should end here, caller will show question to user
        # Create a new AIMessage with the extracted text content
        state["messages"].append(AIMessage(content=resp_text))
    return state


def split_node(state: MessagesState, config: Optional[RunnableConfig] = None) -> MessagesState:
    """Split multi-part queries into subqueries."""
    # Get config from LangGraph configurable
    config = config or {}
    app_config = config.get("configurable", {})
    rag_chat_config = app_config.get("rag_chat", {})
    split_config = rag_chat_config.get("split", {})
    model_config = split_config.get("model", {})
    
    # Initialize LLM
    llm = ChatBedrockConverse(
        model=model_config.get("id", "us.anthropic.claude-3-7-sonnet-20250219-v1:0"),
        region_name=model_config.get("region", "us-east-1"),
        temperature=model_config.get("temperature", 0.0),
    )
    
    user = [m for m in state["messages"] if m.type == "human"][-1]
    resp = (split_prompt | llm).invoke({"question": user.content})
    resp_text = extract_text_content(resp.content)
    # Naive parse - later you could parse with json mode
    subqs = [
        line.strip("0123456789. ").strip() for line in resp_text.splitlines() if line.strip()
    ]
    state["messages"].append(
        SystemMessage(
            name="subqueries",
            content="\n".join(subqs),
        )
    )
    return state


def answer_node(state: MessagesState, config: Optional[RunnableConfig] = None) -> MessagesState:
    """Generate answer using retrieved context."""
    # Get config from LangGraph configurable
    config = config or {}
    app_config = config.get("configurable", {})
    rag_chat_config = app_config.get("rag_chat", {})
    generation_config = rag_chat_config.get("generation", {})
    model_config = generation_config.get("model", {})
    
    # Initialize LLM (e.g. Claude via Bedrock Converse)
    llm = ChatBedrockConverse(
        model=model_config.get("id", "us.anthropic.claude-3-7-sonnet-20250219-v1:0"),
        region_name=model_config.get("region", "us-east-1"),
        temperature=model_config.get("temperature", 0.0),
    )

    user = [m for m in state["messages"] if isinstance(m, HumanMessage)][-1]
    ctx_msgs = [m for m in state["messages"] if getattr(m, "name", "") == "retriever_context"]
    context = ctx_msgs[-1].content if ctx_msgs else ""
    resp = (answer_prompt | llm).invoke({"context": context, "question": user.content})
    resp_text = extract_text_content(resp.content)
    state["messages"].append(AIMessage(content=resp_text))
    return state
