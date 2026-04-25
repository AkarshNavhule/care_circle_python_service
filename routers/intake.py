import asyncio
import logging
import uuid
from typing import List

from fastapi import APIRouter, BackgroundTasks, File, Form, UploadFile

from services import data_processor, image_ocr, pdf_extractor

router = APIRouter()
logger = logging.getLogger(__name__)


def _is_pdf(content_type: str) -> bool:
    return content_type == "application/pdf"


async def _run_ocr_background(
    file_id: str,
    photo_jobs: list[tuple[str, bytes, str]],
    old_image_jobs: list[tuple[str, bytes, str]],
) -> None:
    """Run all OCR calls concurrently and patch the saved file when done."""
    logger.info("[%s] Background OCR started — %d prescription photo(s), %d old prescription image(s)",
                file_id, len(photo_jobs), len(old_image_jobs))

    async def ocr_one(filename: str, file_bytes: bytes, mime: str) -> str:
        logger.info("[%s] OCR start: %s (%s, %d bytes)", file_id, filename, mime, len(file_bytes))
        text = await image_ocr.extract_text_from_image(file_bytes, mime)
        logger.info("[%s] OCR done: %s → %d chars", file_id, filename, len(text))
        return text

    # Fire all OCR calls at once
    photo_coros = [ocr_one(fn, b, m) for fn, b, m in photo_jobs]
    old_image_coros = [ocr_one(fn, b, m) for fn, b, m in old_image_jobs]

    photo_results, old_image_results = await asyncio.gather(
        asyncio.gather(*photo_coros),
        asyncio.gather(*old_image_coros),
    )

    data_processor.update_ocr(file_id, "extracted_prescription_photos", list(photo_results))
    data_processor.update_ocr(file_id, "extracted_old_prescriptions", list(old_image_results))
    data_processor.update_ocr(file_id, "ocr_status", "done")
    logger.info("[%s] Background OCR complete. File updated.", file_id)


@router.post("/intake")
async def submit_intake(
    background_tasks: BackgroundTasks,
    full_name: str = Form(...),
    age_or_dob: str = Form(...),
    gender: str = Form(...),
    city: str = Form(...),
    height_weight: str = Form(...),
    primary_language: str = Form(...),
    diagnosed_conditions: str = Form(...),
    current_medications: str = Form(...),
    known_allergies: str = Form(...),
    otc_meds_supplements: str = Form(...),
    recent_doctor_visits: str = Form(...),
    doctor_contact_info: str = Form(...),
    medication_consistency: str = Form(...),
    caregiver_info: str = Form(...),
    typical_day: str = Form(...),
    main_concern: str = Form(...),
    recent_hospitalizations: str = Form(...),
    prescription_photos: List[UploadFile] = File(...),
    lab_reports: List[UploadFile] = File(...),
    old_prescriptions: List[UploadFile] = File(...),
):
    file_id = uuid.uuid4().hex
    logger.info("[%s] Intake request received for patient: %s", file_id, full_name)

    form_data = {
        "full_name": full_name,
        "age_or_dob": age_or_dob,
        "gender": gender,
        "city": city,
        "height_weight": height_weight,
        "primary_language": primary_language,
        "diagnosed_conditions": diagnosed_conditions,
        "current_medications": current_medications,
        "known_allergies": known_allergies,
        "otc_meds_supplements": otc_meds_supplements,
        "recent_doctor_visits": recent_doctor_visits,
        "doctor_contact_info": doctor_contact_info,
        "medication_consistency": medication_consistency,
        "caregiver_info": caregiver_info,
        "typical_day": typical_day,
        "main_concern": main_concern,
        "recent_hospitalizations": recent_hospitalizations,
    }

    # --- Read all file bytes upfront (must happen before response is sent) ---

    # prescription photos — always images, OCR in background
    photo_jobs: list[tuple[str, bytes, str]] = []
    for f in prescription_photos:
        photo_jobs.append((f.filename or "unknown", await f.read(), f.content_type or "image/jpeg"))
    logger.info("[%s] Queued %d prescription photo(s) for background OCR", file_id, len(photo_jobs))

    # lab reports — always PDFs, extract now (fast, no network)
    logger.info("[%s] Extracting %d lab report PDF(s)", file_id, len(lab_reports))
    lab_texts: list[str] = []
    for f in lab_reports:
        raw = await f.read()
        try:
            text = pdf_extractor.extract_text_from_pdf(raw)
            logger.info("[%s] PDF extracted: %s → %d chars", file_id, f.filename, len(text))
        except Exception as e:
            logger.error("[%s] PDF failed: %s — %s", file_id, f.filename, e)
            text = f"[PDF extraction error: {e}]"
        lab_texts.append(text)

    # old prescriptions — PDFs extracted now, images queued for background OCR
    old_image_jobs: list[tuple[str, bytes, str]] = []
    old_pdf_texts: list[str] = []
    for f in old_prescriptions:
        raw = await f.read()
        if _is_pdf(f.content_type or ""):
            try:
                text = pdf_extractor.extract_text_from_pdf(raw)
                logger.info("[%s] Old prescription PDF: %s → %d chars", file_id, f.filename, len(text))
            except Exception as e:
                logger.error("[%s] Old prescription PDF failed: %s — %s", file_id, f.filename, e)
                text = f"[PDF extraction error: {e}]"
            old_pdf_texts.append(text)
        else:
            old_image_jobs.append((f.filename or "unknown", raw, f.content_type or "image/jpeg"))
            old_pdf_texts.append("[OCR pending]")
            logger.info("[%s] Queued old prescription image for background OCR: %s", file_id, f.filename)

    # --- Save immediately with placeholders for OCR fields ---
    payload = {
        **form_data,
        "extracted_prescription_photos": ["[OCR pending]"] * len(photo_jobs),
        "extracted_lab_reports": lab_texts,
        "extracted_old_prescriptions": old_pdf_texts,
        "ocr_status": "processing",
    }
    file_path = data_processor.save(file_id, payload)
    logger.info("[%s] Saved to %s — responding now, OCR running in background", file_id, file_path)

    # --- Schedule concurrent OCR as background task ---
    background_tasks.add_task(_run_ocr_background, file_id, photo_jobs, old_image_jobs)

    return {
        "status": "accepted",
        "file_id": file_id,
        "file_path": file_path,
        "message": "Form data saved. OCR is processing in the background and will update the file when complete.",
    }
