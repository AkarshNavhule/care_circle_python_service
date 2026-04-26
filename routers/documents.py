import logging
from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from db.client import get_db
from middleware.auth import get_current_user
from models.responses import SignedUrlResponse
from services.storage import generate_signed_url

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/{document_id}/url", response_model=SignedUrlResponse)
async def get_document_url(
    document_id: str,
    current_user: dict = Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """
    Generate a 15-minute signed URL for a patient document.
    The requesting user must be linked to the patient via patient_guardians.
    """
    # Fetch document
    doc_resp = (
        db.table("documents")
        .select("id, patient_id, storage_path, is_deleted")
        .eq("id", document_id)
        .maybe_single()
        .execute()
    )
    if not doc_resp.data:
        raise HTTPException(status_code=404, detail="Document not found")

    doc = doc_resp.data
    if doc["is_deleted"]:
        raise HTTPException(status_code=410, detail="Document has been removed")

    patient_id = doc["patient_id"]

    # Verify the requesting user is linked to this patient
    link_resp = (
        db.table("patient_guardians")
        .select("id")
        .eq("patient_id", patient_id)
        .eq("user_id", current_user["id"])
        .eq("is_active", True)
        .maybe_single()
        .execute()
    )
    if not link_resp.data:
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this patient's documents",
        )

    signed_url = generate_signed_url(doc["storage_path"], expires_in=900)
    logger.info(
        "Signed URL generated for document %s by user %s", document_id, current_user["id"]
    )
    return SignedUrlResponse(document_id=document_id, signed_url=signed_url)
