# Data

**MIMIC-IV Clinical Database Demo, v2.2** (PhysioNet). De-identified electronic health records for
100 patients admitted to Beth Israel Deaconess Medical Center, Boston, in 31 tables
(`hosp/` hospital-wide data, `icu/` ICU data). Free-text notes are not included.

- Source: https://physionet.org/content/mimic-iv-demo/2.2/
- License: Open Data Commons Open Database License v1.0 (ODbL), full text in
  `raw/mimic-iv-demo-2.2/LICENSE.txt`. The files are included here unchanged, as the license allows,
  so the project runs without a separate download.
- Integrity: `raw/mimic-iv-demo-2.2/SHA256SUMS.txt` (all 34 files verified).

## Citations
- Johnson, A., Bulgarelli, L., Pollard, T., Horng, S., Celi, L. A., & Mark, R. (2023).
  MIMIC-IV Clinical Database Demo (version 2.2). PhysioNet. https://doi.org/10.13026/dp1f-ex47
- Johnson, A.E.W., Bulgarelli, L., Shen, L. et al. MIMIC-IV, a freely accessible electronic health
  record dataset. Sci Data 10, 1 (2023). https://doi.org/10.1038/s41597-022-01899-x
- PhysioNet standard citation, as requested on the dataset page: Pollard et al. (2026), Nature Health,
  https://doi.org/10.1038/s44360-026-00096-z

## Building the database
`python -m src.build_db` loads every `.csv.gz` into `data/mimic_demo.duckdb` (about 37 MB, not
committed). It also happens automatically the first time the app or evaluation needs it.
