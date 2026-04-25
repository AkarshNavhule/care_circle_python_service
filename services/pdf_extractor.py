import io

import pdfplumber
import fitz  


def _extract_with_pdfplumber(file_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        parts = [page.extract_text() for page in pdf.pages if page.extract_text()]
    return "\n".join(parts)


def _extract_with_fitz(file_bytes: bytes) -> str:
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    try:
        result = _extract_with_pdfplumber(file_bytes)
        if result.strip():
            return result
    except Exception:
        pass

    try:
        result = _extract_with_fitz(file_bytes)
        if result.strip():
            return result
    except Exception:
        pass

    return "[PDF text extraction failed]"
