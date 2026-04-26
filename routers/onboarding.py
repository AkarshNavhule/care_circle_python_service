"""
POST /api/onboarding/submit

Handles the full onboarding submission:
  1. Synchronous: create DB rows, upload files to R2, return immediately.
  2. Async background: extract documents, run flags, generate summary.
"""
from __future__ import annotations

import json
import logging
from typing import Annotated, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from supabase import Client

from db.client import get_db
from middleware.auth import get_current_user
from models.responses import OnboardingAcceptedResponse
from services import storage as storage_service
from services import flags as flags_service
from services import summary as summary_service
from services.extraction_pipeline import process_all_documents

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/submit", response_model=OnboardingAcceptedResponse)
async def submit_onboarding(
    background_tasks: BackgroundTasks,
    # ── Patient demographics ───────────────────────────────────────────────
    patient_json: str = Form(..., description="JSON string matching PatientDemographics model"),
    # ── Clinical data (JSON-encoded lists) ────────────────────────────────
    stated_conditions_json: str = Form(default="[]"),
    stated_medications_json: str = Form(default="[]"),
    allergies_json: str = Form(default="[]"),
    doctors_json: str = Form(default="[]"),
    compliance_json: str = Form(default="{}"),
    # ── File metadata (JSON list parallel to files list) ──────────────────
    files_metadata_json: str = Form(default="[]"),
    # ── Uploaded files ─────────────────────────────────────────────────────
    files: List[UploadFile] = File(default=[]),
    # ── Auth & DB ──────────────────────────────────────────────────────────
    current_user: dict = Depends(get_current_user),
    db: Client = Depends(get_db),
):
    guardian_id = current_user["id"]
    logger.info("[onboarding] Submit started by guardian_id=%s", guardian_id)

    # ── Parse JSON form fields ────────────────────────────────────────────
    logger.debug("[onboarding] Parsing form fields")
    try:
        patient_data = json.loads(patient_json)
        stated_conditions = json.loads(stated_conditions_json)
        stated_medications = json.loads(stated_medications_json)
        allergies = json.loads(allergies_json)
        doctors = json.loads(doctors_json)
        compliance = json.loads(compliance_json)
        files_metadata = json.loads(files_metadata_json)
    except json.JSONDecodeError as exc:
        logger.error("[onboarding] JSON parse error: %s", exc)
        raise HTTPException(status_code=422, detail=f"Invalid JSON in form field: {exc}") from exc

    logger.info(
        "[onboarding] Parsed: conditions=%d, medications=%d, allergies=%d, doctors=%d, files=%d",
        len(stated_conditions), len(stated_medications),
        len(allergies), len(doctors), len(files),
    )

    # ── Step 1: Create patient ────────────────────────────────────────────
    logger.info("[onboarding] Step 1: Creating patient record for '%s'", patient_data.get("full_name"))
    patient_row = {
        "full_name": patient_data["full_name"],
        "date_of_birth": patient_data["date_of_birth"],
        "gender": patient_data.get("gender"),
        "city": patient_data["city"],
        "state": patient_data.get("state"),
        "primary_language": patient_data.get("primary_language", "hindi"),
        "weight_kg": patient_data.get("weight_kg"),
        "height_cm": patient_data.get("height_cm"),
        "onboarding_status": "in_progress",
        "medication_compliance": compliance.get("medication_compliance", "unknown"),
    }
    patient_resp = db.table("patients").insert(patient_row).execute()
    if not patient_resp.data:
        logger.error("[onboarding] Failed to insert patient row")
        raise HTTPException(status_code=500, detail="Failed to create patient record")
    patient_id = patient_resp.data[0]["id"]
    logger.info("[onboarding] Step 1 done: patient_id=%s", patient_id)

    # ── Step 2: Link guardian to patient ──────────────────────────────────
    logger.info("[onboarding] Step 2: Linking guardian_id=%s to patient_id=%s", guardian_id, patient_id)
    db.table("patient_guardians").insert({
        "patient_id": patient_id,
        "user_id": guardian_id,
        "role": "primary_guardian",
        "relationship": "guardian",
        "can_edit_profile": True,
        "can_confirm_flags": True,
        "can_upload_docs": True,
        "can_submit_checkins": True,
        "receives_alerts": True,
        "alert_severity_min": "low",
    }).execute()
    logger.info("[onboarding] Step 2 done: guardian linked")

    # ── Step 3: Caregiver (if provided) ───────────────────────────────────
    # Caregiver cannot be inserted into user_profiles until they sign up via Supabase Auth.
    # Skipped here — caregiver invite flow handles linking after they register.
    if compliance.get("has_caregiver"):
        logger.info(
            "[onboarding] Step 3: Caregiver '%s' noted but skipped (pending signup)",
            compliance.get("caregiver_name"),
        )
    else:
        logger.info("[onboarding] Step 3: No caregiver provided")

    # ── Step 4: Stated diagnoses ──────────────────────────────────────────
    logger.info("[onboarding] Step 4: Inserting %d stated condition(s)", len(stated_conditions))
    if stated_conditions:
        db.table("diagnoses").insert([
            {
                "patient_id": patient_id,
                "condition_name": c["condition_name"],
                "confirmation_status": "confirmed",
                "confirmed_by_guardian": True,
                "source": "guardian_stated",
            }
            for c in stated_conditions
        ]).execute()

    # ── Step 5: Stated medications ────────────────────────────────────────
    logger.info("[onboarding] Step 5: Inserting %d stated medication(s)", len(stated_medications))
    if stated_medications:
        db.table("medications").insert([
            {
                "patient_id": patient_id,
                "drug_name_generic": m["drug_name"],
                "drug_name_original_ocr": m["drug_name"],
                "dose_mg": m.get("dose_mg"),
                "frequency": m.get("frequency"),
                "timing": m.get("timing"),
                "is_otc": m.get("is_otc", False),
                "is_supplement": m.get("is_supplement", False),
                "source": "guardian_stated",
                "confirmed_by_guardian": True,
            }
            for m in stated_medications
        ]).execute()

    # ── Step 6: Allergies ─────────────────────────────────────────────────
    logger.info("[onboarding] Step 6: Inserting %d allergy/allergies", len(allergies))
    if allergies:
        db.table("allergies").insert([
            {
                "patient_id": patient_id,
                "allergen": a["allergen"],
                "severity": a.get("severity", "unknown"),
                "reaction_type": a.get("reaction_type"),
                "source": "guardian_stated",
            }
            for a in allergies
        ]).execute()

    # ── Step 7: Doctors ───────────────────────────────────────────────────
    logger.info("[onboarding] Step 7: Inserting %d doctor(s)", len(doctors))
    if doctors:
        db.table("doctors").insert([
            {
                "patient_id": patient_id,
                "full_name": d["full_name"],
                "specialty": d.get("specialty"),
                "hospital_name": d.get("hospital_name"),
                "city": d.get("city"),
                "phone": d.get("phone"),
                "email": d.get("email"),
                "is_primary_physician": d.get("is_primary_physician", False),
                "source": "guardian_stated",
            }
            for d in doctors
        ]).execute()

    # ── Steps 8–9: Upload files to R2, create documents rows ─────────────
    logger.info("[onboarding] Steps 8-9: Uploading %d file(s) to R2", len(files))
    document_ids: list[str] = []
    for i, upload in enumerate(files):
        file_bytes = await upload.read()
        meta = files_metadata[i] if i < len(files_metadata) else {}
        doc_type = meta.get("document_type", "other")
        doc_date = meta.get("document_date")
        content_type = upload.content_type or "application/octet-stream"

        logger.info(
            "[onboarding] Uploading file %d/%d: name=%s, type=%s, size=%d bytes",
            i + 1, len(files), upload.filename, content_type, len(file_bytes),
        )
        storage_path = storage_service.upload_file(
            patient_id=patient_id,
            document_type=doc_type,
            filename=upload.filename or f"file_{i}",
            file_bytes=file_bytes,
            content_type=content_type,
        )
        logger.info("[onboarding] File %d uploaded to R2: path=%s", i + 1, storage_path)

        doc_resp = db.table("documents").insert({
            "patient_id": patient_id,
            "uploaded_by": guardian_id,
            "storage_path": storage_path,
            "original_filename": upload.filename or f"file_{i}",
            "file_type": content_type if content_type in (
                "image/jpeg", "image/png", "application/pdf"
            ) else "application/pdf",
            "file_size_bytes": len(file_bytes),
            "document_type": doc_type,
            "document_date": doc_date,
            "extraction_status": "pending",
        }).execute()

        if doc_resp.data:
            doc_id = doc_resp.data[0]["id"]
            document_ids.append(doc_id)
            logger.info("[onboarding] Document row created: id=%s, type=%s", doc_id, doc_type)

    logger.info(
        "[onboarding] Sync complete: patient_id=%s, %d doc(s) queued for background extraction",
        patient_id, len(document_ids),
    )

    # ── Step 10: Queue async background pipeline ──────────────────────────
    background_tasks.add_task(
        _run_background_pipeline, patient_id, document_ids, db
    )

    return OnboardingAcceptedResponse(patient_id=patient_id)


async def _run_background_pipeline(patient_id: str, document_ids: list[str], db: Client):
    """Steps 11–16: extraction → flags → summary → status update."""
    try:
        if document_ids:
            await process_all_documents(document_ids, patient_id, db)

        open_flags = await flags_service.detect_and_create_flags(patient_id, db)
        await summary_service.generate_and_save(patient_id, db, trigger_event="onboarding")

        critical_count = sum(1 for f in open_flags if f.get("severity") == "critical")
        status = "complete" if critical_count == 0 else "clarification_needed"

        db.table("patients").update({
            "onboarding_status": status,
            "completeness_score": _compute_completeness(patient_id, db),
        }).eq("id", patient_id).execute()

        logger.info("Background pipeline done for patient %s → status=%s", patient_id, status)
    except Exception as exc:
        logger.exception("Background pipeline failed for patient %s: %s", patient_id, exc)
        db.table("patients").update({"onboarding_status": "clarification_needed"}).eq(
            "id", patient_id
        ).execute()


def _compute_completeness(patient_id: str, db: Client) -> int:
    """
    Simple completeness score based on filled Layer 3 tables.
    0–100 returned as integer.
    """
    score = 0
    if db.table("medications").select("id").eq("patient_id", patient_id).limit(1).execute().data:
        score += 25
    if db.table("diagnoses").select("id").eq("patient_id", patient_id).limit(1).execute().data:
        score += 25
    if db.table("lab_results").select("id").eq("patient_id", patient_id).limit(1).execute().data:
        score += 25
    if db.table("doctors").select("id").eq("patient_id", patient_id).limit(1).execute().data:
        score += 25
    return score


def _placeholder_uuid() -> str:
    import uuid
    return str(uuid.uuid4())
