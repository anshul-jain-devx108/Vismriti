-- Demo warehouse for Vismriti.
--
-- Mounted by docker-compose.yml into the postgres container's
-- /docker-entrypoint-initdb.d, so it runs once on first boot of an empty
-- volume. Creates the same seven-table healthcare topology that the DataHub
-- fixtures and the seeded Azure DataHub instance describe, so a plan
-- generated from metadata can actually be executed against real rows.
--
-- Key layout is deliberately inconsistent across schemas, because real
-- warehouses are: the patient-facing tables key on patient_id, the
-- event/feature tables inherited a generic user_id from an older pipeline.
-- Vismriti resolves which column to use per table via VISMRITI_ID_COLUMN_MAP.
--
-- Derived tables also carry a <id_column>_hash column. Downstream pipelines
-- that dropped the raw integer id still keep the salted-free SHA-256 of the
-- subject's email, which is the only handle an erasure job has on them.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS marts;
CREATE SCHEMA IF NOT EXISTS analytics_sandbox;


-- Source of truth. Carries the PII columns DataHub tags as PII.email /
-- PII.phone / PII.name. Erasure anonymizes these in place rather than
-- deleting the row, so foreign keys from clinical records stay intact.
CREATE TABLE raw.patients (
    patient_id   INTEGER PRIMARY KEY,
    name         TEXT,
    email        TEXT,
    phone        TEXT,
    date_of_birth DATE,
    created_at   TIMESTAMPTZ DEFAULT now()
);

-- Second PII source. Keyed on user_id, not patient_id.
CREATE TABLE raw.support_tickets (
    ticket_id      INTEGER PRIMARY KEY,
    user_id        INTEGER,
    user_id_hash   TEXT,
    reporter_email TEXT,
    subject        TEXT,
    body           TEXT,
    created_at     TIMESTAMPTZ DEFAULT now()
);

-- Derived from raw.patients. No PII tags of its own, which is exactly why a
-- static catalog misses it.
CREATE TABLE raw.appointments (
    appointment_id   INTEGER PRIMARY KEY,
    user_id          INTEGER,
    user_id_hash     TEXT,
    appointment_date DATE,
    doctor           TEXT,
    notes            TEXT
);

-- dbt-managed model. Vismriti does not delete from it directly; it emits a
-- dbt run so the model rebuilds from the anonymized source.
CREATE TABLE staging.patients_clean (
    patient_id       INTEGER PRIMARY KEY,
    patient_id_hash  TEXT,
    name             TEXT,
    email            TEXT,
    is_active        BOOLEAN DEFAULT true
);

CREATE TABLE marts.patient_360 (
    patient_id       INTEGER PRIMARY KEY,
    patient_id_hash  TEXT,
    name             TEXT,
    email            TEXT,
    total_visits     INTEGER,
    lifetime_value   NUMERIC(10, 2)
);

-- Feature table behind churn_model_v3. Keyed on user_id.
CREATE TABLE marts.churn_features (
    user_id       INTEGER PRIMARY KEY,
    user_id_hash  TEXT,
    churn_score   NUMERIC(4, 3),
    visits_90d    INTEGER,
    last_seen_at  TIMESTAMPTZ
);

-- The residual-risk table: an analyst's fork with no owner and no tags.
-- Vismriti flags it for manual review instead of generating SQL, because
-- nobody can confirm what deleting from it would break.
CREATE TABLE analytics_sandbox.priya_analysis_2024 (
    patient_id  INTEGER,
    email       TEXT,
    cohort      TEXT,
    notes       TEXT
);


-- Demo subject: patient_id 48291, priya.sharma@example.com.
-- The *_hash values are SHA-256 of the lowercased, trimmed email, matching
-- vismriti.services.subject_resolver._sha256_hex.
INSERT INTO raw.patients (patient_id, name, email, phone, date_of_birth) VALUES
    (48291, 'Priya Sharma',  'priya.sharma@example.com', '+91-98200-11223', '1991-04-17'),
    (48292, 'Arjun Mehta',   'arjun.mehta@example.com',  '+91-98200-44556', '1988-11-02'),
    (48293, 'Fatima Khan',   'fatima.khan@example.com',  '+91-98200-77889', '1995-06-30');

INSERT INTO raw.support_tickets (ticket_id, user_id, user_id_hash, reporter_email, subject, body) VALUES
    (9001, 48291, '6cd1cf87520cfbc7e19f29ac8b858b773765202593cef5958de192930e121f34', 'priya.sharma@example.com', 'Billing query',    'Charged twice for the March consultation.'),
    (9002, 48292, NULL,                                                                'arjun.mehta@example.com',  'Reschedule',       'Need to move my appointment to next week.'),
    (9003, 48291, '6cd1cf87520cfbc7e19f29ac8b858b773765202593cef5958de192930e121f34', 'priya.sharma@example.com', 'Records request',  'Please send my discharge summary.');

INSERT INTO raw.appointments (appointment_id, user_id, user_id_hash, appointment_date, doctor, notes) VALUES
    (5001, 48291, '6cd1cf87520cfbc7e19f29ac8b858b773765202593cef5958de192930e121f34', '2024-03-11', 'Dr. Rao',    'Follow-up, routine.'),
    (5002, 48291, '6cd1cf87520cfbc7e19f29ac8b858b773765202593cef5958de192930e121f34', '2024-07-22', 'Dr. Iyer',   'Annual physical.'),
    (5003, 48293, NULL,                                                                '2024-08-02', 'Dr. Rao',    'Initial consult.');

INSERT INTO staging.patients_clean (patient_id, patient_id_hash, name, email) VALUES
    (48291, '6cd1cf87520cfbc7e19f29ac8b858b773765202593cef5958de192930e121f34', 'Priya Sharma', 'priya.sharma@example.com'),
    (48292, NULL,                                                                'Arjun Mehta',  'arjun.mehta@example.com');

INSERT INTO marts.patient_360 (patient_id, patient_id_hash, name, email, total_visits, lifetime_value) VALUES
    (48291, '6cd1cf87520cfbc7e19f29ac8b858b773765202593cef5958de192930e121f34', 'Priya Sharma', 'priya.sharma@example.com', 7, 41250.00),
    (48292, NULL,                                                                'Arjun Mehta',  'arjun.mehta@example.com',  2,  8100.00);

INSERT INTO marts.churn_features (user_id, user_id_hash, churn_score, visits_90d, last_seen_at) VALUES
    (48291, '6cd1cf87520cfbc7e19f29ac8b858b773765202593cef5958de192930e121f34', 0.184, 3, '2024-08-14T09:12:00Z'),
    (48292, NULL,                                                                0.702, 0, '2024-05-01T16:40:00Z');

INSERT INTO analytics_sandbox.priya_analysis_2024 (patient_id, email, cohort, notes) VALUES
    (48291, 'priya.sharma@example.com', 'high-value-retained', 'Ad-hoc fork for the Q3 retention deck. Never productionized.'),
    (48293, 'fatima.khan@example.com',  'new',                 'Comparison row.');


-- Read-only role used by the plan/verify path. The executor connects as the
-- owner because it issues UPDATE and DELETE; anything that only needs to
-- resolve a subject id should use this role instead.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vismriti_ro') THEN
        CREATE ROLE vismriti_ro LOGIN PASSWORD 'vismriti_ro';
    END IF;
END
$$;

GRANT USAGE ON SCHEMA raw, staging, marts, analytics_sandbox TO vismriti_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA raw, staging, marts, analytics_sandbox TO vismriti_ro;
