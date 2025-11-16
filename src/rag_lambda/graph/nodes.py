"""LangGraph nodes for RAG pipeline."""

import os
from typing import List

import yaml
from langchain_aws import ChatBedrockConverse
from langchain_community.retrievers.bedrock import AmazonKnowledgeBasesRetriever
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate

from .state import MessagesState

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

# Initialize LLM (Claude via Bedrock Converse)
llm = ChatBedrockConverse(
    model=_bedrock_config.get("model", {}).get("id", "us.anthropic.claude-3-7-sonnet-20250219-v1:0"),
    region_name=_bedrock_config.get("region", _aws_config.get("region", "us-east-1")),
    temperature=_bedrock_config.get("model", {}).get("temperature", 0.1),
)

# Bedrock KB retriever
kb_id = _bedrock_config.get("knowledge_base_id", "")
kb_retriever = AmazonKnowledgeBasesRetriever(
    knowledge_base_id=kb_id,
    region_name=_bedrock_config.get("region", _aws_config.get("region", "us-east-1")),
    retrieval_config={
        "vectorSearchConfiguration": {
            "numberOfResults": _bedrock_config.get("retrieval", {}).get("number_of_results", 10)
        }
    },
)

# Query rewrite prompt
rewrite_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", "Rewrite the user query for retrieval. Expand acronyms and fix typos."),
        ("human", "{query}"),
    ]
)


def rewrite_node(state: MessagesState) -> MessagesState:
    """Rewrite user query for better retrieval."""
    last_user = [m for m in state["messages"] if isinstance(m, HumanMessage)][-1]
    rewritten = (rewrite_prompt | llm).invoke({"query": last_user.content})
    state["messages"].append(AIMessage(name="rewriter", content=rewritten.content))
    return state


def retrieve_node(state: MessagesState) -> MessagesState:
    """Retrieve relevant documents from knowledge base."""
    last_user = [m for m in state["messages"] if isinstance(m, HumanMessage)][-1]
    docs = kb_retriever.get_relevant_documents(last_user.content)
    # Attach retrieved docs as a synthetic system message
    context_text = "\n\n".join(d.page_content for d in docs)
    state["messages"].append(
        SystemMessage(
            name="retriever_context",
            content=f"Relevant context:\n{context_text}",
        )
    )
    # Capture document metadata for sources
    sources = []
    for doc in docs:
        source_info = {
            "document_id": doc.metadata.get("id", doc.metadata.get("source", "unknown")),
            "source_type": doc.metadata.get("source_type", "document"),
            "score": doc.metadata.get("score", 0.0),
            "snippet": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
        }
        sources.append(source_info)
    state["sources"] = sources
    return state


# Answer generation prompt
answer_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a RAG assistant. Use the provided context. "
            "If the answer is not in the context, say you don't know.",
        ),
        ("system", "{context}"),
        ("human", "{question}"),
    ]
)


def answer_node(state: MessagesState) -> MessagesState:
    """Generate answer using retrieved context."""
    user = [m for m in state["messages"] if isinstance(m, HumanMessage)][-1]
    ctx_msgs = [m for m in state["messages"] if getattr(m, "name", "") == "retriever_context"]
    context = ctx_msgs[-1].content if ctx_msgs else ""
    resp = (answer_prompt | llm).invoke({"context": context, "question": user.content})
    state["messages"].append(resp)
    return state


# Clarification prompt
clarify_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a query assistant. Decide if you need clarification.\n"
            "If the query is underspecified, respond ONLY with a clarifying question.\n"
            "If it's clear, respond with the word CLEAR.",
        ),
        ("human", "{question}"),
    ]
)


def clarify_node(state: MessagesState) -> MessagesState:
    """Ask clarifying questions for underspecified queries."""
    user = [m for m in state["messages"] if m.type == "human"][-1]
    resp = (clarify_prompt | llm).invoke({"question": user.content})
    if resp.content.strip().upper() != "CLEAR":
        # Ask a clarifying question - graph should end here, caller will show question to user
        state["messages"].append(resp)
    return state


# Subquery splitting prompt
split_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "If the user query has multiple distinct questions, split it into a numbered list "
            "of simpler queries. If not, return just the original query as item 1.",
        ),
        ("human", "{question}"),
    ]
)


def split_node(state: MessagesState) -> MessagesState:
    """Split multi-part queries into subqueries."""
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

