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
            "You are a RAG assistant. Use the provided context. "
            "If the answer is not in the context, say you don't know.",
        ),
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

