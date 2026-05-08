"""Tests for graph prompt requirements."""

from src.rag_lambda.graph.prompts import answer_prompt


def test_answer_prompt_instructs_numbered_source_citations():
    """Answer prompt requires bracketed citations and source quotes."""
    messages = answer_prompt.format_messages(
        history="No previous conversation.",
        context=(
            "Relevant context:\n"
            "<source number=\"1\">\n"
            "source_number: 1\n"
            "<metadata>\nsource: duct-guide.pdf\n</metadata>\n"
            "<chunk>\nDuct systems distribute conditioned air.\n</chunk>\n"
            "</source>"
        ),
        question="What is a duct system?",
    )
    system_text = messages[0].content

    assert "Cite factual claims with the source number in square brackets" in system_text
    assert "e.g. [1]" in system_text
    assert "Place citations immediately after" in system_text
    assert "include a short quote from the relevant source chunk" in system_text
    assert "Do not invent source numbers" in system_text
