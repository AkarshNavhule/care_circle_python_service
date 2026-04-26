"""
Extraction pipeline: OCR/PDF → LLM structured extraction → DB writes (Layer 2 + Layer 3).

Routing:
  - Images (jpeg, png, gif, bmp, webp, tiff, …) → Google Vision OCR → LLM
  - PDFs → pdfplumber text extraction → LLM
        (if pdfplumber returns no text, document is marked needs_review)

Called as a FastAPI BackgroundTask. One coroutine per document, all run concurrently.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

from supabase import Client

from services import ocr as ocr_service
from services import llm as llm_service
from services.pdf_extractor import extract_text_from_pdf

logger = logging.getLogger(__name__)

OCR_MODEL_LABEL = "google-vision-v1"
PDF_MODEL_LABEL = "pdfplumber-v1"
LLM_MODEL_LABEL = "openrouter/gemini-2.0-flash"

THRESHOLD_AUTO_ACCEPT = 0.90
THRESHOLD_LOW_FLAG = 0.75
THRESHOLD_REJECT = 0.60


async def process_document(document_id: str, patient_id: str, db: Client) -> None:
    """Full processing pipeline for a single document."""
    logger.info("[pipeline][%s] Starting extraction for patient=%s", document_id, patient_id)
    try:
        await _set_status(db, document_id, "processing")

        # ── Fetch document record ────────────────────────────────────────────
        logger.debug("[pipeline][%s] Fetching document record from DB", document_id)
        doc_resp = db.table("documents").select("*").eq("id", document_id).single().execute()
        doc = doc_resp.data
        document_type = doc["document_type"]
        file_type = doc["file_type"]
        storage_path = doc["storage_path"]
        logger.info(
            "[pipeline][%s] Document: type=%s, file_type=%s, path=%s",
            document_id, document_type, file_type, storage_path,
        )

        # ── Fetch raw file bytes from R2 ─────────────────────────────────────
        logger.debug("[pipeline][%s] Fetching file bytes from R2", document_id)
        from services.storage import _get_s3
        from config.settings import settings

        s3 = _get_s3()
        s3_obj = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: s3.get_object(Bucket=settings.r2_bucket_name, Key=storage_path),
        )
        file_bytes: bytes = s3_obj["Body"].read()
        logger.info("[pipeline][%s] Fetched %d bytes from R2", document_id, len(file_bytes))

        # ── Route: PDF vs Image ──────────────────────────────────────────────
        is_pdf = file_type == "application/pdf"
        is_image = file_type in ocr_service.IMAGE_EXTENSIONS

        if is_pdf:
            logger.info("[pipeline][%s] Routing to pdfplumber (PDF)", document_id)
            raw_text = await asyncio.get_event_loop().run_in_executor(
                None, extract_text_from_pdf, file_bytes
            )
            if not raw_text.strip():
                logger.warning(
                    "[pipeline][%s] pdfplumber returned no text — marking needs_review",
                    document_id,
                )
                await _set_status(
                    db, document_id, "needs_review",
                    error="PDF has no extractable text layer; re-upload a clearer scan",
                )
                return
            ocr_text = raw_text
            # PDF text extraction is treated as high-confidence
            ocr_confidence = 0.95
            ocr_model_used = PDF_MODEL_LABEL
            logger.info(
                "[pipeline][%s] pdfplumber extracted %d chars, confidence=%.2f",
                document_id, len(ocr_text), ocr_confidence,
            )

        elif is_image:
            logger.info(
                "[pipeline][%s] Routing to Google Vision OCR (image, content_type=%s)",
                document_id, file_type,
            )
            ocr_result = await asyncio.get_event_loop().run_in_executor(
                None, ocr_service.extract_text_with_confidence, file_bytes, file_type
            )
            ocr_text = ocr_result["full_text"]
            ocr_confidence = ocr_result["confidence"]
            ocr_model_used = OCR_MODEL_LABEL
            logger.info(
                "[pipeline][%s] Google Vision extracted %d chars, confidence=%.3f",
                document_id, len(ocr_text), ocr_confidence,
            )

            if ocr_confidence < THRESHOLD_REJECT and not ocr_text.strip():
                logger.warning(
                    "[pipeline][%s] OCR confidence=%.3f below reject threshold=%.2f — needs_review",
                    document_id, ocr_confidence, THRESHOLD_REJECT,
                )
                await _set_status(
                    db, document_id, "needs_review",
                    error="OCR confidence too low; re-upload a clearer image",
                )
                return

        else:
            logger.warning(
                "[pipeline][%s] Unsupported file_type=%s — marking needs_review",
                document_id, file_type,
            )
            await _set_status(
                db, document_id, "needs_review",
                error=f"Unsupported file type: {file_type}",
            )
            return

        # ── LLM structured extraction ─────────────────────────────────────────
        logger.info(
            "[pipeline][%s] Sending %d chars to LLM for document_type=%s",
            document_id, len(ocr_text), document_type,
        )
        extracted_data = await llm_service.extract_structured_data(ocr_text, document_type)
        if "error" in extracted_data:
            logger.error(
                "[pipeline][%s] LLM extraction failed: %s", document_id, extracted_data["error"]
            )
            await _set_status(db, document_id, "failed", error=f"LLM error: {extracted_data['error']}")
            return

        overall_llm_confidence = float(extracted_data.get("overall_confidence", ocr_confidence))
        logger.info(
            "[pipeline][%s] LLM extraction done, overall_confidence=%.3f",
            document_id, overall_llm_confidence,
        )

        field_confidences: dict = {}
        flagged_fields: list = []
        _collect_field_confidences(
            extracted_data, document_type, field_confidences, flagged_fields, THRESHOLD_LOW_FLAG
        )
        if flagged_fields:
            logger.warning(
                "[pipeline][%s] %d field(s) below low-confidence threshold: %s",
                document_id, len(flagged_fields), [f["field"] for f in flagged_fields],
            )

        # ── Insert document_extractions row ───────────────────────────────────
        logger.debug("[pipeline][%s] Inserting document_extractions row", document_id)
        extraction_row = {
            "document_id": document_id,
            "raw_ocr_text": ocr_text,
            "ocr_confidence": ocr_confidence,
            "ocr_model_used": ocr_model_used,
            "extracted_data": extracted_data,
            "extraction_model": LLM_MODEL_LABEL,
            "overall_confidence": overall_llm_confidence,
            "field_confidences": field_confidences,
            "flagged_fields": flagged_fields if flagged_fields else None,
        }
        ext_resp = db.table("document_extractions").insert(extraction_row).execute()
        extraction_id = ext_resp.data[0]["id"]
        logger.info("[pipeline][%s] document_extractions row created: id=%s", document_id, extraction_id)

        # ── Merge into Layer 3 ────────────────────────────────────────────────
        document_date_str = doc.get("document_date")
        document_date = (
            datetime.fromisoformat(document_date_str).date() if document_date_str else None
        )
        logger.info(
            "[pipeline][%s] Merging into Layer 3, document_type=%s, document_date=%s",
            document_id, document_type, document_date,
        )

        if document_type == "prescription":
            await _merge_prescription(
                db, patient_id, document_id, extraction_id,
                extracted_data, ocr_confidence, document_date,
            )
        elif document_type == "lab_report":
            await _merge_lab_report(
                db, patient_id, document_id, extraction_id,
                extracted_data, ocr_confidence, document_date,
            )
        elif document_type == "discharge_summary":
            await _merge_discharge_summary(
                db, patient_id, document_id, extraction_id, extracted_data,
            )
        else:
            logger.info(
                "[pipeline][%s] document_type=%s has no Layer 3 merge handler — skipping",
                document_id, document_type,
            )

        await _set_status(db, document_id, "completed")
        logger.info("[pipeline][%s] Extraction pipeline complete", document_id)

    except Exception as exc:
        logger.exception("[pipeline][%s] Extraction pipeline failed: %s", document_id, exc)
        await _set_status(db, document_id, "failed", error=str(exc))


async def process_all_documents(
    document_ids: list[str], patient_id: str, db: Client
) -> None:
    logger.info(
        "[pipeline] Starting concurrent extraction for %d document(s), patient=%s",
        len(document_ids), patient_id,
    )
    await asyncio.gather(
        *[process_document(doc_id, patient_id, db) for doc_id in document_ids],
        return_exceptions=True,
    )
    logger.info("[pipeline] All document extractions finished for patient=%s", patient_id)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _set_status(db: Client, document_id: str, status: str, error: str | None = None):
    logger.debug("[pipeline][%s] Setting extraction_status=%s", document_id, status)
    update: dict = {"extraction_status": status}
    if error:
        update["extraction_error"] = error
    db.table("documents").update(update).eq("id", document_id).execute()


def _collect_field_confidences(
    extracted_data: dict,
    document_type: str,
    field_confidences: dict,
    flagged_fields: list,
    threshold: float,
):
    items = []
    if document_type == "prescription":
        items = extracted_data.get("medications", [])
    elif document_type == "lab_report":
        items = extracted_data.get("tests", [])

    for i, item in enumerate(items):
        conf = item.get("confidence", 1.0)
        name = item.get("drug_name_generic") or item.get("test_name") or f"item_{i}"
        field_confidences[name] = conf
        if conf < threshold:
            flagged_fields.append({"field": name, "confidence": conf})


async def _merge_prescription(
    db: Client,
    patient_id: str,
    document_id: str,
    extraction_id: str,
    extracted_data: dict,
    ocr_confidence: float,
    prescription_date: date | None,
):
    medications = extracted_data.get("medications", [])
    if not medications:
        logger.info("[pipeline][%s] No medications found in extraction", document_id)
        return

    rows = []
    for med in medications:
        conf = float(med.get("confidence", ocr_confidence))
        auto_confirmed = conf >= THRESHOLD_AUTO_ACCEPT
        rows.append({
            "patient_id": patient_id,
            "drug_name_generic": med.get("drug_name_generic", "unknown"),
            "drug_name_brand": med.get("drug_name_brand"),
            "drug_name_original_ocr": med.get("drug_name_generic"),
            "dose_mg": med.get("dose_mg"),
            "dose_unit": med.get("dose_unit", "mg"),
            "frequency": med.get("frequency"),
            "timing": med.get("timing"),
            "source": "document_extracted",
            "source_document_id": document_id,
            "source_extraction_id": extraction_id,
            "prescription_date": prescription_date.isoformat() if prescription_date else None,
            "confirmed_by_guardian": auto_confirmed,
            "extraction_confidence": conf,
            "currency_uncertain": (
                prescription_date is not None
                and (date.today() - prescription_date).days > 180
            ),
        })
        logger.debug(
            "[pipeline][%s] Medication: %s, confidence=%.3f, auto_confirmed=%s",
            document_id, med.get("drug_name_generic"), conf, auto_confirmed,
        )

    db.table("medications").insert(rows).execute()
    logger.info("[pipeline][%s] Inserted %d medication(s)", document_id, len(rows))


async def _merge_lab_report(
    db: Client,
    patient_id: str,
    document_id: str,
    extraction_id: str,
    extracted_data: dict,
    ocr_confidence: float,
    report_date: date | None,
):
    tests = extracted_data.get("tests", [])
    if not tests:
        logger.info("[pipeline][%s] No lab tests found in extraction", document_id)
        return

    today = date.today()
    rows = []
    for test in tests:
        rd = report_date or today
        rows.append({
            "patient_id": patient_id,
            "source_document_id": document_id,
            "source_extraction_id": extraction_id,
            "test_name": test.get("test_name", "unknown"),
            "test_category": test.get("test_category"),
            "value_numeric": test.get("value_numeric"),
            "value_text": test.get("value_text"),
            "unit": test.get("unit"),
            "reference_low": test.get("reference_low"),
            "reference_high": test.get("reference_high"),
            "is_flagged": bool(test.get("is_flagged", False)),
            "flag_direction": test.get("flag_direction"),
            "report_date": rd.isoformat(),
            "is_stale": (today - rd).days > 180,
            "lab_name": extracted_data.get("lab_name"),
            "extraction_confidence": float(test.get("confidence", ocr_confidence)),
        })
        logger.debug(
            "[pipeline][%s] Lab test: %s, value=%s %s, flagged=%s",
            document_id,
            test.get("test_name"),
            test.get("value_numeric") or test.get("value_text"),
            test.get("unit", ""),
            test.get("is_flagged", False),
        )

    db.table("lab_results").insert(rows).execute()
    logger.info("[pipeline][%s] Inserted %d lab result(s)", document_id, len(rows))


async def _merge_discharge_summary(
    db: Client,
    patient_id: str,
    document_id: str,
    extraction_id: str,
    extracted_data: dict,
):
    diagnoses = extracted_data.get("diagnoses", [])
    if not diagnoses:
        logger.info("[pipeline][%s] No diagnoses found in discharge summary", document_id)
        return

    for diag in diagnoses:
        db.table("diagnoses").insert({
            "patient_id": patient_id,
            "condition_name": diag.get("condition_name", "unknown"),
            "icd_code": diag.get("icd_code"),
            "confirmation_status": "suspected",
            "confirmed_by_guardian": False,
            "source": "document_extracted",
            "source_document_id": document_id,
        }).execute()
        logger.debug(
            "[pipeline][%s] Diagnosis: %s (ICD: %s)",
            document_id, diag.get("condition_name"), diag.get("icd_code"),
        )

    logger.info(
        "[pipeline][%s] Inserted %d diagnosis/diagnoses from discharge summary",
        document_id, len(diagnoses),
    )
