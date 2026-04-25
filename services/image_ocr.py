import base64
import logging
import os

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
VISION_MODEL = "qwen/qwen2.5-vl-72b-instruct"

OCR_PROMPT = (
    "This is a doctor's prescription or medical document. "
    "Extract ALL text exactly as written. "
    "Include medicine names, dosages, frequencies, doctor name, date, and any instructions. "
    "Return plain text only."
)


async def extract_text_from_image(file_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return "[OCR failed: OPENROUTER_API_KEY not set]"

    b64 = base64.b64encode(file_bytes).decode("utf-8")
    data_url = f"data:{mime_type};base64,{b64}"

    payload = {
        "model": VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": OCR_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://care-circle-service.local",
        "X-Title": "Care_Circle_Service",
    }

    logger.info("Calling OpenRouter OCR model (%s), image size: %d bytes", VISION_MODEL, len(file_bytes))
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(OPENROUTER_API_URL, json=payload, headers=headers)
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
            logger.info("OpenRouter OCR returned %d chars", len(text))
            return text
    except httpx.HTTPStatusError as e:
        logger.error("OpenRouter HTTP error: %s", e.response.status_code)
        return f"[OCR failed: HTTP {e.response.status_code}]"
    except httpx.TimeoutException:
        logger.error("OpenRouter request timed out")
        return "[OCR failed: request timed out]"
    except (KeyError, IndexError):
        logger.error("OpenRouter unexpected response format")
        return "[OCR failed: unexpected response format]"
    except Exception as e:
        logger.error("OpenRouter unexpected error: %s", e)
        return f"[OCR failed: {e}]"
