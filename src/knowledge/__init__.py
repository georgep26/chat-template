"""Shared knowledge primitives — vector search, chunk reads, S3 artifact fetch.

This package is consumed by the RAG Lambda (in-process), the Knowledge API
Lambda (HTTP wrapper), and the Library API Lambda (document-row reads).
Importing the package alone is intentionally cheap — each submodule pulls
its own heavier dependencies (e.g. ``langchain_aws`` in ``vector_search``)
only when imported directly. This lets the Library API Lambda depend on
``knowledge.document_read`` without paying for the LangChain stack.
"""
