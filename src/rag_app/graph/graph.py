"""LangGraph graph construction for RAG pipeline."""

from langgraph.graph import END, StateGraph

from .nodes import answer_node, clarify_node, retrieve_node, rewrite_node, split_node
from .state import MessagesState


def build_rag_graph():
    """Build and compile the RAG LangGraph with query pipeline enhancements."""
    graph = StateGraph(MessagesState)
    graph.add_node("rewrite", rewrite_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("split", split_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("answer", answer_node)
    graph.set_entry_point("rewrite")
    graph.add_edge("rewrite", "clarify")
    graph.add_edge("clarify", "split")
    graph.add_edge("split", "retrieve")
    graph.add_edge("retrieve", "answer")
    graph.add_edge("answer", END)
    return graph.compile()

