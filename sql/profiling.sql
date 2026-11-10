-- profiling.sql: the data-profiling queries that uncovered the traps documented in
-- src/catalog.yaml and targeted by the benchmark questions.
-- Run with: python -m scripts.profile_data   (writes reports/profiling.md)
-- Each query is preceded by a "name" comment so the runner can label it.

-- name: Table sizes and the ID hierarchy (patients > admissions > ICU stays)
SELECT
    (SELECT COUNT(*) FROM patients)                         AS patients,
    (SELECT COUNT(*) FROM admissions)                       AS admissions,
    (SELECT COUNT(*) FROM icustays)                         AS icu_stays,
    (SELECT COUNT(DISTINCT subject_id) FROM icustays)       AS patients_with_icu_stay,
    (SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM admissions GROUP BY subject_id)) AS max_admissions_one_patient;

-- name: Trap - ICD codes that exist in BOTH version 9 and version 10 (join on code AND version)
SELECT COUNT(*) AS codes_in_both_versions
FROM (
    SELECT icd_code FROM d_icd_diagnoses
    GROUP BY icd_code
    HAVING COUNT(DISTINCT icd_version) > 1
);

-- name: Trap - lab test names that map to more than one itemid
SELECT d.label, d.itemid, d.fluid, d.category, COUNT(l.itemid) AS results
FROM d_labitems d
LEFT JOIN labevents l ON l.itemid = d.itemid
WHERE d.label IN ('Glucose', 'Creatinine', 'Potassium', 'Hemoglobin')
GROUP BY ALL
ORDER BY d.label, results DESC;

-- name: Trap - look-alike lab names ('%lactate%' also matches lactate dehydrogenase)
SELECT d.label, d.itemid, d.fluid, COUNT(*) AS results, MAX(l.valuenum) AS max_value
FROM labevents l
JOIN d_labitems d ON l.itemid = d.itemid
WHERE d.label ILIKE '%lactate%'
GROUP BY ALL
ORDER BY results DESC;

-- name: Trap - look-alike ICU items ('%heart rate%' also matches alarm settings)
SELECT d.itemid, d.label, d.category, COUNT(*) AS measurements, ROUND(AVG(c.valuenum), 1) AS avg_value
FROM chartevents c
JOIN d_items d ON c.itemid = d.itemid
WHERE d.label ILIKE '%heart rate%'
GROUP BY ALL
ORDER BY measurements DESC;

-- name: Trap - '%invasive ventilation%' also matches Non-invasive ventilation
SELECT d.itemid, d.label, COUNT(DISTINCT p.stay_id) AS icu_stays
FROM procedureevents p
JOIN d_items d ON p.itemid = d.itemid
WHERE d.label ILIKE '%invasive ventilation%'
GROUP BY ALL
ORDER BY icu_stays DESC;

-- name: Trap - DRG codes come in two systems (admissions counted twice without a filter)
SELECT drg_type,
       COUNT(*)                AS rows,
       COUNT(DISTINCT hadm_id) AS admissions
FROM drgcodes
GROUP BY drg_type;

-- name: Trap - microbiology has one row per organism x antibiotic; 'CANCELLED' is not an organism
SELECT org_name,
       COUNT(*)                          AS rows,
       COUNT(DISTINCT micro_specimen_id) AS specimens
FROM microbiologyevents
WHERE org_name IN ('ESCHERICHIA COLI', 'CANCELLED')
GROUP BY org_name;

-- name: Trap - interpretation codes are single letters, not words
SELECT interpretation, COUNT(*) AS results
FROM microbiologyevents
GROUP BY interpretation
ORDER BY results DESC;

-- name: Trap - outpatient measurements are stored as text with units in the name
SELECT result_name, COUNT(*) AS rows, MIN(result_value) AS example_value
FROM omr
GROUP BY result_name
ORDER BY rows DESC
LIMIT 6;

-- name: Trap - dates are shifted into the future for privacy
SELECT MIN(admittime) AS first_admission, MAX(dischtime) AS last_discharge
FROM admissions;

-- name: Trap - anchor_age is age in the anchor year, not at each admission
SELECT p.subject_id, p.anchor_age, p.anchor_year, a.admittime,
       p.anchor_age + year(a.admittime) - p.anchor_year AS age_at_admission
FROM admissions a
JOIN patients p ON a.subject_id = p.subject_id
WHERE year(a.admittime) <> p.anchor_year
ORDER BY p.subject_id, a.admittime
LIMIT 5;
