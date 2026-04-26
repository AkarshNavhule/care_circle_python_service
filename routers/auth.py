import logging
from fastapi import APIRouter, Depends, Header, HTTPException
from supabase import Client

from db.client import get_db
from models.requests import SetRoleRequest
from models.responses import SetRoleResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/set-role", response_model=SetRoleResponse)
async def set_role(
    body: SetRoleRequest,
    authorization: str = Header(..., description="Bearer <supabase_jwt>"),
    db: Client = Depends(get_db),
):
    """
    Screen Zero: called immediately after Supabase Auth sign-up.
    Creates or updates the user_profiles row with role and display info.
    """
    # Verify JWT
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization.removeprefix("Bearer ").strip()

    try:
        auth_resp = db.auth.get_user(token)
        if not auth_resp or not auth_resp.user:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
    except Exception as exc:
        logger.warning("JWT verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc

    user_id = auth_resp.user.id

    profile_data = {
        "id": user_id,
        "full_name": body.full_name,
        "phone": body.phone,
        "email": body.email or auth_resp.user.email,
        "role": body.role,
        "agency_name": body.agency_name,
        "is_professional": body.role == "caregiver" and bool(body.agency_name),
    }

    # Upsert — handles both first-time and repeat calls
    result = db.table("user_profiles").upsert(profile_data).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create user profile")

    next_step_map = {
        "guardian": "patient_onboarding",
        "caregiver": "patient_onboarding",
        "patient": "self_onboarding",
    }

    logger.info("Role set for user %s: %s", user_id, body.role)
    return SetRoleResponse(
        user_profile_id=user_id,
        role=body.role,
        next_step=next_step_map.get(body.role, "patient_onboarding"),
    )
