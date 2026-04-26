"""
Patient summary generation.

Builds a structured snapshot of the patient's current state, sends it to the
LLM, and persists the result in patient_summaries with is_current = TRUE.
"""
from __future__ import annotations

import logging
from datetime import date

from supabase import Client

from services.llm import generate_patient_summary

logger = logging.getLogger(__name__)

LLM_MODEL_LABEL = "openrouter/gemini-2.0-flash"


async def generate_and_save(patient_id: str, db: Client, trigger_event: str = "onboarding") -> str:
    """
    Generate a fresh patient summary and persist it.
    Marks any previous summaries as is_current = FALSE.
    Returns the generated summary text.
    """
    snapshot = _build_snapshot(patient_id, db)
    summary_text = await generate_patient_summary(snapshot)

    # Mark previous summaries stale
    db.table("patient_summaries").update({"is_current": False}).eq(
        "patient_id", patient_id
    ).eq("is_current", True).execute()

    # Determine next version number
    latest = (
        db.table("patient_summaries")
        .select("version")
        .eq("patient_id", patient_id)
        .order("version", desc=True)
        .limit(1)
        .execute()
    ).data
    version = (latest[0]["version"] + 1) if latest else 1

    db.table("patient_summaries").insert({
        "patient_id": patient_id,
        "summary_text": summary_text,
        "snapshot_data": snapshot,
        "version": version,
        "generated_by_model": LLM_MODEL_LABEL,
        "trigger_event": trigger_event,
        "is_current": True,
    }).execute()

    logger.info("[%s] Patient summary v%d saved (%d chars)", patient_id, version, len(summary_text))
    return summary_text


def _compute_age(dob_str: str | None) -> int | None:
    if not dob_str:
        return None
    try:
        dob = date.fromisoformat(str(dob_str)[:10])
        today = date.today()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    except Exception:
        return None


def _build_snapshot(patient_id: str, db: Client) -> dict:
    """Collect confirmed Layer 3 data into a concise snapshot dict for the LLM."""
    patient = (
        db.table("patients").select("*").eq("id", patient_id).single().execute()
    ).data or {}

    medications = (
        db.table("medications")
        .select("drug_name_generic,drug_name_brand,dose_mg,dose_unit,frequency,timing,source,confirmed_by_guardian,is_current")
        .eq("patient_id", patient_id)
        .eq("is_current", True)
        .eq("is_deleted", False)
        .execute()
    ).data or []

    diagnoses = (
        db.table("diagnoses")
        .select("condition_name,confirmation_status,source,confirmed_by_guardian")
        .eq("patient_id", patient_id)
        .execute()
    ).data or []

    lab_results = (
        db.table("lab_results")
        .select("test_name,value_numeric,unit,is_flagged,flag_direction,report_date")
        .eq("patient_id", patient_id)
        .order("report_date", desc=True)
        .limit(20)
        .execute()
    ).data or []

    open_flags = (
        db.table("open_flags")
        .select("flag_type,severity,title,description")
        .eq("patient_id", patient_id)
        .eq("status", "open")
        .execute()
    ).data or []

    allergies = (
        db.table("allergies")
        .select("allergen,severity,reaction_type")
        .eq("patient_id", patient_id)
        .execute()
    ).data or []

    return {
        "patient": {
            "name": patient.get("full_name"),
            "age": _compute_age(patient.get("date_of_birth")),
            "gender": patient.get("gender"),
            "city": patient.get("city"),
            "weight_kg": patient.get("weight_kg"),
            "height_cm": patient.get("height_cm"),
            "medication_compliance": patient.get("medication_compliance"),
        },
        "diagnoses": diagnoses,
        "medications": medications,
        "recent_lab_results": lab_results,
        "allergies": allergies,
        "open_flags": open_flags,
    }
