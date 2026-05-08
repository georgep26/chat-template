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
            "- Cite factual claims with the source number in square brackets, e.g. [1].\n"
            "- Place citations immediately after the sentence or clause they support.\n"
            "- If a claim uses multiple sources, cite each source, e.g. [1][3].\n"
            "- Only cite retrieved source blocks. Do not invent source numbers.\n"
            "- If the answer is not supported by the retrieved context or conversation history, say you don't know.\n\n"
            "Answer style:\n"
            "- Start with a brief direct answer.\n"
            "- When possible, include a short quote from the relevant source chunk that directly supports the answer.\n"
            "- Put source quotes in quotation marks and cite the source after the quote.",
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
