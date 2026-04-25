import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def save(file_id: str, payload: dict) -> str:
    path = DATA_DIR / f"care_intake_{file_id}.txt"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def update_ocr(file_id: str, key: str, texts) -> None:
    path = DATA_DIR / f"care_intake_{file_id}.txt"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[key] = texts
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
