-- ============================================================
-- CareCircle Service — Full Database Schema v2.0
-- Run this in the Supabase SQL editor (once).
-- Supabase Auth creates auth.users automatically.
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- TABLE 1: user_profiles
-- Extends Supabase auth.users with app-level profile data.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_profiles (
  id              UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  full_name       TEXT NOT NULL,
  phone           TEXT,
  email           TEXT,
  role            TEXT DEFAULT 'guardian'
                  CHECK (role IN ('guardian','caregiver','patient','admin')),
  agency_name     TEXT,
  is_professional BOOLEAN DEFAULT FALSE,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────
-- TABLE 2: patients
-- One row per patient. Everything else links here.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS patients (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  full_name               TEXT NOT NULL,
  date_of_birth           DATE NOT NULL,
  -- age_years is computed at query time via: DATE_PART('year', AGE(date_of_birth))
  -- Cannot use GENERATED ALWAYS AS because AGE() is not immutable in PostgreSQL.
  gender                  TEXT CHECK (gender IN ('male','female','other')),
  city                    TEXT NOT NULL,
  state                   TEXT,
  primary_language        TEXT DEFAULT 'hindi',
  weight_kg               NUMERIC(5,2),
  height_cm               NUMERIC(5,2),
  onboarding_status       TEXT DEFAULT 'pending'
                          CHECK (onboarding_status IN
                          ('pending','in_progress','clarification_needed','complete')),
  onboarding_completed_at TIMESTAMPTZ,
  completeness_score      INT DEFAULT 0
                          CHECK (completeness_score BETWEEN 0 AND 100),
  medication_compliance   TEXT
                          CHECK (medication_compliance IN
                          ('consistent','sometimes_forgets','often_forgets','unknown')),
  is_deleted              BOOLEAN DEFAULT FALSE,
  created_at              TIMESTAMPTZ DEFAULT NOW(),
  updated_at              TIMESTAMPTZ DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────
-- TABLE 3: patient_guardians
-- Links users to patients with per-relationship permissions.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS patient_guardians (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id          UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
  user_id             UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
  role                TEXT NOT NULL
                      CHECK (role IN ('primary_guardian','secondary_guardian',
                             'caregiver','patient')),
  relationship        TEXT,
  can_edit_profile    BOOLEAN DEFAULT FALSE,
  can_confirm_flags   BOOLEAN DEFAULT FALSE,
  can_upload_docs     BOOLEAN DEFAULT TRUE,
  can_submit_checkins BOOLEAN DEFAULT TRUE,
  receives_alerts     BOOLEAN DEFAULT TRUE,
  alert_severity_min  TEXT DEFAULT 'low'
                      CHECK (alert_severity_min IN ('low','medium','high','critical')),
  visit_frequency     TEXT,
  visit_days          TEXT[],
  is_active           BOOLEAN DEFAULT TRUE,
  created_at          TIMESTAMPTZ DEFAULT NOW(),
  updated_at          TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (patient_id, user_id)
);

-- ────────────────────────────────────────────────────────────
-- TABLE 4: documents  (Layer 1)
-- File references only — never BLOBs. Files live in R2.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id        UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
  uploaded_by       UUID NOT NULL REFERENCES user_profiles(id),
  storage_bucket    TEXT DEFAULT 'patient-documents',
  storage_path      TEXT NOT NULL,
  original_filename TEXT NOT NULL,
  file_type         TEXT NOT NULL
                    CHECK (file_type IN
                    ('image/jpeg','image/png','application/pdf')),
  file_size_bytes   INT,
  document_type     TEXT NOT NULL
                    CHECK (document_type IN
                    ('prescription','lab_report','discharge_summary','other')),
  document_date     DATE,
  extraction_status TEXT DEFAULT 'pending'
                    CHECK (extraction_status IN
                    ('pending','processing','completed','failed','needs_review')),
  extraction_error  TEXT,
  is_deleted        BOOLEAN DEFAULT FALSE,
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  updated_at        TIMESTAMPTZ DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────
-- TABLE 5: document_extractions  (Layer 2)
-- OCR + LLM output. Never modified; version incremented on re-run.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS document_extractions (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  raw_ocr_text        TEXT,
  ocr_confidence      NUMERIC(4,3) CHECK (ocr_confidence BETWEEN 0 AND 1),
  ocr_model_used      TEXT,
  extracted_data      JSONB,
  extraction_model    TEXT,
  overall_confidence  NUMERIC(4,3) CHECK (overall_confidence BETWEEN 0 AND 1),
  field_confidences   JSONB,
  flagged_fields      JSONB,
  guardian_corrections JSONB,
  corrected_by        UUID REFERENCES user_profiles(id),
  corrected_at        TIMESTAMPTZ,
  extraction_version  INT DEFAULT 1,
  created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────
-- TABLE 6: doctors
-- Clinical metadata for attending physicians.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS doctors (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id           UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
  full_name            TEXT NOT NULL,
  specialty            TEXT,
  hospital_name        TEXT,
  city                 TEXT,
  phone                TEXT,
  email                TEXT,
  source               TEXT DEFAULT 'guardian_stated'
                       CHECK (source IN
                       ('guardian_stated','document_extracted','caregiver_stated')),
  source_document_id   UUID REFERENCES documents(id),
  is_primary_physician BOOLEAN DEFAULT FALSE,
  created_at           TIMESTAMPTZ DEFAULT NOW(),
  updated_at           TIMESTAMPTZ DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────
-- TABLE 7: medications  (Layer 3)
-- Confirmed medication list with full source trail.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS medications (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id             UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
  drug_name_generic      TEXT NOT NULL,
  drug_name_brand        TEXT,
  drug_name_original_ocr TEXT,
  dose_mg                NUMERIC(8,2),
  dose_unit              TEXT DEFAULT 'mg',
  frequency              TEXT
                         CHECK (frequency IN
                         ('once_daily','twice_daily','three_times_daily',
                          'as_needed','other')),
  timing                 TEXT,
  source                 TEXT NOT NULL
                         CHECK (source IN
                         ('guardian_stated','document_extracted','caregiver_stated')),
  source_document_id     UUID REFERENCES documents(id),
  source_extraction_id   UUID REFERENCES document_extractions(id),
  prescribing_doctor_id  UUID REFERENCES doctors(id),
  prescription_date      DATE,
  confirmed_by_guardian  BOOLEAN DEFAULT FALSE,
  confirmed_at           TIMESTAMPTZ,
  confirmation_note      TEXT,
  extraction_confidence  NUMERIC(4,3),
  is_current             BOOLEAN DEFAULT TRUE,
  is_otc                 BOOLEAN DEFAULT FALSE,
  is_supplement          BOOLEAN DEFAULT FALSE,
  currency_uncertain     BOOLEAN DEFAULT FALSE,
  is_deleted             BOOLEAN DEFAULT FALSE,
  deleted_reason         TEXT,
  created_at             TIMESTAMPTZ DEFAULT NOW(),
  updated_at             TIMESTAMPTZ DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────
-- TABLE 8: diagnoses  (Layer 3)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS diagnoses (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id           UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
  condition_name       TEXT NOT NULL,
  icd_code             TEXT,
  confirmation_status  TEXT NOT NULL DEFAULT 'suspected'
                       CHECK (confirmation_status IN
                       ('confirmed','suspected','borderline','ruled_out')),
  confirmed_by_guardian BOOLEAN DEFAULT FALSE,
  confirmed_at         TIMESTAMPTZ,
  source               TEXT NOT NULL
                       CHECK (source IN
                       ('guardian_stated','document_extracted','llm_inferred')),
  source_document_id   UUID REFERENCES documents(id),
  managing_doctor_id   UUID REFERENCES doctors(id),
  diagnosed_date       DATE,
  notes                TEXT,
  created_at           TIMESTAMPTZ DEFAULT NOW(),
  updated_at           TIMESTAMPTZ DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────
-- TABLE 9: lab_results  (Layer 3)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lab_results (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id           UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
  source_document_id   UUID NOT NULL REFERENCES documents(id),
  source_extraction_id UUID NOT NULL REFERENCES document_extractions(id),
  test_name            TEXT NOT NULL,
  test_category        TEXT,
  value_numeric        NUMERIC(12,4),
  value_text           TEXT,
  unit                 TEXT,
  reference_low        NUMERIC(12,4),
  reference_high       NUMERIC(12,4),
  is_flagged           BOOLEAN DEFAULT FALSE,
  flag_direction       TEXT
                       CHECK (flag_direction IN
                       ('high','low','critical_high','critical_low')),
  flag_acknowledged    BOOLEAN DEFAULT FALSE,
  flag_acknowledged_by UUID REFERENCES user_profiles(id),
  report_date          DATE NOT NULL,
  is_stale             BOOLEAN DEFAULT FALSE,
  lab_name             TEXT,
  extraction_confidence NUMERIC(4,3),
  created_at           TIMESTAMPTZ DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────
-- TABLE 10: allergies
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS allergies (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id         UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
  allergen           TEXT NOT NULL,
  reaction_type      TEXT,
  severity           TEXT CHECK (severity IN ('mild','moderate','severe','unknown')),
  source             TEXT CHECK (source IN ('guardian_stated','document_extracted')),
  source_document_id UUID REFERENCES documents(id),
  created_at         TIMESTAMPTZ DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────
-- TABLE 11: open_flags
-- Every unresolved item lives here. Drives post-onboarding monitoring.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS open_flags (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id           UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
  flag_type            TEXT NOT NULL
                       CHECK (flag_type IN (
                         'drug_interaction',
                         'lab_anomaly',
                         'stale_report',
                         'currency_unknown',
                         'ocr_low_confidence',
                         'missing_doctor_info',
                         'unconfirmed_diagnosis',
                         'conflict_unresolved',
                         'missing_test'
                       )),
  severity             TEXT NOT NULL
                       CHECK (severity IN ('low','medium','high','critical')),
  title                TEXT NOT NULL,
  description          TEXT NOT NULL,
  linked_document_id   UUID REFERENCES documents(id),
  linked_medication_id UUID REFERENCES medications(id),
  linked_lab_result_id UUID REFERENCES lab_results(id),
  guardian_response    TEXT,
  guardian_responded_at TIMESTAMPTZ,
  status               TEXT DEFAULT 'open'
                       CHECK (status IN
                       ('open','acknowledged','resolved','dismissed')),
  resolved_at          TIMESTAMPTZ,
  resolved_by          UUID REFERENCES user_profiles(id),
  resolution_note      TEXT,
  check_again_after    DATE,
  created_at           TIMESTAMPTZ DEFAULT NOW(),
  updated_at           TIMESTAMPTZ DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────
-- TABLE 12: patient_summaries
-- LLM-generated plain-language briefing. Fed into LLM context for queries.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS patient_summaries (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id         UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
  summary_text       TEXT NOT NULL,
  snapshot_data      JSONB,
  version            INT DEFAULT 1,
  generated_at       TIMESTAMPTZ DEFAULT NOW(),
  generated_by_model TEXT,
  trigger_event      TEXT,
  is_current         BOOLEAN DEFAULT TRUE
);

-- ────────────────────────────────────────────────────────────
-- UPDATED_AT TRIGGERS
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
  t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'user_profiles','patients','patient_guardians','documents',
    'doctors','medications','diagnoses','open_flags'
  ] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_updated_at ON %s', t, t);
    EXECUTE format(
      'CREATE TRIGGER trg_%s_updated_at
       BEFORE UPDATE ON %s
       FOR EACH ROW EXECUTE FUNCTION update_updated_at()',
      t, t
    );
  END LOOP;
END;
$$;

-- ────────────────────────────────────────────────────────────
-- ROW LEVEL SECURITY
-- Enable RLS on all patient-linked tables.
-- Backend uses service role key (bypasses RLS) for all writes.
-- Frontend Supabase client (anon key) is restricted by these policies.
-- ────────────────────────────────────────────────────────────
ALTER TABLE user_profiles       ENABLE ROW LEVEL SECURITY;
ALTER TABLE patients            ENABLE ROW LEVEL SECURITY;
ALTER TABLE patient_guardians   ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents           ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_extractions ENABLE ROW LEVEL SECURITY;
ALTER TABLE doctors             ENABLE ROW LEVEL SECURITY;
ALTER TABLE medications         ENABLE ROW LEVEL SECURITY;
ALTER TABLE diagnoses           ENABLE ROW LEVEL SECURITY;
ALTER TABLE lab_results         ENABLE ROW LEVEL SECURITY;
ALTER TABLE allergies           ENABLE ROW LEVEL SECURITY;
ALTER TABLE open_flags          ENABLE ROW LEVEL SECURITY;
ALTER TABLE patient_summaries   ENABLE ROW LEVEL SECURITY;

-- Users can read/update their own profile
CREATE POLICY "user_profiles_own" ON user_profiles
  FOR ALL USING (auth.uid() = id);

-- Users can see patient_guardian rows they own
CREATE POLICY "patient_guardians_own" ON patient_guardians
  FOR ALL USING (auth.uid() = user_id);

-- Helper: is the requesting user linked to this patient?
CREATE OR REPLACE FUNCTION user_linked_to_patient(pid UUID) RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    SELECT 1 FROM patient_guardians
    WHERE patient_id = pid AND user_id = auth.uid() AND is_active = TRUE
  );
$$ LANGUAGE sql SECURITY DEFINER;

-- Patients: accessible if user is linked
CREATE POLICY "patients_linked" ON patients
  FOR ALL USING (user_linked_to_patient(id));

-- Documents: accessible if user is linked to the patient
CREATE POLICY "documents_linked" ON documents
  FOR ALL USING (user_linked_to_patient(patient_id));

-- All other patient-linked tables share the same pattern
CREATE POLICY "document_extractions_linked" ON document_extractions
  FOR ALL USING (
    EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id AND user_linked_to_patient(d.patient_id))
  );

CREATE POLICY "doctors_linked"    ON doctors    FOR ALL USING (user_linked_to_patient(patient_id));
CREATE POLICY "medications_linked" ON medications FOR ALL USING (user_linked_to_patient(patient_id));
CREATE POLICY "diagnoses_linked"  ON diagnoses  FOR ALL USING (user_linked_to_patient(patient_id));
CREATE POLICY "lab_results_linked" ON lab_results FOR ALL USING (user_linked_to_patient(patient_id));
CREATE POLICY "allergies_linked"  ON allergies  FOR ALL USING (user_linked_to_patient(patient_id));
CREATE POLICY "open_flags_linked" ON open_flags  FOR ALL USING (user_linked_to_patient(patient_id));
CREATE POLICY "patient_summaries_linked" ON patient_summaries FOR ALL USING (user_linked_to_patient(patient_id));
