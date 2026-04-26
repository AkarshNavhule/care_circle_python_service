from __future__ import annotations
from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ── Auth ──────────────────────────────────────────────────────────────────────

class SetRoleRequest(BaseModel):
    role: Literal["guardian", "caregiver", "patient"]
    full_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    relationship: Optional[str] = None  # required when role = guardian or caregiver
    agency_name: Optional[str] = None   # required when role = caregiver


# ── Onboarding sub-models ─────────────────────────────────────────────────────

class PatientDemographics(BaseModel):
    full_name: str
    date_of_birth: date
    gender: Literal["male", "female", "other"]
    city: str
    state: Optional[str] = None
    primary_language: str = "hindi"
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None


class StatedCondition(BaseModel):
    condition_name: str
    managing_doctor: Optional[str] = None


class StatedMedication(BaseModel):
    drug_name: str
    dose_mg: Optional[float] = None
    frequency: Optional[Literal[
        "once_daily", "twice_daily", "three_times_daily", "as_needed", "other"
    ]] = None
    timing: Optional[str] = None
    is_otc: bool = False
    is_supplement: bool = False


class StatedAllergy(BaseModel):
    allergen: str
    severity: Optional[Literal["mild", "moderate", "severe", "unknown"]] = "unknown"
    reaction_type: Optional[str] = None


class DoctorInput(BaseModel):
    full_name: str
    specialty: Optional[str] = None
    hospital_name: Optional[str] = None
    city: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    is_primary_physician: bool = False


class ComplianceInfo(BaseModel):
    medication_compliance: Literal[
        "consistent", "sometimes_forgets", "often_forgets", "unknown"
    ] = "unknown"
    has_caregiver: bool = False
    caregiver_name: Optional[str] = None
    caregiver_visit_frequency: Optional[str] = None
    caregiver_phone: Optional[str] = None
    guardian_primary_concern: Optional[str] = None


class FileMetadata(BaseModel):
    """JSON-encoded metadata sent alongside each file in multipart."""
    document_type: Literal["prescription", "lab_report", "discharge_summary", "other"]
    document_date: Optional[date] = None
