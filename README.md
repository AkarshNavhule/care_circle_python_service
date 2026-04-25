# Care Circle Service

A FastAPI service that accepts patient intake forms via multipart/form-data. It extracts text from uploaded PDFs and runs OCR on prescription images using the Qwen2.5-VL vision model via OpenRouter. All data is saved locally as a structured JSON file.

---

## Features

- Accepts 17 patient intake fields (demographics, medical history, caregiver info)
- Extracts text from lab report PDFs using `pdfplumber` + `PyMuPDF` fallback
- OCR on prescription images via [OpenRouter](https://openrouter.ai) (`qwen/qwen2.5-vl-72b-instruct`)
- Returns instantly — OCR runs concurrently in the background
- Saves output to `data/care_intake_<id>.txt` as a JSON file

---

## Project Structure

```
Care_Circle_Service/
├── main.py                  # App entry point
├── routers/
│   └── intake.py            # POST /api/v1/intake
├── services/
│   ├── pdf_extractor.py     # PDF text extraction
│   ├── image_ocr.py         # OpenRouter vision OCR
│   └── data_processor.py    # Save & update intake files
├── data/                    # Generated intake .txt files (git-ignored)
├── .env                     # Your API key (git-ignored)
├── .env.example             # Template
├── requirements.txt
└── README.md
```

---

## Setup

### 1. Clone & enter the directory

```bash
git clone -b <branch_name> <repository_url>cd Care_Circle_Service
```

### 2. Create virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set your OpenRouter API key:

```
OPENROUTER_API_KEY=sk-or-...
```

### 5. Run the server

```bash
uvicorn main:app --reload --port 8000
```

---

## API

### `POST /api/v1/intake`

**Content-Type:** `multipart/form-data`

#### Text fields

| Field | Description |
|---|---|
| `full_name` | Patient full name |
| `age_or_dob` | Age or date of birth |
| `gender` | Gender |
| `city` | City / location |
| `height_weight` | Height and weight |
| `primary_language` | Primary language |
| `diagnosed_conditions` | Current diagnosed conditions |
| `current_medications` | Medications (name, dose, frequency, timing) |
| `known_allergies` | Known allergies & adverse reactions |
| `otc_meds_supplements` | OTC meds & supplements |
| `recent_doctor_visits` | Recent doctor visits |
| `doctor_contact_info` | Doctor name, hospital, specialty |
| `medication_consistency` | Does patient take meds consistently or skip? |
| `caregiver_info` | Caregiver presence and visit frequency |
| `typical_day` | Meals, sleep, activity description |
| `main_concern` | What the patient/family is most worried about |
| `recent_hospitalizations` | Recent hospitalizations not already mentioned |

#### File fields

| Field | Type | Description |
|---|---|---|
| `prescription_photos` | image (jpg/png) | Doctor prescription photos — OCR'd |
| `lab_reports` | PDF | Lab reports — text extracted |
| `old_prescriptions` | PDF or image | Old prescriptions — PDF extracted or OCR'd |

#### Response

```json
{
  "status": "accepted",
  "file_id": "abc123...",
  "file_path": "...\\data\\care_intake_abc123....txt",
  "message": "Form data saved. OCR is processing in the background and will update the file when complete."
}
```

The response is returned immediately. OCR results are written to the file in the background. Check `ocr_status` in the saved file:
- `"processing"` — OCR still running
- `"done"` — all OCR complete

---

## Testing

Open Swagger UI at [http://localhost:8000/docs](http://localhost:8000/docs) to test the endpoint interactively.
