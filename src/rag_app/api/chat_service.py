"""Chat service that orchestrates RAG graph execution."""

import os
from typing import List

import yaml
from langchain_core.messages import BaseMessage, HumanMessage

from ..graph.graph import build_rag_graph
from ..memory.factory import create_history_store
from ..memory.summary import summarize_messages
from .models import ChatRequest, ChatResponse

# Build graph once at module level
graph = build_rag_graph()

# Load configuration for summarization threshold
_config_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "..",
    "config",
    "app_config.yml",
)
_config_path = os.path.abspath(_config_path)

with open(_config_path, "r") as f:
    _config = yaml.safe_load(f)

_summarization_threshold = _config.get("rag", {}).get("memory", {}).get("summarization_threshold", 20)


def handle_chat(req: ChatRequest) -> ChatResponse:
    """
    Handle a chat request by invoking the RAG graph with memory integration.

    Args:
        req: Chat request containing conversation_id, user_id, message, and metadata

    Returns:
        Chat response with answer, sources, and conversation_id
    """
    # Initialize memory store
    memory_store = create_history_store()

    # Load prior messages
    prior_messages: List[BaseMessage] = memory_store.get_messages(req.conversation_id)

    # Check if summarization is needed
    if len(prior_messages) > _summarization_threshold:
        # Summarize older messages, keep recent ones
        recent_messages = prior_messages[-10:]  # Keep last 10 messages
        older_messages = prior_messages[:-10]
        summary = summarize_messages(older_messages)
        prior_messages = [summary] + recent_messages

    # Initial state with prior messages and new user message
    state = {
        "messages": prior_messages + [HumanMessage(content=req.message)],
    }

    # Invoke graph
    final_state = graph.invoke(state)

    # Extract new messages (everything after prior_messages + user message)
    new_messages = final_state["messages"][len(prior_messages) + 1 :]

    # Append new messages to memory store
    if new_messages:
        memory_store.append_messages(req.conversation_id, new_messages)

    # Extract answer from final state
    ai_msgs = [m for m in final_state["messages"] if m.type == "ai"]
    answer = ai_msgs[-1].content if ai_msgs else ""

    # Extract sources from final state
    from .models import Source

    sources = []
    if "sources" in final_state:
        for source_dict in final_state["sources"]:
            sources.append(
                Source(
                    document_id=source_dict.get("document_id", "unknown"),
                    source_type=source_dict.get("source_type", "document"),
                    score=source_dict.get("score", 0.0),
                    snippet=source_dict.get("snippet", ""),
                )
            )

    return ChatResponse(
        conversation_id=req.conversation_id,
        answer=answer,
        sources=sources,
    )

