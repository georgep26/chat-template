"""Knowledge API Lambda — agent-callable retrieval primitives.

Exposes vector search, document chunk listing, and individual chunk fetch
over HTTP so external agents (e.g. via MCP) and the RAG Lambda graph share
a single retrieval contract. The actual logic lives in ``src/knowledge/``;
this package only contains the HTTP handler.
"""
