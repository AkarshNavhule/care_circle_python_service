"""
Flag detection engine.

Runs after all documents have been extracted and Layer 3 tables are populated.
Inserts rows into open_flags for every issue found.
"""
from __future__ import annotations

import logging
from datetime import date

from supabase import Client

logger = logging.getLogger(__name__)

# Months after which a prescription is considered currency-uncertain
PRESCRIPTION_STALE_DAYS = 180

# Common Indian formulary interaction pairs (drug_a, drug_b, description)
# Expand this list as the product matures.
KNOWN_INTERACTIONS: list[tuple[str, str, str]] = [
    ("metformin", "contrast", "Metformin must be held before iodinated contrast — risk of lactic acidosis"),
    ("warfarin", "aspirin", "Warfarin + Aspirin increases bleeding risk significantly"),
    ("warfarin", "ibuprofen", "Warfarin + NSAIDs (Ibuprofen) increases bleeding risk"),
    ("warfarin", "diclofenac", "Warfarin + NSAIDs (Diclofenac) increases bleeding risk"),
    ("methotrexate", "aspirin", "Aspirin reduces methotrexate clearance — toxicity risk"),
    ("digoxin", "amiodarone", "Amiodarone increases digoxin levels — toxicity risk"),
    ("simvastatin", "amlodipine", "High-dose simvastatin + amlodipine raises myopathy risk"),
    ("lithium", "ibuprofen", "NSAIDs reduce lithium clearance — toxicity risk"),
    ("clopidogrel", "omeprazole", "Omeprazole reduces clopidogrel antiplatelet effect"),
]


async def detect_and_create_flags(patient_id: str, db: Client) -> list[dict]:
    """
    Run all checks and insert open_flags rows. Returns list of created flag dicts.
    """
    flags: list[dict] = []

    flags += _check_ocr_low_confidence(patient_id, db)
    flags += _check_unconfirmed_medications(patient_id, db)
    flags += _check_lab_anomalies(patient_id, db)
    flags += _check_stale_reports(patient_id, db)
    flags += _check_drug_interactions(patient_id, db)
    flags += _check_stated_vs_extracted_conflicts(patient_id, db)

    if flags:
        db.table("open_flags").insert(flags).execute()
        logger.info("[%s] Created %d open flag(s)", patient_id, len(flags))

    return flags


# ── Individual checks ─────────────────────────────────────────────────────────

def _check_ocr_low_confidence(patient_id: str, db: Client) -> list[dict]:
    """Flag any document where OCR confidence < 0.75."""
    resp = (
        db.table("document_extractions")
        .select("id, document_id, ocr_confidence")
        .lt("ocr_confidence", 0.75)
        .execute()
    )
    flags = []
    for row in resp.data or []:
        conf = row["ocr_confidence"]
        severity = "high" if conf < 0.60 else "medium"
        flags.append(_flag(
            patient_id=patient_id,
            flag_type="ocr_low_confidence",
            severity=severity,
            title="Low OCR confidence on uploaded document",
            description=(
                f"OCR read this document with {conf:.0%} confidence. "
                "Please review the extracted data carefully or re-upload a clearer scan."
            ),
            linked_document_id=row["document_id"],
        ))
    return flags


def _check_unconfirmed_medications(patient_id: str, db: Client) -> list[dict]:
    """Flag extracted medications not yet confirmed by guardian."""
    resp = (
        db.table("medications")
        .select("id, drug_name_generic, extraction_confidence")
        .eq("patient_id", patient_id)
        .eq("source", "document_extracted")
        .eq("confirmed_by_guardian", False)
        .eq("is_deleted", False)
        .execute()
    )
    flags = []
    for med in resp.data or []:
        conf = med.get("extraction_confidence") or 0.0
        if conf < 0.85:
            flags.append(_flag(
                patient_id=patient_id,
                flag_type="ocr_low_confidence",
                severity="high",
                title=f"Unconfirmed medication: {med['drug_name_generic']}",
                description=(
                    f"We read '{med['drug_name_generic']}' from a document "
                    f"(confidence {conf:.0%}). Please confirm this is correct."
                ),
                linked_medication_id=med["id"],
            ))
    return flags


def _check_lab_anomalies(patient_id: str, db: Client) -> list[dict]:
    """Flag all lab results marked as out-of-range."""
    resp = (
        db.table("lab_results")
        .select("id, test_name, value_numeric, unit, flag_direction")
        .eq("patient_id", patient_id)
        .eq("is_flagged", True)
        .eq("flag_acknowledged", False)
        .execute()
    )
    flags = []
    for result in resp.data or []:
        direction = result.get("flag_direction", "high")
        severity = "critical" if "critical" in (direction or "") else "high"
        value_str = (
            f"{result['value_numeric']} {result['unit'] or ''}".strip()
            if result.get("value_numeric") else "see report"
        )
        flags.append(_flag(
            patient_id=patient_id,
            flag_type="lab_anomaly",
            severity=severity,
            title=f"Abnormal lab value: {result['test_name']}",
            description=(
                f"{result['test_name']} is {direction.replace('_', ' ')} at {value_str}. "
                "Please discuss with the patient's doctor."
            ),
            linked_lab_result_id=result["id"],
        ))
    return flags


def _check_stale_reports(patient_id: str, db: Client) -> list[dict]:
    """Flag lab results older than 6 months."""
    resp = (
        db.table("lab_results")
        .select("id, test_name, report_date")
        .eq("patient_id", patient_id)
        .eq("is_stale", True)
        .execute()
    )
    flags = []
    seen_docs: set[str] = set()
    for result in resp.data or []:
        doc_id = result.get("id", "")
        if doc_id in seen_docs:
            continue
        seen_docs.add(doc_id)
        flags.append(_flag(
            patient_id=patient_id,
            flag_type="stale_report",
            severity="low",
            title="Lab report older than 6 months",
            description=(
                f"The {result['test_name']} result is from {result['report_date']} "
                "which is over 6 months ago. Consider requesting a fresh test."
            ),
            linked_lab_result_id=result["id"],
        ))
    return flags


def _check_drug_interactions(patient_id: str, db: Client) -> list[dict]:
    """Check medication list against known interaction pairs."""
    resp = (
        db.table("medications")
        .select("id, drug_name_generic, drug_name_brand")
        .eq("patient_id", patient_id)
        .eq("is_current", True)
        .eq("is_deleted", False)
        .execute()
    )
    meds = resp.data or []
    # Build a set of normalised drug names for quick lookup
    med_names = {
        (m.get("drug_name_generic") or "").lower().strip()
        for m in meds
    }
    med_names |= {
        (m.get("drug_name_brand") or "").lower().strip()
        for m in meds
    }
    med_names.discard("")

    med_id_map: dict[str, str] = {}
    for m in meds:
        generic = (m.get("drug_name_generic") or "").lower().strip()
        if generic:
            med_id_map[generic] = m["id"]

    flags = []
    for drug_a, drug_b, description in KNOWN_INTERACTIONS:
        a_present = any(drug_a in name for name in med_names)
        b_present = any(drug_b in name for name in med_names)
        if a_present and b_present:
            linked_a = next(
                (mid for name, mid in med_id_map.items() if drug_a in name), None
            )
            flags.append(_flag(
                patient_id=patient_id,
                flag_type="drug_interaction",
                severity="critical",
                title=f"Drug interaction: {drug_a.title()} + {drug_b.title()}",
                description=description,
                linked_medication_id=linked_a,
            ))
    return flags


def _check_stated_vs_extracted_conflicts(patient_id: str, db: Client) -> list[dict]:
    """
    Flag medications stated by guardian that do not appear in any document extraction.
    Indicates the document may be missing or the guardian may have mis-stated the drug.
    """
    stated = (
        db.table("medications")
        .select("id, drug_name_generic")
        .eq("patient_id", patient_id)
        .eq("source", "guardian_stated")
        .eq("is_deleted", False)
        .execute()
    ).data or []

    extracted_names = {
        row["drug_name_generic"].lower().strip()
        for row in (
            db.table("medications")
            .select("drug_name_generic")
            .eq("patient_id", patient_id)
            .eq("source", "document_extracted")
            .execute()
        ).data or []
    }

    flags = []
    for med in stated:
        name = (med.get("drug_name_generic") or "").lower().strip()
        if name and not any(name in ex or ex in name for ex in extracted_names):
            flags.append(_flag(
                patient_id=patient_id,
                flag_type="conflict_unresolved",
                severity="medium",
                title=f"Medication not found in documents: {med['drug_name_generic']}",
                description=(
                    f"You mentioned {med['drug_name_generic']} but we could not find it "
                    "in any uploaded document. Please upload a prescription or confirm."
                ),
                linked_medication_id=med["id"],
            ))
    return flags


# ── Factory ───────────────────────────────────────────────────────────────────

def _flag(
    *,
    patient_id: str,
    flag_type: str,
    severity: str,
    title: str,
    description: str,
    linked_document_id: str | None = None,
    linked_medication_id: str | None = None,
    linked_lab_result_id: str | None = None,
) -> dict:
    return {
        "patient_id": patient_id,
        "flag_type": flag_type,
        "severity": severity,
        "title": title,
        "description": description,
        "linked_document_id": linked_document_id,
        "linked_medication_id": linked_medication_id,
        "linked_lab_result_id": linked_lab_result_id,
        "status": "open",
    }
