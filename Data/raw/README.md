# Hospital Management Dataset (synthetic)

The five CSV files in this folder are the unmodified files of:

- **Title:** Hospital Management Dataset — "Patient, Doctor, Appointment & Billing Records"
- **Author:** Kanak Baghel
- **Source:** https://www.kaggle.com/datasets/kanakbaghel/hospital-management-dataset (version 1)
- **License:** [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)

The data is synthetic: the people, phone numbers, emails and insurance numbers are not real.

Files (byte sizes match the Kaggle listing):

- `patients.csv` (5,626 bytes, 50 rows)
- `doctors.csv` (962 bytes, 10 rows)
- `appointments.csv` (10,907 bytes, 200 rows)
- `treatments.csv` (11,072 bytes, 200 rows)
- `billing.csv` (10,018 bytes, 200 rows)

The CC BY-SA 4.0 license applies to these data files. If you share a modified version of them, keep the
attribution and share it under the same license. MedLens's own code has no license file; ask the project
owner before reusing it outside the team.

`database/load_data.py` reads these files by name from this folder (`Data/raw/<table>.csv`).
