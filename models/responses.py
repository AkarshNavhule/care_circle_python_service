from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel


# ── Auth ──────────────────────────────────────────────────────────────────────

class SetRoleResponse(BaseModel):
    user_profile_id: str
    role: str
    next_step: str


# ── Onboarding ────────────────────────────────────────────────────────────────

class OnboardingAcceptedResponse(BaseModel):
    status: str = "processing"
    patient_id: str
    message: str = (
        "Patient record created. Documents are being processed in the background. "
        "Use Supabase Realtime to receive the completion event."
    )


# ── Document URL ──────────────────────────────────────────────────────────────

class SignedUrlResponse(BaseModel):
    document_id: str
    signed_url: str
    expires_in: int = 900
