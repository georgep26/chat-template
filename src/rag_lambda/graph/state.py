"""LangGraph state definition for RAG pipeline."""

from typing import Any, Dict, List, TypedDict

from langchain_core.messages import BaseMessage


class MessagesState(TypedDict, total=False):
    """State for the RAG graph containing conversation messages."""

    messages: List[BaseMessage]
    sources: List[Dict[str, Any]]  # Document metadata for sources
    retrieval_config: Dict[str, Any]  # Retrieval configuration

