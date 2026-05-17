"""Tests for app-catalog source enrichment."""

from langchain_core.documents import Document

from knowledge.source_enrichment import enrich_vector_results
from knowledge.vector_search import VectorSearchResult


class FakeStore:
    def get_chunks_by_ids(self, *, chunk_ids, tenant_id, access_tiers=None):
        del tenant_id, access_tiers
        return {
            chunk_ids[0]: {
                "chunk_id": chunk_ids[0],
                "document_id": "doc-1",
                "tenant_id": "tenant-a",
                "hierarchy_node_id": "R303",
                "metadata": {
                    "custom_metadata": {
                        "hierarchy_index_inline": [
                            {"hierarchy_node_id": "R303.10", "char_start": 10, "char_end": 50}
                        ]
                    }
                },
            }
        }


def test_enrich_vector_results_merges_catalog_metadata(monkeypatch):
    monkeypatch.setattr("knowledge.source_enrichment.create_chunk_read_store", lambda _config: FakeStore())
    result = VectorSearchResult(
        document=Document(page_content="chunk text", metadata={"source_metadata": {"chunk_id": "c1"}}),
        document_id="doc-1",
        chunk_id="c1",
        score=0.7,
        source_type="pdf",
        chunk="chunk text",
        metadata={"chunk_id": "c1", "score": 0.7},
        raw_metadata={},
    )

    enriched = enrich_vector_results(
        results=[result],
        config={"rag_chat": {"chat_history_store": {}}},
        tenant_id="tenant-a",
    )

    metadata = enriched[0].metadata
    assert metadata["hierarchy_node_id"] == "R303"
    assert metadata["hierarchy_index_inline"][0]["hierarchy_node_id"] == "R303.10"
    assert enriched[0].document.metadata["source_metadata"]["hierarchy_node_id"] == "R303"
