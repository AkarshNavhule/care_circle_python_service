import logging
import os

from google.cloud import vision

from config.settings import settings

logger = logging.getLogger(__name__)

if settings.google_application_credentials:
    os.environ.setdefault(
        "GOOGLE_APPLICATION_CREDENTIALS", settings.google_application_credentials
    )

_vision_client: vision.ImageAnnotatorClient | None = None

IMAGE_EXTENSIONS = {
    "image/jpeg", "image/jpg", "image/png", "image/gif",
    "image/bmp", "image/webp", "image/tiff",
}


def _get_client() -> vision.ImageAnnotatorClient:
    global _vision_client
    if _vision_client is None:
        logger.info("[ocr] Initialising Google Vision client")
        _vision_client = vision.ImageAnnotatorClient()
    return _vision_client


def extract_text_with_confidence(file_bytes: bytes, content_type: str = "") -> dict:
    """
    Run Google Vision document_text_detection on raw image bytes.
    Only call this for image files (jpeg, png, etc.) — not PDFs.

    Returns:
        {"full_text": str, "confidence": float, "blocks": [...]}

    Confidence thresholds:
        >= 0.90  → auto-accept
        0.75–0.89 → accept + low flag
        0.60–0.74 → clarification needed
        < 0.60   → needs_review
    """
    logger.info(
        "[ocr] Running Google Vision OCR, content_type=%s, size=%d bytes",
        content_type or "unknown",
        len(file_bytes),
    )
    client = _get_client()
    image = vision.Image(content=file_bytes)
    response = client.document_text_detection(image=image)

    if response.error.message:
        logger.error("[ocr] Google Vision error: %s", response.error.message)
        return {"full_text": "", "confidence": 0.0, "blocks": []}

    full_text = response.full_text_annotation.text or ""
    blocks: list[dict] = []
    confidences: list[float] = []

    for page in response.full_text_annotation.pages:
        for block in page.blocks:
            block_conf = block.confidence or 0.0
            block_text_parts = []
            for paragraph in block.paragraphs:
                for word in paragraph.words:
                    block_text_parts.append("".join(s.text for s in word.symbols))
            block_text = " ".join(block_text_parts)
            if block_text.strip():
                blocks.append({"text": block_text, "confidence": round(block_conf, 4)})
                confidences.append(block_conf)

    avg_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
    logger.info(
        "[ocr] Google Vision result: %d chars, %d blocks, avg_confidence=%.3f",
        len(full_text),
        len(blocks),
        avg_confidence,
    )
    return {"full_text": full_text, "confidence": avg_confidence, "blocks": blocks}
