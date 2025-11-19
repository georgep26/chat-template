"""
Retrieval module with filtering and metadata injection.
Handles Bedrock Knowledge Base retrieval with retrieval filters.
"""
from typing import Dict, List
from langchain_core.documents import Document
from langchain_aws.retrievers.bedrock import AmazonKnowledgeBasesRetriever
from .prompt_config import KB_ID, AWS_REGION, DEFAULT_TOP_K


def build_filters(retrieval_filters: Dict) -> Dict:
    """
    Build the Bedrock KB metadata filter structure from retrieval filters.
    
    Accepts format: {"key1": ['val1', 'val2'], "key2": ['val1']}
    Converts to Bedrock filters with OR logic for multiple values per key,
    and AND logic between different keys.
    
    Args:
        retrieval_filters: Dictionary where keys are filter field names and 
                          values are lists of filter values (e.g., {"key1": ["val1", "val2"]})
    
    Returns:
        Filter dictionary compatible with Bedrock KB retrieval config
    """
    if not retrieval_filters:
        return {}
    
    and_all = []
    
    for key, values in retrieval_filters.items():
        if not values:
            continue
        
        # Ensure values is a list
        if not isinstance(values, list):
            values = [values]
        
        # If single value, add directly as equals filter
        if len(values) == 1:
            and_all.append({
                "equals": {"key": key, "value": values[0]}
            })
        else:
            # Multiple values: create OR group
            or_all = [{"equals": {"key": key, "value": val}} for val in values]
            and_all.append({"orAll": or_all})
    
    return {"andAll": and_all} if and_all else {}


def make_retriever(retrieval_filters: Dict) -> AmazonKnowledgeBasesRetriever:
    """
    Create a Bedrock Knowledge Base retriever with retrieval filters.
    
    Args:
        retrieval_filters: Dictionary of retrieval filters
    
    Returns:
        Configured AmazonKnowledgeBasesRetriever instance
    """
    filters = build_filters(retrieval_filters)

    retrieval_config = {
        "vectorSearchConfiguration": {"numberOfResults": DEFAULT_TOP_K}
    }
    if filters:
        retrieval_config["filters"] = filters

    return AmazonKnowledgeBasesRetriever(
        knowledge_base_id=KB_ID,
        region_name=AWS_REGION,
        retrieval_config=retrieval_config,
    )


def docs_to_context(docs: List[Document]) -> str:
    """
    Convert retrieved docs into metadata-rich blocks that go into the model prompt.
    Includes all available metadata fields dynamically.
    
    Args:
        docs: List of retrieved Document objects with metadata
    
    Returns:
        Formatted context string with XML tags and all metadata fields
    """
    blocks = []
    for d in docs:
        m = d.metadata or {}
        
        # Build metadata section with all available fields
        metadata_lines = []
        for key, value in m.items():
            if value is not None:
                metadata_lines.append(f"{key}: {value}")
        
        # Build the context block
        block = "<Text Context:>\n"
        if metadata_lines:
            block += "\n".join(metadata_lines) + "\n"
        block += (d.page_content or "")[:2000]
        
        blocks.append(block)
    
    return "\n\n".join(blocks)


def docs_to_citations(docs: List[Document]) -> List[Dict]:
    """
    Build the citations payload we return to the frontend.
    Returns all metadata available in each document.
    
    Args:
        docs: List of retrieved Document objects with metadata
    
    Returns:
        List of citation dictionaries containing all metadata fields from each document
    """
    cites = []
    for d in docs:
        m = d.metadata or {}
        # Include all metadata fields
        citation = dict(m)
        # Add snippet/context from page_content
        citation["snippet"] = (d.page_content or "")[:300]
        cites.append(citation)
    return cites

