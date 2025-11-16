"""RAG pipeline for evaluation purposes."""

from typing import List, Tuple

from langchain_core.messages import HumanMessage

from src.rag_lambda.main import build_rag_graph

# Build graph once at module level
graph = build_rag_graph()


def run_rag(question: str) -> Tuple[str, List[str]]:
    """
    Run RAG pipeline for a single question (for evaluation).

    Args:
        question: User question to process

    Returns:
        Tuple of (answer, contexts) where contexts is a list of retrieved context strings
    """
    state = {"messages": [HumanMessage(content=question)]}
    final_state = graph.invoke(state)

    # Extract answer
    ai_msgs = [m for m in final_state["messages"] if m.type == "ai"]
    answer = ai_msgs[-1].content if ai_msgs else ""

    # Extract contexts
    ctx_msgs = [m for m in final_state["messages"] if getattr(m, "name", "") == "retriever_context"]
    contexts = [m.content for m in ctx_msgs]

    return answer, contexts

