"""Response shapes for the Library API Lambda."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class CatalogDocument(BaseModel):
    """One row of the authorized document catalog."""

    document_id: str
    tenant_id: str
    collection_id: str
    document_title: str
    document_description: Optional[str] = None
    filename: str
    document_type: str
    source_status: str
    access_tier: str
    location: str
    source_location: str
    publish_date: Optional[str] = None
    version_label: Optional[str] = None
    jurisdiction: Optional[str] = None
    code_family: Optional[str] = None
    uploaded_at: Optional[str] = None
    ingested_at: Optional[str] = None
    metadata: Dict[str, Any] = {}


class ListDocumentsResponse(BaseModel):
    documents: List[CatalogDocument]
    next_offset: Optional[int] = None


class GetDocumentResponse(BaseModel):
    document: CatalogDocument
