"""Tests for LangGraph node behavior."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.rag_lambda.graph import nodes


def test_format_conversation_history_keeps_visible_prior_messages():
    """Conversation history excludes internal graph messages."""
    history = nodes.format_conversation_history(
        [
            SystemMessage(name="conversation_summary", content="Earlier HVAC discussion."),
            HumanMessage(content="What is a duct system?"),
            AIMessage(content="A duct system moves conditioned air."),
            AIMessage(name="rewriter", content="internal rewrite"),
            SystemMessage(name="retriever_context", content="internal retrieval context"),
        ]
    )

    assert "Conversation summary: Earlier HVAC discussion." in history
    assert "User: What is a duct system?" in history
    assert "Assistant: A duct system moves conditioned air." in history
    assert "internal rewrite" not in history
    assert "internal retrieval context" not in history


def test_format_prompt_messages_for_debug_shows_roles_and_names():
    """Prompt debug output keeps role boundaries visible."""
    formatted = nodes.format_prompt_messages_for_debug(
        [
            SystemMessage(content="System instructions."),
            SystemMessage(name="retriever_context", content="Retrieved context."),
            HumanMessage(content="Latest question."),
        ]
    )

    assert "--- SYSTEM ---\nSystem instructions." in formatted
    assert "--- SYSTEM name=retriever_context ---\nRetrieved context." in formatted
    assert "--- HUMAN ---\nLatest question." in formatted


def test_answer_node_passes_prior_history_to_prompt(monkeypatch):
    """Answer generation receives previous turns separately from the latest question."""
    captured = {}

    class FakePrompt:
        def __or__(self, llm):
            del llm

            class FakeChain:
                def invoke(self, values):
                    captured.update(values)
                    return AIMessage(content="test answer")

            return FakeChain()

    monkeypatch.setattr(nodes, "answer_prompt", FakePrompt())
    monkeypatch.setattr(nodes, "ChatBedrockConverse", lambda **kwargs: object())

    state = {
        "messages": [
            HumanMessage(content="What is a duct system?"),
            AIMessage(content="A duct system moves conditioned air."),
            HumanMessage(content="Can you explain that more simply?"),
            AIMessage(name="rewriter", content="internal rewrite"),
            SystemMessage(name="retriever_context", content="Relevant context:\nDuct systems distribute air."),
        ]
    }

    result = nodes.answer_node(state, config={"configurable": {"rag_chat": {}}})

    assert captured["question"] == "Can you explain that more simply?"
    assert captured["context"] == "Relevant context:\nDuct systems distribute air."
    assert captured["history"] == (
        "User: What is a duct system?\n"
        "Assistant: A duct system moves conditioned air."
    )
    assert result["messages"][-1].content == "test answer"
