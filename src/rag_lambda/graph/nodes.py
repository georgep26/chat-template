"""LangGraph nodes for RAG pipeline."""

import os
from typing import Any, Dict, List

import yaml
from langchain_aws import ChatBedrockConverse
from langchain_community.retrievers.bedrock import AmazonKnowledgeBasesRetriever
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from .prompts import answer_prompt, clarify_prompt, rewrite_prompt, split_prompt
from .state import MessagesState


def rewrite_node(state: MessagesState, config: Dict[str, Any]) -> MessagesState:
    """Rewrite user query for better retrieval."""
    # Get config from LangGraph configurable
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
    state["messages"].append(AIMessage(name="rewriter", content=rewritten.content))
    return state


def clarify_node(state: MessagesState, config: Dict[str, Any]) -> MessagesState:
    """Ask clarifying questions for underspecified queries."""
    # Get config from LangGraph configurable
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
    if resp.content.strip().upper() != "CLEAR":
        # Ask a clarifying question - graph should end here, caller will show question to user
        state["messages"].append(resp)
    return state


def split_node(state: MessagesState, config: Dict[str, Any]) -> MessagesState:
    """Split multi-part queries into subqueries."""
    # Get config from LangGraph configurable
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
    # Naive parse - later you could parse with json mode
    subqs = [
        line.strip("0123456789. ").strip() for line in resp.content.splitlines() if line.strip()
    ]
    state["messages"].append(
        SystemMessage(
            name="subqueries",
            content="\n".join(subqs),
        )
    )
    return state


def answer_node(state: MessagesState, config: Dict[str, Any]) -> MessagesState:
    """Generate answer using retrieved context."""
    # Get config from LangGraph configurable
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
    state["messages"].append(resp)
    return state
