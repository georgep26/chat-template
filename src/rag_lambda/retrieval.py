"""
Retrieval module with RBAC filtering and metadata injection.
Handles Bedrock Knowledge Base retrieval with role-based and UI filters.
"""
from typing import Dict, List
from langchain_core.documents import Document
from langchain_aws.retrievers.bedrock import AmazonKnowledgeBasesRetriever
from .prompt_config import KB_ID, AWS_REGION, DEFAULT_TOP_K


def build_filters(user_roles: List[str], ui_filters: Dict) -> Dict:
    """
    Build the Bedrock KB metadata filter structure for RBAC + user-specified filters.
    
    Example:
    - Only docs where allowed_groups contains one of user's roles
    - Filter on document_type and version
    
    Args:
        user_roles: List of user role strings (e.g., ["Finance", "HR"])
        ui_filters: Dictionary of UI-specified filters (e.g., {"document_type": "Policy", "version": "2023.4"})
    
    Returns:
        Filter dictionary compatible with Bedrock KB retrieval config
    """
    f = {"andAll": []}

    # RBAC filtering: user must have at least one role that matches allowed_groups
    if user_roles:
        f["andAll"].append({
            "orAll": [{"contains": {"key": "allowed_groups", "value": role}}
                      for role in user_roles]
        })

    # UI-specified filters
    if (doc_type := ui_filters.get("document_type")):
        f["andAll"].append({
            "equals": {"key": "document_type", "value": doc_type}
        })

    if (version := ui_filters.get("version")):
        f["andAll"].append({
            "equals": {"key": "document_version_number", "value": version}
        })

    # Return empty dict if no filters (no filtering applied)
    return f if f["andAll"] else {}


def make_retriever(user_roles: List[str], ui_filters: Dict) -> AmazonKnowledgeBasesRetriever:
    """
    Create a Bedrock Knowledge Base retriever with RBAC and UI filters.
    
    Args:
        user_roles: List of user role strings
        ui_filters: Dictionary of UI-specified filters
    
    Returns:
        Configured AmazonKnowledgeBasesRetriever instance
    """
    filters = build_filters(user_roles, ui_filters)

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
    This is how the LLM 'sees' document_title / version / etc.
    
    Args:
        docs: List of retrieved Document objects with metadata
    
    Returns:
        Formatted context string with document metadata
    """
    blocks = []
    for i, d in enumerate(docs, 1):
        m = d.metadata or {}
        header = (
            f"[CONTEXT #{i}]\n"
            f"Title: {m.get('document_title','?')} | "
            f"Type: {m.get('document_type','?')} | "
            f"Version: {m.get('document_version_number','?')}\n"
            f"Location: {m.get('s3_uri', m.get('source',''))}"
            f"{' #page='+str(m['page']) if 'page' in m else ''}\n"
            "----\n"
        )
        # Truncate long chunks to control token budget
        blocks.append(header + (d.page_content or "")[:2000])
    return "\n\n".join(blocks)


def docs_to_citations(docs: List[Document]) -> List[Dict]:
    """
    Build the citations payload we return to the frontend.
    
    Args:
        docs: List of retrieved Document objects with metadata
    
    Returns:
        List of citation dictionaries with title, type, version, s3_uri, page, snippet
    """
    cites = []
    for d in docs:
        m = d.metadata or {}
        cites.append({
            "title":   m.get("document_title"),
            "type":    m.get("document_type"),
            "version": m.get("document_version_number"),
            "s3_uri":  m.get("s3_uri", m.get("source")),
            "page":    m.get("page"),
            "snippet": (d.page_content or "")[:300],
        })
    return cites

