import logging
from fastapi import Depends, HTTPException, Header
from db.client import get_db
from supabase import Client

logger = logging.getLogger(__name__)


async def get_current_user(
    authorization: str = Header(..., description="Bearer <supabase_jwt>"),
    db: Client = Depends(get_db),
) -> dict:
    """
    Verify the Supabase JWT and return the user's profile.
    Raises HTTP 401 if the token is missing, invalid, or expired.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header format")

    token = authorization.removeprefix("Bearer ").strip()

    logger.debug("[auth] Verifying JWT token (first 20 chars): %s...", token[:20])
    try:
        auth_response = db.auth.get_user(token)
        if auth_response is None or auth_response.user is None:
            logger.warning("[auth] JWT verification returned no user")
            raise HTTPException(status_code=401, detail="Invalid or expired token")
    except Exception as exc:
        logger.warning("[auth] JWT verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc

    user_id = auth_response.user.id
    logger.info("[auth] JWT verified, user_id=%s", user_id)

    logger.debug("[auth] Fetching user_profiles row for user_id=%s", user_id)
    profile_response = (
        db.table("user_profiles").select("*").eq("id", user_id).maybe_single().execute()
    )

    profile_data = profile_response.data if profile_response is not None else None
    if not profile_data:
        logger.warning("[auth] No user_profiles row found for user_id=%s", user_id)
        raise HTTPException(
            status_code=403,
            detail="User profile not found. Call POST /api/auth/set-role first.",
        )

    logger.info("[auth] User profile loaded: user_id=%s, role=%s", user_id, profile_data.get("role"))
    return profile_data
