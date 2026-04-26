import json
import logging

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_PRESCRIPTION_PROMPT = """You are a clinical data extraction assistant.
Extract ALL medications from the following OCR text of a medical prescription.

Return ONLY a JSON object with this exact structure:
{
  "medications": [
    {
      "drug_name_generic": "string",
      "drug_name_brand": "string or null",
      "dose_mg": number or null,
      "dose_unit": "mg or as stated",
      "frequency": "once_daily|twice_daily|three_times_daily|as_needed|other",
      "timing": "string or null",
      "confidence": 0.0-1.0
    }
  ],
  "prescribing_doctor": "string or null",
  "hospital": "string or null",
  "prescription_date": "YYYY-MM-DD or null",
  "overall_confidence": 0.0-1.0
}

OCR TEXT:
"""

_LAB_REPORT_PROMPT = """You are a clinical data extraction assistant.
Extract ALL lab test results from the following OCR text of a medical lab report.

Return ONLY a JSON object with this exact structure:
{
  "tests": [
    {
      "test_name": "string",
      "test_category": "string or null",
      "value_numeric": number or null,
      "value_text": "string or null",
      "unit": "string or null",
      "reference_low": number or null,
      "reference_high": number or null,
      "is_flagged": true|false,
      "flag_direction": "high|low|critical_high|critical_low|null",
      "confidence": 0.0-1.0
    }
  ],
  "lab_name": "string or null",
  "report_date": "YYYY-MM-DD or null",
  "overall_confidence": 0.0-1.0
}

OCR TEXT:
"""

_DISCHARGE_PROMPT = """You are a clinical data extraction assistant.
Extract key clinical information from the following discharge summary text.

Return ONLY a JSON object with this exact structure:
{
  "diagnoses": [{"condition_name": "string", "icd_code": "string or null"}],
  "medications_at_discharge": [{"drug_name": "string", "dose": "string or null"}],
  "attending_doctor": "string or null",
  "hospital": "string or null",
  "admission_date": "YYYY-MM-DD or null",
  "discharge_date": "YYYY-MM-DD or null",
  "follow_up_instructions": "string or null",
  "overall_confidence": 0.0-1.0
}

OCR TEXT:
"""

_SUMMARY_PROMPT = """You are a senior physician writing a clinical briefing for a family caregiver.
Write a clear, plain-language health summary for the patient described below.
Cover: confirmed diagnoses, current medications with dosages and timing, notable lab results and what they mean,
open concerns the family should monitor, and the overall risk level.
Keep it under 300 words. Use simple language — no jargon.

Patient data:
"""

_PROMPTS = {
    "prescription": _PRESCRIPTION_PROMPT,
    "lab_report": _LAB_REPORT_PROMPT,
    "discharge_summary": _DISCHARGE_PROMPT,
}


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://care-circle-service.local",
        "X-Title": "CareCircle_Service",
    }


async def extract_structured_data(ocr_text: str, document_type: str) -> dict:
    """
    Send OCR text to OpenRouter and return structured extraction as a dict.
    Falls back to {"error": "..."} on failure.
    """
    prompt = _PROMPTS.get(document_type, _PRESCRIPTION_PROMPT)
    payload = {
        "model": settings.openrouter_model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "user", "content": prompt + ocr_text},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(_OPENROUTER_URL, json=payload, headers=_headers())
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            return json.loads(raw)
    except httpx.HTTPStatusError as exc:
        logger.error("LLM HTTP error %s: %s", exc.response.status_code, exc.response.text[:200])
        return {"error": f"HTTP {exc.response.status_code}"}
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        logger.error("LLM response parse error: %s", exc)
        return {"error": "parse_error"}
    except Exception as exc:
        logger.error("LLM unexpected error: %s", exc)
        return {"error": str(exc)}


async def generate_patient_summary(patient_snapshot: dict) -> str:
    """
    Generate a plain-language patient briefing from a structured snapshot.
    Returns summary text or an error placeholder.
    """
    snapshot_json = json.dumps(patient_snapshot, indent=2, default=str)
    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "user", "content": _SUMMARY_PROMPT + snapshot_json},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(_OPENROUTER_URL, json=payload, headers=_headers())
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.error("Summary generation failed: %s", exc)
        return f"[Summary generation failed: {exc}]"
