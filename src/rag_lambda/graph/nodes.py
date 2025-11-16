"""LangGraph nodes for RAG pipeline."""

import os
from typing import List

import yaml
from langchain_aws import ChatBedrockConverse
from langchain_community.retrievers.bedrock import AmazonKnowledgeBasesRetriever
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from .prompts import answer_prompt, clarify_prompt, rewrite_prompt, split_prompt
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




def rewrite_node(state: MessagesState) -> MessagesState:
    """Rewrite user query for better retrieval."""
    last_user = [m for m in state["messages"] if isinstance(m, HumanMessage)][-1]
    rewritten = (rewrite_prompt | llm).invoke({"query": last_user.content})
    state["messages"].append(AIMessage(name="rewriter", content=rewritten.content))
    return state





def answer_node(state: MessagesState) -> MessagesState:
    """Generate answer using retrieved context."""
    user = [m for m in state["messages"] if isinstance(m, HumanMessage)][-1]
    ctx_msgs = [m for m in state["messages"] if getattr(m, "name", "") == "retriever_context"]
    context = ctx_msgs[-1].content if ctx_msgs else ""
    resp = (answer_prompt | llm).invoke({"context": context, "question": user.content})
    state["messages"].append(resp)
    return state


def clarify_node(state: MessagesState) -> MessagesState:
    """Ask clarifying questions for underspecified queries."""
    user = [m for m in state["messages"] if m.type == "human"][-1]
    resp = (clarify_prompt | llm).invoke({"question": user.content})
    if resp.content.strip().upper() != "CLEAR":
        # Ask a clarifying question - graph should end here, caller will show question to user
        state["messages"].append(resp)
    return state


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

