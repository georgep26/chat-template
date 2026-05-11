"""Tests for graph prompt requirements."""

from src.rag_lambda.graph.prompts import answer_prompt


def test_answer_prompt_instructs_cite_block_citations():
    """Answer prompt requires <cite source="N">...</cite> blocks for verbatim quotes."""
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

    # New cite-block format is required.
    assert '<cite source="N">' in system_text
    assert "EXACT QUOTE COPIED VERBATIM FROM THE CHUNK" in system_text
    assert "Do not paraphrase" in system_text
    # Bare-marker fallback is still documented.
    assert "<[N]>" in system_text
    # Backend post-processing reservation is preserved.
    assert "Do not write plain [N] citations" in system_text
    assert "Do not invent source numbers" in system_text
