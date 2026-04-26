# Care Circle Service

A FastAPI backend for patient onboarding, medical document extraction, and health monitoring. Guardians submit patient demographics and upload medical documents (prescriptions, lab reports, discharge summaries). The service extracts structured clinical data using Google Vision (images) and pdfplumber (PDFs), then runs an LLM over the text to produce structured JSON saved to the database.

---

## Features

- **Auth** — Supabase JWT verification; role assignment (guardian / caregiver / patient)
- **Onboarding** — Patient demographics, stated conditions, medications, allergies, doctors, file uploads
- **Document extraction pipeline** (background):
  - Images (jpeg, png, gif, bmp, webp, tiff) → Google Vision OCR → LLM structured extraction
  - PDFs → pdfplumber text extraction → LLM structured extraction
  - Confidence-gated auto-accept / low-flag / needs-review per field
- **Layer 3 DB writes** — Extracted data merged into `medications`, `lab_results`, `diagnoses` tables
- **Flags & summary** — Automated flag detection and plain-language patient summary generation
- **Storage** — Files uploaded to Cloudflare R2

---

## Project Structure

```
Care_Circle_Service/
├── main.py                        # App entry point, router registration
├── config/
│   └── settings.py                # Pydantic settings (reads .env)
├── db/
│   └── client.py                  # Supabase client
├── middleware/
│   └── auth.py                    # JWT verification + user profile fetch
├── models/
│   ├── requests.py                # Request body models
│   └── responses.py               # Response models
├── routers/
│   ├── auth.py                    # POST /api/auth/set-role
│   ├── onboarding.py              # POST /api/onboarding/submit
│   └── documents.py               # Document-related endpoints
├── services/
│   ├── ocr.py                     # Google Vision OCR (images only)
│   ├── pdf_extractor.py           # pdfplumber PDF text extraction
│   ├── extraction_pipeline.py     # Full doc pipeline: route → extract → DB write
│   ├── llm.py                     # OpenRouter LLM calls (extraction + summary)
│   ├── storage.py                 # Cloudflare R2 upload/download
│   ├── flags.py                   # Clinical flag detection
│   └── summary.py                 # Patient summary generation
├── .env                           # Your secrets (git-ignored)
├── .env.example                   # Template
├── requirements.txt
└── README.md
```

---

## Setup

### 1. Clone & enter the directory

```bash
git clone <repository_url>
cd Care_Circle_Service
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

Edit `.env` with your credentials:

```env
# Supabase
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_KEY=<service-role-key>

# Cloudflare R2
R2_ACCOUNT_ID=<account-id>
R2_ACCESS_KEY_ID=<access-key>
R2_SECRET_ACCESS_KEY=<secret-key>
R2_BUCKET_NAME=patient-documents

# Google Vision (path to service account JSON)
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

# OpenRouter
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=google/gemini-2.0-flash-001
```

### 5. Run the server

```bash
uvicorn main:app --reload --port 8000
```

---

## API

### `POST /api/auth/set-role`

Called once after Supabase sign-up. Creates the `user_profiles` row.

**Headers:** `Authorization: Bearer <supabase_jwt>`

**Body (JSON):**

| Field | Required | Description |
|---|---|---|
| `role` | yes | `guardian` / `caregiver` / `patient` |
| `full_name` | yes | Display name |
| `phone` | yes | Phone number |
| `email` | no | Defaults to Supabase auth email |
| `agency_name` | no | For professional caregivers |

**Response:**
```json
{
  "user_profile_id": "uuid",
  "role": "guardian",
  "next_step": "patient_onboarding"
}
```

---

### `POST /api/onboarding/submit`

Submits full patient onboarding. Returns immediately; document extraction runs in the background.

**Headers:** `Authorization: Bearer <supabase_jwt>`

**Content-Type:** `multipart/form-data`

| Field | Type | Required | Description |
|---|---|---|---|
| `patient_json` | JSON string | yes | Patient demographics (see below) |
| `stated_conditions_json` | JSON string | no | `[{"condition_name": "..."}]` |
| `stated_medications_json` | JSON string | no | `[{"drug_name": "...", "dose_mg": 500, ...}]` |
| `allergies_json` | JSON string | no | `[{"allergen": "...", "severity": "..."}]` |
| `doctors_json` | JSON string | no | `[{"full_name": "...", "specialty": "..."}]` |
| `compliance_json` | JSON string | no | Medication compliance and caregiver info |
| `files_metadata_json` | JSON string | no | `[{"document_type": "prescription", "document_date": "YYYY-MM-DD"}]` |
| `files` | file(s) | no | Images (jpeg/png/etc.) or PDFs |

**`patient_json` fields:**

| Field | Required |
|---|---|
| `full_name` | yes |
| `date_of_birth` | yes (`YYYY-MM-DD`) |
| `city` | yes |
| `gender` | no |
| `state` | no |
| `primary_language` | no (default: `hindi`) |
| `weight_kg` | no |
| `height_cm` | no |

**Response:**
```json
{ "patient_id": "uuid" }
```

**Example curl:**
```bash
curl -X POST "http://localhost:8000/api/onboarding/submit" \
  -H "Authorization: Bearer <jwt>" \
  -F 'patient_json={"full_name":"Ramesh Kumar","date_of_birth":"1950-06-15","city":"Mumbai"}' \
  -F 'stated_conditions_json=[{"condition_name":"Diabetes Type 2"}]' \
  -F 'stated_medications_json=[{"drug_name":"Metformin","dose_mg":500,"frequency":"twice_daily"}]' \
  -F 'allergies_json=[]' \
  -F 'doctors_json=[]' \
  -F 'compliance_json={"medication_compliance":"good"}' \
  -F 'files_metadata_json=[{"document_type":"prescription","document_date":"2026-04-01"}]' \
  -F 'files=@/path/to/prescription.jpg'
```

---

## Document Extraction Pipeline

Files are processed in the background after `/api/onboarding/submit` returns.

```
Image file  →  Google Vision OCR  →  LLM extraction  →  DB (medications / lab_results / diagnoses)
PDF file    →  pdfplumber          →  LLM extraction  →  DB
```

**Confidence thresholds:**

| Confidence | Action |
|---|---|
| ≥ 0.90 | Auto-accept |
| 0.75 – 0.89 | Accept + low-confidence flag |
| 0.60 – 0.74 | Clarification question created |
| < 0.60 | `needs_review` — re-upload recommended |

`documents.extraction_status` is updated at each stage: `pending` → `processing` → `completed` / `needs_review` / `failed`.

---

## Authentication Flow

1. Sign up via Supabase Auth (email + password)
2. Sign in to get a JWT: `POST https://<project>.supabase.co/auth/v1/token?grant_type=password`
3. Call `POST /api/auth/set-role` with the JWT to create your profile
4. Use the same JWT as `Authorization: Bearer <token>` on all subsequent requests

---

## Interactive Docs

Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)

ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)
