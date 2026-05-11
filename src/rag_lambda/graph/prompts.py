"""Prompt templates for RAG pipeline nodes."""

from langchain_core.prompts import ChatPromptTemplate

# Query rewrite prompt
rewrite_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", "You are a pre-reviewer for a chatbot. Rewrite the user query for retrieval. Expand acronyms and fix typos. Do not answer the question, just rewrite it for retrieval."),
        ("human", "{query}"),
    ]
)

# Answer generation prompt
answer_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a RAG assistant. Use the conversation history to understand follow-up questions "
            "and use the retrieved context for factual grounding. Retrieved context is provided as "
            "numbered source blocks with metadata and chunk text; use the metadata to understand each "
            "chunk's origin, score, document, page, or other available attributes.\n\n"
            "Citation rules:\n"
            "- Whenever you cite a source, wrap the exact supporting text in a cite block:\n"
            "    <cite source=\"N\">EXACT QUOTE COPIED VERBATIM FROM THE CHUNK</cite>\n"
            "  where N is the source number shown in the source block.\n"
            "- Inside <cite>, copy the text VERBATIM from the chunk. Do not paraphrase, summarize, "
            "  reword, or add ellipses. Preserve original punctuation and capitalization. If the "
            "  backend cannot match your quote against the chunk text it will drop the quote.\n"
            "- Keep each quote short — one sentence or one phrase is ideal.\n"
            "- Place the <cite> block in the sentence where the claim is made.\n"
            "- If a single claim is supported by multiple sources, emit one <cite> block per source, "
            "  back-to-back: <cite source=\"1\">...</cite><cite source=\"3\">...</cite>.\n"
            "- If you need to attribute a claim to a source without quoting (rare), you may fall back "
            "  to the bare marker <[N]>. Prefer <cite> whenever a verbatim quote is available.\n"
            "- Do not write plain [N] citations — plain [N] is reserved for backend post-processing.\n"
            "- Only cite retrieved source blocks. Do not invent source numbers.\n"
            "- Do not cite conversation history. If answering only from conversation history, include no source citations.\n"
            "- If the answer is not supported by the retrieved context or conversation history, say you don't know.\n\n"
            "Answer style:\n"
            "- Start with a brief direct answer.\n"
            "- Weave <cite> blocks into your sentences where they support the claim.",
        ),
        ("system", "Conversation history:\n{history}"),
        ("system", "{context}"),
        ("human", "{question}"),
    ]
)

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
