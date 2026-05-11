"""Figure extraction for structured PDFs.

Consumes the same Textract block list the table extractor uses
(``FeatureTypes=["TABLES","LAYOUT"]``) and turns each ``LAYOUT_FIGURE``
block into a standalone chunk artifact with:

  * a 1–2 sentence LLM-generated description (Nova Lite multimodal)
  * an optional verbatim caption copied from the page
  * an S3 pointer to the cropped page image (kb_artifacts/.../figures/)

We crop the page region using the Textract bounding box (normalized
coords) against a pypdfium2-rendered page bitmap. When pypdfium2 or PIL
aren't available the chunk is still emitted with a whole-page crop placeholder
key — the test suite doesn't exercise real rendering.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Sequence

from document_ingestion_lambda.file_type_processors.pdf import (
    PdfChunkArtifact,
    build_bedrock_sidecar_metadata,
    build_chunk_markdown,
    estimate_token_count,
    join_s3_key,
)

try:  # pragma: no cover - import error reported at runtime
    import pypdfium2 as pdfium
except ImportError:
    pdfium = None  # type: ignore[assignment]

try:  # pragma: no cover
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Config + result types
# ---------------------------------------------------------------------------


@dataclass
class FigureExtractionConfig:
    """Knobs read from ``ingestion.pdf_figure_*`` keys."""

    enabled: bool = True
    summary_model_id: str = "amazon.nova-lite-v1:0"
    summary_temperature: float = 0.0
    summary_max_tokens: int = 384
    render_scale: float = 2.0
    max_figures: int = 200


@dataclass
class FigureExtractionResult:
    status: str = "completed"
    figure_count: int = 0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# (pdf_path, page_number, scale) -> PIL.Image-compatible object with .crop and .save
PageRenderer = Callable[[Path, int, float], Any]


def extract_figure_chunk_artifacts(
    *,
    blocks: Sequence[dict[str, Any]],
    pdf_path: Path,
    document_metadata: dict[str, Any],
    destination_bucket: str,
    index_prefix: str,
    artifacts_prefix: str,
    bedrock_client: Any | None,
    s3_client: Any | None,
    config: FigureExtractionConfig,
    page_renderer: PageRenderer | None = None,
) -> tuple[list[PdfChunkArtifact], FigureExtractionResult]:
    """Emit one chunk per LAYOUT_FIGURE block.

    Each chunk's S3 artifact body is its description; the cropped page
    image is uploaded separately to ``artifacts_prefix`` and linked via
    ``asset_location``. When ``s3_client`` is None or the crop fails the
    chunk is still emitted with ``asset_location`` pointing at the planned
    key (so a later pass can backfill the bytes).
    """
    if not config.enabled:
        return [], FigureExtractionResult(
            status="skipped", warnings=["figure_extraction_disabled"]
        )

    figure_blocks = [b for b in blocks if b.get("BlockType") == "LAYOUT_FIGURE"]
    if not figure_blocks:
        return [], FigureExtractionResult(status="completed", figure_count=0)

    by_id = {b["Id"]: b for b in blocks if isinstance(b, dict) and b.get("Id")}

    # Order figures by (Page, BoundingBox.Top) so chunk ids match reading order.
    figure_blocks.sort(
        key=lambda b: (
            int(b.get("Page", 1)),
            float((b.get("Geometry") or {}).get("BoundingBox", {}).get("Top", 0.0)),
        )
    )

    artifacts: list[PdfChunkArtifact] = []
    warnings: list[str] = []
    document_id = document_metadata["document_id"]
    chunk_index = 0
    per_page_index: dict[int, int] = {}

    for figure_block in figure_blocks:
        if len(artifacts) >= config.max_figures:
            warnings.append(f"max_figures_hit:{config.max_figures}")
            break

        page_number = int(figure_block.get("Page", 1))
        per_page_index[page_number] = per_page_index.get(page_number, 0) + 1
        figure_index_on_page = per_page_index[page_number]
        chunk_index += 1

        bbox = (figure_block.get("Geometry") or {}).get("BoundingBox") or {}
        caption = _nearby_caption_text(figure_block, by_id)

        chunk_id = (
            f"{document_id}_figure_{page_number:04d}_{figure_index_on_page:02d}"
        )
        asset_key = join_s3_key(
            artifacts_prefix, document_id, "figures", f"{chunk_id}.png"
        )

        cropped_png = _try_crop_figure(
            pdf_path=pdf_path,
            page_number=page_number,
            bbox=bbox,
            scale=config.render_scale,
            renderer=page_renderer,
        )
        if cropped_png is not None and s3_client is not None:
            try:
                s3_client.put_object(
                    Bucket=destination_bucket,
                    Key=asset_key,
                    Body=cropped_png,
                    ContentType="image/png",
                )
            except Exception as exc:
                warnings.append(
                    f"figure_asset_upload_failed:{type(exc).__name__}:{chunk_id}"
                )

        description, caption_from_model = _describe_figure(
            png_bytes=cropped_png,
            bedrock_client=bedrock_client,
            config=config,
        )
        final_caption = caption or caption_from_model
        body = (
            f"{description}\n\nCaption: {final_caption}"
            if (description and final_caption)
            else (description or final_caption or "")
        )

        key = join_s3_key(index_prefix, document_id, "figures", f"{chunk_id}.md")
        title = (
            f"{document_metadata['document_title']} - figure on page {page_number}"
        )
        metadata = {
            "metadata_schema_version": document_metadata["metadata_schema_version"],
            "record_type": "chunk",
            "document_id": document_id,
            "chunk_id": chunk_id,
            "tenant_id": document_metadata["tenant_id"],
            "collection_id": document_metadata["collection_id"],
            "document_title": document_metadata["document_title"],
            "title": title,
            "document_type": document_metadata["document_type"],
            "filename": document_metadata["filename"],
            "location": f"s3://{destination_bucket}/{key}",
            "source_location": document_metadata["source_location"],
            "asset_location": f"s3://{destination_bucket}/{asset_key}",
            "chunk_type": "figure",
            "chunk_index": chunk_index,
            "page_start": page_number,
            "page_end": page_number,
            "content_type": "text/markdown",
            "token_count": estimate_token_count(body),
            "char_count": len(body),
            "access_tier": document_metadata.get("access_tier", "restricted"),
            "description": description or None,
            "caption": final_caption or None,
            "custom_metadata": {
                "figure_index_on_page": figure_index_on_page,
                "bbox": bbox,
            },
            "processing": {
                "strategy": "textract_figure_extraction",
                "summary_model_id": config.summary_model_id if description else None,
            },
        }
        artifacts.append(
            PdfChunkArtifact(
                key=key,
                sidecar_key=f"{key}.metadata.json",
                markdown=build_chunk_markdown(metadata, body),
                sidecar_metadata=build_bedrock_sidecar_metadata(metadata),
                catalog_metadata=metadata,
            )
        )

    return artifacts, FigureExtractionResult(
        status="completed",
        figure_count=len(artifacts),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_LAYOUT_TEXT_TYPES = {"LAYOUT_TEXT", "LAYOUT_TITLE", "LAYOUT_HEADER", "LAYOUT_FOOTER"}


def _nearby_caption_text(
    figure_block: dict[str, Any], by_id: dict[str, dict[str, Any]]
) -> str | None:
    """Best-effort: pull caption text from a LAYOUT_TEXT child of the figure.

    Textract sometimes attaches a short caption ``LAYOUT_TEXT`` block as a
    child of the ``LAYOUT_FIGURE``. We use that when present. We don't
    invent captions — when no child text exists we return None.
    """
    for relation in figure_block.get("Relationships", []) or []:
        if relation.get("Type") != "CHILD":
            continue
        for child_id in relation.get("Ids", []):
            child = by_id.get(child_id)
            if not child:
                continue
            if child.get("BlockType") in _LAYOUT_TEXT_TYPES:
                text = _collect_block_text(child, by_id)
                if text:
                    return text.strip()
    return None


def _collect_block_text(block: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str:
    """Walk WORD/LINE children of a layout block to gather its text."""
    pieces: list[str] = []
    for relation in block.get("Relationships", []) or []:
        if relation.get("Type") != "CHILD":
            continue
        for child_id in relation.get("Ids", []):
            child = by_id.get(child_id)
            if not child:
                continue
            block_type = child.get("BlockType")
            if block_type == "WORD":
                pieces.append(str(child.get("Text") or ""))
            elif block_type == "LINE":
                pieces.append(str(child.get("Text") or ""))
    return " ".join(p for p in pieces if p).strip()


def _try_crop_figure(
    *,
    pdf_path: Path,
    page_number: int,
    bbox: dict[str, Any],
    scale: float,
    renderer: PageRenderer | None,
) -> bytes | None:
    """Render the page and crop to the figure bounding box.

    Returns the PNG bytes, or None on any failure. Bounding box uses
    normalized coordinates (0.0-1.0) per Textract's spec.
    """
    if not bbox:
        return None
    if renderer is not None:
        try:
            image = renderer(pdf_path, page_number, scale)
            return _crop_with_pil(image, bbox)
        except Exception:
            return None
    if pdfium is None or Image is None:
        return None
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            if page_number < 1 or page_number > len(doc):
                return None
            page = doc[page_number - 1]
            try:
                bitmap = page.render(scale=scale)
                try:
                    pil_image = bitmap.to_pil()
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            doc.close()
        return _crop_with_pil(pil_image, bbox)
    except Exception:
        return None


def _crop_with_pil(image: Any, bbox: dict[str, Any]) -> bytes | None:
    if image is None or Image is None:
        return None
    width, height = image.size
    left = max(0, int(float(bbox.get("Left", 0)) * width))
    top = max(0, int(float(bbox.get("Top", 0)) * height))
    right = min(width, int((float(bbox.get("Left", 0)) + float(bbox.get("Width", 0))) * width))
    bottom = min(height, int((float(bbox.get("Top", 0)) + float(bbox.get("Height", 0))) * height))
    if right <= left or bottom <= top:
        return None
    cropped = image.crop((left, top, right, bottom))
    buffer = BytesIO()
    cropped.save(buffer, format="PNG")
    return buffer.getvalue()


def _describe_figure(
    *,
    png_bytes: bytes | None,
    bedrock_client: Any | None,
    config: FigureExtractionConfig,
) -> tuple[str, str | None]:
    """Multimodal call: describe the figure + maybe capture a visible caption.

    Returns ``(description, caption_or_none)``. Returns empty/None on any
    failure or when no Bedrock client is available.
    """
    if bedrock_client is None or not png_bytes:
        return "", None

    system = (
        "You are describing one figure from a structured document for a retrieval "
        "index. Look at the image and return ONLY a JSON object with keys:\n"
        "  description: 1-2 sentences naming what the figure depicts and its purpose. "
        "Do NOT invent details that aren't visible. No quotes, no preamble.\n"
        "  caption: if a caption is visible IN THE IMAGE, copy it verbatim. "
        "Otherwise set this to null. NEVER invent a caption."
    )
    try:
        response = bedrock_client.converse(
            modelId=config.summary_model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"text": "Describe this figure."},
                        {"image": {"format": "png", "source": {"bytes": png_bytes}}},
                    ],
                }
            ],
            system=[{"text": system}],
            inferenceConfig={
                "temperature": config.summary_temperature,
                "maxTokens": config.summary_max_tokens,
            },
        )
        text = _extract_text_from_converse_response(response)
        parsed = _parse_figure_json(text)
        return parsed.get("description") or "", parsed.get("caption")
    except Exception:
        return "", None


def _extract_text_from_converse_response(response: dict[str, Any]) -> str:
    output = response.get("output") or {}
    message = output.get("message") or {}
    for item in message.get("content") or []:
        if isinstance(item, dict) and "text" in item:
            return str(item["text"])
    return ""


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_figure_json(text: str) -> dict[str, Any]:
    stripped = text.strip().strip("`")
    if stripped.lower().startswith("json"):
        stripped = stripped[4:].strip()
    try:
        return _coerce_figure(json.loads(stripped))
    except Exception:
        pass
    match = _JSON_BLOCK_RE.search(text)
    if match:
        try:
            return _coerce_figure(json.loads(match.group(0)))
        except Exception:
            pass
    return {"description": text.strip() or None, "caption": None}


def _coerce_figure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("not_an_object")
    description = raw.get("description")
    caption = raw.get("caption")
    return {
        "description": (description.strip() if isinstance(description, str) else None),
        "caption": (caption.strip() if isinstance(caption, str) and caption else None),
    }


__all__ = [
    "FigureExtractionConfig",
    "FigureExtractionResult",
    "extract_figure_chunk_artifacts",
]
