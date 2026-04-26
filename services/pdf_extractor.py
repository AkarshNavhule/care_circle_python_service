import io
import logging

import pdfplumber

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text from PDF using pdfplumber. Returns empty string if no text layer found."""
    logger.info("[pdf_extractor] Starting PDF text extraction, size=%d bytes", len(file_bytes))
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages_text = []
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    pages_text.append(text)
                    logger.debug("[pdf_extractor] Page %d: extracted %d chars", i + 1, len(text))
                else:
                    logger.debug("[pdf_extractor] Page %d: no text layer found", i + 1)
            result = "\n".join(pages_text)
        logger.info(
            "[pdf_extractor] Extraction complete: %d pages, %d total chars",
            len(pdf.pages) if hasattr(pdf, "pages") else 0,
            len(result),
        )
        return result
    except Exception as exc:
        logger.error("[pdf_extractor] pdfplumber failed: %s", exc)
        return ""
