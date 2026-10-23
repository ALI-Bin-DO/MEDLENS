# MedLens Data Catalog

## Database overview
- Database name: medlens
- Database engine: MySQL
- Contains synthetic hospital data.
- Only read-only queries are allowed.
- Tables: patients, doctors, appointments, treatments, billing.
- The currency of monetary amounts is not specified.

## patients
Purpose: Stores patient registration and contact information.
Grain: One row per patient.
Primary key: patient_id.

Columns:
- patient_id: Unique patient identifier.
- first_name: Patient's first name.
- last_name: Patient's last name.
- gender: M or F.
- date_of_birth: Patient's birth date.
- contact_number: Phone number stored as text.
- address: Patient's address.
- registration_date: Date the patient registered.
- insurance_provider: Name of the insurance provider.
- insurance_number: Unique insurance number, when provided.
- email: Patient's email. Different patients may share an email.

## doctors
Purpose: Stores doctor information.
Grain: One row per doctor.
Primary key: doctor_id.

Columns:
- doctor_id: Unique doctor identifier.
- first_name: Doctor's first name.
- last_name: Doctor's last name.
- specialization: Doctor's medical specialty.
- phone_number: Phone number stored as text.
- years_experience: Years of professional experience.
- hospital_branch: Hospital branch associated with the doctor.
- email: Unique doctor's email.

## appointments
Purpose: Stores appointments between patients and doctors.
Grain: One row per appointment.
Primary key: appointment_id.

Columns:
- appointment_id: Unique appointment identifier.
- patient_id: References patients.patient_id.
- doctor_id: References doctors.doctor_id.
- appointment_date: Date of the appointment.
- appointment_time: Time of the appointment.
- reason_for_visit: Reason for the appointment.
- status: Scheduled, Completed, Cancelled, or No-show.

Relationships:
- appointments.patient_id = patients.patient_id
- appointments.doctor_id = doctors.doctor_id

## treatments
Purpose: Stores treatments associated with appointments.
Grain: One row per treatment.
Primary key: treatment_id.

Columns:
- treatment_id: Unique treatment identifier.
- appointment_id: References appointments.appointment_id.
- treatment_type: Type of treatment.
- description: Description of the treatment.
- cost: Non-negative treatment cost.
- treatment_date: Date the treatment occurred.

Relationships:
- treatments.appointment_id = appointments.appointment_id
- To find the patient's or doctor's information, join through appointments.

## billing
Purpose: Stores billing records associated with patients and treatments.
Grain: One row per bill.
Primary key: bill_id.

Columns:
- bill_id: Unique bill identifier.
- patient_id: References patients.patient_id.
- treatment_id: References treatments.treatment_id.
- bill_date: Date the bill was issued. There is no column for the date or time a bill was paid.
- amount: Non-negative billed amount.
- payment_method: Cash, Credit Card, or Insurance.
- payment_status: Paid, Pending, or Failed. It is the current status; the data does not record when it changed.

Relationships:
- billing.patient_id = patients.patient_id
- billing.treatment_id = treatments.treatment_id

## Query rules
- Generate read-only SELECT queries.
- Use only the tables and columns documented above.
- Use appointments.appointment_date for appointment dates.
- Use treatments.treatment_date for treatment dates.
- Use billing.bill_date for billing dates.
- Use billing.amount for billed amounts.
- Use treatments.cost for treatment costs.
- For paid bill totals, filter billing.payment_status = 'Paid'.
- Do not assume that billed amounts and treatment costs are interchangeable.
- Joining appointments to treatments may repeat an appointment.
- Joining treatments to billing may repeat a treatment.
- When counting unique patients, use COUNT(DISTINCT patient_id)
  with the appropriate table alias.
- The schema does not guarantee that billing.patient_id matches
  the patient associated with the treatment's appointment.

## Dates and time periods
- Date columns: registration_date, date_of_birth, appointment_date, treatment_date, bill_date.
  appointment_time is a time of day.
- Filter a period with a full-date range, start inclusive, end exclusive: March 2023 is
  col >= '2023-03-01' AND col < '2023-04-01'. MONTH(col) alone matches every year.
- Relative periods (this month, last month, last year) come from the calendar given with the
  question. Last month of January is December of the previous year.
- Group months with DATE_FORMAT(col, '%Y-%m'). Ages: TIMESTAMPDIFF(YEAR, date_of_birth, CURDATE()).

## Information that is not stored
- Payment date: billing has bill_date (issue date) and the current payment_status only. "Paid bills
  issued in March" is answerable; "bills paid in March" needs the user to accept bill_date first.
- Diagnoses, symptoms, prescriptions, test results, schedules, rooms, staff other than doctors,
  insurance claims, currencies and appointment end times are not stored.
- Say what is missing; offer a related column if one exists, otherwise explain it cannot be answered.

## Supported SQL
- One read-only SELECT with JOIN ... ON / LEFT JOIN ... ON, WHERE, GROUP BY, HAVING, ORDER BY, LIMIT, CASE.
- Not supported: WITH, subqueries, UNION, window functions. A question that needs a subquery
  (for example "treatments above their type's average") cannot be answered in one query.

## Business terms
- "Hospital" or "each hospital" means a hospital branch: doctors.hospital_branch, reached from
  appointments through doctors. There is no separate hospitals table.
- Revenue is not defined: the data has billed amounts (billing.amount) and payment_status. Ask whether
  the total billed amount or the total of paid bills is meant.
- Operating ratio, efficiency, utilization and similar ratios are not defined; ask what to divide by what.
- Profit and margin cannot be computed: no hospital expenses are stored.
- Doctors are named by first_name and last_name; titles such as "Dr." are not part of either.

## Appointments with their bills
- Path: appointments -> treatments (treatments.appointment_id) -> billing (billing.treatment_id).
  appointments.appointment_id and billing.treatment_id identify different things; never compare them.
- "All appointments" with invoice amounts: start FROM appointments and LEFT JOIN treatments and billing,
  whatever else is asked. Appointments without a treatment or bill stay in the result with NULL amounts.
  Conditions on treatments or billing go in their ON clause; in WHERE they remove those appointments.
- "Only appointments that have invoices" (or with a Paid bill, a bill above some amount): INNER JOIN.
- A missing invoice is NULL; a recorded bill can have amount 0. Show 0 for a missing total only when the
  question asks for zero (COALESCE(SUM(b.amount), 0)); otherwise leave it NULL.
- An appointment can have several treatments, and a treatment several bills: a list has one row per bill
  (individual invoices). For one row per appointment, GROUP BY the appointment and use COUNT(b.bill_id),
  SUM(b.amount).
- "Appointments" means every status unless the question names one (for example Completed).

## Counting after joins
- Joins to treatments or billing repeat rows of appointments, patients and doctors: count them with
  COUNT(DISTINCT a.appointment_id), COUNT(DISTINCT t.treatment_id); count bills with COUNT(b.bill_id).
- Do not SUM(t.cost) after joining billing: a treatment's cost repeats once per bill. Two treatments
  with equal costs are still two treatments, so never SUM(DISTINCT t.cost).
- The billed patient is billing.patient_id; the appointment's patient is appointments.patient_id. Use the
  one the question names; they are not assumed to match.
- Group by what the question names: "per branch" is GROUP BY d.hospital_branch only; "per doctor" groups
  by d.doctor_id (with the name and, if asked, the branch).
- GROUP BY a month or date lists only periods that have rows. Show a period with zero only when the
  question asks for it, with one COUNT(CASE WHEN ... THEN 1 END) column per requested period.
